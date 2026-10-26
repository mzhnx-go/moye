"""压感 UI：数位板事件采集、开关、钢笔/荧光笔差异、PDF 视图与设置面板。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.core.settings import Settings
from sketchpad.core.strokes import has_pressure, pressure_widths
from sketchpad.core.strokes import Document
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.inkitem import InkItem
from sketchpad.ui.pdfview import PdfView
from sketchpad.ui.settings_dialog import SettingsDialog

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp):
    view = SketchView(SketchScene(Document()), Settings())
    view.resize(400, 300)
    view.show()
    QTest.qWaitForWindowExposed(view)
    _ALIVE.append(view)
    yield view
    view.close()


def _tablet(widget, kind, x, y, pressure, buttons):
    device = QPointingDevice()
    local = QPointF(x, y)
    global_pos = QPointF(widget.viewport().mapToGlobal(local.toPoint()))
    ev = QTabletEvent(kind, device, local, global_pos, pressure,
                      0.0, 0.0, 0.0, 0.0, 0.0,
                      Qt.NoModifier, Qt.LeftButton, buttons)
    QApplication.sendEvent(widget.viewport(), ev)


def _draw_tablet(widget, points):
    (x0, y0, p0), *moves, (xn, yn, pn) = points
    _tablet(widget, QEvent.TabletPress, x0, y0, p0, Qt.LeftButton)
    for x, y, p in moves:
        _tablet(widget, QEvent.TabletMove, x, y, p, Qt.LeftButton)
    _tablet(widget, QEvent.TabletRelease, xn, yn, pn, Qt.NoButton)


# ---------- 画布：采集与工具差异 ----------

def test_pen_tablet_records_pressure_and_uses_ink_item(canvas):
    _draw_tablet(canvas, [(50, 50, 0.1), (90, 60, 0.6), (140, 80, 1.0)])
    strokes = canvas.document.strokes
    assert len(strokes) == 1
    stroke = strokes[0]
    assert has_pressure(stroke)
    assert len(stroke.pressures) == len(stroke.points)
    assert min(stroke.pressures) <= 0.1 + 1e-6
    assert max(stroke.pressures) >= 0.99
    widths = pressure_widths(stroke)
    expected = [stroke.width * (0.35 + 0.65 * p) for p in stroke.pressures]
    assert widths == pytest.approx(expected, rel=1e-6)
    assert max(widths) == pytest.approx(stroke.width, rel=0.02)

    items = [item for item in canvas.scene.items() if item.data(0) is stroke]
    assert len(items) == 1 and isinstance(items[0], InkItem)


def test_highlighter_and_eraser_ignore_pressure(canvas):
    canvas.set_tool("highlighter")
    _draw_tablet(canvas, [(50, 50, 0.1), (90, 60, 0.6), (140, 80, 1.0)])
    assert canvas.document.strokes[0].pressures is None

    canvas.set_tool("eraser")
    _draw_tablet(canvas, [(300, 250, 0.2), (320, 260, 0.8), (340, 270, 1.0)])
    assert len(canvas.document.strokes) == 1                 # 橡皮不新增笔迹
    assert canvas.document.strokes[0].pressures is None


def test_pressure_toggle_off_falls_back_to_constant_width(canvas):
    canvas.set_pressure_enabled(False)
    _draw_tablet(canvas, [(50, 50, 0.1), (90, 60, 0.6), (140, 80, 1.0)])
    assert canvas.document.strokes[0].pressures is None
    canvas.set_pressure_enabled(True)


def test_mouse_stroke_has_no_pressure(canvas):
    from PySide6.QtCore import QPoint
    QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=QPoint(50, 50))
    QTest.mouseMove(canvas.viewport(), QPoint(120, 80), delay=5)
    QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=QPoint(140, 90))
    assert canvas.document.strokes[0].pressures is None


# ---------- PDF 视图 ----------

def _make_pdf(path):
    doc = pymupdf.open()
    doc.new_page(width=300.0, height=400.0)
    doc.save(str(path))
    doc.close()
    return path


def test_pdf_pen_tablet_records_pressure(qapp, tmp_path):
    view = PdfView(Settings())
    view.resize(300, 240)
    view.show()
    QTest.qWaitForWindowExposed(view)
    _ALIVE.append(view)
    view.open(_make_pdf(tmp_path / "p.pdf"))
    try:
        view.set_tool("pen")
        p0 = view.mapFromScene(QPointF(40.0, 60.0))
        p1 = view.mapFromScene(QPointF(100.0, 90.0))
        p2 = view.mapFromScene(QPointF(160.0, 120.0))
        _draw_tablet(view, [(p0.x(), p0.y(), 0.2),
                            (p1.x(), p1.y(), 0.7),
                            (p2.x(), p2.y(), 1.0)])
        strokes = view.document.pages[0].strokes
        assert len(strokes) == 1
        assert has_pressure(strokes[0])
        assert len(strokes[0].pressures) == len(strokes[0].points)
    finally:
        view.close_document()
        view.close()


def test_pdf_pressure_toggle_and_highlighter(qapp, tmp_path):
    view = PdfView(Settings(pressure_enabled=False))
    view.resize(300, 240)
    view.show()
    QTest.qWaitForWindowExposed(view)
    _ALIVE.append(view)
    view.open(_make_pdf(tmp_path / "p.pdf"))
    try:
        view.set_tool("pen")
        p0, p1 = view.mapFromScene(QPointF(40.0, 60.0)), view.mapFromScene(
            QPointF(120.0, 110.0))
        _draw_tablet(view, [(p0.x(), p0.y(), 0.1), (p1.x(), p1.y(), 1.0)])
        assert view.document.pages[0].strokes[0].pressures is None

        view.set_pressure_enabled(True)
        view.set_tool("highlighter")
        _draw_tablet(view, [(p0.x(), p0.y(), 0.1), (p1.x(), p1.y(), 1.0)])
        assert view.document.pages[0].strokes[1].pressures is None
    finally:
        view.close_document()
        view.close()


# ---------- 设置面板 ----------

def test_settings_dialog_pressure_checkbox(qapp):
    dlg = SettingsDialog(Settings(pressure_enabled=True))
    try:
        assert dlg.pressure_check.text() == "压感（数位板）"
        assert dlg.pressure_check.isChecked() is True
        dlg.pressure_check.setChecked(False)
        assert dlg.values().pressure_enabled is False
        dlg.reset_button.click()
        assert dlg.pressure_check.isChecked() is True
    finally:
        dlg.close()
