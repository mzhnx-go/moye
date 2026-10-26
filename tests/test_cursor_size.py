"""画笔光标：落笔缩小、停笔 3 秒后恢复；橡皮维持大圆环。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Document
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.cursor import (CURSOR_RADIUS_PX, CURSOR_RESTORE_MS,
                                 ERASER_RADIUS_PX, WRITING_CURSOR_RADIUS_PX)
from sketchpad.ui.pdfview import PdfView

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def draft(qapp):
    view = SketchView(SketchScene(Document()), Settings())
    view.resize(600, 500)
    view.show()
    qapp.processEvents()
    _ALIVE.append(view)
    yield view
    view.close()


@pytest.fixture
def pdf(qapp):
    view = PdfView(Settings())
    view.resize(600, 500)
    view.show()
    qapp.processEvents()
    _ALIVE.append(view)
    yield view
    view.close()


def test_canvas_keeps_visible_arrow_until_first_mouse_move(draft):
    assert draft.viewport().cursor().shape() == Qt.ArrowCursor
    draft._cursor_pos = QPointF(10.0, 10.0)   # 等价于收到第一次 move
    draft.set_tool("highlighter")
    draft.set_tool("pen")
    assert draft.viewport().cursor().shape() == Qt.BlankCursor


def test_draft_pen_cursor_shrinks_while_drawing_and_restores_after_3s(draft):
    assert draft.dot_radius_px() == CURSOR_RADIUS_PX
    draft.set_tool("pen")
    draft._begin(QPointF(20.0, 20.0))
    assert draft.dot_radius_px() == WRITING_CURSOR_RADIUS_PX
    draft._finish_stroke(QPointF(40.0, 40.0))

    assert draft.dot_radius_px() == WRITING_CURSOR_RADIUS_PX   # 停笔后先保持小点
    assert draft._cursor_restore_timer.isActive() is True
    assert draft._cursor_restore_timer.interval() == CURSOR_RESTORE_MS == 3000

    draft._restore_cursor_size()
    assert draft.dot_radius_px() == CURSOR_RADIUS_PX


def test_draft_eraser_keeps_large_ring(draft):
    draft.set_tool("eraser")
    draft._compact_cursor_for_writing()
    assert draft.dot_radius_px() == ERASER_RADIUS_PX


def test_switching_tool_restores_cursor_immediately(draft):
    draft.set_tool("pen")
    draft._compact_cursor_for_writing()
    assert draft.dot_radius_px() == WRITING_CURSOR_RADIUS_PX
    draft.set_tool("highlighter")
    assert draft.dot_radius_px() == CURSOR_RADIUS_PX


def test_pdf_pen_cursor_uses_same_small_and_restore(pdf):
    pdf.set_tool("pen")
    assert pdf.dot_radius_px() == CURSOR_RADIUS_PX
    pdf._compact_cursor_for_writing()
    assert pdf.dot_radius_px() == WRITING_CURSOR_RADIUS_PX
    pdf._schedule_cursor_restore()
    assert pdf._cursor_restore_timer.isActive() is True
    assert pdf._cursor_restore_timer.interval() == 3000
    pdf._restore_cursor_size()
    assert pdf.dot_radius_px() == CURSOR_RADIUS_PX
