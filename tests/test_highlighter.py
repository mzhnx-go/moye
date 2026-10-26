"""荧光笔（highlighter）渲染/导出离屏测试：画布 item、落笔、PDF 缓存、导出顺序与透明度、sidecar、橡皮撤销。

样例 PDF 全在 tmp_path 现造；不碰 data/。透明度统一取 `HIGHLIGHTER_OPACITY`（0.35 → alpha 89）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsPathItem

from sketchpad.core import pdfstore
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import HIGHLIGHTER_OPACITY, HIGHLIGHTER_WIDTH, Document, Stroke
from sketchpad.pdf.document import PdfSource, export_with_strokes
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.pdfview import STROKE_TAIL, PdfView

ALPHA = round(255 * HIGHLIGHTER_OPACITY)     # 89
_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def canvas_view(qapp):
    v = SketchView(SketchScene(Document()))
    v.resize(400, 300)
    v.show()
    QTest.qWaitForWindowExposed(v)
    _ALIVE.append(v)
    yield v
    v.close()


@pytest.fixture
def pdf_view(qapp):
    v = PdfView(Settings())
    v.resize(260, 180)
    v.show()
    QTest.qWaitForWindowExposed(v)
    _ALIVE.append(v)
    yield v
    v.close_document()
    v.close()


def _make_pdf(path, size=(200.0, 300.0)):
    doc = pymupdf.open()
    doc.new_page(width=size[0], height=size[1])
    doc.save(str(path))
    doc.close()
    return path


def _send_move(widget, x: float, y: float, buttons=Qt.NoButton) -> None:
    local = QPointF(x, y)
    global_pos = QPointF(widget.viewport().mapToGlobal(QPoint(int(x), int(y))))
    ev = QMouseEvent(QEvent.MouseMove, local, global_pos, Qt.NoButton, buttons,
                     Qt.NoModifier)
    QApplication.sendEvent(widget.viewport(), ev)


def _drag(widget, points: list[QPoint]) -> None:
    QTest.mousePress(widget.viewport(), Qt.LeftButton, pos=points[0])
    for pos in points[1:-1]:
        _send_move(widget, pos.x(), pos.y(), Qt.LeftButton)
    QTest.mouseRelease(widget.viewport(), Qt.LeftButton, pos=points[-1])


def _path_item_for(view, stroke: Stroke) -> QGraphicsPathItem:
    """取承载该笔迹的矢量项（画布把 scene 存成属性，PDF 视图是 scene() 方法）。"""
    scene = view.scene
    items = [i for i in (scene() if callable(scene) else scene).items()
             if isinstance(i, QGraphicsPathItem) and i.data(0) is stroke]
    assert len(items) == 1
    return items[0]


# ---- 画布 ----

def test_canvas_highlighter_uses_its_own_color_not_pen_color(canvas_view):
    """画笔选蓝、荧光笔选深黄：各落各的色，切换工具不互相覆盖。"""
    canvas_view.set_color("#2980b9")
    canvas_view.set_highlighter_color("#d4a017")

    canvas_view.set_tool("highlighter")
    _drag(canvas_view, [QPoint(50, 50), QPoint(150, 50)])
    canvas_view.set_tool("pen")
    _drag(canvas_view, [QPoint(50, 120), QPoint(150, 120)])

    hi, pen = canvas_view.document.strokes
    assert hi.color == "#d4a017"
    assert pen.color == "#2980b9"


def test_pdf_highlighter_uses_its_own_color_not_pen_color(pdf_view, tmp_path):
    pdf = _make_pdf(tmp_path / "hl-color.pdf")
    pdf_view.open(pdf)
    QTest.qWait(80)
    pdf_view.set_color("#2980b9")
    pdf_view.set_highlighter_color("#d4a017")

    pdf_view.set_tool("highlighter")
    _drag(pdf_view, [pdf_view.mapFromScene(QPointF(40.0, 60.0)),
                     pdf_view.mapFromScene(QPointF(150.0, 90.0))])
    pdf_view.set_tool("pen")
    _drag(pdf_view, [pdf_view.mapFromScene(QPointF(40.0, 160.0)),
                     pdf_view.mapFromScene(QPointF(150.0, 190.0))])

    hi, pen = pdf_view.document.pages[0].strokes
    assert hi.color == "#d4a017"
    assert pen.color == "#2980b9"


def test_canvas_highlighter_item_is_translucent_and_uses_its_width(qapp):
    hi = Stroke([(0.0, 0.0), (80.0, 0.0)], "#ffd400", HIGHLIGHTER_WIDTH, "highlighter")
    pen = Stroke([(0.0, 40.0), (80.0, 40.0)], "#ffffff", 3.0)
    scene = SketchScene(Document([hi, pen]))
    hi_item = next(i for i in scene.items() if i.data(0) is hi)
    pen_item = next(i for i in scene.items() if i.data(0) is pen)
    assert hi_item.pen().color().alpha() == ALPHA
    assert hi_item.pen().widthF() == pytest.approx(HIGHLIGHTER_WIDTH)
    assert pen_item.pen().color().alpha() == 255
    assert pen_item.pen().widthF() == pytest.approx(3.0)


def test_canvas_drag_with_highlighter_tool_records_tool_and_width(canvas_view):
    canvas_view.set_tool("highlighter")
    _drag(canvas_view, [QPoint(50, 50), QPoint(90, 50), QPoint(150, 70)])

    strokes = canvas_view.document.strokes
    assert len(strokes) == 1
    stroke = strokes[0]
    assert stroke.tool == "highlighter"
    assert stroke.width == pytest.approx(HIGHLIGHTER_WIDTH)
    item = _path_item_for(canvas_view, stroke)
    assert item.pen().color().alpha() == ALPHA


def test_canvas_live_preview_uses_highlighter_width_and_alpha(canvas_view, monkeypatch):
    """落笔未固化时的实时预览也必须是 16 宽、0.35 透明（否则抬笔瞬间会“变淡/变粗”）。"""
    import sketchpad.ui.canvas as canvas_module

    seen: list[tuple[float, float]] = []
    original = canvas_module._make_pen

    def spy(color, width, scale, opacity=1.0):
        seen.append((width, opacity))
        return original(color, width, scale, opacity)

    monkeypatch.setattr(canvas_module, "_make_pen", spy)
    canvas_view.set_tool("highlighter")
    QTest.mousePress(canvas_view.viewport(), Qt.LeftButton, pos=QPoint(100, 80))
    canvas_view.grab()                       # 强制走一次 drawForeground
    preview_calls = list(seen)               # 抬笔前取：只统计实时预览
    QTest.mouseRelease(canvas_view.viewport(), Qt.LeftButton, pos=QPoint(100, 80))

    assert any(width == pytest.approx(HIGHLIGHTER_WIDTH)
               and opacity == pytest.approx(HIGHLIGHTER_OPACITY)
               for width, opacity in preview_calls)
    assert canvas_view.dot_radius_px() == pytest.approx(6.0)


# ---- PDF ----

def test_pdf_drawing_with_highlighter_records_tool_and_translucent_item(pdf_view, tmp_path):
    pdf = _make_pdf(tmp_path / "hl.pdf")
    pdf_view.open(pdf)
    QTest.qWait(80)
    layer = pdf_view._layers[0]
    pdf_view.set_tool("highlighter")
    _drag(pdf_view, [pdf_view.mapFromScene(QPointF(40.0, 60.0)),
                     pdf_view.mapFromScene(QPointF(150.0, 90.0))])

    strokes = pdf_view.document.pages[0].strokes
    assert len(strokes) == 1
    stroke = strokes[0]
    assert stroke.tool == "highlighter"
    # 线宽按固定 900px 页宽基准换算，不再跟着当前窗口宽度变
    from sketchpad.ui.pdfview import PEN_WIDTH_REFERENCE_PX
    ref_zoom = PEN_WIDTH_REFERENCE_PX / layer.width
    assert stroke.width == pytest.approx(HIGHLIGHTER_WIDTH / ref_zoom, rel=1e-6)
    assert stroke.width * pdf_view.zoom == pytest.approx(
        HIGHLIGHTER_WIDTH * pdf_view.zoom / ref_zoom, abs=0.5)

    item = _path_item_for(pdf_view, stroke)
    assert item.pen().color().alpha() == ALPHA
    assert item.pen().widthF() == pytest.approx(stroke.width)
    assert item.zValue() < 2.0                  # 荧光笔在画笔层之下


def test_pdf_cache_keeps_highlighter_translucent_and_pen_opaque(pdf_view, tmp_path):
    total = STROKE_TAIL + 4                     # 20 条：前 4 条烘进缓存
    strokes = [
        Stroke([(20.0, 30.0), (80.0, 30.0)], "#0000ff", 3.0),                 # 0 pen
        Stroke([(120.0, 30.0), (180.0, 30.0)], "#ff0000", HIGHLIGHTER_WIDTH,
               "highlighter"),                                                # 1 hi
        Stroke([(20.0, 50.0), (80.0, 50.0)], "#0000ff", 3.0),                 # 2 pen
        Stroke([(120.0, 50.0), (180.0, 50.0)], "#ff0000", HIGHLIGHTER_WIDTH,
               "highlighter"),                                                # 3 hi
    ]
    strokes += [Stroke([(20.0 + i * 8.0, 90.0), (20.0 + i * 8.0, 110.0)],
                       "#00ff00", 2.0) for i in range(total - 4)]
    pdf = _make_pdf(tmp_path / "cached.pdf")
    pdfstore.save_sidecar(pdf, PdfDocument([Page(200.0, 300.0, 0, strokes)]))
    pdf_view.open(pdf)
    QTest.qWait(150)

    layer = pdf_view._layers[0]
    assert len(layer.cache_strokes) == 4
    assert len(layer.stroke_items) == STROKE_TAIL
    image = layer.cache_item.pixmap().toImage()
    zoom = layer.cache_zoom
    assert zoom and zoom > 0

    def alpha_at(x: float, y: float) -> int:
        return image.pixelColor(round(x * zoom), round(y * zoom)).alpha()

    pen_alpha = alpha_at(50.0, 30.0)
    hi_alpha = alpha_at(150.0, 30.0)
    assert pen_alpha == 255                     # 画笔：完全不透明
    assert hi_alpha == pytest.approx(ALPHA, abs=3)   # 荧光笔：0.35 透明
    # 另一行同样成立（不是只有某一条被画上）
    assert alpha_at(50.0, 50.0) == 255
    assert alpha_at(150.0, 50.0) == pytest.approx(ALPHA, abs=3)


# ---- 导出 ----

def test_export_draws_highlights_before_pen_with_opacity(tmp_path):
    pdf = _make_pdf(tmp_path / "src.pdf", size=(200.0, 200.0))
    pen = Stroke([(10.0, 120.0), (150.0, 120.0)], "#0000ff", 3.0)
    hi = Stroke([(10.0, 40.0), (150.0, 40.0)], "#ff0000", HIGHLIGHTER_WIDTH,
                "highlighter")
    out = tmp_path / "out.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [[pen, hi]], out)   # 模型顺序：画笔在前

    doc = pymupdf.open(str(out))
    drawings = doc[0].get_drawings()
    doc.close()
    assert len(drawings) == 2
    # 荧光笔先落墨（压在下面），即便模型里画笔排在前面
    assert drawings[0]["color"] == (1.0, 0.0, 0.0)
    assert drawings[0]["stroke_opacity"] == pytest.approx(HIGHLIGHTER_OPACITY, abs=1e-3)
    assert drawings[1]["color"] == (0.0, 0.0, 1.0)
    assert drawings[1]["stroke_opacity"] == pytest.approx(1.0, abs=1e-3)


# ---- sidecar / 橡皮 ----

def test_sidecar_round_trip_keeps_highlighter_tool(tmp_path):
    pdf = tmp_path / "round.pdf"
    hi = Stroke([(10.0, 20.0), (60.0, 20.0)], "#ffd400", HIGHLIGHTER_WIDTH,
                "highlighter")
    pdfstore.save_sidecar(pdf, PdfDocument([Page(200.0, 300.0, 0, [hi])]))
    loaded = pdfstore.load_sidecar(pdf, 1)
    assert loaded is not None
    strokes = loaded.pages[0].strokes
    assert len(strokes) == 1
    assert strokes[0].tool == "highlighter"
    assert strokes[0].width == pytest.approx(HIGHLIGHTER_WIDTH)


def test_eraser_and_undo_work_on_highlighter_stroke(canvas_view):
    canvas_view.set_tool("highlighter")
    _drag(canvas_view, [QPoint(50, 60), QPoint(150, 60)])
    assert len(canvas_view.document.strokes) == 1

    canvas_view.set_tool("eraser")
    QTest.mousePress(canvas_view.viewport(), Qt.LeftButton, pos=QPoint(100, 60))
    QTest.mouseRelease(canvas_view.viewport(), Qt.LeftButton, pos=QPoint(100, 60))
    assert canvas_view.document.strokes == []
    assert canvas_view.history.depth == 2       # 落笔 1 + 擦除 1

    canvas_view.history.pop_undo()               # 一次撤销把整条荧光笔拿回来
    assert len(canvas_view.document.strokes) == 1
    restored = canvas_view.document.strokes[0]
    assert restored.tool == "highlighter"
    assert restored.width == pytest.approx(HIGHLIGHTER_WIDTH)
    assert len(_path_items(canvas_view)) == 1


def _path_items(view) -> list:
    return [i for i in view.scene.items()
            if isinstance(i, QGraphicsPathItem) and i.data(0) is not None]
