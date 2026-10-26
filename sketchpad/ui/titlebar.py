"""自绘标题栏：置顶放到最小化旁边（用户 2026-10-20 要求）。

无边框窗口 + 这条 36px 的栏：左边标题，中间可挂工作区标签条（2026-10-23 改造），
右边依次是 置顶 / 最小化 / 最大化 / 关闭。

设置齿轮默认仍在（`show_settings=True`），主窗与草稿窗传 False 把它让给工具栏
（2026-10-23 用户要求「窗口级按钮都收到工具栏」）；对象仍创建，避免调用方
`titlebar.settings_button` 取到 None。
拖动用 QWindow.startSystemMove()（交给 Windows，双击最大化由本类发信号）。
窗口边缘缩放不在这里做——由 MainWindow.nativeEvent 的 WM_NCHITTEST 处理。

只有标题文字和空白处参与窗口拖动；中间控件（标签条）和窗口按钮里的点击
一律不触发拖动/双击最大化，标签自己的鼠标事件原样送到它和它的子控件。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QHBoxLayout, QLabel, QSizePolicy, QTabBar, QToolButton,
    QWidget,
)

from sketchpad.ui import icons

TITLE_BAR_HEIGHT = 36


def _round_avatar(size: int = 24) -> QPixmap:
    """把应用图标裁成圆形小头像；标题栏左侧的品牌锚点。"""
    source = icons.app_icon().pixmap(size * 2, size * 2)
    rounded = QPixmap(size * 2, size * 2)
    rounded.fill(Qt.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size * 2, size * 2))
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, source)
    painter.end()
    rounded.setDevicePixelRatio(2.0)
    return rounded


class TitleBar(QWidget):
    settingsRequested = Signal()
    pinToggled = Signal(bool)
    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()

    def __init__(self, title: str, parent: QWidget | None = None,
                 show_settings: bool = True, compact: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        # QWidget 子类不吃 QSS 背景（PROGRESS §5）
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(TITLE_BAR_HEIGHT)

        self._center_widget: QWidget | None = None

        row = QHBoxLayout(self)
        if compact:
            row.setContentsMargins(2, 0, 2, 0)  # 左右留白交给 TopChromePanel（6px + 外壳 8px）
        else:
            row.setContentsMargins(10, 0, 4, 0)  # 草稿窗没有玻璃面板，保留原缩进
        row.setSpacing(5)

        self.avatar = QLabel(self)
        self.avatar.setObjectName("TitleAvatar")
        self.avatar.setFixedSize(24, 24)
        self.avatar.setPixmap(_round_avatar(24))
        self.avatar.setToolTip("墨页")
        row.addWidget(self.avatar, 0, Qt.AlignVCenter)

        self.title_label = QLabel(title, self)
        row.addWidget(self.title_label, 0, Qt.AlignVCenter)
        # 没有中间控件时由这根弹簧把按钮推到右边；set_center_widget 会把它换成控件
        row.addStretch(1)

        self.settings_button = self._button(icons.gear_icon(), "设置 (Ctrl+,)")
        # 设置已挪到工具栏：按钮对象留着（老调用点还 setToolTip / click），只是不进布局
        self.settings_button.setVisible(show_settings)
        self.pin_button = self._button(icons.pin_icon(), "置顶", checkable=True)
        self.pin_button.toggled.connect(self.pinToggled.emit)
        self.min_button = self._button(icons.minimize_icon(), "最小化")
        self.max_button = self._button(icons.maximize_icon(), "最大化")
        self.close_button = self._button(icons.close_icon(), "关闭（收到托盘）")
        self.close_button.setObjectName("CloseButton")
        self._window_buttons = ((self.settings_button,) if show_settings else ()) + (
            self.pin_button, self.min_button, self.max_button, self.close_button)
        for button in self._window_buttons:
            # 24px 高的按钮在 36px 栏里垂直居中
            row.addWidget(button, 0, Qt.AlignVCenter)

        self.settings_button.clicked.connect(self.settingsRequested.emit)
        self.min_button.clicked.connect(self.minimizeRequested.emit)
        self.max_button.clicked.connect(self.maximizeRequested.emit)
        self.close_button.clicked.connect(self.closeRequested.emit)

    def _button(self, icon: QIcon, tip: str, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("WinButton")
        button.setIcon(icon)
        button.setIconSize(QSize(12, 12))
        button.setToolTip(tip)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.NoFocus)
        button.setFixedSize(32, 24)
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        return button

    # ---------- 中间控件（工作区标签条） ----------

    @property
    def center_widget(self) -> QWidget | None:
        return self._center_widget

    def set_center_widget(self, widget: QWidget | None) -> None:
        """把控件插到标题文字和窗口按钮之间，占满中间的可伸缩空间。

        再调一次会换掉旧的：旧的先从布局里摘掉、reparent 到 None（不销毁，
        调用方可能还要复用）。传 None 则恢复成弹簧，按钮仍在右边。
        """
        old = self._center_widget
        if old is widget:
            return
        row = self.layout()
        if old is not None:
            self._center_widget = None
            old.removeEventFilter(self)
            for child in old.findChildren(QTabBar):
                child.removeEventFilter(self)
            row.removeWidget(old)
            old.setParent(None)          # 只断开父子关系，不 deleteLater
            old.hide()
        # 去掉占位弹簧（新控件要顶替它的伸缩位）；不要假设弹簧在固定下标：
        # 标题栏现在左侧还有头像，弹簧位置会随左边控件数量变化。
        for index in range(row.count()):
            item = row.itemAt(index)
            if item is not None and item.spacerItem() is not None:
                row.takeAt(index)
                break
        # 中间控件/弹簧都插在窗口按钮之前，标题和头像留在最左
        insert_at = row.count()
        for button in self._window_buttons:
            index = row.indexOf(button)
            if index >= 0:
                insert_at = min(insert_at, index)
        if widget is None:
            row.insertStretch(insert_at, 1)
            return
        self._center_widget = widget
        widget.setParent(self)
        widget.setMinimumWidth(0)        # 窗口变窄时允许被裁，不顶宽窗口
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.insertWidget(insert_at, widget, 1)
        widget.installEventFilter(self)
        for child in widget.findChildren(QTabBar):
            child.installEventFilter(self)
        widget.show()

    # ---------- 标题 / 最大化状态 ----------

    def set_title(self, text: str) -> None:
        """更新左侧标题文字，并同步窗口标题（任务栏 / 辅助工具读这一个）。"""
        self.title_label.setText(text)
        window = self.window()
        if window is not None and window is not self:
            window.setWindowTitle(text)

    def set_maximized(self, on: bool) -> None:
        """最大化后图标换成"还原"，提示同步。"""
        self.max_button.setIcon(icons.restore_icon() if on else icons.maximize_icon())
        self.max_button.setToolTip("还原" if on else "最大化")

    # ---------- 拖动 / 双击最大化 ----------

    def _is_window_drag_area(self, pos: QPoint) -> bool:
        """标题栏自身坐标里的 pos 是否属于"窗口拖动区"。

        标签页（含关闭键）、+ / 分屏 / 窗口按钮上的点击不算；标签条两侧的
        空白、标签间空隙、标题文字区域都算。用户 2026-10-24 明确要求：
        “按住空白部分要能移动软件，与按住标签页的逻辑不同”。
        """
        child = self.childAt(pos)
        while child is not None and child is not self:
            if child in self._window_buttons:
                return False
            if isinstance(child, QTabBar):
                return child.tabAt(child.mapFrom(self, pos)) < 0
            if isinstance(child, QAbstractButton):
                return False
            # 中间控件本身（TabStrip 背景）通常就是可拖空白；继续向上没有意义
            if child is self._center_widget:
                inner = child.childAt(child.mapFrom(self, pos))
                if inner is None:
                    return True
                if isinstance(inner, QTabBar):
                    return inner.tabAt(inner.mapFrom(self, pos)) < 0
                if isinstance(inner, QAbstractButton):
                    return False
                return True
            child = child.parentWidget()
        return True

    def eventFilter(self, obj, event) -> bool:
        """标签条空白：Qt 的 QTabBar 会自己吃掉按下事件，标题栏收不到。

        在 TabStrip/QTabBar 上补一层过滤器：只有“不是标签”的空白处才转成
        窗口拖动/双击最大化；标签本身和 + / 分屏按钮原样放行给拖拽引擎。
        """
        if (event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick)
                and event.button() == Qt.LeftButton
                and (obj is self._center_widget or isinstance(obj, QTabBar))):
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            if self._is_window_drag_area(pos):
                if event.type() == QEvent.MouseButtonDblClick:
                    self.maximizeRequested.emit()
                else:
                    handle = self.window().windowHandle()
                    if handle is not None:
                        handle.startSystemMove()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, ev) -> None:
        # 事件从子控件冒泡上来时坐标要用全局坐标重映射，才和本栏几何对得上
        pos = self.mapFromGlobal(ev.globalPosition().toPoint())
        if ev.button() == Qt.LeftButton and self._is_window_drag_area(pos):
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        pos = self.mapFromGlobal(ev.globalPosition().toPoint())
        if ev.button() == Qt.LeftButton and self._is_window_drag_area(pos):
            self.maximizeRequested.emit()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)
