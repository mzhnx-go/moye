"""主窗多标签：独立会话、切标签不串状态、关闭/去重。"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.core.strokes import Stroke

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path, pages=3):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=595.0, height=842.0)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    w._quitting = True
    w.close()


def _doc_entries(win):
    return [e for e in win.tabs.entries if not e.draft]


def _draw(view):
    view.document.pages[0].strokes.append(
        Stroke([(20.0, 20.0), (80.0, 80.0)], "#e8eaed", 2.0))
    view.scene().rebind() if callable(view.scene) else view.scene.rebind()
    view.history.push(type("Cmd", (), {"page_index": 0, "undo": lambda s: None,
                                        "redo": lambda s: None})())


def test_open_two_docs_creates_tabs_and_keeps_state(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    b = _make_pdf(tmp_path / "b.pdf")
    assert win._enter_document(a) is True
    view_a = win.pdf_view
    view_a.document.pages[0].strokes.append(
        Stroke([(20.0, 20.0), (80.0, 80.0)], "#e8eaed", 2.0))
    view_a.viewport().update()
    scroll_a = view_a.verticalScrollBar().value()

    assert win._enter_document(b) is True
    assert len(_doc_entries(win)) == 2
    assert win.pdf_view.pdf_path == b
    assert win.pdf_view is not view_a

    entry_a = next(e for e in _doc_entries(win) if e.sessions[0].path == a)
    win.tabs.set_current_entry(entry_a)
    assert win.pdf_view is view_a
    assert len(view_a.document.pages[0].strokes) == 1
    assert view_a.verticalScrollBar().value() == scroll_a


def test_duplicate_path_activates_existing_tab(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    assert win._enter_document(a) is True
    view = win.pdf_view
    assert win._enter_document(a) is True
    assert len(_doc_entries(win)) == 1
    assert win.pdf_view is view


def test_dirty_marker_and_close_tab(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    b = _make_pdf(tmp_path / "b.pdf")
    win._enter_document(a)
    view = win.pdf_view
    view.document.pages[0].strokes.append(
        Stroke([(10.0, 10.0), (30.0, 30.0)], "#e8eaed", 2.0))
    win._on_strokes_changed()
    entry_a = next(e for e in _doc_entries(win) if e.sessions[0].path == a)
    assert entry_a.title.startswith("● ")
    win._enter_document(b)
    assert len(_doc_entries(win)) == 2

    win._close_entry(entry_a)
    assert all(e.sessions[0].path != a for e in _doc_entries(win))
    assert win.pdf_view.pdf_path == b


def test_enter_draft_closes_all_document_tabs(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    b = _make_pdf(tmp_path / "b.pdf")
    win._enter_document(a)
    win._enter_document(b)
    win._enter_draft()
    assert win.mode == "draft"
    assert _doc_entries(win) == []
    assert win.stack.currentWidget() is win.view


def test_merge_two_documents_into_split_and_route_active_pane(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    b = _make_pdf(tmp_path / "b.pdf")
    win._enter_document(a)
    left = win.pdf_view
    win._enter_document(b)
    right = win.pdf_view
    entries = _doc_entries(win)
    ea = next(e for e in entries if e.sessions[0].path == a)
    eb = next(e for e in entries if e.sessions[0].path == b)

    win._merge_document_entries(ea, eb)
    docs = _doc_entries(win)
    assert len(docs) == 1
    merged = docs[0]
    assert merged.is_split is True
    assert merged.title == "a.pdf | b.pdf"
    assert win._active_entry is merged
    assert win.pdf_view is left                     # 默认左侧活动
    assert left.isVisible() and right.isVisible()   # 分屏两栏必须真的显示内容
    split = merged.page
    assert split.sessions[0].pane.isVisible() and split.sessions[1].pane.isVisible()
    split.set_active(split.sessions[1])
    assert win.pdf_view is right
    assert win.tabs.current_entry is merged          # 切活动栏不换标签

    # 两侧状态独立
    left.document.pages[0].strokes.append(
        Stroke([(10.0, 10.0), (40.0, 40.0)], "#e8eaed", 2.0))
    assert len(right.document.pages[0].strokes) == 0


def test_unsplit_restores_two_tabs(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    b = _make_pdf(tmp_path / "b.pdf")
    win._enter_document(a)
    va = win.pdf_view
    win._enter_document(b)
    vb = win.pdf_view
    ea, eb = _doc_entries(win)
    win._merge_document_entries(ea, eb)
    split = _doc_entries(win)[0].page

    win._unsplit_document_entry(split)
    docs = _doc_entries(win)
    assert len(docs) == 2
    assert all(not entry.is_split for entry in docs)
    paths = {entry.sessions[0].path for entry in docs}
    assert paths == {a, b}
    assert va.parentWidget() is not None and vb.parentWidget() is not None


def test_close_split_tab_closes_both_sessions(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    b = _make_pdf(tmp_path / "b.pdf")
    win._enter_document(a)
    win._enter_document(b)
    ea, eb = _doc_entries(win)
    win._merge_document_entries(ea, eb)
    merged = _doc_entries(win)[0]
    win._close_entry(merged)
    assert _doc_entries(win) == []
    assert win.mode == "draft"
