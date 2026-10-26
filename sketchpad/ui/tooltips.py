"""全局 tooltip 控制器：鼠标在控件上停满 1 秒才显示提示。

不用 QProxyStyle：QApplication.setStyle() 会删掉旧样式，而代理样式又引用旧样式，
测试里重复安装会直接崩。这里改成应用级事件过滤器：
- 拦截 Qt 原生 ToolTip 事件，避免系统默认延迟先把提示弹出来；
- 记录当前有 tooltip 的控件，用单次 QTimer 从“进入控件”开始计时 1 秒；
- 鼠标离开/按下/窗口隐藏就取消。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QTabBar, QToolTip, QWidget

TOOLTIP_DELAY_MS = 1000


class TooltipController(QObject):
    def __init__(self, app: QApplication, delay_ms: int = TOOLTIP_DELAY_MS) -> None:
        super().__init__(app)
        self._app = app
        self.delay_ms = max(0, int(delay_ms))
        self._widget: QWidget | None = None
        self._text = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show)
        app.installEventFilter(self)

    # --- event filter ---

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind == QEvent.ToolTip:
            # 有我们接管的提示就压掉原生；标签页/列表项等原生提示放行
            return self._candidate(obj if isinstance(obj, QWidget) else None) is not None
        if kind in (QEvent.Enter, QEvent.MouseMove) and isinstance(obj, QWidget):
            self._consider(obj)
        elif kind in (QEvent.Leave, QEvent.Hide, QEvent.WindowDeactivate):
            if obj is self._widget:
                self._cancel()
        elif kind in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            self._cancel()
        return False

    def _consider(self, widget: QWidget) -> None:
        candidate = self._candidate(widget)
        if candidate is None:
            return
        target, text = candidate
        if target is self._widget and text == self._text:
            return
        self._cancel()
        self._widget = target
        self._text = text
        self._timer.start(self.delay_ms)

    @staticmethod
    def _candidate(widget: QWidget | None):
        """返回 (控件, 提示文本)：控件自身的 tooltip，或标签页当前 tab 的提示。"""
        current = widget
        while current is not None:
            text = current.toolTip().strip()
            if text:
                return current, text
            if isinstance(current, QTabBar):
                index = current.tabAt(current.mapFromGlobal(QCursor.pos()))
                if 0 <= index < current.count():
                    tip = current.tabToolTip(index).strip()
                    if tip:
                        return current, tip
            current = current.parentWidget()
        return None

    def _show(self) -> None:
        widget, text = self._widget, self._text
        self._widget, self._text = None, ""
        if widget is None or not text:
            return
        try:
            if widget.isVisible() and widget.underMouse():
                QToolTip.showText(QCursor.pos(), text, widget)
        except RuntimeError:
            pass

    def _cancel(self) -> None:
        self._timer.stop()
        self._widget = None
        self._text = ""


def install(app: QApplication, delay_ms: int = TOOLTIP_DELAY_MS) -> TooltipController:
    """安装一次；重复调用会替换旧控制器。"""
    old = getattr(app, "_tooltip_controller", None)
    if old is not None:
        app.removeEventFilter(old)
        old.deleteLater()
    controller = TooltipController(app, delay_ms)
    app._tooltip_controller = controller      # 防回收
    return controller
