"""无边框窗口的公共外壳：补 WS_THICKFRAME、DWM 圆角、边缘按下交给系统缩放。

主窗与草稿窗共用这一份（原先只在 MainWindow 里各写一遍）。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from sketchpad.ui.theme import OUTER_BORDER

EDGE = 6                    # 判定"压在边缘"的像素
_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000
# SWP_NOSIZE|SWP_NOMOVE|SWP_NOZORDER|SWP_FRAMECHANGED
_SWP_KEEP_PLACE_FRAME_CHANGED = 0x0027
# SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
_SWP_TOPMOST_ONLY = 0x0013
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2


class WindowBorder(QWidget):
    """无边框窗口最外层的 1px 圆角边框；透明、鼠标穿透，只负责可见边界。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)
        self.raise_()

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.parentWidget() and ev.type() == QEvent.Resize:
            self.setGeometry(self.parentWidget().rect())
            self.raise_()
        return False

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(OUTER_BORDER))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0), 10.0, 10.0)


def install_window_border(window: QWidget) -> WindowBorder:
    """在主窗/草稿窗最上层装一条可见外框；父控件持有它，调用方留引用即可。"""
    return WindowBorder(window)


def set_native_topmost(window: QWidget, on: bool) -> bool:
    """Win32 直接切置顶，不隐藏窗口（QWidget.setWindowFlag 会 hide/show）。

    返回 False 表示当前平台/句柄不支持，调用方自行回退。成功后窗口的
    Z 序立刻变化，几何、滚动位置与视图缩放都不受影响。
    """
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint)
        user32.SetWindowPos.restype = wintypes.BOOL
        hwnd = int(window.winId())
        if not hwnd:
            return False
        insert_after = _HWND_TOPMOST if on else _HWND_NOTOPMOST
        return bool(user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                                        _SWP_TOPMOST_ONLY))
    except Exception:
        return False                # 非 Windows / 句柄还没建好：交给调用方回退


def enable_native_resize(window: QWidget) -> None:
    """无边框窗口没有 WS_THICKFRAME：只靠 WM_NCHITTEST 给 HTLEFT 也进不了缩放循环。

    把该样式位补回来（只加样式、不重画边框），边缘缩放才真正生效。
    """
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint)
        hwnd = int(window.winId())
        style = user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
        if not style & _WS_THICKFRAME:
            user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style | _WS_THICKFRAME)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_KEEP_PLACE_FRAME_CHANGED)
    except Exception:
        pass                    # 非 Windows / 句柄还没建好：保持原样，不影响功能


def apply_rounded_corners(window: QWidget) -> None:
    """Windows 11：给无边框窗口加 DWM 圆角；失败就保持直角，不影响功能。"""
    try:
        value = ctypes.c_int(2)          # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()), 33, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def native_hit_test(window: QWidget, event_type, message) -> int | None:
    """WM_NCHITTEST：光标落在哪条边上就返回对应的 HT* 值，其余消息返回 None。

    边缘要显示缩放光标、并让系统认下这次按下，光靠 EdgeResizeFilter 不够。
    """
    if event_type != b"windows_generic_MSG" or window.isMaximized():
        return None
    try:
        msg = wintypes.MSG.from_address(int(message))
    except Exception:
        return None
    if msg.message != 0x0084:                # WM_NCHITTEST
        return None
    x = ctypes.c_short(msg.lParam & 0xFFFF).value
    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
    # 用 Win32 外框矩形判定：Qt 的 mapFromGlobal/width 在无边框 + 高 DPI
    # 下会与 lParam 的物理屏幕坐标错位，曾把窗口右/下大片区域误判成边缘。
    try:
        hwnd = int(window.winId())
        rect = wintypes.RECT()
        ok = ctypes.windll.user32.GetWindowRect(
            ctypes.c_void_p(hwnd), ctypes.byref(rect))
        if not ok or rect.right <= rect.left or rect.bottom <= rect.top:
            return None                  # 句柄未就绪/取不到外框：别猜边缘
        left = x - rect.left <= EDGE
        right = rect.right - x <= EDGE
        top = y - rect.top <= EDGE
        bottom = rect.bottom - y <= EDGE
    except Exception:
        return None
    if top and left:
        return 13                            # HTTOPLEFT
    if top and right:
        return 14                            # HTTOPRIGHT
    if bottom and left:
        return 16                            # HTBOTTOMLEFT
    if bottom and right:
        return 17                            # HTBOTTOMRIGHT
    if left:
        return 10                            # HTLEFT
    if right:
        return 11                            # HTRIGHT
    if top:
        return 12                            # HTTOP
    if bottom:
        return 15                            # HTBOTTOM
    return None


class EdgeResizeFilter(QObject):
    """应用级过滤器：左键压在窗口边缘 6px 内 → 让系统开始缩放。

    装在 QApplication 上而不是窗口上：按下发生在子控件（画布视口、工具栏）里，
    窗口级过滤器看不到。每个窗口一个实例，只认 obj.window() 是自己那个窗口的事件；
    实例同时挂在 window._edge_resize_filter 上，避免被 Python 回收（PROGRESS §5）。
    """

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)         # 父对象是窗口：窗口销毁时过滤器一起走
        self._window = window

    @classmethod
    def install(cls, window: QWidget) -> "EdgeResizeFilter":
        """建过滤器、装到应用级并留引用；重复安装返回已有的那个。"""
        existing = getattr(window, "_edge_resize_filter", None)
        if isinstance(existing, cls):
            return existing
        filter_ = cls(window)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(filter_)
        window._edge_resize_filter = filter_
        return filter_

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            try:
                if obj.window() is self._window and self.start_at(
                        ev.globalPosition().toPoint()):
                    return True
            except Exception:
                pass                    # 窗口/子控件正在销毁时别把事件链带崩
        return False

    def start_at(self, global_pos: QPoint) -> bool:
        """光标压在窗口边缘 6px 内按下 → 交给系统开始缩放。"""
        window = self._window
        if window.isMaximized() or window.isFullScreen():
            return False
        rect = window.geometry()
        if not rect.contains(global_pos):
            return False
        edges = Qt.Edges()
        if global_pos.x() - rect.left() <= EDGE:
            edges |= Qt.LeftEdge
        elif rect.right() - global_pos.x() <= EDGE:
            edges |= Qt.RightEdge
        if global_pos.y() - rect.top() <= EDGE:
            edges |= Qt.TopEdge
        elif rect.bottom() - global_pos.y() <= EDGE:
            edges |= Qt.BottomEdge
        if not edges:
            return False
        handle = window.windowHandle()
        if handle is None:
            return False
        return bool(handle.startSystemResize(edges))
