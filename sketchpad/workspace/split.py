"""双文档左右分屏容器：两栏 QSplitter + 活动 pane 高亮。

只依赖 Qt 与 ui.icons / ui.theme。session 走鸭子类型（``.pane`` / ``.tab_title``）。
组件不关闭、不删除 session，也不做保存：它只负责把两个 pane 摆进分屏、
把"哪一边是活动的"记下来、以及在用户要求合并时把 pane 交还给主窗。

标签页里已经显示 ``A | B``，所以这里不再放标题行；只在右上角浮一个
小小的「取消分屏」按钮（另带右键菜单同名项），不占内容高度。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QMenu,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sketchpad.ui import icons, theme

SPLIT_HANDLE_WIDTH = 4
UNSPLIT_MARGIN = 8

_SPLIT_QSS = f"""
QWidget#SplitWorkspace {{ background: {theme.WINDOW_BG}; }}
QSplitter#SplitSplitter {{ background: {theme.WINDOW_BG}; }}
QSplitter#SplitSplitter::handle {{ background: {theme.BORDER}; }}
QSplitter#SplitSplitter::handle:hover {{ background: {theme.ACCENT}; }}
/* 两侧都留 2px 左边框（透明/强调色），切换活动时不改变几何，内容不抖 */
QFrame#SplitPane {{
    background: {theme.WINDOW_BG};
    border: none;
    border-left: 2px solid transparent;
}}
QFrame#SplitPane[active="true"] {{
    border-left: 2px solid {theme.ACCENT};
    background: {theme.SURFACE_HOVER};
}}
QToolButton#UnsplitButton {{
    background: {theme.SURFACE};
    color: {theme.TEXT};
    border: 1px solid {theme.BORDER};
    border-radius: 5px;
    padding: 2px 8px;
    font-size: 11px;
}}
QToolButton#UnsplitButton:hover {{ background: {theme.SURFACE_HOVER}; }}
QToolButton#UnsplitButton:pressed {{ background: {theme.SURFACE_PRESSED}; }}
QMenu#SplitContextMenu {{
    background: {theme.SURFACE};
    color: {theme.TEXT};
    border: 1px solid {theme.BORDER};
    padding: 4px;
}}
QMenu#SplitContextMenu::item {{
    background: transparent;
    color: {theme.TEXT};
    padding: 5px 22px 5px 12px;
    border-radius: 4px;
}}
QMenu#SplitContextMenu::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}
"""


class SplitWorkspace(QWidget):
    """两栏分屏。set_sessions 恰好收 2 个 session，把 pane reparent 进 QSplitter。"""

    activeChanged = Signal(object)     # session
    unsplitRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SplitWorkspace")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # setStyleSheet 会级联到子树（文档画布就在子树里），所以每条规则都带 #名字
        self.setStyleSheet(_SPLIT_QSS)

        self._sessions: list = []
        self._panes: list[QFrame] = []
        self._active = None

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setObjectName("SplitSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(SPLIT_HANDLE_WIDTH)
        self.splitter.setOpaqueResize(True)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setContextMenuPolicy(Qt.CustomContextMenu)
        self.splitter.customContextMenuRequested.connect(self._show_context_menu)

        self.unsplit_button = QToolButton(self)
        self.unsplit_button.setObjectName("UnsplitButton")
        self.unsplit_button.setText("取消分屏")
        self.unsplit_button.setIcon(icons.split_icon())
        self.unsplit_button.setIconSize(QSize(14, 14))
        self.unsplit_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.unsplit_button.setToolTip("取消分屏，合并为一个标签")
        self.unsplit_button.setCursor(Qt.PointingHandCursor)
        self.unsplit_button.setFocusPolicy(Qt.NoFocus)
        self.unsplit_button.hide()
        # clicked 会带一个 bool，emit() 不收参数，必须吃掉它
        self.unsplit_button.clicked.connect(lambda _checked=False: self.unsplitRequested.emit())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.splitter, 1)

    # --- 只读状态 ---

    @property
    def sessions(self) -> list:
        return list(self._sessions)

    @property
    def active_session(self):
        return self._active

    @property
    def title(self) -> str:
        return " | ".join(session.tab_title for session in self._sessions)

    # --- 装配 ---

    def set_sessions(self, sessions: list) -> None:
        sessions = list(sessions)
        if len(sessions) != 2:
            raise ValueError(f"SplitWorkspace 需要恰好 2 个 session，收到 {len(sessions)}")
        if self._sessions:
            self.detach_sessions()
        self._sessions = sessions
        self._panes = []
        for session in sessions:
            frame = self._make_pane(session)
            self._panes.append(frame)
            self.splitter.addWidget(frame)
        self.splitter.setSizes([1, 1])          # 初始各 50%
        self.unsplit_button.show()
        self.unsplit_button.raise_()
        self._place_unsplit_button()
        self.set_active(sessions[0], emit=False)

    def _make_pane(self, session) -> QFrame:
        """pane 外套一层 QFrame：边框高亮画在它身上，pane 本身不动。"""
        frame = QFrame(self.splitter)
        frame.setObjectName("SplitPane")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        frame.setProperty("active", False)
        frame.setContextMenuPolicy(Qt.CustomContextMenu)
        frame.customContextMenuRequested.connect(self._show_context_menu)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(session.pane)          # addWidget 自动把 pane reparent 过来
        # detach_sessions() 会 hide() pane 再置父；重新装配时必须显式 show，
        # 否则分屏标签只剩两条空栏（用户实测 2026-10-23）。
        session.pane.show()
        frame.show()

        self._install_activation_filter(frame)
        return frame

    def _install_activation_filter(self, frame: QFrame) -> None:
        """点/聚焦到 pane 里任何子控件都算激活这一侧。

        子控件在装配时就装好过滤（画布的 viewport 也在其中）；之后新建的
        子控件靠管道的 FocusIn 再兜一层，不会把活动态锁死在旧控件上。
        """
        frame.installEventFilter(self)
        for child in frame.findChildren(QWidget):
            child.installEventFilter(self)

    def _remove_activation_filter(self, pane: QWidget) -> None:
        pane.removeEventFilter(self)
        for child in pane.findChildren(QWidget):
            child.removeEventFilter(self)

    # --- 活动 pane ---

    def set_active(self, session, emit: bool = True) -> None:
        index = self._index_of(session)
        if index < 0:
            return
        changed = session is not self._active
        self._active = session
        for i, frame in enumerate(self._panes):
            active = i == index
            if frame.property("active") != active:
                frame.setProperty("active", active)
                frame.style().unpolish(frame)
                frame.style().polish(frame)
            frame.update()
        if emit and changed:
            self.activeChanged.emit(session)

    def _index_of(self, session) -> int:
        for index, item in enumerate(self._sessions):
            if item is session:
                return index
        return -1

    # --- 拆开 ---

    def detach_sessions(self) -> list:
        """把两个 pane 从 splitter 上取下（parent 置 None），不删除、不关闭。"""
        sessions = list(self._sessions)
        for frame, session in zip(self._panes, self._sessions):
            pane = session.pane
            layout = frame.layout()
            if layout is not None:
                layout.removeWidget(pane)
            pane.setParent(None)
            pane.hide()
            self._remove_activation_filter(pane)
            frame.setParent(None)
            frame.deleteLater()
        self._panes = []
        self._sessions = []
        self._active = None
        self.unsplit_button.hide()
        return sessions

    # --- 事件 ---

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.MouseButtonPress, QEvent.FocusIn) and self._panes:
            index = self._pane_index_for(watched)
            if index >= 0:
                self.set_active(self._sessions[index])
        return False

    def _pane_index_for(self, watched) -> int:
        if not isinstance(watched, QWidget):
            return -1
        for index, frame in enumerate(self._panes):
            if watched is frame or frame.isAncestorOf(watched):
                return index
        return -1

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_unsplit_button()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for frame in self._panes:               # 装配后才建出来的子控件补装过滤
            self._install_activation_filter(frame)
        self._place_unsplit_button()

    def _place_unsplit_button(self) -> None:
        """右上角浮层：只盖住一条小方块，不占内容高度。"""
        button = self.unsplit_button
        button.adjustSize()
        x = max(0, self.width() - button.width() - UNSPLIT_MARGIN)
        button.move(x, UNSPLIT_MARGIN)
        button.raise_()

    # --- 右键 ---

    def _show_context_menu(self, pos) -> None:
        source = self.sender()
        if not isinstance(source, QWidget):
            source = self
        menu = QMenu(self)
        menu.setObjectName("SplitContextMenu")
        menu.setAttribute(Qt.WA_DeleteOnClose, True)
        action = menu.addAction("取消分屏")
        action.triggered.connect(lambda _checked=False: self.unsplitRequested.emit())
        menu.exec(source.mapToGlobal(pos))


__all__ = ["SplitWorkspace"]
