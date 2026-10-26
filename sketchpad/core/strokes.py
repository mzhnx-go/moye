"""笔迹数据模型：世界坐标点列 + 颜色 + 线宽。本模块不依赖 Qt。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MIN_POINT_DIST = 1.5
MAX_POINTS_PER_STROKE = 4000
HIGHLIGHTER_WIDTH = 16.0      # 荧光笔线宽（屏幕/世界像素；PDF 里按适应页宽换算成点）
HIGHLIGHTER_OPACITY = 0.35    # 不遮住底下的字；导出 PDF 用同一值
TEXT_HIGHLIGHT = "text_highlight"   # 选字荧光：points 按每两个点存一个矩形 (x0,y0),(x1,y1)
# 压感笔迹的线宽系数：压力 0 → base*0.35，压力 1 → base*1.0
PRESSURE_MIN_RATIO = 0.35
PRESSURE_MAX_RATIO = 1.0

Point = tuple[float, float]


@dataclass
class Stroke:
    points: list[Point]
    color: str
    width: float
    tool: str = "pen"
    # 与 points 等长的压力序列（0..1）；None = 恒定宽度。放末尾保持旧位置参数兼容
    pressures: list[float] | None = None


@dataclass
class TextBlock:
    """草稿画布上的一段文字（T 工具落点输入；草稿不落盘，仅内存）。"""

    pos: Point
    text: str
    color: str = "#e8eaed"
    size: float = 16.0


@dataclass
class Viewport:
    scale: float = 1.0
    origin: Point = (0.0, 0.0)


@dataclass
class Document:
    strokes: list[Stroke] = field(default_factory=list)
    texts: list[TextBlock] = field(default_factory=list)
    viewport: Viewport = field(default_factory=Viewport)


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
    # 末点保留：抬笔位置丢了会改变笔迹形状，判重再追加以免密集尾部出现重复点
    last = points[-1]
    if len(points) > 1 and kept[-1] != last:
        kept.append(last)
    return kept


def _as_pressure(value: object) -> float | None:
    """单个压力值 → float；bool/非数字/非有限值都算非法（返回 None）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def has_pressure(stroke: Stroke) -> bool:
    """这条笔迹是否该按压感画：pressure 与点等长、全为有限数、且不全为 0。

    长度对不上（旧数据/半截数据）按"无压感"处理，绝不按错位数据画。
    """
    pressures = stroke.pressures
    if not pressures or len(pressures) != len(stroke.points):
        return False
    active = False
    for raw in pressures:
        value = _as_pressure(raw)
        if value is None:
            return False
        if value != 0.0:
            active = True
    return active


def pressure_widths(stroke: Stroke) -> list[float]:
    """逐点完整线宽；无压感返回空列表（调用方回退恒定宽度）。"""
    if not has_pressure(stroke):
        return []
    base = float(stroke.width)
    span = PRESSURE_MAX_RATIO - PRESSURE_MIN_RATIO
    widths: list[float] = []
    for raw in stroke.pressures or []:
        value = _as_pressure(raw) or 0.0
        value = min(1.0, max(0.0, value))
        widths.append(base * (PRESSURE_MIN_RATIO + span * value))
    return widths


def _vertex_normal(normals: list[Point | None], index: int) -> Point:
    """顶点法线：相邻两条非零线段单位法线的平均（端点/零长段就近取一条）。"""
    candidates = [normals[j] for j in (index - 1, index)
                  if 0 <= j < len(normals) and normals[j] is not None]
    if candidates:
        ax = sum(n[0] for n in candidates)
        ay = sum(n[1] for n in candidates)
        length = math.hypot(ax, ay)
        # 长度 0 只有 180° 折返（两条法线正好相反），此时用就近的那条
        if length > 1e-9:
            return (ax / length, ay / length)
    for j in range(index - 1, -1, -1):
        if normals[j] is not None:
            return normals[j]
    for j in range(index, len(normals)):
        if normals[j] is not None:
            return normals[j]
    return (0.0, -1.0)


def ribbon_polygon(points: list[Point], widths: list[float]) -> list[Point] | None:
    """变宽带的闭合多边形：左侧正向 + 右侧反向。

    `widths` 是逐点**完整线宽**（内部取半宽）；法线逐顶点取相邻线段单位法线
    的平均，零长度段跳过，端点只用唯一相邻段。点数 < 2 或长度不匹配返回
    None（单点由调用方画圆）。纯数学，不依赖 Qt。
    """
    count = len(points)
    if count < 2 or widths is None or len(widths) != count:
        return None
    normals: list[Point | None] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        normals.append(None if length <= 1e-12 else (-dy / length, dx / length))
    left: list[Point] = []
    right: list[Point] = []
    for index, (x, y) in enumerate(points):
        nx, ny = _vertex_normal(normals, index)
        half = float(widths[index]) / 2.0
        left.append((x + nx * half, y + ny * half))
        right.append((x - nx * half, y - ny * half))
    right.reverse()
    return left + right


