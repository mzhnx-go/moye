"""独立笔记标签：多篇笔记各自独立，且能和 PDF 分屏。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.core.strokes import Stroke

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


def _pdf(tmp_path):
    path = tmp_path / "讲义.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text((40, 60), "PDF")
    doc.save(str(path))
    doc.close()
    return path


def test_multiple_notes_have_independent_canvas(win):
    first_entry = win._draft_entry
    first_view = win.view
    first_doc = win.controller.document
    first_doc.strokes.append(Stroke([(1.0, 1.0), (2.0, 2.0)], "#fff", 2.0))

    win._new_draft_entry()
    second_view = win.view
    second_doc = win.controller.document
    assert second_view is not first_view
    assert second_doc is not first_doc
    assert second_doc.strokes == []
    second_doc.strokes.append(Stroke([(3.0, 3.0), (4.0, 4.0)], "#fff", 2.0))

    win.tabs.set_current_entry(first_entry)
    assert win.view is first_view
    assert win.controller.document is first_doc
    assert len(first_doc.strokes) == 1

    win._new_draft_entry()                       # 再新建第三篇
    assert len([e for e in win.tabs.entries if e.sessions
                and getattr(e.sessions[0], "kind", "") == "note"]) == 3


def test_note_and_pdf_can_split(win, tmp_path):
    pdf = _pdf(tmp_path)
    note_entry = win._draft_entry
    assert note_entry is not None
    note_session = note_entry.sessions[0]
    assert win._enter_document(pdf) is True
    pdf_entry = win.tabs.current_entry

    win._merge_document_entries(note_entry, pdf_entry)
    merged = win.tabs.current_entry
    assert merged.is_split is True
    assert merged.title == f"笔记 1 | {pdf.name}"
    assert [getattr(s, "kind", "pdf") for s in merged.sessions] == ["note", "pdf"]

    # 默认活动是笔记：工具栏是笔记模式，画在笔记自己的文档里
    assert win.mode == "draft"
    assert win.view is note_session.view
    win.view.document.strokes.append(Stroke([(5.0, 5.0), (6.0, 6.0)], "#fff", 2.0))

    split = merged.page
    pdf_session = merged.sessions[1]
    split.set_active(pdf_session)
    assert win.mode == "document"
    assert win.pdf_view is pdf_session.view
    assert win.pdf_view.document.pages[0].strokes == []

    split.set_active(note_session)
    assert win.mode == "draft"
    assert win.view is note_session.view
    assert len(win.view.document.strokes) == 1


def test_pdf_note_tool_still_creates_note_from_page_click(win, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    path = _pdf(tmp_path)
    assert win._enter_document(path) is True
    assert win.toolbox.note_button.isHidden() is False
    win._apply_tool("note")
    view = win.pdf_view
    QTest.mouseClick(view.viewport(), Qt.LeftButton,
                     pos=view.viewport().rect().center())
    QTest.qWait(20)
    assert len(view.document.pages[0].notes) == 1
    assert win.controller.document is not None
