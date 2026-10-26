"""多标签工作区：顶部标签栏 + QStackedWidget 页面堆。

只依赖 Qt 与 ui.icons / ui.theme。会话对象走鸭子类型：必须有 ``.pane``（QWidget）
与 ``.tab_title``（str）。组件只发信号，不删标签——关闭前要主窗确认保存，
确认后由主窗调 ``remove_entry()``。

标签顺序：草稿固定在第 0 位（拖动后会被纠正回 0），文档标签按添加顺序排在后面。
标签文字完全由 ``TabEntry.title`` 生成（session.tab_title 里带不带 ``●`` 由主窗决定，
组件不解析），``update_title`` 只是重读一遍。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sketchpad.ui import icons, theme
from sketchpad.workspace.tabdrag import TabDragController

DEFAULT_DRAFT_TITLE = "笔记 1"
CLOSE_BUTTON_SIZE = 16
CLOSE_ICON_SIZE = 10
ENTRY_ID = itertools.count(1)


@dataclass(eq=False)
class TabEntry:
    """一个标签页：1 个 session 是普通文档，2 个是左右分屏。"""

    sessions: list
    page: QWidget
    draft: bool = False
    draft_title: str = DEFAULT_DRAFT_TITLE   # 草稿没有 session，标题单独存
    custom_title: str | None = None          # 用户重命名；None = 用默认/文件名
    entry_id: int = field(default_factory=lambda: next(ENTRY_ID))  # DnD MIME 用

    @property
    def title(self) -> str:
        if self.custom_title:
            return self.custom_title
        if self.draft:
            return self.draft_title
        return " | ".join(session.tab_title for session in self.sessions)

    @property
    def is_split(self) -> bool:
        return len(self.sessions) == 2


# 只按 objectName 命中：setStyleSheet 会级联到子控件（分屏里的文档画布也在子树里），
# 每条规则都带 #名字 才不会把别人的按钮背景刷掉。
_TABS_QSS = f"""
QWidget#WorkspaceTabs {{ background: {theme.WINDOW_BG}; }}
QWidget#TabStrip {{ background: transparent; }}
QTabBar#WorkspaceTabBar {{
    background: transparent;
    border: none;
}}
QTabBar#WorkspaceTabBar::tab {{
    background: transparent;
    color: {theme.TEXT_DIM};
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 4px 9px;
    margin: 2px 3px 0 0;
    min-width: 56px;
}}
QTabBar#WorkspaceTabBar::tab:hover {{ background: {theme.SURFACE_HOVER}; color: {theme.TEXT}; }}
QTabBar#WorkspaceTabBar::tab:selected {{ background: {theme.SURFACE}; color: {theme.TEXT}; border: 1px solid {theme.BORDER_STRONG}; }}
QToolButton#TabDraftButton {{
    background: transparent;
    border: none;
    border-radius: 5px;
    padding: 0;
}}
QToolButton#TabDraftButton:hover {{ background: {theme.SURFACE_HOVER}; }}
QToolButton#TabDraftButton:pressed {{ background: {theme.SURFACE_PRESSED}; }}
QToolButton#TabSplitButton {{
    background: transparent;
    border: none;
    border-radius: 5px;
    padding: 0;
}}
QToolButton#TabSplitButton:hover {{ background: {theme.SURFACE_HOVER}; }}
QToolButton#TabSplitButton:pressed {{ background: {theme.SURFACE_PRESSED}; }}
QToolButton#TabSplitButton:disabled {{ background: transparent; }}
QToolButton#TabClose {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0;
}}
QToolButton#TabClose:hover {{ background: {theme.SURFACE_PRESSED}; }}
QMenu#TabContextMenu {{
    background: {theme.SURFACE};
    color: {theme.TEXT};
    border: 1px solid {theme.BORDER};
    padding: 4px;
}}
QMenu#TabContextMenu::item {{
    background: transparent;
    color: {theme.TEXT};
    padding: 5px 22px 5px 12px;
    border-radius: 4px;
}}
QMenu#TabContextMenu::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}
QMenu#TabContextMenu::item:disabled {{ color: {theme.TEXT_MUTED}; }}
QMenu#TabContextMenu::separator {{ height: 1px; background: {theme.BORDER}; margin: 4px 6px; }}
"""


class TabStrip(QWidget):
    """标签栏本体：tabbar + 分屏按钮。

    单独成控件是为了让主窗能把它整块搬进标题栏（``titlebar.set_center_widget``），
    而 ``WorkspaceTabs`` 仍持有 ``stack`` 和业务接口。QSS 挂在 strip 自己身上，
    reparent 后样式跟着走。

    这里不放「打开文件 +」按钮：打开文件走 文件菜单 / Ctrl+O / 空态按钮。
    也不放「新建草稿」按钮：新建草稿走 文件菜单 / 快捷键。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TabStrip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_TABS_QSS)

        self.tabbar = QTabBar(self)
        self.tabbar.setObjectName("WorkspaceTabBar")
        # 拖动完全交给 TabDragController；不让 QTabBar 自己搬标签
        self.tabbar.setMovable(False)
        self.tabbar.setExpanding(False)     # 放不下时横向滚动，而不是把标签压窄
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setElideMode(Qt.ElideRight)
        self.tabbar.setDrawBase(False)
        # 键盘焦点留给画布（Ctrl+Tab 由主窗处理），点标签不改绘图焦点
        self.tabbar.setFocusPolicy(Qt.NoFocus)
        self.tabbar.setContextMenuPolicy(Qt.CustomContextMenu)

        self.split_button = QToolButton(self)
        self.split_button.setObjectName("TabSplitButton")
        self.split_button.setIcon(icons.split_icon())
        self.split_button.setIconSize(QSize(16, 16))
        self.split_button.setToolTip("与另一文档分屏")
        self.split_button.setCursor(Qt.PointingHandCursor)
        self.split_button.setFocusPolicy(Qt.NoFocus)
        self.split_button.setFixedSize(24, 24)
        self.split_button.setEnabled(False)
        # 分屏按钮已挪到工具栏（2026-10-23）；对象与信号保留，避免外部调用点断掉
        self.split_button.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 0)
        layout.setSpacing(2)
        # 标签按内容取宽；多余宽度留到 strip 末尾。
        self.tabbar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.tabbar)
        layout.addWidget(self.split_button)
        layout.addStretch(1)


