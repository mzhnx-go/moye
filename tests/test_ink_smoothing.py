"""抬笔平滑：去抖但不弓直线、不抹拐角，位移有上限；off 原样保留。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math

import pytest
from PySide6.QtWidgets import QApplication

from sketchpad.core.settings import Settings, load, save
from sketchpad.core.smoothing import smooth_points
from sketchpad.core.strokes import Document
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.pdfview import PdfView


def _noisy_line(count: int = 80, noise: float = 0.2):
    return [(float(i), noise if i % 2 else -noise) for i in range(count)]


def _dist_to_polyline(p, points) -> float:
    best = math.dist(p, points[0])
    for a, b in zip(points, points[1:]):
        vx, vy = b[0] - a[0], b[1] - a[1]
        seg2 = vx * vx + vy * vy
        if seg2 == 0.0:
            continue
        t = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / seg2))
        best = min(best, math.dist(p, (a[0] + t * vx, a[1] + t * vy)))
    return best


def test_off_and_unknown_levels_keep_points_verbatim():
    points = _noisy_line()
    assert smooth_points(points, "off") == points
    assert smooth_points(points, "blue") == points


def test_short_strokes_are_unchanged():
    for points in ([], [(1.0, 1.0)], [(1.0, 1.0), (2.0, 2.0)],
                   [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]):
        assert smooth_points(points, "low") == points


def test_endpoints_are_preserved():
    points = _noisy_line()
    out = smooth_points(points, "low")
    assert out[0] == points[0]
    assert out[-1] == points[-1]


def test_noisy_straight_line_is_flatter():
    points = _noisy_line(noise=0.2)
    points[0] = (points[0][0], 0.0)      # 端点保留原值，不能把端点噪声算进平滑效果
    points[-1] = (points[-1][0], 0.0)
    out = smooth_points(points, "low")
    before = math.sqrt(sum(p[1] ** 2 for p in points) / len(points))
    after = math.sqrt(sum(p[1] ** 2 for p in out) / len(out))
    assert after < before
    assert max(abs(p[1]) for p in out) <= 0.6


def test_sharp_corner_is_pinned_and_legs_stay_straight():
    # 干净的 90° 拐角：拐角顶点必须原样保留
    points = [(float(i), 0.0) for i in range(51)]
    points += [(50.0, float(i)) for i in range(1, 40)]
    out = smooth_points(points, "low")
    assert (50.0, 0.0) in out
    ideal = [(0.0, 0.0), (50.0, 0.0), (50.0, 39.0)]
    assert max(_dist_to_polyline(p, ideal) for p in out) <= 0.5


def test_noisy_corner_stays_within_shape_budget():
    # 横线带抖动 + 90° 拐角：拐角附近的点仍在 1.2px 内，整体不偏离原折线
    points = [(float(i), 0.2 if i % 2 else -0.2) for i in range(50)]
    points += [(50.0, float(i)) for i in range(1, 40)]
    out = smooth_points(points, "low")
    assert max(_dist_to_polyline(p, points) for p in out) <= 0.35 + 0.5 + 0.05
    corner = min(out, key=lambda p: abs(p[0] - 50.0) + abs(p[1]))
    assert abs(corner[0] - 50.0) <= 1.2 and abs(corner[1]) <= 1.2


def test_output_stays_within_budget_of_original_shape():
    """RDP 容差 + 单次位移上限共同限制：输出点离原折线不超过 eps+max_shift。"""
    rng = [(float(i), math.sin(i / 3.0) * 10 + (0.3 if i % 3 == 0 else -0.2))
           for i in range(120)]
    out = smooth_points(rng, "low")
    assert len(out) <= len(rng)
    assert max(_dist_to_polyline(p, rng) for p in out) <= 0.35 + 0.5 + 0.05


def test_medium_simplifies_at_least_as_much_as_low():
    points = _noisy_line(noise=0.3)
    assert len(smooth_points(points, "medium")) <= len(smooth_points(points, "low"))


# ---------- 设置 / 视图接线 ----------

def test_settings_round_trip_and_bad_value(tmp_path):
    path = tmp_path / "s.json"
    save(path, Settings(ink_smoothing="medium"))
    assert load(path).ink_smoothing == "medium"
    path.write_text('{"ink_smoothing": "max"}', encoding="utf-8")
    assert load(path).ink_smoothing == "low"


def test_views_read_smoothing_from_settings(qapp):
    settings = Settings(ink_smoothing="medium")
    canvas = SketchView(SketchScene(Document()), settings)
    try:
        assert canvas.ink_smoothing == "medium"
        canvas.set_ink_smoothing("off")
        assert canvas.ink_smoothing == "off"
        canvas.set_ink_smoothing("bogus")
        assert canvas.ink_smoothing == "low"
    finally:
        canvas.close()
    pdf = PdfView(settings)
    try:
        assert pdf.ink_smoothing == "medium"
        pdf.set_ink_smoothing("off")
        assert pdf.ink_smoothing == "off"
    finally:
        pdf.close()


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])
