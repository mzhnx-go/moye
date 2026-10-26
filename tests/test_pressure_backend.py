"""数位板压感后端：宽度映射、ribbon 几何、同步抽稀/平滑、sidecar、导出、InkItem。

纯数据 + offscreen Qt，不开真实窗口；PDF 全部在 tmp_path 现造。
"""
import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsPathItem, QStyleOptionGraphicsItem

from sketchpad.core.notes import Note
from sketchpad.core.pdfstore import (Page, PdfDocument, PdfFormatError, from_json,
                                     to_json)
from sketchpad.core.strokes import (HIGHLIGHTER_OPACITY, PRESSURE_MAX_RATIO,
                                    PRESSURE_MIN_RATIO, Stroke, has_pressure,
                                    pressure_widths, ribbon_polygon,
                                    smooth_with_pressures, thin_with_pressures)
from sketchpad.pdf.document import (PdfSource, _scale_strokes, export_with_notes,
                                    export_with_strokes)
from sketchpad.ui.inkitem import InkItem, draw_ink_stroke, stroke_path


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _rgb(color: str) -> tuple[float, float, float]:
    text = color.lstrip("#")
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# ---------- 1. 压力 → 线宽 ----------


def test_pressure_ratios_are_the_documented_values():
    assert PRESSURE_MIN_RATIO == 0.35
    assert PRESSURE_MAX_RATIO == 1.0


def test_pressure_widths_zero_half_one_scale_base_width():
    base = 10.0
    stroke = Stroke([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)], "#000000", base,
                    pressures=[0.0, 0.5, 1.0])
    assert pressure_widths(stroke) == pytest.approx([3.5, 6.75, 10.0])


def test_pressure_widths_clamp_out_of_range_values():
    stroke = Stroke([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)], "#000000", 4.0,
                    pressures=[-3.0, 1.5, 0.25])
    assert pressure_widths(stroke) == pytest.approx(
        [4.0 * 0.35, 4.0 * 1.0, 4.0 * (0.35 + 0.65 * 0.25)])


def test_has_pressure_rejects_missing_short_and_all_zero():
    points = [(0.0, 0.0), (1.0, 0.0)]
    assert has_pressure(Stroke(points, "#000000", 2.0)) is False
    assert has_pressure(Stroke(points, "#000000", 2.0, pressures=None)) is False
    assert has_pressure(Stroke(points, "#000000", 2.0, pressures=[])) is False
    # 长度与点不符
    assert has_pressure(Stroke(points, "#000000", 2.0, pressures=[0.5])) is False
    # 全 0：等于没按，按恒定宽度画
    assert has_pressure(Stroke(points, "#000000", 2.0, pressures=[0.0, 0.0])) is False
    # 含非法值也不当压感
    assert has_pressure(Stroke(points, "#000000", 2.0, pressures=[0.5, None])) is False
    assert has_pressure(Stroke(points, "#000000", 2.0, pressures=[0.5, "x"])) is False
    # 正常
    assert has_pressure(Stroke(points, "#000000", 2.0, pressures=[0.0, 0.4])) is True


def test_pressure_widths_empty_without_pressure():
    assert pressure_widths(Stroke([(0.0, 0.0), (1.0, 0.0)], "#000000", 2.0)) == []


# ---------- 2. ribbon 多边形 ----------


