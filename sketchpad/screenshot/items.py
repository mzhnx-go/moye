"""截图编辑器里的标注图元。

全部用场景坐标（= 截图物理像素）绘制，导出时直接把场景渲染成图，
所以屏幕预览与保存/复制逐像素一致。图元不自己处理撤销——编辑器统一
记录「加入/移除/裁剪」命令。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF, QTextCursor)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem

from sketchpad.screenshot.constants import MARKER_ALPHA, NUMBER_RADIUS


def _selectable_flags() -> QGraphicsItem.GraphicsItemFlag:
    return (QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable)


class ShapeItem(QGraphicsItem):
    """矩形 / 椭圆 / 箭头。矩形和椭圆用 rect，箭头用 p1→p2 保留方向。"""

    def __init__(self, kind: str, start: QPointF, end: QPointF,
                 color: str, width: int) -> None:
        super().__init__()
        self.kind = kind
        self.color = QColor(color)
        self.width = max(1, int(width))
        self._p1 = QPointF(start)
        self._p2 = QPointF(end)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)

    # ---------- 几何 ----------

    def set_end(self, end: QPointF) -> None:
        self._p2 = QPointF(end)
        self.prepareGeometryChange()

    def rect(self) -> QRectF:
        return QRectF(self._p1, self._p2).normalized()

    def boundingRect(self) -> QRectF:
        pad = self.width + 12.0
        return self.rect().adjusted(-pad, -pad, pad, pad)

    def _arrow_head(self) -> QPolygonF | None:
        dx = self._p2.x() - self._p1.x()
        dy = self._p2.y() - self._p1.y()
        length = math.hypot(dx, dy)
        if length < 1.0:
            return None
        head = max(10.0, self.width * 4.0)
        ux, uy = dx / length, dy / length
        left = QPointF(self._p2.x() - ux * head + uy * head * 0.5,
                       self._p2.y() - uy * head - ux * head * 0.5)
        right = QPointF(self._p2.x() - ux * head - uy * head * 0.5,
                        self._p2.y() - uy * head + ux * head * 0.5)
        return QPolygonF([QPointF(self._p2), left, right])

    # ---------- 绘制 ----------

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self.color, self.width, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "rect":
            painter.drawRect(self.rect())
        elif self.kind == "ellipse":
            painter.drawEllipse(self.rect())
        else:
            painter.drawLine(self._p1, self._p2)
            head = self._arrow_head()
            if head is not None:
                painter.setBrush(QBrush(self.color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPolygon(head)


class StrokeItem(QGraphicsItem):
    """画笔 / 马克笔：折线路径。马克笔半透明、更宽。"""

    def __init__(self, points: list[QPointF], color: str, width: int,
                 marker: bool = False) -> None:
        super().__init__()
        self.color = QColor(color)
        self.width = max(1, int(width)) * (2 if marker else 1)
        self.marker = marker
        self._points = [QPointF(p) for p in points]
        self._update(cache=False)

    def _build_path(self) -> QPainterPath:
        path = QPainterPath()
        if not self._points:
            return path
        path.moveTo(self._points[0])
        if len(self._points) == 1:
            path.lineTo(self._points[0] + QPointF(0.01, 0.01))
            return path
        for point in self._points[1:]:
            path.lineTo(point)
        return path

    def _update(self, cache: bool = True) -> None:
        self.prepareGeometryChange()
        self._path = self._build_path()
        self._bounding = self._path.boundingRect().adjusted(
            -self.width, -self.width, self.width, self.width)
        if cache:
            self.update()

    def add_point(self, point: QPointF) -> None:
        self._points.append(QPointF(point))
        self._update()

    def point_count(self) -> int:
        return len(self._points)

    def boundingRect(self) -> QRectF:
        return self._bounding

    def paint(self, painter: QPainter, option, widget=None) -> None:
        color = QColor(self.color)
        if self.marker:
            color.setAlphaF(MARKER_ALPHA)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, self.width, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)


class TextItem(QGraphicsTextItem):
    """点选落字的文本块；双击进入编辑，失焦提交。"""

    def __init__(self, text: str, color: str, size: int,
                 on_finish=None) -> None:
        super().__init__()
        self._on_finish = on_finish
        self._editing = False
        self.setPlainText(text)
        self.setDefaultTextColor(QColor(color))
        font = QFont(self.font())
        font.setPixelSize(max(8, int(size)))
        self.setFont(font)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def start_editing(self) -> None:
        self._editing = True
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def stop_editing(self) -> None:
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        if self.hasFocus():
            self.clearFocus()          # 触发 focusOutEvent 提交
        if self._editing:              # 没有控件焦点时也要提交一次
            self._editing = False
            if self._on_finish is not None:
                self._on_finish(self)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.stop_editing()
            event.accept()
            return
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.stop_editing()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if self._editing:
            self._editing = False
            self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            if self._on_finish is not None:
                self._on_finish(self)

    def mouseDoubleClickEvent(self, event) -> None:
        self.start_editing()
        event.accept()


class NumberItem(QGraphicsItem):
    """圆形序号：1、2、3… 白字实心圆。"""

    def __init__(self, number: int, center: QPointF, color: str,
                 radius: int = NUMBER_RADIUS) -> None:
        super().__init__()
        self.number = number
        self.color = QColor(color)
        self.radius = int(radius)
        self.setPos(QPointF(center) - QPointF(self.radius, self.radius))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.radius * 2.0, self.radius * 2.0)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.setBrush(QBrush(self.color))
        r = self.radius
        painter.drawEllipse(QRectF(1, 1, r * 2.0 - 2, r * 2.0 - 2))
        font = QFont(painter.font())
        font.setPixelSize(int(r * 1.25))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(0, 0, r * 2.0, r * 2.0),
                         Qt.AlignmentFlag.AlignCenter, str(self.number))


class RegionEffectItem(QGraphicsItem):
    """马赛克 / 模糊：保存从底图算好的小图，贴回原位置。

    放在 z=-1：后画的框/文字仍然盖在效果之上，不会被"打码"吃掉。
    """

    def __init__(self, image: QImage, position: QPointF) -> None:
        super().__init__()
        self._pixmap = QPixmap.fromImage(image)
        self._size = QRectF(0, 0, image.width(), image.height())
        self.setPos(QPointF(position))
        self.setZValue(-1)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)

    def size(self):
        return self._size.size()

    def boundingRect(self) -> QRectF:
        return self._size

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, self._pixmap)
