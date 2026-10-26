"""笔迹数据模型：屏幕坐标点列 + 颜色 + 线宽。本模块不依赖 Qt。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MIN_POINT_DIST = 1.5
MAX_POINTS_PER_STROKE = 4000

Point = tuple[float, float]


@dataclass
class Stroke:
    points: list[Point]
    color: str
    width: float
    tool: str = "pen"


@dataclass
class Document:
    strokes: list[Stroke] = field(default_factory=list)


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def thin(points: list[Point], min_dist: float = MIN_POINT_DIST) -> list[Point]:
    """丢掉离上一个保留点过密的点；首点与末点始终保留。"""
    if not points:
        return []
    kept = [points[0]]
    for p in points[1:]:
        if dist(kept[-1], p) >= min_dist:
            kept.append(p)
    last = points[-1]
    if len(points) > 1 and kept[-1] != last:
        kept.append(last)
    return kept


def stroke_distance(stroke: Stroke, p: Point) -> float:
    """点到笔迹折线的最短距离；单点笔迹退化为点距，空笔迹无距离。"""
    pts = stroke.points
    if not pts:
        return math.inf
    best = dist(pts[0], p)
    for a, b in zip(pts, pts[1:]):
        vx, vy = b[0] - a[0], b[1] - a[1]
        seg_len_sq = vx * vx + vy * vy
        if seg_len_sq == 0.0:
            continue
        t = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / seg_len_sq))
        best = min(best, dist((a[0] + t * vx, a[1] + t * vy), p))
    return best
