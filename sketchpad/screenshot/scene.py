"""截图编辑场景：底图 + 标注图元。

场景坐标就是截图物理像素（入口统一 `imageops.normalize`），所以
`render_output()` 出来的图与编辑时看到的完全一致，不依赖窗口缩放。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene

BASE_Z = -1000.0


class EditorScene(QGraphicsScene):
    def __init__(self, image: QImage, parent=None) -> None:
        super().__init__(parent)
        self._base_item = QGraphicsPixmapItem()
        self._base_item.setZValue(BASE_Z)
        self._base_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.addItem(self._base_item)
        self.set_base(image, keep_size=False)

    def set_base(self, image: QImage, keep_size: bool = False) -> None:
        self._base_item.setPixmap(QPixmap.fromImage(image))
        self._base_item.setPos(0, 0)
        if not keep_size:
            self.setSceneRect(QRectF(image.rect()))

    def base_item(self) -> QGraphicsPixmapItem:
        return self._base_item

    def base_image(self) -> QImage:
        return self._base_item.pixmap().toImage()

    def annotation_items(self) -> list:
        return [item for item in self.items() if item is not self._base_item]

    def render_region(self, rect: QRectF) -> QImage:
        """只渲染场景的一部分；选区导出/复制都走这里。

        `QGraphicsView.drawForeground` 里的压暗层与手柄不属于场景，
        因此不会被导出——这是 in-place 遮罩方案的关键。
        """
        rect = QRectF(rect).intersected(self.sceneRect())
        if rect.width() < 1 or rect.height() < 1:
            return QImage()
        focus = self.focusItem()
        if focus is not None:
            focus.clearFocus()
        selected = list(self.selectedItems())
        if selected:
            self.clearSelection()
        image = QImage(rect.size().toSize(),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.render(painter, QRectF(image.rect()), rect)
        painter.end()
        image.setDevicePixelRatio(1.0)
        for item in selected:
            if item.scene() is self:
                item.setSelected(True)
        return image

    def render_output(self) -> QImage:
        """把底图与所有标注合成一张新图；不修改场景状态。"""
        return self.render_region(self.sceneRect())
