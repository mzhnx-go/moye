"""截图编辑会话：场景、工具状态、撤销栈、裁剪、按选区导出。

无 QWidget，供全屏遮罩（生产路径）和旧的独立编辑器共用同一套编辑逻辑。
坐标一律是冻结截图的物理像素。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage

from sketchpad.screenshot import imageops
from sketchpad.screenshot.constants import (DEFAULT_COLOR, DEFAULT_WIDTH,
                                            TEXT_SIZE, TOOL_MOSAIC,
                                            TOOL_SELECT)
from sketchpad.screenshot.items import (NumberItem, RegionEffectItem, TextItem)
from sketchpad.screenshot.scene import EditorScene


class ScreenshotSession:
    def __init__(self, image: QImage, on_undo_changed=None) -> None:
        self.scene = EditorScene(imageops.normalize(image))
        self.tool = TOOL_SELECT
        self.color = DEFAULT_COLOR
        self.pen_width = DEFAULT_WIDTH
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._number = 0
        self._on_undo_changed = on_undo_changed

    # ---------- 工具/图元 ----------

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        buttons = (Qt.MouseButton.LeftButton if tool == TOOL_SELECT
                   else Qt.MouseButton.NoButton)
        for item in self.scene.annotation_items():
            item.setAcceptedMouseButtons(buttons)

    def set_color(self, color: str) -> None:
        self.color = color
        for item in self.scene.selectedItems():
            if isinstance(item, TextItem):
                item.setDefaultTextColor(QColor(color))
                item.update()
            elif hasattr(item, "color"):
                item.color = QColor(color)
                item.update()

    def set_width(self, width: int) -> None:
        self.pen_width = int(width)
        for item in self.scene.selectedItems():
            if isinstance(item, TextItem):
                font = item.font()
                font.setPixelSize(max(8, round(TEXT_SIZE * width / DEFAULT_WIDTH)))
                item.setFont(font)
            elif hasattr(item, "width") and not callable(getattr(item, "width")):
                item.prepareGeometryChange()
                item.width = int(width) * (2 if getattr(item, "marker", False) else 1)
                item.update()

    def commit_item(self, item) -> None:
        command = (lambda: self.scene.removeItem(item),
                   lambda: self.scene.addItem(item))
        item._undo_command = command
        self._push(*command)
        self._bind_mouse(item)

    def _bind_mouse(self, item) -> None:
        item.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton if self.tool == TOOL_SELECT
            else Qt.MouseButton.NoButton)

    def place_text(self, pos: QPointF) -> TextItem:
        item = TextItem("", self.color, TEXT_SIZE,
                        on_finish=self._text_finished)
        item.setPos(pos)
        self.scene.addItem(item)
        self.commit_item(item)
        item.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        return item

    def _text_finished(self, item: TextItem) -> None:
        if not item.toPlainText().strip():
            self.scene.removeItem(item)
            command = getattr(item, "_undo_command", None)
            if command is not None and self._undo and self._undo[-1] is command:
                self._undo.pop()
                self._redo.clear()
                self._notify()

    def place_number(self, pos: QPointF) -> NumberItem:
        self._number += 1
        item = NumberItem(self._number, pos, self.color)
        self.scene.addItem(item)
        self.commit_item(item)
        return item

    # ---------- 区域效果/裁剪 ----------

    def apply_region_effect(self, tool: str, rect: QRectF) -> None:
        rect_i = rect.toAlignedRect().intersected(
            self.scene.sceneRect().toAlignedRect())
        if rect_i.width() < 4 or rect_i.height() < 4:
            return
        base = self.scene.base_image()
        offset = self.scene.base_item().pos().toPoint()
        local = rect_i.translated(-offset)
        image = (imageops.pixelate(base, local) if tool == TOOL_MOSAIC
                 else imageops.blur_region(base, local))
        if image.isNull():
            return
        item = RegionEffectItem(image, QPointF(rect_i.topLeft()))
        self.scene.addItem(item)
        self.commit_item(item)

    def apply_crop(self, rect: QRectF) -> bool:
        rect_i = rect.toAlignedRect().intersected(
            self.scene.sceneRect().toAlignedRect())
        if rect_i.width() < 8 or rect_i.height() < 8:
            return False
        before = self._snapshot()
        self._do_crop(rect_i)
        after = self._snapshot()
        self._push(lambda: self._restore_snapshot(before),
                   lambda: self._restore_snapshot(after))
        return True

    def _do_crop(self, rect: QRect) -> None:
        base = self.scene.base_image()
        offset = self.scene.base_item().pos().toPoint()
        cropped = imageops.crop(base, rect.translated(-offset))
        if cropped.isNull():
            return
        base_item = self.scene.base_item()
        from PySide6.QtGui import QPixmap
        base_item.setPixmap(QPixmap.fromImage(cropped))
        base_item.setPos(QPointF(rect.topLeft()))
        self.scene.setSceneRect(QRectF(rect))

    def _snapshot(self) -> tuple[QImage, QPointF, QRectF]:
        return (self.scene.base_image().copy(),
                QPointF(self.scene.base_item().pos()),
                QRectF(self.scene.sceneRect()))

    def _restore_snapshot(self, snapshot) -> None:
        image, position, rect = snapshot
        from PySide6.QtGui import QPixmap
        base_item = self.scene.base_item()
        base_item.setPixmap(QPixmap.fromImage(image.copy()))
        base_item.setPos(position)
        self.scene.setSceneRect(rect)

    # ---------- 撤销 ----------

    def _push(self, undo, redo) -> None:
        self._undo.append((undo, redo))
        self._redo.clear()
        self._notify()

    def _notify(self) -> None:
        if callable(self._on_undo_changed):
            self._on_undo_changed(bool(self._undo), bool(self._redo))

    def undo(self) -> bool:
        if not self._undo:
            return False
        undo, redo = self._undo.pop()
        undo()
        self._redo.append((undo, redo))
        self._notify()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        undo, redo = self._redo.pop()
        redo()
        self._undo.append((undo, redo))
        self._notify()
        return True

    def delete_selected(self) -> bool:
        selected = [item for item in self.scene.selectedItems()
                    if item is not self.scene.base_item()]
        if not selected:
            return False
        for item in selected:
            self.scene.removeItem(item)
        self._push(lambda: [self.scene.addItem(item) for item in selected],
                   lambda: [self.scene.removeItem(item) for item in selected])
        return True

    # ---------- 导出 ----------

    def render_region(self, rect: QRectF) -> QImage:
        return self.scene.render_region(rect)

    def render_output(self) -> QImage:
        return self.scene.render_output()
