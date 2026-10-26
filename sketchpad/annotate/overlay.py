"""透明置顶批注层：铺满主屏，负责原生窗口样式、模式切换与 PNG 导出。

Win32 扩展样式是这个窗口的核心（Qt 的窗口标志管不到它们）：

- ``WS_EX_TOOLWINDOW``   不进任务栏 / Alt+Tab，始终保留；
- ``WS_EX_NOACTIVATE``   穿透与隐藏时必须加；draw 模式必须去掉，否则拿不到
  键盘焦点，B / Q / E 这些快捷键直接失效；
- ``WS_EX_TRANSPARENT``  穿透模式加，鼠标点击落到底下的软件；
- ``WS_EX_LAYERED``      Qt 透明窗口自带，永远不要关掉。

穿透模式整层都不接收点击，连工具栏也点不动。所以工具栏本身必须是一个独立的
顶层窗口（Toolbar 自己就是独立 hwnd），它不挂在覆盖层下面，不受 WS_EX_TRANSPARENT
影响：穿透模式下工具栏照常显示、照常可点，点那个变绿的穿透按钮就回到批注。
"""
from __future__ import annotations

import ctypes
import sys
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from sketchpad.annotate.canvas import Canvas
from sketchpad.annotate.config import Config
from sketchpad.annotate.constants import ACCENT, APP_NAME, TOOLBAR_MARGIN_TOP
from sketchpad.annotate.toolbar import ORIENT_VERTICAL, Toolbar

# --- Win32 常量（与 windows.h 一致） ---
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200

SELF_HEAL_MS = 250               # 自愈巡检间隔


class _POINT(ctypes.Structure):
    """WindowFromPoint 的参数必须是结构体按值传，塞成整数会返回 0。"""
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# 延迟加载的 user32；非 Windows 或加载失败时保持为空
_WIN32: dict = {"user32": None, "get": None, "set": None, "failed": False}


_FULLSCREEN_STATE = {"failed": False, "query": None}


def exclusive_fullscreen_active() -> bool:
    """前台是否处于独占全屏/演示模式：这种状态下普通 TOPMOST 无法叠加。"""
    if sys.platform != "win32" or _FULLSCREEN_STATE["failed"]:
        return False
    if _FULLSCREEN_STATE["query"] is None:
        try:
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            query = shell32.SHQueryUserNotificationState
            query.argtypes = [ctypes.POINTER(ctypes.c_int)]
            query.restype = ctypes.c_long
            _FULLSCREEN_STATE["query"] = query
        except (OSError, AttributeError):
            _FULLSCREEN_STATE["failed"] = True
            return False
    try:
        state = ctypes.c_int()
        if _FULLSCREEN_STATE["query"](ctypes.byref(state)) != 0:
            return False
        # 3 = QUNS_RUNNING_D3D_FULL_SCREEN, 4 = QUNS_PRESENTATION_MODE
        return state.value in (3, 4)
    except (OSError, ValueError):
        return False


def _win32_api() -> dict | None:
    """取 user32 及已设原型的读写句柄函数；不可用时返回 None。"""
    if sys.platform != "win32" or _WIN32["failed"]:
        return None
    if _WIN32["user32"] is not None:
        return _WIN32
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_style = getattr(user32, "GetWindowLongPtrW", None)
        set_style = getattr(user32, "SetWindowLongPtrW", None)
        if get_style is None or set_style is None:      # 32 位 Python 没有 Ptr 版本
            get_style, set_style = user32.GetWindowLongW, user32.SetWindowLongW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_int
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.SetForegroundWindow.restype = ctypes.c_int
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.IsWindow.argtypes = [ctypes.c_void_p]
        user32.IsWindow.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = ctypes.c_uint
        user32.AttachThreadInput.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
        user32.AttachThreadInput.restype = ctypes.c_int
        user32.SetFocus.argtypes = [ctypes.c_void_p]
        user32.SetFocus.restype = ctypes.c_void_p
        user32.GetCapture.argtypes = []
        user32.GetCapture.restype = ctypes.c_void_p
        user32.ReleaseCapture.argtypes = []
        user32.ReleaseCapture.restype = ctypes.c_int
        user32.WindowFromPoint.argtypes = [_POINT]
        user32.WindowFromPoint.restype = ctypes.c_void_p
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = ctypes.c_uint
    except (OSError, AttributeError):
        _WIN32["failed"] = True
        return None
    _WIN32.update(user32=user32, kernel32=kernel32, get=get_style, set=set_style)
    return _WIN32


