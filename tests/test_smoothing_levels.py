"""平滑档位回归：低/中保持原行为，高/超高在抬笔后做曲线圆角消除颗粒感。

只针对无压感路径（smooth_points）验证；压感路径 smooth_with_pressures 不在本次范围。
"""
from __future__ import annotations

import math

from sketchpad.core.settings import INK_SMOOTHING_LEVELS, _ink_smoothing
from sketchpad.core.smoothing import smooth_points as smooth_core
from sketchpad.annotate.core.smoothing import smooth_points as smooth_anno


# 一条含直线段、平缓斜弯、90° 横折的折线
_SAMPLE = [
    (0, 0), (10, 0), (20, 0), (30, 0), (40, 0),
    (50, 10), (60, 20), (70, 30), (80, 40), (90, 50),
    (90, 60), (90, 70), (90, 80), (90, 90),
    (80, 90), (70, 90), (60, 90), (50, 90),
]
_CORNER = (90, 90)   # 真正的 90° 横折处，应被所有档位保留


def _total_turn(pts):
    """所有内点的相邻段夹角绝对值之和（弧度），衡量折线棱角总量。"""
    total = 0.0
    for i in range(1, len(pts) - 1):
        ax = pts[i][0] - pts[i - 1][0]
        ay = pts[i][1] - pts[i - 1][1]
        bx = pts[i + 1][0] - pts[i][0]
        by = pts[i + 1][1] - pts[i][1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la < 1e-9 or lb < 1e-9:
            continue
        dot = (ax * bx + ay * by) / (la * lb)
        dot = max(-1.0, min(1.0, dot))
        total += math.acos(dot)
    return total


def _has_point(pts, xy, tol=1.0):
    return any(abs(p[0] - xy[0]) <= tol and abs(p[1] - xy[1]) <= tol for p in pts)


def test_levels_include_high_ultra():
    assert "high" in INK_SMOOTHING_LEVELS
    assert "ultra" in INK_SMOOTHING_LEVELS
    assert _ink_smoothing({"ink_smoothing": "high"}) == "high"
    assert _ink_smoothing({"ink_smoothing": "ultra"}) == "ultra"
    assert _ink_smoothing({"ink_smoothing": "bogus"}) == "low"


def test_low_is_deterministic():
    assert smooth_core(_SAMPLE, "low") == smooth_core(_SAMPLE, "low")


def test_high_ultra_are_densified():
    # 低/中走原分支不做曲线加密；高/超高应在末尾追加圆角细分点。
    low = smooth_core(_SAMPLE, "low")
    high = smooth_core(_SAMPLE, "high")
    ultra = smooth_core(_SAMPLE, "ultra")
    assert len(high) > len(low)
    assert len(ultra) >= len(high)


def test_high_ultra_smooth_out_grain():
    low = smooth_core(_SAMPLE, "low")
    high = smooth_core(_SAMPLE, "high")
    ultra = smooth_core(_SAMPLE, "ultra")
    # 圆角把棱角磨平，总转角不应比低档更大。
    assert _total_turn(high) <= _total_turn(low) + 1e-6
    assert _total_turn(ultra) <= _total_turn(high) + 1e-6


def test_corner_preserved_across_levels():
    # 横折 (90,90) 在所有档位都保留，不被圆角抹成圆角。
    for lvl in ("low", "medium", "high", "ultra"):
        out = smooth_core(_SAMPLE, lvl)
        assert _has_point(out, _CORNER), f"{lvl} 丢失了 90° 拐角"


def test_annotate_copy_supports_high_ultra():
    low = smooth_anno(_SAMPLE, "low")
    high = smooth_anno(_SAMPLE, "high")
    ultra = smooth_anno(_SAMPLE, "ultra")
    assert len(high) > len(low)
    assert len(ultra) >= len(high)
    assert _has_point(high, _CORNER)
    assert _has_point(ultra, _CORNER)


def test_pressure_path_high_ultra_densified():
    """压感路径：高/超高也做圆角加密，且压力序列必须与点一一对应。"""
    from sketchpad.core.strokes import smooth_with_pressures
    pressures = [0.1 + 0.8 * (i / (len(_SAMPLE) - 1)) for i in range(len(_SAMPLE))]
    low_pts, low_prs = smooth_with_pressures(_SAMPLE, pressures, "low")
    high_pts, high_prs = smooth_with_pressures(_SAMPLE, pressures, "high")
    ultra_pts, ultra_prs = smooth_with_pressures(_SAMPLE, pressures, "ultra")
    assert len(low_pts) == len(_SAMPLE)          # 低档：点数不变、压力逐点对齐
    assert len(low_prs) == len(low_pts)
    assert len(high_pts) > len(low_pts)          # 高/超高：加密
    assert len(high_prs) == len(high_pts)        # 压力不能错位，否则渲染会错
    assert len(ultra_prs) == len(ultra_pts)
    assert len(ultra_pts) >= len(high_pts)
    assert all(0.0 <= p <= 1.0 for p in high_prs)


def test_pressure_smoothing_reduces_jumps():
    """压力跳变（实测 0.04↔0.99）要被抹平，否则线宽忽粗忽细、没有笔锋。"""
    from sketchpad.core.strokes import _smooth_pressure
    jitter = [0.05, 0.99, 0.04, 0.98, 0.05, 0.99]

    def max_step(seq):
        return max(abs(seq[i + 1] - seq[i]) for i in range(len(seq) - 1))

    smoothed = _smooth_pressure(jitter, passes=2)
    assert len(smoothed) == len(jitter)
    assert max_step(smoothed) < max_step(jitter)


def test_pressure_path_low_medium_unchanged():
    """低/中不加密、也不改压力序列（保持原行为）。"""
    from sketchpad.core.strokes import smooth_with_pressures
    pressures = [0.5] * len(_SAMPLE)
    out, prs = smooth_with_pressures(_SAMPLE, pressures, "medium")
    assert len(out) == len(_SAMPLE)
    assert len(prs) == len(out)
    assert all(abs(p - 0.5) < 1e-9 for p in prs)


def test_canvas_pdfview_import_with_new_levels():
    # 覆盖 canvas/pdfview 里 import 改名拼写风险；导入成功即说明 INK_SMOOTHING_LEVELS 可用。
    import sketchpad.ui.canvas as canvas_mod
    import sketchpad.ui.pdfview as pdfview_mod
    assert "high" in INK_SMOOTHING_LEVELS
    # 防"漏 import"复发：set_ink_smoothing 引用的是模块全局名，光 import 成功不会报错，
    # 只有真正调用到才 NameError —— 必须显式断言名字在模块命名空间里。
    assert hasattr(canvas_mod, "INK_SMOOTHING_LEVELS")
    assert hasattr(pdfview_mod, "INK_SMOOTHING_LEVELS")
