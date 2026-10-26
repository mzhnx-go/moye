"""目录面板：按书签层级展示 PDF 大纲，点击跳到对应页。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sketchpad.ui import theme

WIDTH = 260


class TocPanel(QWidget):
    """左侧目录树。层级 1 起；每项在第 0 列 UserRole 存 0 基页码。"""

    pageRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TocPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(WIDTH)

        self.title_label = QLabel("目录", self)
        self.title_label.setObjectName("TocTitle")

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("TocTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        # 点目录不抢画布焦点：键盘输入（翻页/绘图快捷键）留给 app 层路由
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.itemClicked.connect(self._on_item_clicked)

        self.empty_label = QLabel("此文档没有目录", self)
        self.empty_label.setObjectName("TocEmpty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.addWidget(self.title_label)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty_label, 1)

        self._apply_style()
        self.clear_entries()

    def _apply_style(self) -> None:
        """按 objectName 命中深色主题，别写成全局 QTreeWidget 规则去罩别的控件。"""
        self.setStyleSheet(f"""
QWidget#TocPanel {{ background: {theme.WINDOW_BG}; }}
QLabel#TocTitle {{ color: {theme.TEXT_DIM}; font-size: 12px; padding: 0 4px; }}
QLabel#TocEmpty {{ color: {theme.TEXT_DIM}; font-size: 12px; }}
QTreeWidget#TocTree {{
    background: {theme.WINDOW_BG};
    border: none;
    color: {theme.TEXT};
    outline: none;
}}
QTreeWidget#TocTree::item {{ padding: 4px 2px; border-radius: 5px; }}
QTreeWidget#TocTree::item:hover {{ background: {theme.SURFACE_HOVER}; }}
QTreeWidget#TocTree::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}
""")

    def set_entries(self, entries: list[tuple[int, str, int]]) -> None:
        """按 1 基层级重建树；页码为 0 基。"""
        self.tree.clear()
        stack: list[QTreeWidgetItem] = []
        for level, title, page in entries:
            level = max(1, int(level))
            while len(stack) >= level:
                stack.pop()
            item = QTreeWidgetItem([str(title)])
            item.setData(0, Qt.UserRole, int(page))
            if stack:
                stack[-1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            stack.append(item)
        self.tree.expandAll()
        self._sync_empty()

    def clear_entries(self) -> None:
        self.tree.clear()
        self._sync_empty()

    def _sync_empty(self) -> None:
        empty = self.tree.topLevelItemCount() == 0
        self.tree.setVisible(not empty)
        self.empty_label.setVisible(empty)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        page = item.data(0, Qt.UserRole)
        if page is not None:
            self.pageRequested.emit(int(page))
