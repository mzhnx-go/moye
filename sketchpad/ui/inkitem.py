"""笔迹的 Qt 公共绘制：压感 ribbon 填充、恒定宽度兜底、`InkItem`。

画布、PDF 视图、位图缓存与预览共用这一份，避免三处各写一套压感逻辑。
坐标口径与调用方一致：**item/场景坐标**（画布是世界像素，PDF 视图是 PDF 点）。

- 无压感（或选字荧光）：`draw_ink_stroke` 只提供恒定宽度兜底，调用方原有
  的笔/矩形语义照旧；
- 有压感：用 `ribbon_polygon` 造闭合多边形，NoPen + 纯色 brush 填充；
  端点画圆头，单点画圆。多边形本身已经足够光滑，不再做 Qt 路径平滑。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsPathItem

from sketchpad.core.strokes import (HIGHLIGHTER_OPACITY, TEXT_HIGHLIGHT, Point,
                                    Stroke, has_pressure, pressure_widths,
                                    ribbon_polygon, text_highlight_rects)


def stroke_path(points: list[Point]) -> QPainterPath:
    """逐点直连的折线路径；单点笔迹用零点线段 + 圆头画成实心圆点。

    与画布/PDF 视图原来的 `_stroke_path` 同一口径（采样点已是真实轨迹，
    直连最忠实）；集中在这里供 item、缓存与预览复用。
    """
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0][0], points[0][1])
    if len(points) == 1:
        # Qt 不画零点线段；挪 0.001 个单位，圆头 + 圆角即实心圆点
        path.lineTo(points[0][0] + 0.001, points[0][1])
        return path
    for x, y in points[1:]:
        path.lineTo(x, y)
    return path


def _opacity_for(stroke: Stroke, opacity: float | None) -> float:
    """未显式给 opacity 时：荧光笔 0.35，其余 1.0（与既有语义一致）。"""
    if opacity is None:
        opacity = HIGHLIGHTER_OPACITY if stroke.tool == "highlighter" else 1.0
    return max(0.0, min(1.0, float(opacity)))


def _brush(color: str, opacity: float) -> QColor:
    brush = QColor(color)
    if opacity < 1.0:
        brush.setAlpha(round(255 * opacity))
    return brush


def _ink_pen(stroke: Stroke, zoom_or_scale: float, opacity: float | None) -> QPen:
    """恒定宽度兜底笔：圆头圆角，width*zoom 不足 1 屏幕像素时钳到 1。"""
    scale = float(zoom_or_scale)
    if not scale > 0.0:
        scale = 1.0
    pen = QPen(_brush(stroke.color, _opacity_for(stroke, opacity)))
    pen.setWidthF(max(float(stroke.width), 1.0 / scale))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def draw_ink_stroke(painter: QPainter, stroke: Stroke,
                    width_scale: float = 1.0,
                    opacity: float | None = None) -> None:
    """把一条笔迹画到 `painter` 上（坐标即 stroke.points 的坐标系）。

    `width_scale` 只影响有压感的笔迹（缓存按物理像素倍率放大线宽时用）；
    恒定宽度兜底仍用 `stroke.width` + 1 单位下限。未给 `opacity` 时荧光笔
    0.35、其它 1.0。
    """
    points = stroke.points
    if not points:
        return
    alpha = _opacity_for(stroke, opacity)
    if stroke.tool == TEXT_HIGHLIGHT:
        rects = text_highlight_rects(stroke)
        if not rects:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(_brush(stroke.color, alpha))
        for x0, y0, x1, y1 in rects:
            painter.drawRect(QRectF(min(x0, x1), min(y0, y1),
                                    abs(x1 - x0), abs(y1 - y0)))
        painter.restore()
        return
    if not has_pressure(stroke):
        painter.save()
        painter.setBrush(Qt.NoBrush)
        pen = QPen(_brush(stroke.color, alpha))
        pen.setWidthF(max(float(stroke.width) * float(width_scale), 1.0))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(stroke_path(points))
        painter.restore()
        return
    widths = [w * float(width_scale) for w in pressure_widths(stroke)]
    brush = _brush(stroke.color, alpha)
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(brush)
    if len(points) == 1:
        radius = widths[0] / 2.0
        painter.drawEllipse(QPointF(points[0][0], points[0][1]), radius, radius)
        painter.restore()
        return
    polygon = ribbon_polygon(points, widths)
    if polygon is not None:
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in polygon]))
        # 端点圆头：半径 = 端点半宽
        for (x, y), width in ((points[0], widths[0]), (points[-1], widths[-1])):
            radius = width / 2.0
            painter.drawEllipse(QPointF(x, y), radius, radius)
    painter.restore()


class InkItem(QGraphicsPathItem):
    """一条笔迹的图元：无压感走父类描边，有压感时 paint 改画 ribbon。

    仍是 `QGraphicsPathItem`（擦除、缓存、测试都按 isinstance 认它）；
    data(0) 存 Stroke，pen 保留恒定宽度的元数据供 boundingRect 与外部读取。
    """

    def __init__(self, stroke: Stroke, zoom_or_scale: float = 1.0,
                 opacity: float | None = None) -> None:
        super().__init__()
        self._zoom_or_scale = float(zoom_or_scale)
        self._opacity = opacity
        self.setData(0, stroke)
        self.setPath(stroke_path(stroke.points))
        self.setPen(_ink_pen(stroke, self._zoom_or_scale, opacity))
        self.setBrush(Qt.NoBrush)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        stroke = self.data(0)
        if (stroke is not None and has_pressure(stroke)
                and stroke.tool != TEXT_HIGHLIGHT):
            draw_ink_stroke(painter, stroke, 1.0, self._opacity)
            return
        super().paint(painter, option, widget)
