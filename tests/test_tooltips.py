"""悬浮提示：全局 1 秒延迟 + 工具栏控件都有 tooltip。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QAbstractButton, QApplication, QSlider

from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.ui.find_bar import FindBar
from sketchpad.ui.menubar import Menubar
from sketchpad.ui.note_card import NoteCard
from sketchpad.ui.titlebar import TitleBar
from sketchpad.ui.toolbox import Toolbox
from sketchpad.ui.tooltips import TOOLTIP_DELAY_MS, install


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _missing(widget):
    controls = widget.findChildren(QAbstractButton) + widget.findChildren(QSlider)
    return [type(item).__name__ for item in controls if not item.toolTip().strip()]


def test_tooltip_delay_is_one_second(qapp):
    controller = install(qapp, TOOLTIP_DELAY_MS)
    assert controller.delay_ms == 1000
    assert qapp._tooltip_controller is controller


def test_toolbox_and_titlebar_controls_have_tooltips(qapp):
    box = Toolbox(Settings())
    bar = TitleBar("PageFlow")
    try:
        assert _missing(box) == []
        assert _missing(bar) == []
    finally:
        box.close()
        bar.close()


def test_find_bar_and_note_card_controls_have_tooltips(qapp):
    find = FindBar()
    card = NoteCard(Note(id="n1", x=0.5, y=0.5), Settings())
    try:
        assert _missing(find) == []
        assert _missing(card) == []
    finally:
        find.close()
        card.close()


def test_menu_actions_have_tooltips(qapp):
    bar = Menubar()
    try:
        from PySide6.QtGui import QAction
        missing = [action.text() for action in bar.findChildren(QAction)
                   if action.text() and not action.isSeparator()
                   and not action.toolTip().strip()]
        assert missing == []
    finally:
        bar.close()


def test_main_window_user_buttons_have_tooltips(qapp, tmp_path):
    import main

    window = main.MainWindow(main.AppController(tmp_path / "cfg"))
    try:
        skip = {"qt_menubar_ext_button", "ScrollLeftButton", "ScrollRightButton"}
        missing = [type(button).__name__ for button in window.findChildren(QAbstractButton)
                   if button.objectName() not in skip and not button.toolTip().strip()]
        assert missing == []
    finally:
        window._quitting = True
        window.close()