class OverlayWindow(QWidget):
    """铺在主屏上的批注层；三种模式：hidden / draw / passthrough。"""

    quitRequested = Signal()
    trayMessage = Signal(str)
    modeChanged = Signal(str)       # hidden / draw / passthrough

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.config = config
        self.mode = "hidden"                 # hidden | draw | passthrough
        self._screen = None
        self._primary_connected = False
        # 进 draw 前的前台窗口：切穿透/隐藏时还给它，别让画布继续吃 B/Q/E
        self._previous_foreground = 0

        self.setWindowTitle(APP_NAME)
        # 逐像素透明 + 完全自绘：窗口本身不铺任何底色，视频画面才能透出来
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        # 隐藏 / 穿透状态下 show() 不应激活自己，避免把正在看的视频挤到后台
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.canvas = Canvas(self)
        self.canvas.set_color(self.config.color)
        self.canvas.set_pen_width(self.config.pen_width)
        # 工具栏是独立顶层窗口（不传 parent）：穿透模式下覆盖层整层点穿，
        # 挂在它下面的子控件会一起失联，工具栏必须自己接点击。
        self.toolbar = Toolbar(self.config.toolbar_orientation)
        self.toolbar.setFocusPolicy(Qt.NoFocus)
        self.toolbar.hide()

        self._wire()
        self._bind_screen()
        self.sync_geometry()
        self._sync_toolbar()
        self._apply_win32_styles()

        # 自愈巡检：数位板驱动和 Windows 的鼠标捕获偶尔会失配，画布按住一次之后
        # capture 没释放，所有点击都被批注层吃掉（"画着画着工具栏就点不动"）。
        # 定时掰回来，比等用户重启靠谱。
        self._self_heal_timer = QTimer(self)
        self._self_heal_timer.setInterval(SELF_HEAL_MS)
        self._self_heal_timer.timeout.connect(self._self_heal)
        self._self_heal_timer.start()
        self._fullscreen_warned = False

    # ---------- 初始化 ----------

    def _wire(self) -> None:
        toolbar = self.toolbar
        toolbar.toolChosen.connect(self._on_tool_chosen)
        toolbar.colorChosen.connect(self._on_color_chosen)
        toolbar.widthChosen.connect(self._on_width_chosen)
        toolbar.undoRequested.connect(self._on_undo)
        toolbar.clearRequested.connect(self._on_clear)
        toolbar.passthroughToggled.connect(self._on_passthrough_toggled)
        toolbar.hideRequested.connect(self.hide_overlay)
        toolbar.orientationChanged.connect(self._on_toolbar_orientation)
        toolbar.moved.connect(self._on_toolbar_moved)

        self.canvas.changed.connect(self._sync_toolbar)
        self.canvas.escapePressed.connect(self._on_escape)
        self.canvas.saveRequested.connect(self.save_png)

    def _bind_screen(self) -> None:
        """只服务主屏：主屏几何变了就重新铺满（含任务栏区域）。"""
        self._sync_screen()
        if not self._primary_connected:
            app = QGuiApplication.instance()
            if app is not None:
                app.primaryScreenChanged.connect(self._on_primary_screen_changed)
                self._primary_connected = True

    def _sync_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is self._screen:
            return
        old = self._screen
        if old is not None:
            try:
                old.geometryChanged.disconnect(self._on_screen_geometry_changed)
            except (RuntimeError, TypeError):
                pass
        self._screen = screen
        if screen is not None:
            screen.geometryChanged.connect(self._on_screen_geometry_changed)

    def _on_screen_geometry_changed(self, *_args) -> None:
        self.sync_geometry()

    def _on_primary_screen_changed(self, *_args) -> None:
        self._sync_screen()
        self.sync_geometry()

    def sync_geometry(self) -> None:
        """窗口 = 主屏完整几何（含任务栏）；画布铺满，工具栏/浮条顶部居中。"""
        self._sync_screen()
        screen = self._screen or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.canvas.setGeometry(self.rect())
        self._place_controls()

    def _place_controls(self) -> None:
        """工具栏定位：优先用用户拖到的位置，否则按方向取默认位。

        横排贴顶居中；竖排比横排高一大截，所以贴右边、垂直居中，尽量别挡住
        正在看的内容。两种排布尺寸完全不同，每次都要按 sizeHint 重新夹回屏内——
        换了分辨率或把工具栏拖出屏幕，都不会让它凭空消失。
        """
        screen = self._screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.geometry()
        hint = self.toolbar.sizeHint()
        width = min(max(int(hint.width()), 0), max(area.width(), 0))
        height = min(max(int(hint.height()), 0), max(area.height(), 0))

        pos = self.config.toolbar_pos
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
        else:
            x, y = self._default_toolbar_pos(area, width, height)
        x = max(area.left(), min(x, area.right() - width + 1))
        y = max(area.top(), min(y, area.bottom() - height + 1))
        self._resize_toolbar(x, y, width, height)

    def _resize_toolbar(self, x: int, y: int, width: int, height: int) -> None:
        """摆好工具栏的几何；尺寸变了要先拆掉分层窗口表面再重建。

        工具栏是 WA_TranslucentBackground 的顶层窗口（Windows 上是分层窗口）。
        直接 setGeometry 到完全不同的尺寸时，旧的分层位图不会跟着重建：画面被
        拉成畸形，鼠标命中还按旧位图算——除左上角一小条外整条点不动，只能靠
        hide()/show() 让 Qt 重新创建表面。
        """
        bar = self.toolbar
        visible = bar.isVisible()
        resized = bar.size() != QSize(width, height)
        if visible and resized:
            bar.hide()
        bar.setGeometry(x, y, width, height)
        if visible:
            bar.show()
            bar.raise_()
            # 分层窗口 hide/show 后可能掉到覆盖层下面，补一次原生置顶
            self._raise_toolbar_native()

    def _default_toolbar_pos(self, area, width: int, height: int) -> tuple[int, int]:
        """没设过位置时的落点：横排顶部居中，竖排右侧垂直居中。"""
        if self.toolbar.orientation == ORIENT_VERTICAL:
            return (area.right() - width - TOOLBAR_MARGIN_TOP,
                    area.top() + max(0, (area.height() - height) // 2))
        return (area.left() + max(0, (area.width() - width) // 2),
                area.top() + TOOLBAR_MARGIN_TOP)

    def _on_toolbar_orientation(self, orientation: str) -> None:
        """横排 / 竖排切换：尺寸整个变了，清掉旧位置、回到该方向的默认位并落盘。

        切完必须强制回到 draw 模式并重新提顶：换方向会让工具栏窗口整个重排、
        show 一次，z 序和分层表面都可能落在覆盖层下面，表现为"看得见但整条点不动"。
        这里直接走一遍 enter_draw_mode，保证形态变化后一定是可自由点击的状态。
        """
        if orientation == ORIENT_VERTICAL:
            # 竖排是一条长列（20 多个控件竖着排），矮屏上底部按钮会掉出屏幕、
            # 点都点不到。先按竖排量一下高度，塞不下就当场退回横排。
            self.toolbar.set_orientation(ORIENT_VERTICAL, notify=False)
            screen = self._screen or QGuiApplication.primaryScreen()
            if (screen is not None
                    and self.toolbar.sizeHint().height() > screen.geometry().height()):
                self.toolbar.set_orientation("horizontal", notify=False)
                self.trayMessage.emit("屏幕高度放不下竖排工具栏，已保持横排")
                self.enter_passthrough_mode()
                return
        self.config.toolbar_orientation = orientation
        # 竖排比横排高一截，沿用刚才的坐标多半会顶出屏幕，直接归位更省事
        self.config.toolbar_pos = None
        self.config.save()
        # 用户 2026-10-24：切横/竖排后强制回穿透，避免工具栏重排期间误拦点击
        self.enter_passthrough_mode()

    def _on_toolbar_moved(self, x: int, y: int) -> None:
        """拖动结束：按屏内边界夹一遍再写进配置，下次启动还在原地。"""
        self.config.toolbar_pos = [int(x), int(y)]
        self._place_controls()
        rect = self.toolbar.geometry()
        self.config.toolbar_pos = [int(rect.x()), int(rect.y())]
        self.config.save()

    # ---------- 模式切换 ----------

    def enter_draw_mode(self) -> None:
        """批注模式：拿键盘焦点、画边框、吃掉所有鼠标点击。"""
        hwnd = self._hwnd()
        foreground = self._foreground()
        if foreground and foreground != hwnd:
            self._previous_foreground = foreground
        self.mode = "draw"
        self.sync_geometry()             # 先把工具栏摆到屏幕位置，再亮出来
        # 先去掉 NOACTIVATE 再显示，否则窗口没有资格被激活
        self._apply_win32_styles()
        if not self.isVisible():
            self.show()
        self._apply_win32_styles()
        self.raise_()                    # Qt 层
        self.activateWindow()
        self._raise_native()
        self._show_toolbar()             # 工具栏必须压在覆盖层之上：晚一步提顶
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)
        self._sync_toolbar()
        self.update()
        self.modeChanged.emit(self.mode)

    def enter_passthrough_mode(self) -> None:
        """穿透模式：笔迹还看得见，但点击全部落到底下的软件。

        工具栏留在原处、不隐藏——它自己是独立顶层窗口，不受覆盖层的
        WS_EX_TRANSPARENT 波及，照样点得动；穿透按钮这时变绿，再点一次回批注。
        """
        self.mode = "passthrough"
        self.sync_geometry()
        if not self.isVisible():
            self.show()                  # WA_ShowWithoutActivating：不会抢焦点
        self._apply_win32_styles()
        self._show_toolbar()
        # 画布继续持有焦点的话，B/Q/E 仍会被批注层吃掉；交还给进 draw 前的窗口
        self.canvas.clearFocus()
        self._restore_foreground()
        self._sync_toolbar()
        self.update()
        self.modeChanged.emit(self.mode)

    def hide_overlay(self) -> None:
        """整层收起；配置和笔迹都留在内存里，下次出来还在。"""
        self.mode = "hidden"
        # 笔还按着时收起：先收手势并释放鼠标捕获，否则确认框/其它窗口收不到点击
        try:
            self.canvas.abort_gesture()
        except Exception:
            pass
        api = _win32_api()
        if api is not None:
            try:
                api["user32"].ReleaseCapture()
            except OSError:
                pass
        self.hide()
        self.toolbar.hide()
        self.canvas.clearFocus()
        self._restore_foreground()
        self._apply_win32_styles()
        self.update()
        self.modeChanged.emit(self.mode)

    def toggle_mode(self) -> None:
        """隐藏时默认以穿透打开；可见时在批注/穿透间切换。"""
        if self.mode == "hidden":
            self.enter_passthrough_mode()
        elif self.mode == "draw":
            self.enter_passthrough_mode()
        else:
            self.enter_draw_mode()

    # ---------- Win32 样式 ----------

    def _hwnd(self) -> int:
        try:
            return int(self.winId())
        except RuntimeError:             # 窗口已销毁
            return 0

    def _apply_win32_styles(self) -> None:
        """按当前模式拼扩展样式，改完立刻重申置顶。"""
        api = _win32_api()
        if api is None:
            return
        hwnd = self._hwnd()
        if hwnd == 0:
            return
        try:
            current = int(api["get"](ctypes.c_void_p(hwnd), GWL_EXSTYLE))
            # 只动自己管的两个开关；LAYERED 与 TOOLWINDOW 永远保留
            style = (current | WS_EX_TOOLWINDOW | WS_EX_LAYERED) \
                & ~(WS_EX_NOACTIVATE | WS_EX_TRANSPARENT)
            if self.mode != "draw":
                style |= WS_EX_NOACTIVATE
            if self.mode == "passthrough":
                style |= WS_EX_TRANSPARENT
            if style != current:
                api["set"](ctypes.c_void_p(hwnd), GWL_EXSTYLE, style)
            api["user32"].SetWindowPos(
                ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST),
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        except (OSError, ValueError):
            return

    def _reassert_overlay_topmost(self) -> None:
        """把覆盖层重新提成 TOPMOST，不抢焦点。

        其他软件刚进入无边框全屏/置顶时可能压到批注层上面；每 250ms 轻量
        重申一次，笔迹才能在穿透模式下继续可见。独占全屏由 _self_heal 提前
        拦截并提示，不在那里硬顶。
        """
        api = _win32_api()
        if api is None:
            return
        hwnd = self._hwnd()
        if hwnd == 0 or not self.isVisible():
            return
        try:
            api["user32"].SetWindowPos(
                ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST),
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        except (OSError, ValueError):
            return

    def _raise_native(self) -> None:
        """把本进程顶到前台。

        置顶工具窗光靠 Qt 的 activateWindow 不够；从穿透回 draw 时 Windows 的
        前台锁又会拦住 SetForegroundWindow。AttachThreadInput 把输入队列临时
        接到当前前台线程上，这是不注入进程的标准做法。
        """
        api = _win32_api()
        if api is None:
            return
        hwnd = self._hwnd()
        if hwnd == 0:
            return
        user32, kernel32 = api["user32"], api["kernel32"]
        try:
            foreground = int(user32.GetForegroundWindow() or 0)
            if foreground == hwnd:
                return
            current = int(kernel32.GetCurrentThreadId())
            owner = (int(user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground), None))
                     if foreground else 0)
            attached = False
            if owner and owner != current:
                attached = bool(user32.AttachThreadInput(owner, current, True))
            try:
                user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
                user32.SetFocus(ctypes.c_void_p(hwnd))
            finally:
                if attached:
                    user32.AttachThreadInput(owner, current, False)
        except OSError:
            return

    def _foreground(self) -> int:
        api = _win32_api()
        if api is None:
            return 0
        try:
            return int(api["user32"].GetForegroundWindow() or 0)
        except OSError:
            return 0

    def _restore_foreground(self) -> None:
        """把前台还给进 draw 前的窗口；窗口已关就静默放弃。"""
        api = _win32_api()
        target, self._previous_foreground = self._previous_foreground, 0
        if api is None or not target or target == self._hwnd():
            return
        try:
            if api["user32"].IsWindow(ctypes.c_void_p(target)):
                api["user32"].SetForegroundWindow(ctypes.c_void_p(target))
        except OSError:
            return

    # ---------- 画布 / 工具栏 ----------

    def _sync_toolbar(self) -> None:
        """画布状态变化（落笔、撤销、换色、换工具）后刷新工具栏。"""
        self.toolbar.reflect_tool(getattr(self.canvas, "tool", "pen"))
        self.toolbar.reflect_color(getattr(self.canvas, "color", self.config.color))
        self.toolbar.reflect_width(getattr(self.canvas, "pen_width",
                                           self.config.pen_width))
        history = getattr(self.canvas, "history", None)
        self.toolbar.reflect_history(bool(getattr(history, "can_undo", False)))
        self.toolbar.reflect_mode(self.mode)   # 穿透时那颗按钮要变绿

    def _show_toolbar(self) -> None:
        """显示工具栏并重新提到最上：两位都是 TOPMOST，谁后提谁在上面。"""
        self._place_controls()
        self.toolbar.show()
        self.toolbar.raise_()
        self._raise_toolbar_native()

    def _raise_toolbar_native(self) -> None:
        """把工具栏窗口提成覆盖层之上的 TOPMOST。

        覆盖层和工具栏都是 TOPMOST，z 序由调用顺序决定；覆盖层刚被 SetWindowPos
        顶到前台时会盖上来，这里补一刀，保证按钮始终点得到。
        """
        api = _win32_api()
        if api is None:
            return
        try:
            hwnd = int(self.toolbar.winId())
        except RuntimeError:             # 窗口已销毁
            return
        if hwnd == 0:
            return
        try:
            api["user32"].SetWindowPos(
                ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST),
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        except OSError:
            return

    def _self_heal(self) -> None:
        """兜底巡检：把"鼠标捕获泄漏"和"工具栏被盖住"这两种失效掰回来。

        只看两个硬指标，不做猜测：
        1. `GetCapture()` 还挂在批注层上，但左键早就松开了 → 收笔 + 释放捕获。
           这是"画多了就点不动工具栏"最可能的成因：capture 不释放时，鼠标消息
           全进批注层，工具栏连 WM_LBUTTONDOWN 都收不到。
        2. 工具栏中心点 `WindowFromPoint` 命中的不是它自己 → 重新提顶。
        """
        if self.mode == "hidden":
            return
        api = _win32_api()
        if api is None:
            return
        user32 = api["user32"]
        hwnd = self._hwnd()

        buttons = QGuiApplication.mouseButtons()
        try:
            capture = int(user32.GetCapture() or 0)
        except (OSError, ValueError):
            capture = 0
        if capture and not (buttons & Qt.LeftButton):
            # 左键都松开了还有 capture，就是残留（正常拖拽期间左键一定按着）。
            # 属于本线程的那份才收手势；跨进程的 ReleaseCapture 本来就无效，不报错。
            if capture == hwnd:
                self.canvas.abort_gesture()  # 顺手把半截笔迹收掉
            try:
                user32.ReleaseCapture()
            except OSError:
                pass

        exclusive = exclusive_fullscreen_active()
        self._reassert_overlay_topmost()
        if self.toolbar.isVisible():
            # 覆盖层和工具栏都是 TOPMOST；提完覆盖层后必须把工具栏再压回去
            self._raise_toolbar_native()
        if exclusive:
            if not self._fullscreen_warned:
                self._fullscreen_warned = True
                self.trayMessage.emit(
                    "当前前台程序为独占全屏/演示模式，批注层无法叠加；"
                    "请切换为窗口或无边框全屏")
            return
        self._fullscreen_warned = False

        bar = self.toolbar
        if not bar.isVisible():
            return
        try:
            bar_hwnd = int(bar.winId())
        except RuntimeError:
            return
        if bar_hwnd == 0:
            return
        center = bar.mapToGlobal(bar.rect().center())
        try:
            hit = int(user32.WindowFromPoint(_POINT(center.x(), center.y())) or 0)
        except (OSError, ValueError):
            return
        if hit and hit != bar_hwnd:
            self._raise_toolbar_native()

    def activate_tool(self, tool: str) -> None:
        """全局工具热键：切工具并把批注层拉到前台（Alt+2 钢笔 / Alt+3 橡皮）。"""
        self.canvas.set_tool(tool)
        if self.mode != "draw":
            self.enter_draw_mode()
        self._sync_toolbar()

    def _on_escape(self) -> None:
        """Esc 不再收起整层（用户反馈误按就丢批注）。

        正在画/擦时只收掉当前手势；其余情况什么都不做。收起仍走工具栏
        按钮或 Ctrl+Alt+H。
        """
        if self.canvas._drawing or self.canvas._erasing:
            self.canvas.abort_gesture()

    def clear_canvas(self) -> None:
        """全局清空热键：清掉全屏批注层的笔迹。"""
        self._on_clear()

    def _on_tool_chosen(self, tool: str) -> None:
        """三态互斥：点钢笔 / 橡皮就是回到批注，不能一边穿透一边画。"""
        self.canvas.set_tool(tool)
        if self.mode == "passthrough":
            self.enter_draw_mode()
        self._sync_toolbar()

    def _on_color_chosen(self, color: str) -> None:
        """色块/取色器选了颜色：改笔色、立即落盘，并把焦点还给画布。"""
        self.canvas.set_color(color)
        if self.canvas.color == color:
            self.config.color = color
            self.config.save()
        self._sync_toolbar()
        # QColorDialog 关闭后前台可能停在对话框上，B/Q/E 需要画布焦点
        if self.isVisible():
            self.activateWindow()
            self.canvas.setFocus(Qt.ActiveWindowFocusReason)

    def _on_width_chosen(self, width: float) -> None:
        """粗细档位：改线宽并立即落盘，和换色走同一套流程。"""
        self.canvas.set_pen_width(width)
        # 只有真的落到位才写配置：非法值被画布夹住或忽略时不污染 settings
        if abs(self.canvas.pen_width - float(width)) < 1e-6:
            self.config.pen_width = self.canvas.pen_width
            self.config.save()
        self._sync_toolbar()

    def _on_passthrough_toggled(self) -> None:
        """穿透是三选一里的一个状态：点它就进穿透，回批注要点钢笔 / 橡皮。"""
        if self.mode != "passthrough":
            self.enter_passthrough_mode()

    def _on_undo(self) -> None:
        self.canvas.undo()
        self._sync_toolbar()

    def _on_clear(self) -> None:
        self.canvas.clear_all()
        self._sync_toolbar()

    # ---------- 导出 ----------

    def save_png(self) -> bool:
        """导出只有笔迹的透明 PNG；无笔迹也照常落一张空图。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = self.config.storage_dir
        path = folder / f"annotation-{stamp}.png"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            # 同一秒连按两次也各存一份，不覆盖上一张
            serial = 2
            while path.exists():
                path = folder / f"annotation-{stamp}-{serial}.png"
                serial += 1
            ok = bool(self.canvas.render_png(str(path)))
        except Exception:
            # 导出是保底功能：画布内部出错也只报失败，不能让批注层跟着崩
            ok = False
        if ok:
            self.trayMessage.emit(f"已保存透明 PNG：{path.name}")
        else:
            self.trayMessage.emit("PNG 保存失败，请检查 data/annotations 目录")
        return ok

    # ---------- 事件 ----------

    def paintEvent(self, event) -> None:
        """draw 模式画 1px 半透明 ACCENT 边框；其余像素全透明。"""
        if self.mode != "draw":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        color = QColor(ACCENT)
        color.setAlpha(120)
        painter.setPen(QPen(color, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.sync_geometry()             # 隐藏期间换了分辨率也能对齐
        if self.mode in ("draw", "passthrough"):
            self._show_toolbar()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.canvas.setGeometry(self.rect())
        self._place_controls()

    def closeEvent(self, event) -> None:
        """没有标题栏，Alt+F4 之类只当隐藏处理，进程和热键继续留着。"""
        event.ignore()
        self.hide_overlay()