def thin_with_pressures(points: list[Point], pressures: list[float] | None,
                        min_dist: float = MIN_POINT_DIST
                        ) -> tuple[list[Point], list[float] | None]:
    """与 `thin()` 同样的保留规则，压力跟点同步丢弃/保留。

    `pressures=None`（或空列表）时原样走 `thin()`（返回 `(点, None)`）；
    有压力但长度与点不符属于调用方 bug，直接 ValueError，不猜怎么对齐。
    """
    if not pressures:
        return thin(points, min_dist), None
    if len(pressures) != len(points):
        raise ValueError(
            f"pressures length {len(pressures)} != points length {len(points)}")
    if not points:
        return [], []
    kept_points = [points[0]]
    kept_pressures = [pressures[0]]
    for point, pressure in zip(points[1:], pressures[1:]):
        if dist(kept_points[-1], point) >= min_dist:
            kept_points.append(point)
            kept_pressures.append(pressure)
    last = points[-1]
    # 末点保留规则与 thin() 逐字一致（抬笔位置决定形状，不能丢）
    if len(points) > 1 and kept_points[-1] != last:
        kept_points.append(last)
        kept_pressures.append(pressures[-1])
    return kept_points, kept_pressures


def smooth_with_pressures(points: list[Point], pressures: list[float] | None,
                          level: str = "low"
                          ) -> tuple[list[Point], list[float] | None]:
    """**点数不变**的抬笔平滑：压力可以逐点对齐，不会因子采样错位。

    与 `smooth_points` 同一套去抖/拐角锁定/位移上限，但不跑 RDP 简化——
    RDP 会删点，压力序列随即对不上轨迹。`pressures=None`（或空列表）时行为
    完全等于 `smooth_points`；`off`/未知档返回副本。
    """
    if not pressures:
        from sketchpad.core.smoothing import smooth_points
        return smooth_points(points, level), None
    if len(pressures) != len(points):
        raise ValueError(
            f"pressures length {len(pressures)} != points length {len(points)}")
    from sketchpad.core.smoothing import (INK_SMOOTHING_LEVELS, _CORNER_DEG,
                                          _DENSIFY_STEPS, _MAX_SHIFT, _PASSES,
                                          _corner_indices, _prefilter, _smooth)
    kept = list(pressures)
    if level not in INK_SMOOTHING_LEVELS or level == "off":
        return list(points), kept
    src = list(points)
    if len(src) < 4:
        return src, kept
    corners = _corner_indices(src, _CORNER_DEG[level], min_len=3.0)
    out = _prefilter(src, _MAX_SHIFT[level])
    for index in corners:
        out[index] = src[index]          # 去抖后把拐角顶点按原样放回
    pinned = _corner_indices(out, _CORNER_DEG[level]) | corners
    out = _smooth(out, pinned, _PASSES[level], _MAX_SHIFT[level])
    if level in _DENSIFY_STEPS:
        # 高/超高：顶点圆角消除颗粒感（压力同步插值），再平滑压力以出笔锋。
        # 低/中保持原行为，不加密也不动压力序列。
        out, kept = _densify_bezier_pressures(
            out, kept, pinned, _DENSIFY_STEPS[level])
        kept = _smooth_pressure(kept, passes=2)
    return out, kept


def _smooth_pressure(pressures: list[float], passes: int = 1,
                     max_step: float = 0.06) -> list[float]:
    """压力序列平滑：抹掉突变但保留力度走势，让粗细渐变（出笔锋）。

    原始笔压在起笔/收笔处会剧烈跳变（实测同一笔内 0.04↔0.99），直接映射到
    线宽就是"忽粗忽细"、看着断断续续。这里做 3 点加权平均并限制单步位移，
    真实力度的整体走势不会被抹平。
    """
    n = len(pressures)
    if n < 3:
        return list(pressures)
    out = list(pressures)
    for _ in range(max(1, passes)):
        src = out
        nxt = list(src)
        for i in range(1, n - 1):
            target = (src[i - 1] + 2.0 * src[i] + src[i + 1]) / 4.0
            step = target - src[i]
            if abs(step) > max_step:
                target = src[i] + (max_step if step > 0 else -max_step)
            nxt[i] = min(1.0, max(0.0, target))
        out = nxt
    return out


