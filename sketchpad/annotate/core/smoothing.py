"""抬笔后的保守笔迹平滑：去抖、简化、锁定拐角，结果仍是普通点列。

逻辑与 E:\\sketchpad 的 sketchpad/core/smoothing.py 一致，只把档位常量
搬到本模块（原模块从 sketchpad.core.settings 导入）。
"""
from __future__ import annotations

import math

Point = tuple[float, float]

INK_SMOOTHING_LEVELS = ("off", "low", "medium", "high", "ultra")

_RDP_EPS = {"low": 0.35, "medium": 0.7, "high": 1.0, "ultra": 1.5}
_PASSES = {"low": 1, "medium": 2, "high": 2, "ultra": 3}
_CORNER_DEG = {"low": 50.0, "medium": 60.0, "high": 70.0, "ultra": 80.0}
_MAX_SHIFT = {"low": 0.5, "medium": 0.9, "high": 1.2, "ultra": 1.6}
_DENSIFY_STEPS = {"high": 4, "ultra": 6}   # 高/超高：相邻拐角之间的曲线圆角细分数
_CORNER_MIN_LEN = 1.5
_RDP_MAX_POINTS = 300
_RDP_MIN_ARC = 32.0
_RDP_MIN_POINTS = 12


def _dedupe(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for p in points:
        if not out or p != out[-1]:
            out.append(p)
    return out


def _rdp(points: list[Point], eps: float) -> list[Point]:
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    eps2 = eps * eps
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        x0, y0 = points[start]
        x1, y1 = points[end]
        dx, dy = x1 - x0, y1 - y0
        seg2 = dx * dx + dy * dy
        best, best_d = -1, 0.0
        for i in range(start + 1, end):
            px, py = points[i]
            if seg2 <= 1e-18:
                d = (px - x0) ** 2 + (py - y0) ** 2
            else:
                t = ((px - x0) * dx + (py - y0) * dy) / seg2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                qx, qy = x0 + t * dx, y0 + t * dy
                d = (px - qx) ** 2 + (py - qy) ** 2
            if d > best_d:
                best, best_d = i, d
        if best >= 0 and best_d > eps2:
            keep[best] = True
            stack.append((start, best))
            stack.append((best, end))
    return [p for p, k in zip(points, keep) if k]


def _corner_indices(points: list[Point], angle_deg: float,
                    min_len: float = _CORNER_MIN_LEN) -> set[int]:
    n = len(points)
    if n < 3:
        return set()
    limit = math.cos(math.radians(angle_deg))
    corners: set[int] = set()
    for i in range(1, n - 1):
        x, y = points[i]
        j = i - 1
        while j > 0:
            dx, dy = x - points[j][0], y - points[j][1]
            if math.hypot(dx, dy) >= min_len:
                break
            j -= 1
        k = i + 1
        while k < n - 1:
            dx, dy = points[k][0] - x, points[k][1] - y
            if math.hypot(dx, dy) >= min_len:
                break
            k += 1
        ax, ay = x - points[j][0], y - points[j][1]
        bx, by = points[k][0] - x, points[k][1] - y
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la < 1e-9 or lb < 1e-9:
            continue
        dot = (ax * bx + ay * by) / (la * lb)
        dot = max(-1.0, min(1.0, dot))
        if dot < limit:
            corners.add(i)
    return corners


def _rdp_forced(points: list[Point], eps: float,
                forced: set[int]) -> tuple[list[Point], set[int]]:
    n = len(points)
    forced = sorted(i for i in forced if 0 < i < n - 1)
    if not forced:
        return _rdp(points, eps), set()
    out: list[Point] = []
    forced_out: set[int] = set()
    start = 0
    for f in forced:
        seg = _rdp(points[start:f + 1], eps)
        if out:
            seg = seg[1:]
        out.extend(seg)
        forced_out.add(len(out) - 1)
        start = f
    seg = _rdp(points[start:], eps)
    if out:
        seg = seg[1:]
    out.extend(seg)
    return out, forced_out


def _prefilter(points: list[Point], max_shift: float) -> list[Point]:
    n = len(points)
    out = list(points)
    for i in range(1, n - 1):
        ox, oy = points[i]
        tx = (points[i - 1][0] + 2.0 * ox + points[i + 1][0]) / 4.0
        ty = (points[i - 1][1] + 2.0 * oy + points[i + 1][1]) / 4.0
        dx, dy = tx - ox, ty - oy
        d = math.hypot(dx, dy)
        if d > max_shift:
            scale = max_shift / d
            tx, ty = ox + dx * scale, oy + dy * scale
        out[i] = (tx, ty)
    return out


def _smooth(points: list[Point], corners: set[int], passes: int,
            max_shift: float) -> list[Point]:
    n = len(points)
    pinned = set(corners)
    for c in corners:
        pinned.update((c - 1, c, c + 1))
    out = list(points)
    for _ in range(passes):
        src = out
        nxt = list(src)
        for i in range(1, n - 1):
            if i in pinned:
                continue
            ox, oy = src[i]
            tx = (src[i - 1][0] + 2.0 * ox + src[i + 1][0]) / 4.0
            ty = (src[i - 1][1] + 2.0 * oy + src[i + 1][1]) / 4.0
            dx, dy = tx - ox, ty - oy
            d = math.hypot(dx, dy)
            if d > max_shift:
                scale = max_shift / d
                tx, ty = ox + dx * scale, oy + dy * scale
            nxt[i] = (tx, ty)
        out = nxt
    return out


def smooth_points(points: list[Point], level: str = "low") -> list[Point]:
    """按强度处理一条抬笔后的笔迹；"off"/未知/太短的笔迹原样返回副本。

    低/中只挪点、直线段仍直连（保持原行为）；高/超高在抬笔后追加「顶点圆角」，
    把相邻直线段的棱角磨成二次贝塞尔弧，消除放大/快写时的颗粒感，拐角点原样保留。
    """
    if level not in INK_SMOOTHING_LEVELS or level == "off":
        return list(points)
    src = _dedupe(points)
    if len(src) < 4:
        return src
    raw_corners = _corner_indices(src, _CORNER_DEG[level], min_len=3.0)
    arc = sum(math.dist(a, b) for a, b in zip(src, src[1:]))
    if (len(src) > _RDP_MAX_POINTS or len(src) < _RDP_MIN_POINTS
            or arc < _RDP_MIN_ARC):
        simple = _prefilter(src, _MAX_SHIFT[level])
        for i in raw_corners:
            simple[i] = src[i]
        forced = raw_corners
    else:
        simple, forced = _rdp_forced(src, _RDP_EPS[level], raw_corners)
    if len(simple) < 4:
        return simple
    corners = _corner_indices(simple, _CORNER_DEG[level]) | forced
    out = _smooth(simple, corners, _PASSES[level], _MAX_SHIFT[level])
    if level in _DENSIFY_STEPS:
        # 高/超高：在相邻拐角之间做顶点圆角，拐角点（横折/钩）保持尖锐。
        out = _densify_bezier(out, corners, _DENSIFY_STEPS[level])
    return out


def _round_segment(seg: list[Point], steps: int) -> list[Point]:
    """把一段折线（首末为拐角、原样保留）的内部顶点用「过相邻中点的二次贝塞尔」圆角。

    直线段因三点共线自然退化、不变形；只有真实弯折处被磨圆，消除直连折线的颗粒感。
    steps 为每段内贝塞尔细分次数（中间采样点数 = steps-1）。
    """
    m = len(seg)
    if m < 3:
        return list(seg)
    out: list[Point] = [seg[0]]
    for i in range(1, m - 1):
        p0, p1, p2 = seg[i - 1], seg[i], seg[i + 1]
        c1 = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
        c2 = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
        for k in range(1, steps):
            t = k / steps
            mt = 1.0 - t
            a = mt * mt
            b = 2.0 * mt * t
            c = t * t
            out.append((a * c1[0] + b * p1[0] + c * c2[0],
                        a * c1[1] + b * p1[1] + c * c2[1]))
        out.append(c2)
    out.append(seg[m - 1])
    return out


def _densify_bezier(points: list[Point], corners: set[int], steps: int) -> list[Point]:
    """按拐角点把路径分段，逐段做顶点圆角；拐角点作为段端点原样保留（横折仍锐利）。"""
    n = len(points)
    if n < 3 or steps < 2:
        return list(points)
    steps = max(2, int(steps))
    idxs = sorted(i for i in corners if 0 < i < n - 1)
    bounds = [0] + idxs + [n - 1]
    out: list[Point] = []
    for s in range(len(bounds) - 1):
        a, b = bounds[s], bounds[s + 1]
        rounded = _round_segment(points[a:b + 1], steps)
        if out:
            rounded = rounded[1:]          # 去掉与上一段共享的拐角点
        out.extend(rounded)
    return out
