"""选字荧光：H 拖过文字生成按行合并的高亮矩形；空白处退回手绘粗线。

高亮复用 Stroke 存储（tool="text_highlight"，points 每两点一矩形），
sidecar 不需要升级；导出为 0.35 不透明度的填充矩形，且先于墨迹绘制。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.core import pdfstore
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import (TEXT_HIGHLIGHT, Stroke, HIGHLIGHTER_WIDTH,
                                    stroke_distance, text_highlight_rects)
from sketchpad.pdf.document import PdfSource, export_with_strokes
from sketchpad.ui.pdfview import PdfView

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "text.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300.0, height=400.0)
    page.insert_text((30, 60), "hello world highlight me", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def view(qapp):
    v = PdfView(Settings())
    v.resize(460, 560)
    v.show()
    QTest.qWaitForWindowExposed(v)
    _ALIVE.append(v)
    yield v
    v.close_document()
    v.close()


def _send_move(view: PdfView, x: float, y: float, buttons=Qt.NoButton) -> None:
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(int(x), int(y))))
    ev = QMouseEvent(QEvent.MouseMove, local, global_pos, Qt.NoButton, buttons,
                     Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def _drag(view: PdfView, points: list[QPoint]) -> None:
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=points[0])
    for pos in points[1:-1]:
        _send_move(view, pos.x(), pos.y(), Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=points[-1])


def _unit_center(view: PdfView, unit) -> QPointF:
    _text, x0, y0, x1, y1, _block, _line = unit
    layer = view._layers[0]
    return QPointF(layer.x + (x0 + x1) / 2.0, layer.y + (y0 + y1) / 2.0)


# ---------- 选字 → 高亮 ----------

def test_highlighter_drag_over_text_creates_merged_rect_highlight(view, pdf):
    view.open(pdf)
    units = view._units(0)
    assert len(units) > 5
    view.set_tool("highlighter")
    view.set_color("#f1c40f")

    _drag(view, [view.mapFromScene(_unit_center(view, units[0])),
                 view.mapFromScene(_unit_center(view, units[-1]))])

    strokes = view.document.pages[0].strokes
    assert len(strokes) == 1
    stroke = strokes[0]
    assert stroke.tool == TEXT_HIGHLIGHT
    rects = text_highlight_rects(stroke)
    assert len(rects) == 1                  # 整行合并成一个矩形
    x0, y0, x1, y1 = rects[0]
    assert x1 > x0 and y1 > y0
    assert y0 < 60 < y1                    # 盖住插入文字的那一行
    assert view._select_page is None       # 提交后清掉临时选区
    assert view._selection_items == []

    # 场景项是填充矩形：NoPen + 0.35 alpha，而不是描边折线
    items = [item for item in view.scene().items() if item.data(0) is stroke]
    assert len(items) == 1
    assert items[0].pen().style() == Qt.NoPen
    assert items[0].brush().color().alpha() == round(255 * 0.35)

    view.history.pop_undo()
    assert view.document.pages[0].strokes == []


def test_highlighter_on_blank_area_falls_back_to_freehand(view, pdf):
    view.open(pdf)
    view.set_tool("highlighter")
    view.set_color("#f1c40f")
    layer = view._layers[0]
    _drag(view, [view.mapFromScene(QPointF(layer.x + 40, layer.y + 300)),
                 view.mapFromScene(QPointF(layer.x + 200, layer.y + 340))])

    strokes = view.document.pages[0].strokes
    assert len(strokes) == 1
    assert strokes[0].tool == "highlighter"
    assert strokes[0].width > 0
    assert len(strokes[0].points) >= 2


def test_v_selection_stays_blue_and_does_not_create_annotation(view, pdf):
    view.open(pdf)
    units = view._units(0)
    view.set_tool("select")
    _drag(view, [view.mapFromScene(_unit_center(view, units[0])),
                 view.mapFromScene(_unit_center(view, units[2]))])
    assert view.document.pages[0].strokes == []
    assert view.selected_text()
    assert view._selection_purpose == "copy"


# ---------- 命中距离 / 橡皮 ----------

def test_stroke_distance_treats_rect_highlight_as_area():
    stroke = Stroke([(10.0, 10.0), (50.0, 30.0)], "#f1c40f", 0.0, TEXT_HIGHLIGHT)
    assert stroke_distance(stroke, (30.0, 20.0)) == 0.0
    assert stroke_distance(stroke, (60.0, 20.0)) == pytest.approx(10.0)
    assert stroke_distance(stroke, (30.0, 40.0)) == pytest.approx(10.0)


def test_eraser_inside_rect_removes_text_highlight(view, pdf):
    view.open(pdf)
    rect_stroke = Stroke([(40.0, 80.0), (200.0, 110.0)], "#f1c40f", 0.0,
                         TEXT_HIGHLIGHT)
    view.document.pages[0].strokes.append(rect_stroke)
    view._rebind_page(0)
    view.set_tool("eraser")
    view._begin_erase(0, (100.0, 95.0))    # 点在矩形内部
    view._finish_erase()
    assert view.document.pages[0].strokes == []


# ---------- 存储 / 导出 ----------

def test_text_highlight_sidecar_round_trip(tmp_path):
    pdf = tmp_path / "rt.pdf"
    doc = pymupdf.open()
    doc.new_page(width=300.0, height=400.0)
    doc.save(str(pdf))
    doc.close()
    stroke = Stroke([(20.0, 30.0), (120.0, 50.0)], "#f1c40f", 0.0, TEXT_HIGHLIGHT)
    pdfstore.save_sidecar(pdf, PdfDocument([Page(300.0, 400.0, 0, [stroke])]))
    loaded = pdfstore.load_sidecar(pdf, 1)
    got = loaded.pages[0].strokes[0]
    assert got.tool == TEXT_HIGHLIGHT
    assert got.points == [(20.0, 30.0), (120.0, 50.0)]


def test_export_draws_rect_highlight_before_pen(tmp_path, pdf):
    highlight = Stroke([(30.0, 80.0), (200.0, 105.0)], "#f1c40f", 0.0,
                       TEXT_HIGHLIGHT)
    pen = Stroke([(30.0, 150.0), (200.0, 180.0)], "#e74c3c", 3.0)
    out = tmp_path / "out.pdf"
    export_with_strokes(PdfSource(pdf), [[highlight, pen]], out)

    doc = pymupdf.open(str(out))
    try:
        drawings = doc[0].get_drawings()
        assert len(drawings) == 2
        rect_draw, line_draw = drawings
        assert rect_draw.get("fill_opacity", 1.0) == pytest.approx(0.35, abs=0.01)
        assert line_draw.get("fill") is None          # 墨迹只描边，不填充
        assert line_draw.get("stroke_opacity", 1.0) == pytest.approx(1.0, abs=0.01)
    finally:
        doc.close()
