"""快捷键一览 / 关于 对话框的离屏测试。"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from sketchpad import __version__
from sketchpad.ui.dialogs import (SHORTCUTS, AboutDialog, CloseConfirmDialog,
                                  ShortcutsDialog)

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def shortcuts(qapp):
    dialog = ShortcutsDialog()
    _ALIVE.append(dialog)
    return dialog


def _texts(widget) -> str:
    return "\n".join(label.text() for label in widget.findChildren(QLabel))


def test_shortcuts_dialog_lists_every_binding(shortcuts):
    text = _texts(shortcuts)
    for action, keys in SHORTCUTS:
        assert action in text
        assert keys in text
    assert shortcuts.objectName() == "ShortcutsDialog"
    assert shortcuts.testAttribute(Qt.WA_StyledBackground) is True


def test_shortcuts_close_accepts(shortcuts):
    shortcuts.close_button.click()
    assert shortcuts.result() == QDialog.Accepted


def test_about_dialog_mentions_version_and_license(qapp):
    dialog = AboutDialog()
    _ALIVE.append(dialog)
    text = _texts(dialog)
    # 版本号只从包元数据来：断言非空且对话框照它渲染，不钉死具体版本
    assert isinstance(__version__, str) and __version__.strip()
    assert f"墨页 v{__version__}" in text
    assert "墨页 v2.0" not in text          # 版本号不再手写死
    assert "PyMuPDF" in text
    assert "AGPL-3.0" in text
    assert dialog.objectName() == "AboutDialog"
    dialog.close_button.click()
    assert dialog.result() == QDialog.Accepted


def test_close_confirm_dialog_has_three_choices(qapp):
    dialog = CloseConfirmDialog("墨页")
    _ALIVE.append(dialog)
    assert dialog.windowTitle() == "关闭 墨页 窗口？"
    assert dialog.windowFlags() & Qt.FramelessWindowHint
    assert dialog.title_label.text() == "关闭 墨页 窗口？"
    assert "后台任务会继续运行" in dialog.body_label.text()
    assert [dialog.cancel_button.text(), dialog.quit_button.text(),
            dialog.minimize_button.text()] == ["取消", "退出应用", "最小化到托盘"]
    assert dialog.minimize_button.isEnabled() is True
    assert dialog.action == ""


def test_close_confirm_buttons_set_action(qapp):
    for button_name, expected in (("cancel_button", "cancel"),
                                  ("minimize_button", "tray"),
                                  ("quit_button", "quit")):
        dialog = CloseConfirmDialog("墨页")
        _ALIVE.append(dialog)
        getattr(dialog, button_name).click()
        assert dialog.action == expected


def test_close_confirm_escape_does_nothing(qapp):
    """Esc 不产生动作：取消要点按钮（按用户要求按钮上就有取消）。"""
    dialog = CloseConfirmDialog("墨页")
    _ALIVE.append(dialog)
    dialog.reject()
    assert dialog.action == ""


def test_close_confirm_without_recovery_cannot_minimize(qapp):
    dialog = CloseConfirmDialog("墨页", can_minimize=False)
    _ALIVE.append(dialog)
    assert dialog.minimize_button.isEnabled() is False
    assert "无法再呼出" in dialog.minimize_button.toolTip()


def test_help_table_covers_every_menu_shortcut(qapp):
    """菜单上展示的组合键必须都在帮助表里（防两处手写漂移）。"""
    from sketchpad.ui.menubar import Menubar

    bar = Menubar()
    try:
        keys = "\n".join(text for _, text in SHORTCUTS)
        missing = sorted({sequence.toString()
                          for action in bar.findChildren(QAction)
                          for sequence in action.shortcuts()
                          if sequence.toString() and sequence.toString() not in keys})
        assert missing == []
    finally:
        bar.close()
