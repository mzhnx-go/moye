"""草稿文字工具：T 切换、点击输入、提交、撤销、Esc 取消。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    w = main.MainWindow(main.AppController(tmp_path / "cfg"))
    w._confirm_unsaved = lambda action: QMessageBox.Discard
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    w._quitting = True
    w.close()


def test_text_tool_menu_switch_does_not_crash(win):
    win._apply_tool("text")
    assert win.view.tool == "text"
    assert win.menubar.text_action.isChecked() is True
    assert win.toolbox.text_button.isChecked() is True
    win._apply_tool("pen")
    assert win.view.tool == "pen"


def test_click_type_and_commit_text(win):
    win._apply_tool("text")
    view = win.view
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=QPoint(160, 140))
    QTest.qWait(20)
    view.setFocus()
    QTest.keyClicks(view, "abc")
    QTest.qWait(20)
    # 点别处提交，再开一个新编辑；第一段应进模型
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=QPoint(300, 260))
    QTest.qWait(20)
    QTest.keyClick(view.viewport(), Qt.Key_Escape)   # 取消第二段（空）
    assert len(win.controller.document.texts) == 1
    block = win.controller.document.texts[0]
    assert block.text == "abc"
    assert block.color == view.color

    win._undo()
    assert win.controller.document.texts == []


def test_t_shortcut_enters_text_tool(win):
    view = win.view
    view.setFocus()
    QTest.keyClick(view, Qt.Key_T)
    assert win.view.tool == "text"
    assert win.toolbox.text_button.isChecked() is True