def _dist(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def test_ribbon_constant_width_is_parallel_offset_on_both_sides():
    points = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    widths = [4.0, 4.0, 4.0]
    polygon = ribbon_polygon(points, widths)
    assert polygon is not None
    assert len(polygon) == 6
    left = polygon[:3]
    right = list(reversed(polygon[3:]))
    for index, (x, y) in enumerate(points):
        # 左右两顶点都在过该点的法线上，距离 = 半宽
        assert _dist(left[index], (x, y)) == pytest.approx(2.0)
        assert _dist(right[index], (x, y)) == pytest.approx(2.0)
        # 左右对称
        assert left[index][0] == pytest.approx(right[index][0])
        assert left[index][1] == pytest.approx(-right[index][1])


def test_ribbon_endpoint_widths_follow_per_point_width():
    points = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    widths = [2.0, 4.0, 6.0]
    polygon = ribbon_polygon(points, widths)
    assert polygon is not None
    left = polygon[:3]
    right = list(reversed(polygon[3:]))
    assert _dist(left[0], points[0]) == pytest.approx(1.0)
    assert _dist(left[1], points[1]) == pytest.approx(2.0)
    assert _dist(left[2], points[2]) == pytest.approx(3.0)
    assert _dist(right[2], points[2]) == pytest.approx(3.0)


def test_ribbon_corner_uses_averaged_normal():
    # 90° 拐角：法线取两条单位法线的平均方向（归一化），半宽不变
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    polygon = ribbon_polygon(points, [4.0, 4.0, 4.0])
    assert polygon is not None
    left = polygon[:3]
    assert _dist(left[1], points[1]) == pytest.approx(2.0)
    # 两段法线 (0,1) 与 (-1,0) 的平均方向是 45°：拐角顶点沿该方向偏半宽
    assert left[1][0] == pytest.approx(10.0 - 2.0 / math.sqrt(2.0))
    assert left[1][1] == pytest.approx(2.0 / math.sqrt(2.0))


def test_ribbon_skips_zero_length_segments():
    points = [(0.0, 0.0), (0.0, 0.0), (10.0, 0.0)]
    polygon = ribbon_polygon(points, [4.0, 4.0, 4.0])
    assert polygon is not None
    assert len(polygon) == 6
    for index, (x, y) in enumerate(points):
        assert polygon[index][1] == pytest.approx(y + 2.0)


def test_ribbon_returns_none_for_single_point_or_length_mismatch():
    assert ribbon_polygon([(0.0, 0.0)], [4.0]) is None
    assert ribbon_polygon([(0.0, 0.0), (1.0, 0.0)], [4.0]) is None
    assert ribbon_polygon([], []) is None


# ---------- 3. 同步抽稀 / 点数不变的平滑 ----------


def test_thin_with_pressures_drops_points_and_pressures_together():
    points = [(0.0, 0.0), (1.49, 0.0), (3.0, 0.0), (3.4, 0.0)]
    pressures = [0.1, 0.2, 0.3, 0.4]
    kept_points, kept_pressures = thin_with_pressures(points, pressures)
    assert kept_points == [(0.0, 0.0), (3.0, 0.0), (3.4, 0.0)]
    assert kept_pressures == [0.1, 0.3, 0.4]


def test_thin_with_pressures_without_pressure_delegates_to_thin():
    points = [(0.0, 0.0), (1.0, 0.0), (9.0, 0.0)]
    assert thin_with_pressures(points, None) == ([(0.0, 0.0), (9.0, 0.0)], None)


def test_thin_with_pressures_rejects_length_mismatch():
    with pytest.raises(ValueError):
        thin_with_pressures([(0.0, 0.0), (1.0, 0.0)], [0.5])


def _noisy_line(count: int = 80, noise: float = 0.2):
    return [(float(i), noise if i % 2 else -noise) for i in range(count)]


def test_smooth_with_pressures_keeps_point_count_and_pressures():
    points = _noisy_line()
    pressures = [i / (len(points) - 1) for i in range(len(points))]
    out_points, out_pressures = smooth_with_pressures(points, pressures, "low")
    assert len(out_points) == len(points)
    assert out_pressures == pressures
    # 去抖有效：噪点变小
    before = math.sqrt(sum(p[1] ** 2 for p in points) / len(points))
    after = math.sqrt(sum(p[1] ** 2 for p in out_points) / len(out_points))
    assert after < before


def test_smooth_with_pressures_pins_sharp_corner():
    points = [(float(i), 0.0) for i in range(51)]
    points += [(50.0, float(i)) for i in range(1, 40)]
    pressures = [0.5] * len(points)
    out, out_pressures = smooth_with_pressures(points, pressures, "low")
    assert len(out) == len(points)
    assert (50.0, 0.0) in out          # 拐角顶点原样保留
    assert out[0] == points[0] and out[-1] == points[-1]
    assert out_pressures == pressures


def test_smooth_with_pressures_off_returns_copies():
    points = _noisy_line(10)
    pressures = [0.3] * 10
    out, out_pressures = smooth_with_pressures(points, pressures, "off")
    assert out == points and out is not points
    assert out_pressures == pressures and out_pressures is not pressures


def test_smooth_with_pressures_without_pressure_matches_smooth_points():
    from sketchpad.core.smoothing import smooth_points
    points = _noisy_line()
    out, out_pressures = smooth_with_pressures(points, None, "low")
    assert out_pressures is None
    assert out == smooth_points(points, "low")


# ---------- 4. sidecar ----------


def _pressure_doc() -> PdfDocument:
    return PdfDocument([
        Page(595.28, 841.89, 0, [
            Stroke([(10.0, 20.5), (30.25, 40.0), (50.0, 60.0)], "#1a1a1a", 3.0,
                   pressures=[0.0, 0.123, 1.0]),
            Stroke([(1.0, 2.0), (3.0, 4.0)], "#f1c40f", 3.0, "pen"),
        ]),
        Page(200.0, 300.0),
    ])


def test_pressure_round_trips_at_three_decimals():
    doc = _pressure_doc()
    loaded = from_json(to_json(doc))
    assert loaded == doc
    assert loaded.pages[0].strokes[0].pressures == [0.0, 0.123, 1.0]


def test_pressure_field_is_pr_and_rounded_to_three():
    text = to_json(_pressure_doc())
    assert '"pr":[0.0,0.123,1.0]' in text


def test_no_pressure_stroke_writes_no_pr_field():
    text = to_json(PdfDocument([Page(
        100.0, 200.0, 0, [Stroke([(1.0, 2.0)], "#000000", 3.0)])]))
    assert '"pr"' not in text
    # 逐字节等于旧格式
    assert text == ('{"version":1,"pages":[{"w":100.0,"h":200.0,"r":0,"strokes":'
                    '[{"p":[[1.0,2.0]],"c":"#000000","w":3.0,"t":"pen"}]}]}')


def test_old_sidecar_without_pr_reads_none():
    text = ('{"version":1,"pages":[{"w":100.0,"h":200.0,"r":0,"strokes":'
            '[{"p":[[1.0,2.0]],"c":"#000000","w":3.0,"t":"pen"}]}]}')
    stroke = from_json(text).pages[0].strokes[0]
    assert stroke.pressures is None
    assert has_pressure(stroke) is False


@pytest.mark.parametrize("bad", [
    pytest.param([0.5], id="too-short"),
    pytest.param([0.5, 0.5, 0.5], id="too-long"),
    pytest.param([0.5, "x"], id="not-a-number"),
    pytest.param([0.5, True], id="boolean"),
    pytest.param([1.5, 0.5], id="above-range"),
    pytest.param([0.5, -0.1], id="below-range"),
    pytest.param("0.5", id="not-a-list"),
])
def test_bad_pressure_raises_format_error_naming_pressure(bad):
    import json
    text = json.dumps({"version": 1, "pages": [{
        "w": 100.0, "h": 200.0, "r": 0, "strokes": [
            {"p": [[1.0, 2.0], [3.0, 4.0]], "c": "#000000", "w": 3.0,
             "t": "pen", "pr": bad}]}]})
    with pytest.raises(PdfFormatError) as excinfo:
        from_json(text)
    message = str(excinfo.value)
    assert "pressure" in message
    assert "page #0 stroke #0" in message


def test_pressure_list_with_nan_is_rejected():
    # JSON 允许 NaN；非有限值必须拒绝，不能画成 NaN 几何
    text = ('{"version":1,"pages":[{"w":100.0,"h":200.0,"r":0,"strokes":'
            '[{"p":[[1.0,2.0],[3.0,4.0]],"c":"#000000","w":3.0,'
            '"pr":[0.5,NaN]}]}]}')
    with pytest.raises(PdfFormatError):
        from_json(text)


# ---------- 5. PDF 导出 ----------


def _make_pdf(path, pages=((300, 300, 0), (300, 300, 0))):
    doc = pymupdf.open()
    for width, height, rotation in pages:
        page = doc.new_page(width=width, height=height)
        if rotation:
            page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


def test_export_pressure_strokes_become_filled_polygons(tmp_path):
    pdf = _make_pdf(tmp_path / "src.pdf")
    pen = Stroke([(20.0, 30.0), (120.0, 30.0), (120.0, 80.0)], "#0000ff", 10.0,
                 pressures=[0.0, 0.5, 1.0])
    highlighter = Stroke([(20.0, 120.0), (200.0, 120.0)], "#ffcc00", 16.0,
                         "highlighter", pressures=[1.0, 1.0])
    out = tmp_path / "out.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [[pen, highlighter], []], out)

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 2
        drawings = doc[0].get_drawings()
        assert doc[1].get_drawings() == []
    finally:
        doc.close()
    # 压感笔迹不再有描边，全是 fill（ribbon + 两个端点半宽圆）
    assert drawings
    assert all("s" not in d["type"] for d in drawings)
    pen_fills = [d for d in drawings if d.get("fill") == pytest.approx(_rgb("#0000ff"))]
    hl_fills = [d for d in drawings if d.get("fill") == pytest.approx(_rgb("#ffcc00"))]
    assert len(pen_fills) == 3
    assert len(hl_fills) == 3
    assert all(d["fill_opacity"] == pytest.approx(1.0) for d in pen_fills)
    assert all(d["fill_opacity"] == pytest.approx(HIGHLIGHTER_OPACITY, abs=1e-6)
               for d in hl_fills)
    # 变宽的 ribbon：起点压力 0 → 半宽 1.75，终点压力 1 → 半宽 5
    polygon = max(pen_fills, key=lambda d: d["rect"].width * d["rect"].height)
    assert polygon["rect"].x0 == pytest.approx(20.0, abs=1e-3)      # 平头起始
    assert polygon["rect"].x1 == pytest.approx(125.0, abs=1e-3)     # 终点半宽 5
    assert polygon["rect"].y0 == pytest.approx(
        30.0 - (10.0 * 0.675 / 2.0) / math.sqrt(2.0), abs=1e-3)     # 中间点 45° 拐角
    assert polygon["rect"].y1 == pytest.approx(80.0, abs=1e-3)


def test_export_single_point_pressure_is_a_circle(tmp_path):
    pdf = _make_pdf(tmp_path / "dot.pdf", pages=((200, 200, 0),))
    stroke = Stroke([(50.0, 60.0)], "#00ff00", 10.0, pressures=[1.0])
    out = tmp_path / "dot-out.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [[stroke]], out)
    doc = pymupdf.open(str(out))
    try:
        drawings = doc[0].get_drawings()
    finally:
        doc.close()
    assert len(drawings) == 1
    assert "f" in drawings[0]["type"]
    assert drawings[0]["fill"] == pytest.approx(_rgb("#00ff00"))
    assert (drawings[0]["rect"].width, drawings[0]["rect"].height) == pytest.approx(
        (10.0, 10.0), abs=1e-3)


def test_export_without_pressure_stays_a_stroke_not_a_fill(tmp_path):
    pdf = _make_pdf(tmp_path / "plain.pdf", pages=((200, 200, 0),))
    stroke = Stroke([(20.0, 30.0), (120.0, 80.0)], "#0000ff", 4.0)
    out = tmp_path / "plain-out.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [[stroke]], out)
    doc = pymupdf.open(str(out))
    try:
        drawings = doc[0].get_drawings()
    finally:
        doc.close()
    assert len(drawings) == 1
    assert drawings[0]["type"] == "s"
    assert drawings[0]["width"] == 4.0


def test_scale_strokes_keeps_pressures_and_scales_geometry():
    stroke = Stroke([(10.0, 10.0), (100.0, 100.0)], "#2c2c2e", 2.0,
                    pressures=[0.0, 1.0])
    scaled = _scale_strokes([stroke], 0.5)
    assert len(scaled) == 1
    out = scaled[0]
    assert out.pressures == [0.0, 1.0]
    assert out.points == [(5.0, 5.0), (50.0, 50.0)]
    assert out.width == pytest.approx(1.0)
    assert out.color == stroke.color and out.tool == stroke.tool


def test_note_page_export_draws_pressure_as_polygon(tmp_path):
    src = _make_pdf(tmp_path / "note.pdf", pages=((300, 400, 0),))
    pen = Stroke([(10.0, 10.0), (100.0, 100.0)], "#2c2c2e", 2.0,
                 pressures=[0.0, 1.0])
    note = Note("n-1", 40.0, 50.0, 560.0, 720.0, "dark", [pen])
    out = tmp_path / "note-out.pdf"
    export_with_notes(PdfSource(src), [[]], [[note]], out)

    doc = pymupdf.open(str(out))
    try:
        note_drawings = doc[1].get_drawings()
    finally:
        doc.close()
    pen_fills = [d for d in note_drawings
                 if d.get("fill") == pytest.approx(_rgb("#2c2c2e"))]
    assert pen_fills
    # 没有退化成描边折线
    assert not [d for d in note_drawings
                if "s" in d["type"] and d.get("color") == pytest.approx(_rgb("#2c2c2e"))]
    # 点与线宽都被 scale 放大：包围盒至少覆盖 90*scale 的对角线
    scale = 300.0 / 560.0
    rect = max(pen_fills, key=lambda d: d["rect"].width * d["rect"].height)["rect"]
    assert rect.width >= 90.0 * scale - 1.0


# ---------- 6. InkItem / draw_ink_stroke ----------


def _paint_to_image(item: InkItem, size=(120, 100)) -> QImage:
    image = QImage(size[0], size[1], QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    try:
        item.paint(painter, QStyleOptionGraphicsItem())
    finally:
        painter.end()
    return image


def test_ink_item_is_still_a_path_item_and_keeps_the_stroke(qapp):
    stroke = Stroke([(10.0, 50.0), (90.0, 50.0)], "#ff0000", 8.0,
                    pressures=[0.2, 1.0])
    item = InkItem(stroke, 1.0)
    assert isinstance(item, QGraphicsPathItem)
    assert item.data(0) is stroke
    assert has_pressure(item.data(0)) is True
    # pen 仍带恒定宽度的元数据（boundingRect 依赖它）
    assert item.pen().widthF() == pytest.approx(8.0)


def test_ink_item_bounding_rect_covers_centerline(qapp):
    stroke = Stroke([(10.0, 50.0), (90.0, 50.0)], "#ff0000", 8.0,
                    pressures=[0.2, 1.0])
    item = InkItem(stroke, 1.0)
    rect = item.boundingRect()
    assert rect.left() <= 10.0 and rect.right() >= 90.0
    assert rect.top() <= 50.0 <= rect.bottom()
    assert rect.contains(QPointF(50.0, 50.0))


def test_ink_item_paints_pressure_ink_on_the_centerline(qapp):
    stroke = Stroke([(10.0, 50.0), (90.0, 50.0)], "#ff0000", 8.0,
                    pressures=[1.0, 1.0])
    image = _paint_to_image(InkItem(stroke, 1.0))
    center = image.pixelColor(50, 50)
    assert center.red() > 200
    assert center.green() < 40 and center.blue() < 40
    # 线外远处仍是透明（没有整块填满）
    assert image.pixelColor(50, 20).alpha() == 0


def test_ink_item_highlighter_uses_35_percent_alpha(qapp):
    stroke = Stroke([(10.0, 50.0), (90.0, 50.0)], "#ffcc00", 8.0,
                    "highlighter", pressures=[1.0, 1.0])
    image = _paint_to_image(InkItem(stroke, 1.0))
    center = image.pixelColor(50, 50)
    assert 70 <= center.alpha() <= 110      # 0.35*255 ≈ 89（抗锯齿 ±容差）
    assert center.red() > 200


def test_ink_item_without_pressure_strokes_through_super(qapp):
    stroke = Stroke([(10.0, 50.0), (90.0, 50.0)], "#ff0000", 8.0)
    image = _paint_to_image(InkItem(stroke, 1.0))
    center = image.pixelColor(50, 50)
    assert center.red() > 200
    assert image.pixelColor(50, 20).alpha() == 0


def test_draw_ink_stroke_single_point_is_a_dot(qapp):
    stroke = Stroke([(50.0, 50.0)], "#0000ff", 12.0, pressures=[1.0])
    image = QImage(100, 100, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        draw_ink_stroke(painter, stroke)
    finally:
        painter.end()
    center = image.pixelColor(50, 50)
    assert center.blue() > 200
    assert image.pixelColor(50, 80).alpha() == 0


def test_draw_ink_stroke_empty_stroke_draws_nothing(qapp):
    image = QImage(20, 20, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        draw_ink_stroke(painter, Stroke([], "#000000", 3.0))
    finally:
        painter.end()
    assert image.pixelColor(10, 10).alpha() == 0


def test_stroke_path_single_point_is_not_empty(qapp):
    path = stroke_path([(5.0, 5.0)])
    assert not path.isEmpty()
    assert stroke_path([]).isEmpty()
