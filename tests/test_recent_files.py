"""最近打开：单条右键删除、顶部批量管理、对话框勾选/清空。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

import main
import sketchpad.app as app_module
from sketchpad.core.settings import remove_recent
from sketchpad.ui.recent_dialog import RecentFilesDialog

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard
    _ALIVE.append(w)
    yield w
    w._quitting = True
    w.close()


def _paths(tmp_path):
    a = tmp_path / "甲.pdf"
    b = tmp_path / "乙.xmind"
    a.write_bytes(b"%PDF-1.4")
    b.write_bytes(b"PK\x03\x04")
    missing = tmp_path / "已删除.md"
    return a, b, missing


def test_remove_recent按归一化路径删除且不改入参():
    source = ["C:/Docs/A.PDF", "C:/Docs/b.pdf"]
    result = remove_recent(source, "c:/docs/a.pdf")
    assert result == ["C:/Docs/b.pdf"]
    assert source == ["C:/Docs/A.PDF", "C:/Docs/b.pdf"]
    assert remove_recent(source, "") == source


def test_recent_dialog勾选删除和清空(qapp, tmp_path):
    a, b, missing = _paths(tmp_path)
    dialog = RecentFilesDialog([str(a), str(b), str(missing)])
    try:
        assert dialog.list.count() == 3
        assert "文件不存在" in dialog.list.item(2).text()
        dialog.list.item(0).setCheckState(Qt.CheckState.Checked)
        dialog.list.item(1).setCheckState(Qt.CheckState.Checked)
        dialog._delete_selected()
        assert dialog.action == "delete"
        assert dialog.selected == [str(a), str(b)]
    finally:
        dialog.deleteLater()

    clear = RecentFilesDialog([str(a)])
    try:
        clear._clear_all()
        assert clear.action == "clear"
        assert clear.selected == []
    finally:
        clear.deleteLater()


def test_recent_menu顶部批量管理且条目带路径(win, tmp_path):
    a, b, missing = _paths(tmp_path)
    win.controller.settings.recent_files = [str(a), str(b), str(missing)]
    win._rebuild_recent_menu()
    actions = win.menubar.recent_menu.actions()
    assert actions[0].text() == "批量管理…"
    assert actions[0].isEnabled() is True
    data = [action.data() for action in actions]
    assert str(a) in data and str(b) in data and str(missing) in data
    assert win.menubar.recent_menu.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu


def test_单条删除只改记录不改文件(win, tmp_path):
    a, b, _missing = _paths(tmp_path)
    win.controller.settings.recent_files = [str(a), str(b)]
    win._rebuild_recent_menu()
    win._remove_recent(str(a))
    assert win.controller.settings.recent_files == [str(b)]
    assert a.exists() and b.exists()
    assert any("已从最近打开移除" in text for text in [win.message_label.text()])


def test_右键删除走上下文菜单(win, tmp_path, monkeypatch):
    a, b, _missing = _paths(tmp_path)
    win.controller.settings.recent_files = [str(a), str(b)]
    win._rebuild_recent_menu()
    target = next(action for action in win.menubar.recent_menu.actions()
                  if action.data() == str(a))
    monkeypatch.setattr(QMenu, "actionAt", lambda self, pos: target)

    def fake_popup(self, _pos):
        action = next(action for action in self.actions()
                      if action.text() == "从最近打开中删除")
        action.trigger()
        self.hide()

    monkeypatch.setattr(QMenu, "popup", fake_popup)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                              QPoint(1, 1), QPoint(1, 1))
    assert win.eventFilter(win.menubar.recent_menu, event) is True
    assert str(a) not in win.controller.settings.recent_files
    assert str(b) in win.controller.settings.recent_files


def test_批量管理删除选中与清空(win, tmp_path, monkeypatch):
    a, b, _missing = _paths(tmp_path)
    win.controller.settings.recent_files = [str(a), str(b)]

    class DeleteDialog:
        action = "delete"
        selected = [str(a)]

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(app_module, "RecentFilesDialog", DeleteDialog)
    win._manage_recent_files()
    assert win.controller.settings.recent_files == [str(b)]

    class ClearDialog(DeleteDialog):
        action = "clear"
        selected = []

    monkeypatch.setattr(app_module, "RecentFilesDialog", ClearDialog)
    win._manage_recent_files()
    assert win.controller.settings.recent_files == []


def test_没有最近记录时批量管理直接提示(win):
    win.controller.settings.recent_files = []
    win._rebuild_recent_menu()
    assert win.menubar.recent_menu.actions()[0].isEnabled() is False
    win._manage_recent_files()
    assert "没有最近打开记录" in win.message_label.text()