def _densify_bezier_pressures(points: list[Point], pressures: list[float],
                              corners: set[int], steps: int
                              ) -> tuple[list[Point], list[float]]:
    """高/超高：与 smooth_points 同一套顶点圆角，同时把压力插值到新点列。

    `_densify_bezier` 只管点，压感路径直接用它会丢掉压力；这里细分时按
    "原折线上的浮点索引"线性插值压力，保证加密后压力序列与点一一对应、不脱位。
    """
    n = len(points)
    if n < 3 or steps < 2 or len(pressures) != n:
        return list(points), list(pressures)
    steps = max(2, int(steps))
    idxs = sorted(i for i in corners if 0 < i < n - 1)
    bounds = [0] + idxs + [n - 1]
    out_pts: list[Point] = []
    out_prs: list[float] = []

    def at(u: float) -> float:
        """u 是原折线上的浮点索引，压力在相邻两点间线性插值。"""
        if u <= 0:
            return pressures[0]
        if u >= n - 1:
            return pressures[-1]
        i = int(u)
        if i >= n - 1:
            return pressures[-1]
        return pressures[i] + (pressures[i + 1] - pressures[i]) * (u - i)

    for s in range(len(bounds) - 1):
        a, b = bounds[s], bounds[s + 1]
        seg = points[a:b + 1]
        m = len(seg)
        if m < 3:
            for j in range(m):
                if out_pts and j == 0:
                    continue                 # 与上一段共享的末点
                out_pts.append(seg[j])
                out_prs.append(pressures[a + j])
            continue
        if not out_pts:                      # 只有首段放段起点
            out_pts.append(seg[0])
            out_prs.append(at(a))
        for i in range(1, m - 1):
            p0, p1, p2 = seg[i - 1], seg[i], seg[i + 1]
            c1 = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
            c2 = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            for k in range(1, steps):
                t = k / steps
                mt = 1.0 - t
                w0, w1, w2 = mt * mt, 2.0 * mt * t, t * t
                out_pts.append((w0 * c1[0] + w1 * p1[0] + w2 * c2[0],
                                w0 * c1[1] + w1 * p1[1] + w2 * c2[1]))
                out_prs.append(at(a + i - 0.5 + t))
            out_pts.append(c2)
            out_prs.append(at(a + i + 0.5))
        out_pts.append(seg[m - 1])
        out_prs.append(at(b))
    return out_pts, out_prs


def is_highlight(stroke: Stroke) -> bool:
    """会画成半透明高亮的笔迹：手绘荧光与选字荧光。"""
    return stroke.tool in ("highlighter", TEXT_HIGHLIGHT)


def text_highlight_rects(stroke: Stroke) -> list[tuple[float, float, float, float]]:
    """选字荧光存的矩形列表；points 每两个点是一组 (x0,y0),(x1,y1)。"""
    pts = stroke.points
    if stroke.tool != TEXT_HIGHLIGHT or len(pts) < 2:
        return []
    return [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
            for i in range(0, len(pts) - 1, 2)]


def stroke_distance(stroke: Stroke, p: Point) -> float:
    """点到笔迹折线的最短距离；单点笔迹退化为点距，空笔迹无距离。

    选字荧光按矩形算：点在矩形内距离为 0，否则到矩形边的距离——橡皮擦到
    高亮块内部也能整块删掉。
    """
    pts = stroke.points
    if not pts:
        return math.inf
    if stroke.tool == TEXT_HIGHLIGHT:
        best = math.inf
        for x0, y0, x1, y1 in text_highlight_rects(stroke):
            dx = max(x0 - p[0], 0.0, p[0] - x1)
            dy = max(y0 - p[1], 0.0, p[1] - y1)
            best = min(best, math.hypot(dx, dy))
            if best == 0.0:
                break
        return best
    best = dist(pts[0], p)
    for a, b in zip(pts, pts[1:]):
        vx, vy = b[0] - a[0], b[1] - a[1]
        seg_len_sq = vx * vx + vy * vy
        if seg_len_sq == 0.0:      # 重合点没有方向，投影无意义，交给上面的点距
            continue
        # t 钳在 [0,1]：垂足落在延长线上时，最近的是端点而不是直线本身
        t = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / seg_len_sq))
        best = min(best, dist((a[0] + t * vx, a[1] + t * vy), p))
    return best
