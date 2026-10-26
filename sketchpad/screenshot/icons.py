"""截图工具栏图标：QPainter 现画，24×24 逻辑坐标、2 倍绘制，不依赖图片资源。

统一黑色线性风格，选中态由按钮的浅蓝底 + 蓝边表达，图标本身不用换色。
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QIcon, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QRadialGradient)

from sketchpad.screenshot.constants import (TOOL_ARROW, TOOL_BLUR, TOOL_CROP,
                                            TOOL_ELLIPSE, TOOL_MARKER,
                                            TOOL_MOSAIC, TOOL_NUMBER, TOOL_PEN,
                                            TOOL_RECT, TOOL_SELECT, TOOL_TEXT)

_SIZE = 24
_SCALE = 2
INK = "#1c1c1e"


def _icon(draw) -> QIcon:
    pixmap = QPixmap(_SIZE * _SCALE, _SIZE * _SCALE)
    pixmap.setDevicePixelRatio(float(_SCALE))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw(painter)
    painter.end()
    return QIcon(pixmap)


def _pen(width: float = 1.8, color: str = INK) -> QPen:
    return QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


@lru_cache(maxsize=None)
def pointer_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        p.setBrush(QColor(INK))
        p.drawPolygon(QPolygonF([QPointF(6, 4), QPointF(18, 12),
                                 QPointF(12.5, 13.5), QPointF(10, 20)]))
    return _icon(draw)


@lru_cache(maxsize=None)
def rect_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(4, 6, 16, 12), 2, 2)
    return _icon(draw)


@lru_cache(maxsize=None)
def ellipse_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(4, 6, 16, 12))
    return _icon(draw)


@lru_cache(maxsize=None)
def arrow_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        p.drawLine(QPointF(5, 18), QPointF(18, 6))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(INK))
        p.drawPolygon(QPolygonF([QPointF(19, 5), QPointF(17.5, 11),
                                 QPointF(13, 7.5)]))
    return _icon(draw)


@lru_cache(maxsize=None)
def pen_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        p.drawLine(QPointF(7, 17), QPointF(17, 7))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(INK))
        p.drawPolygon(QPolygonF([QPointF(4.5, 20), QPointF(9.5, 18.5),
                                 QPointF(6, 15)]))
    return _icon(draw)


@lru_cache(maxsize=None)
def marker_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(2.4))
        p.drawLine(QPointF(7, 17), QPointF(16, 7))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(INK))
        p.drawRoundedRect(QRectF(4.5, 17.5, 6, 3), 1, 1)
    return _icon(draw)


@lru_cache(maxsize=None)
def text_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        font = QFont()
        font.setPixelSize(18)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(INK))
        p.drawText(QRectF(2, 2, 20, 20), Qt.AlignmentFlag.AlignCenter, "T")
    return _icon(draw)


@lru_cache(maxsize=None)
def number_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(4, 4, 16, 16))
        font = QFont()
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(4, 3, 16, 17), Qt.AlignmentFlag.AlignCenter, "1")
    return _icon(draw)


@lru_cache(maxsize=None)
def mosaic_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(4, 4, 16, 16))
        for x in range(3):
            for y in range(3):
                if (x + y) % 2 == 0:
                    p.fillRect(QRectF(4 + x * 16 / 3, 4 + y * 16 / 3,
                                      16 / 3, 16 / 3), QColor(INK))
    return _icon(draw)


@lru_cache(maxsize=None)
def blur_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        gradient = QRadialGradient(QPointF(12, 12), 10)
        gradient.setColorAt(0, QColor(28, 28, 30, 190))
        gradient.setColorAt(0.55, QColor(28, 28, 30, 110))
        gradient.setColorAt(1, QColor(28, 28, 30, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawEllipse(QRectF(2, 2, 20, 20))
        p.setPen(_pen(1.2))
        for radius in (3.0, 6.0, 9.0):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(12, 12), radius, radius)
    return _icon(draw)


@lru_cache(maxsize=None)
def crop_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        p.drawLine(QPointF(7, 3), QPointF(7, 17))
        p.drawLine(QPointF(7, 17), QPointF(21, 17))
        p.drawLine(QPointF(3, 7), QPointF(17, 7))
        p.drawLine(QPointF(17, 7), QPointF(17, 21))
    return _icon(draw)


@lru_cache(maxsize=None)
def undo_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        path = QPainterPath()
        path.moveTo(6, 10)
        path.cubicTo(9, 4, 18, 5, 18, 13)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(INK))
        p.drawPolygon(QPolygonF([QPointF(4, 11), QPointF(10, 7),
                                 QPointF(9, 14)]))
    return _icon(draw)


@lru_cache(maxsize=None)
def redo_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        path = QPainterPath()
        path.moveTo(18, 10)
        path.cubicTo(15, 4, 6, 5, 6, 13)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(INK))
        p.drawPolygon(QPolygonF([QPointF(20, 11), QPointF(14, 7),
                                 QPointF(15, 14)]))
    return _icon(draw)


@lru_cache(maxsize=None)
def copy_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(8, 4, 12, 14), 2, 2)
        p.drawRoundedRect(QRectF(4, 8, 12, 14), 2, 2)
    return _icon(draw)


@lru_cache(maxsize=None)
def save_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
        p.drawRect(QRectF(8, 4, 8, 6))
        p.drawRect(QRectF(7, 14, 10, 6))
    return _icon(draw)


@lru_cache(maxsize=None)
def pin_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.8))
        p.drawLine(QPointF(12, 13), QPointF(12, 21))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(INK))
        p.drawPolygon(QPolygonF([QPointF(6, 4), QPointF(18, 4),
                                 QPointF(15, 12), QPointF(9, 12)]))
    return _icon(draw)


@lru_cache(maxsize=None)
def ocr_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 2, 2)
        p.drawLine(QPointF(7, 9), QPointF(17, 9))
        p.drawLine(QPointF(7, 13), QPointF(17, 13))
        p.drawLine(QPointF(7, 17), QPointF(13, 17))
    return _icon(draw)


@lru_cache(maxsize=None)
def close_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_pen(1.9))
        p.drawLine(QPointF(6, 6), QPointF(18, 18))
        p.drawLine(QPointF(18, 6), QPointF(6, 18))
    return _icon(draw)


@lru_cache(maxsize=None)
def dot_icon(diameter: float = 10.0) -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(INK))
        radius = diameter / 2
        p.drawEllipse(QPointF(12, 12), radius, radius)
    return _icon(draw)


@lru_cache(maxsize=None)
def swatch_icon(color: str) -> QIcon:
    def draw(p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#9a9aa0"), 1.0))
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(4, 4, 16, 16))
    return _icon(draw)


def tool_icon(tool: str) -> QIcon:
    return {
        TOOL_SELECT: pointer_icon,
        TOOL_RECT: rect_icon,
        TOOL_ELLIPSE: ellipse_icon,
        TOOL_ARROW: arrow_icon,
        TOOL_PEN: pen_icon,
        TOOL_MARKER: marker_icon,
        TOOL_TEXT: text_icon,
        TOOL_NUMBER: number_icon,
        TOOL_MOSAIC: mosaic_icon,
        TOOL_BLUR: blur_icon,
        TOOL_CROP: crop_icon,
    }[tool]()
