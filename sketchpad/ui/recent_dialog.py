"""最近打开：批量管理对话框（勾选删除 / 清空全部）。

不直接改设置：只把用户的选择通过 `action`/`selected` 交回主窗，主窗负责
落盘与重建菜单，避免对话框和 settings 互相依赖。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from sketchpad.ui import theme

_PATH_ROLE = Qt.ItemDataRole.UserRole + 1


class RecentFilesDialog(QDialog):
    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("管理最近打开")
        self.resize(620, 460)
        self.setStyleSheet(f"""
            QDialog {{ background: {theme.WINDOW_BG}; }}
            QLabel {{ color: {theme.TEXT}; }}
            QLabel#RecentHint {{ color: {theme.TEXT_DIM}; font-size: 12px; }}
            QListWidget {{ background: {theme.SURFACE}; color: {theme.TEXT};
                           border: 1px solid {theme.BORDER}; border-radius: 6px;
                           outline: none; padding: 4px; }}
            QListWidget::item {{ padding: 6px 4px; }}
            QListWidget::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}
            QPushButton {{ background: {theme.SURFACE_HOVER}; color: {theme.TEXT};
                           border: none; border-radius: 6px; padding: 6px 14px; }}
            QPushButton:hover {{ background: {theme.SURFACE_PRESSED}; }}
            QPushButton:disabled {{ color: {theme.TEXT_DIM}; background: {theme.SURFACE}; }}
            QPushButton#RecentDelete {{ background: #b3261e; color: #ffffff; }}
            QPushButton#RecentDelete:hover {{ background: #c7382f; }}
        """)
        self.action = ""
        self.selected: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        title = QLabel("最近打开的文件", self)
        layout.addWidget(title)
        self.hint = QLabel("勾选要删除的记录；文件本身不会被删除。", self)
        self.hint.setObjectName("RecentHint")
        layout.addWidget(self.hint)
        self.list = QListWidget(self)
        self.list.setIconSize(QSize(0, 0))
        layout.addWidget(self.list, 1)

        for path in paths:
            target = Path(path)
            missing = not target.exists()
            item = QListWidgetItem(
                f"{target.name or path}" + ("    （文件不存在）" if missing else ""))
            item.setData(_PATH_ROLE, path)
            item.setToolTip(path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            if missing:
                item.setForeground(QColor(theme.TEXT_DIM))
            self.list.addItem(item)

        row = QHBoxLayout()
        self.select_all_button = QPushButton("全选", self)
        self.select_all_button.clicked.connect(self._toggle_select_all)
        self.delete_button = QPushButton("删除选中", self)
        self.delete_button.setObjectName("RecentDelete")
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_button = QPushButton("清空全部", self)
        self.clear_button.clicked.connect(self._clear_all)
        close_button = QPushButton("取消", self)
        close_button.clicked.connect(self.reject)
        row.addWidget(self.select_all_button)
        row.addStretch(1)
        row.addWidget(self.delete_button)
        row.addWidget(self.clear_button)
        row.addWidget(close_button)
        layout.addLayout(row)
        self._sync_buttons()

    def _checked_paths(self) -> list[str]:
        result: list[str] = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(_PATH_ROLE))
        return result

    def _sync_buttons(self) -> None:
        has_items = self.list.count() > 0
        self.select_all_button.setEnabled(has_items)
        self.delete_button.setEnabled(has_items)
        self.clear_button.setEnabled(has_items)
        if not has_items:
            self.hint.setText("还没有最近打开记录。")

    def _toggle_select_all(self) -> None:
        check = any(self.list.item(i).checkState() != Qt.CheckState.Checked
                    for i in range(self.list.count()))
        state = Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(state)
        self.select_all_button.setText("全不选" if check else "全选")

    def _delete_selected(self) -> None:
        self.selected = self._checked_paths()
        if not self.selected:
            self.hint.setText("先勾选要删除的记录。")
            return
        self.action = "delete"
        self.accept()

    def _clear_all(self) -> None:
        self.selected = []
        self.action = "clear"
        self.accept()
