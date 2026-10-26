"""截图历史窗口：缩略图列表 + 复制/贴图/重新编辑/删除。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from sketchpad.screenshot.history import HistoryEntry, ScreenshotHistory

_ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
_THUMB = QSize(128, 72)


class HistoryDialog(QDialog):
    pinRequested = Signal(QImage)
    editRequested = Signal(QImage, str)

    def __init__(self, history: ScreenshotHistory, parent=None) -> None:
        super().__init__(parent)
        self.history = history
        self.setWindowTitle("截图历史")
        self.resize(760, 500)
        self.setStyleSheet("""
            QDialog { background: #202024; }
            QListWidget { background: #141416; color: #e8eaed;
                          border: 1px solid #3a3a3e; outline: none; }
            QListWidget::item { padding: 6px; }
            QListWidget::item:selected { background: #0a84ff; color: #ffffff; }
            QLabel { color: #9a9aa0; }
            QPushButton { color: #e8eaed; background: #3a3a3c; border: none;
                          padding: 5px 14px; border-radius: 5px; }
            QPushButton:hover { background: #4a4a4e; }
            QPushButton:disabled { color: #6a6a70; background: #2a2a2e; }
        """)
        layout = QVBoxLayout(self)
        self.empty_label = QLabel("还没有截图历史", self)
        layout.addWidget(self.empty_label)
        self.list = QListWidget(self)
        self.list.setIconSize(_THUMB)
        self.list.setSpacing(2)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        self.list.itemDoubleClicked.connect(lambda _item: self._edit())
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.info_label = QLabel("", self)
        row.addWidget(self.info_label)
        row.addStretch(1)
        self.copy_button = QPushButton("复制", self)
        self.copy_button.clicked.connect(self._copy)
        self.pin_button = QPushButton("贴图", self)
        self.pin_button.clicked.connect(self._pin)
        self.edit_button = QPushButton("重新编辑", self)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button = QPushButton("删除", self)
        self.delete_button.clicked.connect(self._delete)
        open_button = QPushButton("打开目录", self)
        open_button.clicked.connect(self._open_folder)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.close)
        for button in (self.copy_button, self.pin_button, self.edit_button,
                       self.delete_button, open_button, close_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh()

    # ---------- 列表 ----------

    def refresh(self) -> None:
        self.list.clear()
        entries = self.history.entries()
        self.empty_label.setVisible(not entries)
        self.list.setVisible(bool(entries))
        for entry in entries:
            item = QListWidgetItem(self._label(entry))
            item.setData(_ENTRY_ROLE, entry)
            icon = self._icon(entry)
            if icon is not None:
                item.setIcon(icon)
            self.list.addItem(item)
        self._sync_buttons()

    def _label(self, entry: HistoryEntry) -> str:
        created = entry.created.replace("T", " ")
        source = {"region": "区域", "window": "窗口",
                  "fullscreen": "全屏", "last": "上一区域"}.get(entry.source,
                                                              entry.source)
        return f"{created}   {entry.width}×{entry.height}   {source}"

    def _icon(self, entry: HistoryEntry) -> QIcon | None:
        image = self.history.load(entry)
        if image is None:
            return None
        pixmap = QPixmap.fromImage(image).scaled(
            _THUMB, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        return QIcon(pixmap)

    def current_entry(self) -> HistoryEntry | None:
        item = self.list.currentItem()
        return item.data(_ENTRY_ROLE) if item is not None else None

    def _sync_buttons(self) -> None:
        entry = self.current_entry()
        enabled = entry is not None
        for button in (self.copy_button, self.pin_button, self.edit_button,
                       self.delete_button):
            button.setEnabled(enabled)
        if entry is not None:
            self.info_label.setText(f"共 {len(self.history.entries())} 条")
        else:
            self.info_label.setText(
                f"共 {len(self.history.entries())} 条" if self.history.entries()
                else "")

    # ---------- 动作 ----------

    def _copy(self) -> bool:
        entry = self.current_entry()
        if entry is None:
            return False
        image = self.history.load(entry)
        if image is None:
            return False
        QGuiApplication.clipboard().setImage(image)
        self.info_label.setText("已复制到剪贴板")
        return True

    def _pin(self) -> bool:
        entry = self.current_entry()
        if entry is None:
            return False
        image = self.history.load(entry)
        if image is None:
            return False
        self.pinRequested.emit(image)
        return True

    def _edit(self) -> bool:
        entry = self.current_entry()
        if entry is None:
            return False
        image = self.history.load(entry)
        if image is None:
            return False
        self.hide()
        self.editRequested.emit(image, entry.source)
        return True

    def _delete(self) -> bool:
        entry = self.current_entry()
        if entry is None:
            return False
        self.history.delete(entry)
        self.refresh()
        return True

    def _open_folder(self) -> None:
        root = Path(self.history.root)
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
