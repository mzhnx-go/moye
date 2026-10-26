"""工具栏图标：用 QPainter 现画，不依赖图片资源，任意 DPI 都清晰。

统一 24×24 逻辑坐标、2 倍绘制再交给 QIcon，白色描边；选中态由按钮蓝色底区分。
只保留工具栏用得到的图标：撤销、钢笔、橡皮、清空（垃圾桶）、调色盘、收起箭头、
粗细圆点，外加穿透（虚线框 + 指针箭头穿过）。
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QPainter, QPainterPath, QPen,
                           QPixmap)

from sketchpad.annotate import theme
from sketchpad.annotate.constants import SHELL

_SIZE = 24          # 逻辑尺寸
_SCALE = 2          # 实际画 2 倍再交给 QIcon，缩放不发虚


def _icon(draw) -> QIcon:
    pixmap = QPixmap(_SIZE * _SCALE, _SIZE * _SCALE)
    pixmap.setDevicePixelRatio(float(_SCALE))   # QPainter 按 DPR 放大，别再手动 scale
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    draw(painter)
    painter.end()
    return QIcon(pixmap)


DARK_INK = "#0d1a16"        # 选中态是亮绿底，图标必须换成深色才看得清


def ink_color(dark: bool):
    return QColor(DARK_INK) if dark else QColor(theme.TEXT)


def _stroke(width: float = 2.0, dark: bool = False) -> QPen:
    return QPen(ink_color(dark), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


@lru_cache(maxsize=None)
def pen_icon(dark: bool = False) -> QIcon:
    """钢笔：斜笔身 + 实心笔尖。dark=True 是给选中（亮绿底）用的那版。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.2, dark))
        p.drawLine(QPointF(8.0, 16.0), QPointF(17.0, 7.0))
        p.setPen(Qt.NoPen)
        p.setBrush(ink_color(dark))
        p.drawPolygon([QPointF(4.6, 19.4), QPointF(10.3, 17.6), QPointF(6.4, 13.7)])
    return _icon(draw)


@lru_cache(maxsize=None)
def eraser_icon(dark: bool = False) -> QIcon:
    """橡皮：斜放的圆角块，中线分出擦头。"""
    def draw(p: QPainter) -> None:
        p.translate(12.0, 12.0)
        p.rotate(-40.0)
        p.setPen(_stroke(2.0, dark))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(-8.0, -5.0, 16.0, 10.0), 2.0, 2.0)
        p.drawLine(QPointF(-8.0, 0.0), QPointF(0.0, 0.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def clear_icon() -> QIcon:
    """清空：垃圾桶（桶盖 + 提手 + 桶身 + 两条竖纹）。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(5.0, 7.0), QPointF(19.0, 7.0))
        p.drawLine(QPointF(10.0, 4.5), QPointF(14.0, 4.5))
        p.drawRoundedRect(QRectF(7.0, 7.0, 10.0, 12.5), 2.0, 2.0)
        p.drawLine(QPointF(10.5, 10.0), QPointF(10.5, 16.5))
        p.drawLine(QPointF(13.5, 10.0), QPointF(13.5, 16.5))
    return _icon(draw)


@lru_cache(maxsize=None)
def undo_icon() -> QIcon:
    """撤销：逆时针回钩 + 实心箭头。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        path = QPainterPath()
        path.moveTo(7.5, 15.2)
        path.cubicTo(6.0, 8.6, 17.2, 7.0, 18.2, 13.2)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawPolygon([QPointF(4.2, 16.8), QPointF(10.2, 14.8), QPointF(8.8, 10.2)])
    return _icon(draw)


@lru_cache(maxsize=None)
def palette_icon() -> QIcon:
    """调色盘：四色扇面 + 中心挖孔。"""
    def draw(p: QPainter) -> None:
        rect = QRectF(3.5, 3.5, 17.0, 17.0)
        for index, color in enumerate(("#ff453a", "#ffd60a", "#30d158", "#0a84ff")):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawPie(rect, index * 90 * 16, 90 * 16)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(SHELL), 1.5))          # 与工具栏底色同色的缝，扇面才分得开
        p.drawEllipse(rect)
        p.setBrush(QColor(SHELL))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12.0, 12.0), 3.2, 3.2)
    return _icon(draw)


