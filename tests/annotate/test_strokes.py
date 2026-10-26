"""笔迹模型与几何工具：thin / stroke_distance。"""
import math

import pytest

from sketchpad.annotate.core.strokes import (
    MAX_POINTS_PER_STROKE,
    MIN_POINT_DIST,
    Document,
    Stroke,
    stroke_distance,
    thin,
)


def test_thin_保留首末点并过滤过密点():
    points = [(0.0, 0.0), (0.1, 0.0), (10.0, 0.0)]
    out = thin(points)
    assert out[0] == (0.0, 0.0)
    assert out[-1] == (10.0, 0.0)
    # 0.1 像素的采样点远小于最小间距，必须被丢掉
    assert out == [(0.0, 0.0), (10.0, 0.0)]


def test_thin_末点即使过密也必须保留():
    points = [(0.0, 0.0), (10.0, 0.0), (10.1, 0.0)]
    out = thin(points)
    assert out[0] == (0.0, 0.0)
    assert out[-1] == (10.1, 0.0)


def test_thin_空与单点不炸():
    assert thin([]) == []
    assert thin([(3.0, 4.0)]) == [(3.0, 4.0)]


def test_thin_自定义最小间距():
    points = [(0.0, 0.0), (1.0, 0.0), (3.0, 0.0), (4.0, 0.0), (9.0, 0.0)]
    assert thin(points, min_dist=3.0) == [(0.0, 0.0), (3.0, 0.0), (9.0, 0.0)]


def _horizontal_stroke() -> Stroke:
    return Stroke(points=[(0.0, 0.0), (10.0, 0.0)], color="#ffffff", width=2.0)


def test_stroke_distance_线上点为0():
    assert stroke_distance(_horizontal_stroke(), (5.0, 0.0)) == pytest.approx(0.0)


def test_stroke_distance_远处点为垂距():
    assert stroke_distance(_horizontal_stroke(), (5.0, 3.0)) == pytest.approx(3.0)


def test_stroke_distance_超出端点后按端点算():
    assert stroke_distance(_horizontal_stroke(), (14.0, 0.0)) == pytest.approx(4.0)


def test_stroke_distance_单点笔迹退化为点距():
    stroke = Stroke(points=[(2.0, 3.0)], color="#ffffff", width=2.0)
    assert stroke_distance(stroke, (2.0, 7.0)) == pytest.approx(4.0)


def test_stroke_distance_空笔迹返回无穷():
    stroke = Stroke(points=[], color="#ffffff", width=2.0)
    assert math.isinf(stroke_distance(stroke, (1.0, 1.0)))


def test_stroke默认工具是钢笔():
    stroke = Stroke(points=[(0.0, 0.0)], color="#ffffff", width=2.0)
    assert stroke.tool == "pen"


def test_笔迹常量有效():
    assert MIN_POINT_DIST > 0
    assert MAX_POINTS_PER_STROKE >= 100


def test_document_默认空列表且互不共享():
    a = Document()
    b = Document()
    a.strokes.append(Stroke(points=[(0.0, 0.0)], color="#ffffff", width=2.0))
    assert len(a.strokes) == 1
    assert b.strokes == []
