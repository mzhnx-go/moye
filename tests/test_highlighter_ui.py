# tests/test_highlighter_ui.py
"""荧光笔 UI 接线离屏测试：工具栏按钮、工具菜单、H 键路由、默认黄色与目录搬家。

只验证 UI 状态与信号，不碰渲染像素（渲染/导出由核心测试覆盖）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.core.settings import DEFAULT_COLOR, HIGHLIGHTER_COLOR
from sketchpad.ui import icons
from sketchpad.ui.menubar import Menubar
from sketchpad.ui.toolbox import Toolbox

HIGHLIGHTER_YELLOW = HIGHLIGHTER_COLOR        # 深黄，荧光笔默认色
USER_GREEN = "#27ae60"
USER_BLUE = "#2980b9"

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    """主窗（配置落 tmp_path，绝不碰 data/）；对话框/保存确认都不弹。"""
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    if w.settings_dialog is not None:
        w.settings_dialog.close()
    w.pdf_autosave.cancel()
    w.pdf_view.close_document()
    w._quitting = True
    w.close()
    qapp.processEvents()


def _focus(view):
    view.setFocus()
    QTest.qWait(20)


# ---------- 工具栏 ----------

def test_toolbox_has_highlighter_button_next_to_pen(qapp):
    tb = Toolbox()
    tb.show()
    QTest.qWaitForWindowExposed(tb)
    try:
        assert tb.highlighter_button.toolTip().startswith("荧光笔 (H")
        assert tb.highlighter_button.icon().isNull() is False
        assert tb.highlighter_button.icon().cacheKey() == \
            icons.highlighter_icon().cacheKey()
        layout = tb.layout()
        assert layout.indexOf(tb.highlighter_button) == \
            layout.indexOf(tb.pen_button) + 1
        # 两种模式都常驻（选择文字才会按模式隐藏）
        tb.set_select_enabled(False)
        assert tb.highlighter_button.isVisible() is True
        tb.set_select_enabled(True)
        assert tb.highlighter_button.isVisible() is True
    finally:
        tb.close()


def test_toolbox_keeps_pen_and_highlighter_colors_apart(qapp):
    tb = Toolbox()
    try:
        pen_button = next(b for b in tb.color_buttons if b.property("color") == USER_GREEN)
        pen_button.click()
        tb.reflect_tool("highlighter")
        assert HIGHLIGHTER_YELLOW in tb.current_color_swatch.styleSheet()
        blue = next(b for b in tb.color_buttons if b.property("color") == USER_BLUE)
        blue.click()
        assert USER_BLUE in tb.current_color_swatch.styleSheet()
        tb.reflect_tool("pen")
        assert USER_GREEN in tb.current_color_swatch.styleSheet()
        tb.reflect_tool("highlighter")
        assert USER_BLUE in tb.current_color_swatch.styleSheet()
    finally:
        tb.close()


def test_toolbox_highlighter_click_emits_and_reflect_checks_only_it(qapp):
    tb = Toolbox()
    tb.show()
    QTest.qWaitForWindowExposed(tb)
    try:
        got = []
        tb.toolChosen.connect(got.append)
        tb.highlighter_button.click()
        assert got == ["highlighter"]
        assert tb.highlighter_button.isChecked() is True
        assert tb.pen_button.isChecked() is False

        tb.reflect_tool("pen")
        assert tb.pen_button.isChecked() is True
        assert tb.highlighter_button.isChecked() is False
        tb.reflect_tool("highlighter")
        assert tb.highlighter_button.isChecked() is True
        assert [tb.pen_button.isChecked(), tb.select_button.isChecked(),
                tb.eraser_button.isChecked()] == [False, False, False]
        assert got == ["highlighter"]            # 反射不回声
    finally:
        tb.close()


# ---------- 菜单 ----------

def test_menu_highlighter_action_is_in_tools_and_checkable_with_h(qapp):
    bar = Menubar()
    try:
        assert bar.highlighter_action in bar.tools_menu.actions()
        assert bar.highlighter_action.text() == "荧光笔"
        assert bar.highlighter_action.isCheckable() is True
        assert bar.highlighter_action.shortcut().toString() == "H"
        assert bar.highlighter_action.isEnabled() is True

        triggered, tools = [], []
        bar.actionTriggered.connect(triggered.append)
        bar.toolChosen.connect(tools.append)
        bar.highlighter_action.trigger()
        assert (bar.pen_action.isChecked(), bar.highlighter_action.isChecked()) == \
            (False, True)
        bar.set_tool("highlighter")
        assert [bar.pen_action.isChecked(), bar.highlighter_action.isChecked(),
                bar.select_action.isChecked(), bar.eraser_action.isChecked()] == \
            [False, True, False, False]
        assert triggered == ["highlighter"]
        assert tools == ["highlighter"]
    finally:
        bar.close()


def test_menu_toc_lives_in_navigate_and_stays_document_only(qapp):
    bar = Menubar()
    try:
        actions = bar.navigate_menu.actions()
        assert actions[0] is bar.toc_action
        assert actions[1].isSeparator() is True
        assert bar.toc_action not in bar.view_menu.actions()
        assert bar.toc_action.isCheckable() is True

        bar.set_mode("draft")
        assert bar.toc_action.isEnabled() is False
        bar.set_mode("document")
        assert bar.toc_action.isEnabled() is True
        assert bar.navigate_menu.isEnabled() is True
    finally:
        bar.close()


def test_eye_mode_menu_label_is_parchment_while_key_stays_eye(qapp):
    bar = Menubar()
    try:
        assert bar.page_mode_actions["eye"].text() == "护眼（羊皮纸）"
        got = []
        bar.actionTriggered.connect(got.append)
        bar.page_mode_actions["eye"].trigger()
        assert got == ["page_eye"]
    finally:
        bar.close()


# ---------- H 键路由与默认颜色 ----------

def test_h_switches_both_views_and_keeps_pen_color(win):
    assert win.view.color.lower() == DEFAULT_COLOR.lower()
    assert win.view.highlighter_color == HIGHLIGHTER_YELLOW
    win.activateWindow()
    _focus(win.view)

    QTest.keyClick(win.view, Qt.Key_H)
    assert win.view.tool == "highlighter"
    assert win.pdf_view.tool == "highlighter"
    assert win.toolbox.highlighter_button.isChecked() is True
    assert win.menubar.highlighter_action.isChecked() is True
    assert win.toolbox.pen_button.isChecked() is False

    # 荧光笔用自己的深黄，画笔色原样不动；配置里也是两份
    assert win.view.tool_color == HIGHLIGHTER_YELLOW
    assert win.pdf_view.tool_color == HIGHLIGHTER_YELLOW
    assert win.view.color == DEFAULT_COLOR
    assert win.pdf_view.color == DEFAULT_COLOR
    assert win.controller.settings.color == DEFAULT_COLOR
    assert win.controller.settings.highlighter_color == HIGHLIGHTER_YELLOW
    assert win.toolbox.color_buttons[5].isChecked() is True    # 深黄 #d4a017
    assert HIGHLIGHTER_YELLOW in win.toolbox.current_color_swatch.styleSheet()


def test_h_and_pen_colors_are_remembered_separately(win):
    """画笔与荧光笔各记各的：切工具只换色板，不互相覆盖。"""
    win.toolbox.color_buttons[6].click()                     # 画笔选绿 #27ae60
    assert win.view.color == USER_GREEN

    win._apply_tool("highlighter")                           # 荧光笔仍是自己的深黄
    assert win.view.color == USER_GREEN
    assert win.view.tool_color == HIGHLIGHTER_YELLOW
    assert win.toolbox.color_buttons[5].isChecked() is True

    win.toolbox.color_buttons[1].click()                     # 荧光笔选蓝 #2980b9
    assert win.view.highlighter_color == USER_BLUE
    assert win.view.color == USER_GREEN
    assert win.pdf_view.highlighter_color == USER_BLUE
    assert win.controller.settings.highlighter_color == USER_BLUE
    assert win.controller.settings.color == USER_GREEN

    win._apply_tool("pen")                                   # 切回画笔：绿还在
    assert win.view.tool_color == USER_GREEN
    assert win.toolbox.color_buttons[6].isChecked() is True

    win._apply_tool("highlighter")                           # 再切荧光笔：蓝还在
    assert win.view.tool_color == USER_BLUE
    assert win.toolbox.color_buttons[1].isChecked() is True


def test_h_is_ignored_while_a_text_field_has_focus(win, tmp_path):
    """查找框里敲 H 必须是字母 h，不能切荧光笔（与 B/Q/V 同一道放行规则）。"""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=595.0, height=842.0)
    pdf = tmp_path / "text.pdf"
    doc.save(str(pdf))
    doc.close()
    assert win._enter_document(pdf) is True

    win.activateWindow()
    win._show_find()
    QTest.qWait(20)
    field = win.find_bar.input
    field.clear()
    _focus(field)
    assert QApplication.focusWidget() is field

    QTest.keyClick(field, Qt.Key_H)
    assert field.text() == "h"
    assert win.pdf_view.tool != "highlighter"
    assert win.view.tool != "highlighter"
