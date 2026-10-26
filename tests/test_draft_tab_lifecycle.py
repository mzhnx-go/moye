"""草稿标签生命周期：可关闭、空态、再新建、内容清空。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
import pytest

import main
from sketchpad.core.strokes import Stroke
from sketchpad.ui.empty_workspace import EmptyWorkspace

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    w._quitting = True
    w.close()


def _draw_draft(win, points=((20.0, 20.0), (80.0, 80.0))):
    win.controller.document.strokes.append(Stroke(list(points), "#e8eaed", 2.0))
    win.view.scene.rebind()


def test_tabs_live_in_titlebar_and_menu_creates_draft(win):
    assert win.titlebar.center_widget is win.tabs.strip
    assert win.tabs.strip.parentWidget() is win.titlebar
    win._close_entry(win._draft_entry)
    assert win._draft_entry is None

    win._new_draft_entry()               # 文件菜单 / Ctrl+N 走的同一条路

    assert win._draft_entry is not None
    assert win.stack.currentWidget() is win.view


def test_empty_workspace_buttons_emit_actions(win, monkeypatch):
    win._close_entry(win._draft_entry)
    page = win._empty_page
    opened = []
    monkeypatch.setattr(win, "_open_pdf", lambda: opened.append(True))
    page.openRequested.disconnect()
    page.openRequested.connect(win._open_pdf)
    open_button = next(button for button in page.findChildren(QPushButton)
                       if button.text() == "打开文件")
    open_button.click()
    assert opened == [True]


def test_empty_draft_closes_without_confirm_and_shows_empty_page(win, tmp_path):
    entry = win._draft_entry
    asked = []
    win._confirm_close_draft = lambda: asked.append(True) or True

    win._close_entry(entry)

    assert asked == []                       # 空草稿不问
    assert win._draft_entry is None
    assert win.tabs.entries == []
    assert isinstance(win._empty_page, EmptyWorkspace)
    assert win.stack.currentWidget() is win._empty_page
    assert win._empty_page.isVisible() is True


def test_nonempty_draft_needs_confirmation_and_cancel_keeps_it(win):
    _draw_draft(win)
    entry = win._draft_entry
    win._confirm_close_draft = lambda: False

    win._close_entry(entry)

    assert win._draft_entry is entry
    assert entry in win.tabs.entries
    assert len(win.controller.document.strokes) == 1


def test_nonempty_draft_close_clears_ink_history_and_images(win):
    _draw_draft(win)
    assert win.view.insert_image(QImage(8, 8, QImage.Format_ARGB32),
                                 QPointF(30.0, 30.0)) is True
    assert win.view.image_count == 1
    entry = win._draft_entry
    win._confirm_close_draft = lambda: True

    win._close_entry(entry)

    assert win._draft_entry is None
    assert win.controller.document.strokes == []
    assert win.view.image_count == 0
    assert win.view.history.can_undo is False
    assert win.view.history.can_redo is False


def test_new_draft_after_empty_switches_to_a_blank_canvas(win):
    win._close_entry(win._draft_entry)
    assert win._empty_page is not None

    win._new_draft_entry()

    assert win._draft_entry is not None
    assert win.tabs.entries == [win._draft_entry]
    assert win.stack.currentWidget() is win.view
    assert win._empty_page is None              # 有标签后占位页摘掉
    assert win.view.document.strokes == []


def test_new_draft_twice_creates_an_independent_note(win):
    entry = win._draft_entry
    first_view = win.view
    win._new_draft_entry()
    assert win._draft_entry is entry          # 固定首篇不改
    assert len(win.tabs.entries) == 2
    assert win.view is not first_view
    assert win.tabs.current_entry is not entry
    assert win.tabs.current_entry.title == "笔记 2"


def test_closing_background_draft_keeps_current_document(win, tmp_path):
    import pymupdf
    path = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    assert win._enter_document(path) is True
    draft = win._draft_entry
    overview = next(e for e in win.tabs.entries if not e.draft)
    win.tabs.set_current_entry(draft)

    win._close_entry(draft)

    assert win._draft_entry is None
    assert win.tabs.current_entry is overview
    assert win.pdf_view.pdf_path == path