class WorkspaceTabs(QWidget):
    """标签栏容器。只做展示与信号，业务（保存/关窗/建 session）全在主窗。"""

    closeRequested = Signal(object)          # TabEntry，主窗确认保存后再 remove_entry()
    currentChanged = Signal(object)          # TabEntry（清空时发 None）
    splitRequested = Signal(object)          # TabEntry（当前标签点"分屏"按钮）
    closeOthersRequested = Signal(object)    # TabEntry（右键"关闭其他"）
    renameRequested = Signal(object)         # TabEntry（右键/双击标签改名）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkspaceTabs")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_TABS_QSS)

        self._entries: list[TabEntry] = []
        self._tooltips: dict[TabEntry, str] = {}
        self._current: TabEntry | None = None
        self._pinning = False               # 纠正草稿位置时避免重排回调打转
        self._window_id = 0
        self._preview_entry: TabEntry | None = None
        self._preview_text = ""

        self.strip = TabStrip(self)
        # 兼容旧接口：tabbar/按钮都还是 WorkspaceTabs 的属性（指向 strip 的子控件）
        self.tabbar = self.strip.tabbar
        self.split_button = self.strip.split_button

        self.tabbar.currentChanged.connect(self._on_tabbar_current_changed)
        self.tabbar.tabMoved.connect(self._on_tab_moved)
        self.tabbar.tabBarDoubleClicked.connect(self._on_tab_double_clicked)
        self.tabbar.customContextMenuRequested.connect(self._on_context_menu)
        self.split_button.clicked.connect(self._on_split_clicked)

        # 拖拽落点的高亮浮层：插入竖线 + 合并预览 2px 上边框
        self.insertion_marker = QWidget(self.tabbar)
        self.insertion_marker.setObjectName("TabInsertionMarker")
        self.insertion_marker.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.insertion_marker.setStyleSheet(
            f"QWidget#TabInsertionMarker {{ background: {theme.ACCENT};"
            " border-radius: 1px; }")
        self.insertion_marker.hide()

        self.preview_bar = QWidget(self.tabbar)
        self.preview_bar.setObjectName("TabMergePreviewBar")
        self.preview_bar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.preview_bar.setStyleSheet(
            f"QWidget#TabMergePreviewBar {{ background: {theme.ACCENT};"
            " border-radius: 1px; }")
        self.preview_bar.hide()

        self.stack = QStackedWidget(self)
        self.stack.setObjectName("WorkspaceStack")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.strip)
        outer.addWidget(self.stack, 1)

        self.drag_controller = TabDragController(self)
        self.drag_controller.reorderRequested.connect(self.move_entry_to)

    # --- 只读状态 ---

    @property
    def current_entry(self) -> TabEntry | None:
        return self._current

    @property
    def entries(self) -> list[TabEntry]:
        return list(self._entries)

    @property
    def window_id(self) -> int:
        return self._window_id

    def set_window_id(self, window_id: int) -> None:
        self._window_id = int(window_id)

    def index_of(self, entry: TabEntry) -> int:
        return self._index_of(entry)

    def entry_by_id(self, entry_id: int) -> TabEntry | None:
        found = next((entry for entry in self._entries
                      if entry.entry_id == entry_id), None)
        if found is None:                       # 兼容直接传 id(entry) 的调用
            found = next((entry for entry in self._entries
                          if id(entry) == entry_id), None)
        return found

    # --- 增删 ---

    def add_draft(self, page: QWidget, title: str = DEFAULT_DRAFT_TITLE,
                  session=None) -> TabEntry:
        """加笔记/草稿标签；session 给 NoteSession 时它也能参与分屏。"""
        entry = TabEntry(sessions=[session] if session is not None else [],
                         page=page, draft=True, draft_title=title)
        self._insert_entry(entry)
        return entry

    def add_entry(self, sessions: list, page: QWidget) -> TabEntry:
        """加文档标签；2 个 session 就是分屏标签（page 传 SplitWorkspace）。"""
        sessions = list(sessions)
        if len(sessions) not in (1, 2):
            raise ValueError(f"TabEntry 只支持 1 或 2 个 session，收到 {len(sessions)}")
        entry = TabEntry(sessions=sessions, page=page)
        self._insert_entry(entry)
        return entry

    def remove_entry(self, entry: TabEntry) -> None:
        """从标签栏与 stack 里摘掉（不删除 page，调用方决定它的去向）。"""
        index = self._index_of(entry)
        if index < 0:
            return
        if entry is self._preview_entry:
            self.clear_merge_preview()
        self._entries.pop(index)
        self._tooltips.pop(entry, None)
        self.stack.removeWidget(entry.page)      # 只解除布局占用，page 仍存活
        with QSignalBlocker(self.tabbar):
            self.tabbar.removeTab(index)
        self._sync_decorations()
        self._ensure_draft_first()
        if self._entries:
            self._apply_current(self.tabbar.currentIndex())
        else:
            self._current = None
            self.split_button.setEnabled(False)
            self.currentChanged.emit(None)

    def _insert_entry(self, entry: TabEntry) -> None:
        index = 0 if entry.draft else len(self._entries)   # 草稿永远插在第 0 位
        self._entries.insert(index, entry)
        self.stack.addWidget(entry.page)
        with QSignalBlocker(self.tabbar):                # 插完再统一选当前，避免中间态
            self.tabbar.insertTab(index, entry.title)
        self._sync_decorations()
        self.set_current_entry(entry)

    # --- 当前页 ---

    def set_current_entry(self, entry: TabEntry) -> None:
        index = self._index_of(entry)
        if index < 0:
            return
        if self.tabbar.currentIndex() != index:
            self.tabbar.setCurrentIndex(index)          # 走 currentChanged 回调
        else:
            self._apply_current(index)

    def _on_tabbar_current_changed(self, index: int) -> None:
        self._apply_current(index)

    def _apply_current(self, index: int) -> None:
        entry = self._entry_at(index)
        if entry is None:
            self._current = None
            self.split_button.setEnabled(False)
            return
        if self.stack.indexOf(entry.page) >= 0:
            self.stack.setCurrentWidget(entry.page)
        # 已经是分屏的标签没有"再分屏"语义；草稿（无 session）暂不参与分屏
        self.split_button.setEnabled(not entry.draft and not entry.is_split)
        if entry is not self._current:
            self._current = entry
            self.currentChanged.emit(entry)

    # --- 标签文字/提示 ---

    def update_title(self, entry: TabEntry) -> None:
        """session.tab_title 变了（含脏标记）后重读，不解析文字内容。"""
        index = self._index_of(entry)
        if index < 0:
            return
        with QSignalBlocker(self.tabbar):
            self.tabbar.setTabText(index, self._tab_text(entry))
            self.tabbar.setTabToolTip(index, self._tooltip_text(entry))

    def set_tab_tooltip(self, entry: TabEntry, text: str) -> None:
        """空串=恢复默认（跟随标题）。"""
        index = self._index_of(entry)
        if index < 0:
            return
        if text:
            self._tooltips[entry] = text
        else:
            self._tooltips.pop(entry, None)
        with QSignalBlocker(self.tabbar):
            self.tabbar.setTabToolTip(index, self._tooltip_text(entry))

    def _tooltip_text(self, entry: TabEntry) -> str:
        return self._tooltips.get(entry, entry.title)

    # --- 拖动重排 ---

    def _on_tab_moved(self, from_index: int, to_index: int) -> None:
        if self._pinning:
            return
        if not (0 <= from_index < len(self._entries) and 0 <= to_index < len(self._entries)):
            return
        entry = self._entries.pop(from_index)
        self._entries.insert(to_index, entry)
        self._sync_decorations()
        self._ensure_draft_first()

    def move_entry_to(self, entry: TabEntry, target_index: int) -> bool:
        """拖拽重排：把 entry 放到最终下标 target_index（删掉源后插入的位置）。

        草稿永远钉在第 0 位；target_index 落在草稿前面时抬到 1。
        """
        index = self._index_of(entry)
        if index < 0:
            return False
        if entry.draft:
            if index != 0:
                self._move_entry(index, 0)
            return False
        target = max(0, min(int(target_index), len(self._entries) - 1))
        if self._entries and self._entries[0].draft:
            target = max(1, target)
        if target == index:
            return False
        self._move_entry(index, target)
        return True

    def _ensure_draft_first(self) -> None:
        """第一枚笔记仍钉在第 0 位；后来新建的笔记允许自由排。"""
        for index, entry in enumerate(self._entries):
            if entry.draft:
                if index:
                    self._move_entry(index, 0)
                return

    def _move_entry(self, from_index: int, to_index: int) -> None:
        entry = self._entries.pop(from_index)
        self._entries.insert(to_index, entry)
        self._pinning = True
        try:
            with QSignalBlocker(self.tabbar):
                self.tabbar.moveTab(from_index, to_index)
        finally:
            self._pinning = False
        self._sync_decorations()

    # --- 拖拽落点渲染（由 TabDragController 调用） ---

    @property
    def preview_entry(self) -> TabEntry | None:
        return self._preview_entry

    @property
    def preview_text(self) -> str:
        return self._preview_text

    @property
    def merge_preview_active(self) -> bool:
        return self._preview_entry is not None

    def show_merge_preview(self, target_entry: TabEntry, text: str) -> None:
        """目标标签临时显示 ``源 | 目标``，并在标签顶部压一条 2px 强调边框。

        只改渲染，不动 session；``update_title`` / ``_sync_decorations``
        会继续沿用预览文字，直到 ``clear_merge_preview``。
        """
        index = self._index_of(target_entry)
        if index < 0:
            return
        self._preview_entry = target_entry
        self._preview_text = text
        with QSignalBlocker(self.tabbar):
            self.tabbar.setTabText(index, text)
        rect = self.tabbar.tabRect(index)
        if rect.isValid():
            left, top, width = rect.left(), rect.top(), rect.width()
        else:                                        # 布局还没跑完时的兜底
            left, top, width = 0, 0, max(1, self.tabbar.width())
        self.preview_bar.setGeometry(left, max(0, top), max(1, width), 2)
        self.preview_bar.raise_()
        self.preview_bar.show()

    def clear_merge_preview(self) -> None:
        entry = self._preview_entry
        self._preview_entry = None
        self._preview_text = ""
        self.preview_bar.hide()
        index = self._index_of(entry) if entry is not None else -1
        if index >= 0:
            with QSignalBlocker(self.tabbar):
                self.tabbar.setTabText(index, entry.title)

    def show_insertion_marker(self, x: int) -> None:
        """在 tabbar 坐标 x 处画一条 2px 插入线。"""
        width = max(0, self.tabbar.width())
        x = max(0, min(int(x), max(0, width - 1)))
        self.insertion_marker.setGeometry(x, 0, 2, max(1, self.tabbar.height()))
        self.insertion_marker.raise_()
        self.insertion_marker.show()

    def hide_insertion_marker(self) -> None:
        self.insertion_marker.hide()

    def _tab_text(self, entry: TabEntry) -> str:
        if entry is self._preview_entry and self._preview_text:
            return self._preview_text
        return entry.title

    # --- 装饰（文字/提示/关闭按钮） ---

    def _sync_decorations(self) -> None:
        """把 tab 的文字、提示、关闭按钮与 _entries 对齐（移动后索引会变）。"""
        with QSignalBlocker(self.tabbar):
            for index, entry in enumerate(self._entries):
                self.tabbar.setTabText(index, self._tab_text(entry))
                self.tabbar.setTabToolTip(index, self._tooltip_text(entry))
                # 关闭键跟着标签一起被 moveTab 搬走，认出来就别重建（拖动时不抖）
                existing = self.tabbar.tabButton(index, QTabBar.RightSide)
                if isinstance(existing, QToolButton) and existing.property("tab_entry") is entry:
                    continue
                self.tabbar.setTabButton(index, QTabBar.RightSide,
                                         self._make_close_button(entry))

    def _make_close_button(self, entry: TabEntry) -> QToolButton:
        button = QToolButton(self.tabbar)
        button.setObjectName("TabClose")
        button.setProperty("tab_entry", entry)      # 重排后认出"这已经是他那一颗"
        button.setIcon(icons.close_icon())
        button.setIconSize(QSize(CLOSE_ICON_SIZE, CLOSE_ICON_SIZE))
        button.setFixedSize(CLOSE_BUTTON_SIZE, CLOSE_BUTTON_SIZE)
        button.setAutoRaise(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolTip("关闭")
        button.clicked.connect(lambda _checked=False, e=entry: self.closeRequested.emit(e))
        return button

    # --- 按钮 ---

    def _on_split_clicked(self) -> None:
        entry = self._current
        if entry is None or entry.draft or entry.is_split:
            return
        self.splitRequested.emit(entry)

    # --- 右键菜单 ---

    def _on_tab_double_clicked(self, index: int) -> None:
        """双击标签 = 重命名（与右键菜单同一个信号）。"""
        entry = self._entry_at(index)
        if entry is not None:
            self.renameRequested.emit(entry)

    def _on_context_menu(self, pos) -> None:
        index = self.tabbar.tabAt(pos)
        entry = self._entry_at(index)
        if entry is None:
            return
        if self.tabbar.currentIndex() != index:
            self.set_current_entry(entry)          # 右键先选中，跟系统标签栏一致
        menu = self._make_tab_menu(entry)
        menu.setAttribute(Qt.WA_DeleteOnClose, True)   # 每次右键都新建，关掉即回收
        menu.exec(self.tabbar.mapToGlobal(pos))

    def _make_tab_menu(self, entry: TabEntry) -> QMenu:
        """所有标签都能「重命名 / 关闭」；任何标签（含草稿）都能分屏。"""
        menu = QMenu(self.tabbar)
        menu.setObjectName("TabContextMenu")
        rename_action = menu.addAction("重命名…")
        rename_action.triggered.connect(
            lambda _checked=False, e=entry: self.renameRequested.emit(e))
        close_action = menu.addAction("关闭")
        close_action.triggered.connect(
            lambda _checked=False, e=entry: self.closeRequested.emit(e))
        menu.addSeparator()
        if not entry.draft:
            split_action = menu.addAction("分屏…")
            split_action.setEnabled(not entry.is_split)
            split_action.triggered.connect(
                lambda _checked=False, e=entry: self.splitRequested.emit(e))
        others_action = menu.addAction("关闭其他")
        others_action.setEnabled(len(self._entries) > 1)
        others_action.triggered.connect(
            lambda _checked=False, e=entry: self.closeOthersRequested.emit(e))
        return menu

    # --- 索引工具 ---

    def _index_of(self, entry: TabEntry) -> int:
        try:
            return self._entries.index(entry)
        except ValueError:
            return -1

    def _entry_at(self, index: int) -> TabEntry | None:
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None
