"""落笔路径的局部刷新：悬停/拖动/数位板都不应整屏重绘，圆点必须跟着笔走。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Document, Stroke
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.pdfview import PdfView


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text((72, 72), "repaint")
    doc.save(str(path))
    doc.close()


def _record_updates(view):
    calls: list[tuple] = []
    view.viewport().update = lambda *args: calls.append(args)
    return calls


def _partial_only(calls, view) -> None:
    assert calls, "没有触发任何重绘"
    for args in calls:
        assert len(args) == 1, f"出现了整屏 update()：{args!r}"
        rect = args[0]
        assert rect.width() < view.viewport().width()
        assert rect.height() < view.viewport().height()


def _send_mouse_move(view, x: float, y: float, buttons=Qt.LeftButton) -> None:
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(int(x), int(y))))
    ev = QMouseEvent(QEvent.MouseMove, local, global_pos,
                     Qt.NoButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def _send_tablet(view, kind, x, y, pressure, buttons) -> None:
    device = QPointingDevice()
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(local.toPoint()))
    ev = QTabletEvent(kind, device, local, global_pos, pressure,
                      0.0, 0.0, 0.0, 0.0, 0.0,
                      Qt.NoModifier, Qt.LeftButton, buttons)
    QApplication.sendEvent(view.viewport(), ev)


def test_draft_mouse_move_repaints_only_cursor_and_segment(qapp):
    view = SketchView(SketchScene(Document()), Settings())
    view.resize(520, 420)
    view.show()
    qapp.processEvents()
    try:
        view.tool = "pen"
        start = QPointF(160, 150)
        view._begin(start, None)
        calls = _record_updates(view)
        _send_mouse_move(view, 176, 158)
        assert view._drawing is True
        _partial_only(calls, view)
        assert view._cursor_pos == QPointF(176, 158)
        # 预览路径是增量拼出来的，不是每帧重建整条折线
        assert view._preview_path is not None
        assert view._preview_path.elementCount() >= 3
        view._end(QPointF(176, 158), None)
    finally:
        view.close()


def test_draft_eraser_move_repaints_only_hit_area(qapp):
    document = Document()
    document.strokes.append(
        Stroke([(20.0, 20.0), (280.0, 280.0)], "#ffffff", 4.0))
    view = SketchView(SketchScene(document), Settings())
    view.resize(520, 420)
    view.show()
    qapp.processEvents()
    try:
        view.set_tool("eraser")
        view._begin(QPointF(100, 100), None)
        calls = _record_updates(view)
        _send_mouse_move(view, 112, 112)
        _partial_only(calls, view)
        _send_mouse_move(view, 112, 112, Qt.NoButton)
        view._end(QPointF(112, 112), None)
    finally:
        view.close()


def test_draft_tablet_move_updates_cursor(qapp):
    view = SketchView(SketchScene(Document()), Settings())
    view.resize(520, 420)
    view.show()
    qapp.processEvents()
    try:
        view.tool = "pen"
        view.pressure_enabled = True
        _send_tablet(view, QEvent.TabletPress, 120, 120, 0.3, Qt.LeftButton)
        assert view._drawing is True
        calls = _record_updates(view)
        _send_tablet(view, QEvent.TabletMove, 150, 132, 0.7, Qt.LeftButton)
        _partial_only(calls, view)
        assert view._cursor_pos == QPointF(150, 132)
        _send_tablet(view, QEvent.TabletRelease, 150, 132, 0.7, Qt.NoButton)
    finally:
        view.close()


def test_pdf_mouse_move_repaints_only_cursor_and_segment(qapp, tmp_path):
    pdf = tmp_path / "repaint.pdf"
    _make_pdf(pdf)
    view = PdfView(Settings())
    view.resize(800, 620)
    view.show()
    view.open(pdf)
    qapp.processEvents()
    try:
        view.tool = "pen"
        center = view.viewport().rect().center()
        QTest.mousePress(view.viewport(), Qt.LeftButton, pos=center)
        assert view._drawing is True
        calls = _record_updates(view)
        _send_mouse_move(view, center.x() + 12, center.y() + 5)
        _partial_only(calls, view)
        assert view._cursor_pos == QPointF(center.x() + 12, center.y() + 5)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton,
                           pos=QPoint(center.x() + 12, center.y() + 5))
    finally:
        view.close_document()
        view.close()


def test_pdf_tablet_move_updates_cursor(qapp, tmp_path):
    pdf = tmp_path / "repaint-tablet.pdf"
    _make_pdf(pdf)
    view = PdfView(Settings())
    view.resize(800, 620)
    view.show()
    view.open(pdf)
    qapp.processEvents()
    try:
        view.tool = "pen"
        view.pressure_enabled = True
        center = view.viewport().rect().center()
        _send_tablet(view, QEvent.TabletPress,
                     center.x(), center.y(), 0.3, Qt.LeftButton)
        assert view._drawing is True
        calls = _record_updates(view)
        _send_tablet(view, QEvent.TabletMove,
                     center.x() + 14, center.y() + 6, 0.8, Qt.LeftButton)
        _partial_only(calls, view)
        assert view._cursor_pos == QPointF(center.x() + 14, center.y() + 6)
        _send_tablet(view, QEvent.TabletRelease,
                     center.x() + 14, center.y() + 6, 0.8, Qt.NoButton)
    finally:
        view.close_document()
        view.close()
