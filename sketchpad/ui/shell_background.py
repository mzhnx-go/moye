"""主窗的浅桃背景：渐变 + 柔和光斑，让半透明工具栏/标题栏有“玻璃”层次。

不做真正的系统 Acrylic：透明顶层窗口会拖慢手写/滚动；这里用自绘渐变和
低透明度光斑模拟毛玻璃，内容区仍是普通、确定层级。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget


class ShellBackground(QWidget):
    """铺满主窗内容区的浅桃渐变；子控件用 rgba 背景透出这层。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor("#FFF0F5"))
        gradient.setColorAt(0.45, QColor("#FFE3EC"))
        gradient.setColorAt(1.0, QColor("#FFD6E3"))
        painter.fillRect(rect, gradient)

        # 三团低透明度光斑，代替图片素材；不会进入内容区（内容区自己是不透明视图）
        for cx, cy, radius, color in (
            (0.18, 0.10, 0.32, QColor(255, 255, 255, 95)),
            (0.86, 0.18, 0.26, QColor(255, 190, 210, 70)),
            (0.68, 0.88, 0.30, QColor(255, 225, 205, 80)),
        ):
            glow = QRadialGradient(
                QPointF(rect.width() * cx, rect.height() * cy),
                max(1.0, rect.width() * radius))
            glow.setColorAt(0.0, color)
            glow.setColorAt(1.0, QColor(color.red(), color.green(),
                                        color.blue(), 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(
                QPointF(rect.width() * cx, rect.height() * cy),
                rect.width() * radius, rect.width() * radius)
        # 几片很淡的花瓣，只在玻璃/空隙里露出来，不进入内容区
        for cx, cy, angle, alpha in (
            (0.90, 0.07, 28, 54), (0.82, 0.12, -18, 38),
            (0.08, 0.78, -32, 34), (0.15, 0.86, 16, 26),
        ):
            painter.save()
            painter.translate(rect.width() * cx, rect.height() * cy)
            painter.rotate(angle)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 190, 210, alpha))
            painter.drawEllipse(QRectF(-14.0, -28.0, 28.0, 56.0))
            painter.restore()
        painter.end()