@lru_cache(maxsize=None)
def width_icon(diameter: float, dark: bool = False) -> QIcon:
    """粗细档位：一个实心圆点，直径随档位变大，一眼看出粗细。

    未选中时用纯白，选中态压在亮绿底上时换成深色（dark=True）。
    """
    radius = max(1.5, float(diameter) / 2.0)

    def draw(p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(ink_color(dark))
        p.drawEllipse(QPointF(12.0, 12.0), radius, radius)
    return _icon(draw)


@lru_cache(maxsize=None)
def capture_icon() -> QIcon:
    """截图：取景框（四个直角）+ 中间一条快门线。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        for x, y, dx, dy in ((5.0, 5.0, 1, 1), (19.0, 5.0, -1, 1),
                             (5.0, 19.0, 1, -1), (19.0, 19.0, -1, -1)):
            p.drawLine(QPointF(x, y + 4.0 * dy), QPointF(x, y))
            p.drawLine(QPointF(x, y), QPointF(x + 4.0 * dx, y))
        p.drawLine(QPointF(8.0, 13.0), QPointF(16.0, 13.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def orient_icon() -> QIcon:
    """横排 / 竖排切换：对角双箭头（通用 swap 语义），两头都是实心箭头。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        p.drawLine(QPointF(8.0, 16.0), QPointF(16.0, 8.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawPolygon([QPointF(18.6, 5.4), QPointF(18.2, 12.4), QPointF(11.6, 5.8)])
        p.drawPolygon([QPointF(5.4, 18.6), QPointF(5.8, 11.6), QPointF(12.4, 18.2)])
    return _icon(draw)


# 折叠箭头：横向工具栏只用向上，保留四向表以免调用方传错方向
_CHEVRON_POINTS = {
    "up": ((7.0, 14.5), (12.0, 9.5), (17.0, 14.5)),
    "down": ((7.0, 9.5), (12.0, 14.5), (17.0, 9.5)),
    "left": ((14.5, 7.0), (9.5, 12.0), (14.5, 17.0)),
    "right": ((9.5, 7.0), (14.5, 12.0), (9.5, 17.0)),
}


@lru_cache(maxsize=None)
def chevron_icon(direction: str = "up") -> QIcon:
    """收起箭头：direction 取 up（收起）/ down / left / right。"""
    points = _CHEVRON_POINTS.get(direction)
    if points is None:
        raise ValueError(f"chevron_icon: 未知方向 {direction!r}")

    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.2))
        p.drawPolyline([QPointF(x, y) for x, y in points])
    return _icon(draw)


@lru_cache(maxsize=None)
def through_icon(dark: bool = False) -> QIcon:
    """穿透：虚线圆角框 + 指针箭头从框里穿出去。

    dark=True 是给"当前就是穿透"那版用的：按钮底色是亮绿，白描边会糊在
    绿底上，换成深色描边才读得清。
    """
    def draw(p: QPainter) -> None:
        ink = ink_color(dark)
        pen = QPen(ink, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        pen.setDashPattern([2.2, 2.2])          # 单位是笔宽，视觉上就是均匀小虚线
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(3.5, 3.5, 17.0, 17.0), 3.5, 3.5)
        # 指针：笔尖在虚线框内，尾巴从右下角穿出，一眼就是“点下去落到下层”
        p.setPen(QPen(ink, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolygon([QPointF(8.0, 6.6), QPointF(8.0, 19.6), QPointF(10.9, 16.4),
                       QPointF(12.9, 21.0), QPointF(15.0, 20.0), QPointF(13.0, 15.1),
                       QPointF(16.9, 14.9)])
    return _icon(draw)
