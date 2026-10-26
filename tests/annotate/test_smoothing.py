"""抬笔平滑：off 档、直角拐角保护、输入不可变。"""
import math

from sketchpad.annotate.core.smoothing import smooth_points


def test_off档返回等值副本而非同一列表():
    points = [(0.0, 0.0), (3.0, 1.0), (7.0, 2.0), (10.0, 1.0)]
    out = smooth_points(points, "off")

    assert out == points
    assert out is not points
    # 改返回值不能反噬输入
    out[0] = (99.0, 99.0)
    assert points[0] == (0.0, 0.0)


def _right_angle():
    """沿 x 轴从 (0,0) 走到 (40,0)，再沿 y 轴走到 (40,40)。"""
    return [(float(x), 0.0) for x in range(0, 41)] + [
        (40.0, float(y)) for y in range(1, 41)
    ]


def test_low档直角拐角不被抹圆():
    points = _right_angle()
    out = smooth_points(points, "low")

    assert len(out) >= 3
    assert out[0] == points[0]
    assert out[-1] == points[-1]

    # 拐角 (40,0) 附近必须仍有保留下来的点；被抹圆时会顶到 (39.x, 0.x) 之外
    nearest = min(math.dist(p, (40.0, 0.0)) for p in out)
    assert nearest <= 0.5

    corner = min(out, key=lambda p: math.dist(p, (40.0, 0.0)))
    assert corner[0] >= 39.5
    assert corner[1] <= 0.5


def test_medium档同样守住直角():
    out = smooth_points(_right_angle(), "medium")
    nearest = min(math.dist(p, (40.0, 0.0)) for p in out)
    assert nearest <= 0.9


def test_输入列表不被原地修改():
    points = _right_angle()
    before = list(points)
    smooth_points(points, "low")
    smooth_points(points, "medium")
    assert points == before
