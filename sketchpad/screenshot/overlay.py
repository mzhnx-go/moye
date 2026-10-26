"""全屏 in-place 截图编辑器：冻结屏幕、框选、调整选区、直接标注。

与旧的 `ScreenshotEditor` 独立窗口的区别：
- 松手只结束“框选”，不复制、不关闭；选区保留 8 个手柄，可继续缩放/移动；
- 标注直接画在冻结屏幕上，压暗层与手柄属于 foreground，不会进导出图；
- 复制/保存/贴图时才生成“选区+标注”的合成图，然后关闭。
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import (QFileDialog, QFrame, QGraphicsRectItem,
                               QGraphicsView, QMessageBox)

from sketchpad.screenshot import imageops
from sketchpad.screenshot.constants import (SHAPE_TOOLS, TEXT_SIZE,
                                            TOOL_ARROW, TOOL_BLUR, TOOL_CROP,
                                            TOOL_ELLIPSE, TOOL_MARKER,
                                            TOOL_MOSAIC, TOOL_NUMBER, TOOL_PEN,
                                            TOOL_RECT, TOOL_SELECT, TOOL_TEXT)
from sketchpad.screenshot.editor import OCRResultDialog
from sketchpad.screenshot.items import ShapeItem, StrokeItem, TextItem
from sketchpad.screenshot.ocr_worker import start_ocr
from sketchpad.screenshot.selection import SelectionModel
from sketchpad.screenshot.session import ScreenshotSession
from sketchpad.screenshot.toolbar import EditorToolbar

DIM_ALPHA = 110
HANDLE_LOGICAL = 8.0
HANDLE_HIT_LOGICAL = 7.0
BORDER_LOGICAL = 1.5
BLUE = "#0a84ff"
BLUE_GAP = 2.0
BLUE_WIDTH = 2.0


class ScreenshotOverlay(QGraphicsView):
    copied = Signal(QImage)
    saved = Signal(QImage, str)
    pinned = Signal(QImage)
    closed = Signal()
    colorPicked = Signal(str)

    def __init__(self, image: QImage, screen_geometry: QRect,
                 initial_rect: QRectF | None = None,
                 color_mode: bool = False, ocr_enabled: bool = False,
                 ocr_func=None, source: str = "region", parent=None) -> None:
        super().__init__(None)
        self.setWindowTitle("屏幕取色" if color_mode else "截图")
        self.setWindowFlags(Qt.WindowType.Tool
                            | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor("#141416"))
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.TextAntialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(QRect(screen_geometry))
        self._color_mode = bool(color_mode)
        self._ocr_enabled = bool(ocr_enabled)
        self._ocr_func = ocr_func
        self._source = source
        self._done = False
        self._closed_emitted = False
        self._ocr_busy = False
        self._ocr_worker = None
        self._scale = 1.0
        self._drag_mode: str | None = None
        self._drag_handle: str | None = None
        self._drag_start = QPointF()
        self._drag_rect = QRectF()
        self._pending_rect: QRectF | None = None
        self._item_drag = False
        self._preview = None
        self._stroke: StrokeItem | None = None
        self._hover_scene: QPointF | None = None

        self.session = ScreenshotSession(imageops.normalize(image),
                                         on_undo_changed=self._sync_undo)
        self.setScene(self.session.scene)
        self.selection = SelectionModel(
            bounds=QRectF(self.session.scene.sceneRect()))
        if initial_rect is not None:
            self.selection.set_rect(initial_rect)

        self.toolbar = EditorToolbar(ocr_enabled=self._ocr_enabled,
                                     parent=self.viewport(), allow_drag=False)
        self._wire()
        self._apply_fit()
        if self._color_mode:
            self.toolbar.hide()
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.toolbar.setVisible(self.selection.has_selection())
            self._position_toolbar()
            self._update_cursor_idle()

    # ---------- 对外 ----------

    def source(self) -> str:
        return self._source

    def selection_rect(self) -> QRectF | None:
        return self.selection.current()

    def color_mode(self) -> bool:
        return self._color_mode

    def cancel(self) -> None:
        self._finish()

    # ---------- 视图/变换 ----------

    def _apply_fit(self) -> None:
        rect = QRectF(self.session.scene.sceneRect())
        if rect.width() < 1 or rect.height() < 1:
            return
        self.setSceneRect(rect)
        viewport = self.viewport().size()
        scale = min(viewport.width() / rect.width(),
                    viewport.height() / rect.height())
        self._scale = max(0.01, scale)
        self.resetTransform()
        self.scale(self._scale, self._scale)
        self._position_toolbar()
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_fit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_fit()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    # ---------- 前景：压暗/边框/手柄/尺寸/取色 ----------

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        scene_rect = QRectF(self.session.scene.sceneRect())
        hole = self.selection.current()
        if self._drag_mode == "new" and self._pending_rect is not None:
            hole = self._pending_rect
        if self._color_mode:
            painter.fillRect(scene_rect, QColor(0, 0, 0, DIM_ALPHA))
            self._draw_color_chip(painter)
            painter.restore()
            return

        if hole is None:
            painter.fillRect(scene_rect, QColor(0, 0, 0, DIM_ALPHA))
        else:
            self._draw_dim_around(painter, scene_rect, hole)
            self._draw_border(painter, hole,
                              dashed=self._drag_mode == "new")
            if self.selection.has_selection():
                self._draw_handles(painter, hole)
            self._draw_size_label(painter, hole)
        painter.restore()

    def _draw_dim_around(self, painter: QPainter, scene_rect: QRectF,
                         hole: QRectF) -> None:
        dim = QColor(0, 0, 0, DIM_ALPHA)
        h = hole.intersected(scene_rect)
        painter.fillRect(QRectF(scene_rect.left(), scene_rect.top(),
                                scene_rect.width(), h.top() - scene_rect.top()), dim)
        painter.fillRect(QRectF(scene_rect.left(), h.bottom(),
                                scene_rect.width(),
                                scene_rect.bottom() - h.bottom()), dim)
        painter.fillRect(QRectF(scene_rect.left(), h.top(),
                                h.left() - scene_rect.left(), h.height()), dim)
        painter.fillRect(QRectF(h.right(), h.top(),
                                scene_rect.right() - h.right(), h.height()), dim)

    def _draw_border(self, painter: QPainter, rect: QRectF,
                     dashed: bool = False) -> None:
        style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
        # 先画蓝色外圈（视觉上的“边距”），再压白色内框
        artist = painter
        blue_rect = rect.adjusted(-BLUE_GAP / self._scale,
                                  -BLUE_GAP / self._scale,
                                  BLUE_GAP / self._scale,
                                  BLUE_GAP / self._scale)
        artist.setPen(QPen(QColor(BLUE), BLUE_WIDTH / self._scale, style))
        artist.setBrush(Qt.BrushStyle.NoBrush)
        artist.drawRect(blue_rect)
        artist.setPen(QPen(QColor("#ffffff"), BORDER_LOGICAL / self._scale,
                           Qt.PenStyle.SolidLine))
        artist.drawRect(rect)

    def _draw_handles(self, painter: QPainter, rect: QRectF) -> None:
        size = HANDLE_LOGICAL / self._scale
        painter.setPen(QPen(QColor(BLUE), 1.2 / self._scale))
        painter.setBrush(QColor("#ffffff"))
        for point in self.selection.handle_points().values():
            painter.drawRect(QRectF(point.x() - size / 2, point.y() - size / 2,
                                    size, size))

    def _draw_size_label(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont(painter.font())
        font.setPixelSize(max(1, round(13 / self._scale)))
        painter.setFont(font)
        text = f"{int(round(rect.width()))} × {int(round(rect.height()))}"
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 12 / self._scale
        height = metrics.height() + 5 / self._scale
        x = rect.right() - width
        y = rect.bottom() + 5 / self._scale
        scene_rect = self.session.scene.sceneRect()
        if y + height > scene_rect.bottom():
            y = rect.bottom() - height - 5 / self._scale
        box = QRectF(x, y, width, height)
        painter.setPen(QPen(QColor(BLUE), 1.0 / self._scale))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(box, 3 / self._scale, 3 / self._scale)
        painter.setPen(QColor("#1c1c1e"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_color_chip(self, painter: QPainter) -> None:
        hex_value = self.hover_hex()
        if self._hover_scene is None or hex_value is None:
            return
        base_pos = self.session.scene.base_item().pos()
        point = self._hover_scene
        scale = self._scale
        chip = QRectF(point.x() + 18 / scale, point.y() + 18 / scale,
                      86 / scale, 26 / scale)
        scene_rect = self.session.scene.sceneRect()
        if chip.right() > scene_rect.right():
            chip.moveRight(point.x() - 8 / scale)
        if chip.bottom() > scene_rect.bottom():
            chip.moveBottom(point.y() - 8 / scale)
        color = QColor(hex_value)
        painter.setPen(QPen(QColor("#ffffff"), 1 / scale))
        painter.setBrush(color)
        painter.drawRoundedRect(chip, 4 / scale, 4 / scale)
        luminance = (color.red() * 299 + color.green() * 587
                     + color.blue() * 114) / 1000
        font = QFont(painter.font())
        font.setPixelSize(max(1, round(12 / scale)))
        painter.setFont(font)
        painter.setPen(QColor("#000000" if luminance > 140 else "#ffffff"))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, hex_value)

        hint_font = QFont(painter.font())
        hint_font.setPixelSize(max(1, round(14 / scale)))
        painter.setFont(hint_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(scene_rect.left(), scene_rect.top() + 12 / scale,
                                scene_rect.width(), 28 / scale),
                         Qt.AlignmentFlag.AlignHCenter,
                         "点击复制颜色 · 按 C 复制 · Esc 取消")

    # ---------- 取色 ----------

    def hover_hex(self) -> str | None:
        if self._hover_scene is None:
            return None
        base = self.session.scene.base_image()
        pos = self.session.scene.base_item().pos()
        x = int(self._hover_scene.x() - pos.x())
        y = int(self._hover_scene.y() - pos.y())
        if not (0 <= x < base.width() and 0 <= y < base.height()):
            return None
        return QColor(base.pixel(x, y)).name().upper()

    def copy_hover_color(self) -> bool:
        hex_value = self.hover_hex()
        if hex_value is None:
            return False
        QGuiApplication.clipboard().setText(hex_value)
        self.colorPicked.emit(hex_value)
        self._finish()
        return True

    # ---------- 鼠标 ----------

    def _scene_pos(self, event) -> QPointF:
        return self.mapToScene(event.position().toPoint())

    def _clamped(self, point: QPointF) -> QPointF:
        rect = self.session.scene.sceneRect()
        return QPointF(min(max(point.x(), rect.left()), rect.right()),
                       min(max(point.y(), rect.top()), rect.bottom()))

    def _draw_clamped(self, point: QPointF) -> QPointF:
        """绘图点钳在选区内，避免标注跑到选区外（导出本来也只取选区）。"""
        if self.selection.has_selection():
            rect = self.selection.rect
            return QPointF(min(max(point.x(), rect.left()), rect.right()),
                           min(max(point.y(), rect.top()), rect.bottom()))
        return self._clamped(point)

    def mousePressEvent(self, event) -> None:
        if self._done:
            return
        if self._color_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                self._hover_scene = self._clamped(self._scene_pos(event))
                self.copy_hover_color()
                event.accept()
                return
            super().mousePressEvent(event)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self.session.scene.focusItem() is not None:
            self._commit_text()
        pos = self._clamped(self._scene_pos(event))
        view_pos = event.position().toPoint()

        if self.selection.has_selection():
            handle = self.selection.hit_handle(
                pos, HANDLE_HIT_LOGICAL / self._scale)
            if handle is not None:
                self._start_resize(handle, pos)
                event.accept()
                return
            if self.session.tool == TOOL_SELECT:
                item = self.itemAt(view_pos)
                if item is not None and item is not self.session.scene.base_item():
                    self._item_drag = True
                    super().mousePressEvent(event)
                    return
                if self.selection.contains(pos):
                    self._start_move(pos)
                    event.accept()
                    return
                self._start_new_selection(pos)
                event.accept()
                return
            if self.selection.contains(pos):
                self._begin_draw(self.session.tool, pos)
                event.accept()
                return
            self._start_new_selection(pos)
            event.accept()
            return
        self._start_new_selection(pos)
        event.accept()

    def _start_new_selection(self, pos: QPointF) -> None:
        self._drag_mode = "new"
        self._drag_start = pos
        self._pending_rect = QRectF(pos, pos)
        self.toolbar.hide()
        self.viewport().update()

    def _start_move(self, pos: QPointF) -> None:
        self._drag_mode = "move"
        self._drag_start = pos
        self._drag_rect = QRectF(self.selection.rect)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def _start_resize(self, handle: str, pos: QPointF) -> None:
        self._drag_mode = "resize"
        self._drag_handle = handle
        self._drag_start = pos
        self._drag_rect = QRectF(self.selection.rect)
        self.setCursor(self.selection.cursor_for_handle(handle))

    def mouseMoveEvent(self, event) -> None:
        if self._color_mode:
            self._hover_scene = self._clamped(self._scene_pos(event))
            self.viewport().update()
            return
        if self._item_drag:
            super().mouseMoveEvent(event)
            return
        pos = self._clamped(self._scene_pos(event))
        if self._drag_mode == "new":
            self._pending_rect = QRectF(self._drag_start, pos).normalized()
            self.viewport().update()
            event.accept()
            return
        if self._drag_mode == "move":
            delta = pos - self._drag_start
            self.selection.set_rect(self._drag_rect.translated(delta))
            self._after_geometry_change()
            event.accept()
            return
        if self._drag_mode == "resize":
            self.selection.resize(self._drag_handle, pos)
            self._after_geometry_change()
            event.accept()
            return
        if self._drag_mode == "draw":
            self._update_draw(self._draw_clamped(self._scene_pos(event)))
            event.accept()
            return
        self._update_cursor_idle(pos, event.position().toPoint())
        super().mouseMoveEvent(event)

    def _after_geometry_change(self) -> None:
        self._position_toolbar()
        self.viewport().update()

    def mouseReleaseEvent(self, event) -> None:
        if self._done:
            return
        if self._color_mode:
            super().mouseReleaseEvent(event)
            return
        if self._item_drag:
            super().mouseReleaseEvent(event)
            self._item_drag = False
            return
        if self._drag_mode == "new":
            pending = self._pending_rect
            self._drag_mode = None
            self._pending_rect = None
            if pending is not None:
                self.selection.set_rect(pending)
            self.toolbar.setVisible(self.selection.has_selection())
            self._position_toolbar()
            self.viewport().update()
            self._update_cursor_idle(self._scene_pos(event),
                                     event.position().toPoint())
            event.accept()
            return
        if self._drag_mode in ("move", "resize"):
            self._drag_mode = None
            self._drag_handle = None
            self._position_toolbar()
            self.viewport().update()
            event.accept()
            return
        if self._drag_mode == "draw":
            pos = self._draw_clamped(self._scene_pos(event))
            self._finish_draw(pos)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._color_mode or event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        if self.session.tool == TOOL_SELECT:
            item = self.itemAt(event.position().toPoint())
            if isinstance(item, TextItem):
                item.start_editing()
                event.accept()
                return
            pos = self._clamped(self._scene_pos(event))
            if self.selection.contains(pos):
                self.copy_to_clipboard()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_scene = None
        self.viewport().update()
        super().leaveEvent(event)

    def _update_cursor_idle(self, pos: QPointF | None = None,
                            view_pos: QPoint | None = None) -> None:
        if self._color_mode:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if not self.selection.has_selection():
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if pos is None:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        handle = self.selection.hit_handle(pos, HANDLE_HIT_LOGICAL / self._scale)
        if handle is not None:
            self.setCursor(self.selection.cursor_for_handle(handle))
            return
        if self.session.tool == TOOL_SELECT:
            if view_pos is not None:
                item = self.itemAt(view_pos)
                if item is not None and item is not self.session.scene.base_item():
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    return
            if self.selection.contains(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ---------- 绘制 ----------

    def _begin_draw(self, tool: str, pos: QPointF) -> None:
        if tool == TOOL_TEXT:
            item = self.session.place_text(pos)
            item.start_editing()
            return
        if tool == TOOL_NUMBER:
            self.session.place_number(pos)
            return
        self._drag_mode = "draw"
        self._drag_start = pos
        if tool in SHAPE_TOOLS:
            item = ShapeItem(tool, pos, pos, self.session.color,
                             self.session.pen_width)
            self.session.scene.addItem(item)
            self._preview = item
        elif tool in (TOOL_PEN, TOOL_MARKER):
            item = StrokeItem([pos], self.session.color,
                              self.session.pen_width, marker=tool == TOOL_MARKER)
            self.session.scene.addItem(item)
            self._stroke = item
            self._preview = item
        elif tool in (TOOL_MOSAIC, TOOL_BLUR, TOOL_CROP):
            rect_item = QGraphicsRectItem(QRectF(pos, pos))
            rect_item.setPen(QPen(QColor("#ffffff"), 1 / self._scale,
                                  Qt.PenStyle.DashLine))
            rect_item.setBrush(Qt.BrushStyle.NoBrush)
            rect_item.setZValue(10)
            self.session.scene.addItem(rect_item)
            self._preview = rect_item

    def _update_draw(self, pos: QPointF) -> None:
        if isinstance(self._preview, ShapeItem):
            self._preview.set_end(pos)
        elif self._stroke is not None:
            self._stroke.add_point(pos)
        elif isinstance(self._preview, QGraphicsRectItem):
            self._preview.setRect(QRectF(self._drag_start, pos).normalized())
        self.viewport().update()

    def _finish_draw(self, pos: QPointF) -> None:
        tool = self.session.tool
        preview = self._preview
        self._preview = None
        self._drag_mode = None
        if isinstance(preview, ShapeItem):
            preview.set_end(pos)
            if (abs(pos.x() - self._drag_start.x()) < 3
                    and abs(pos.y() - self._drag_start.y()) < 3):
                self.session.scene.removeItem(preview)
            else:
                self.session.commit_item(preview)
        elif isinstance(preview, StrokeItem):
            if preview.point_count() < 2:
                self.session.scene.removeItem(preview)
            else:
                self.session.commit_item(preview)
        elif isinstance(preview, QGraphicsRectItem):
            rect = preview.rect()
            self.session.scene.removeItem(preview)
            if rect.width() >= 4 and rect.height() >= 4:
                if tool == TOOL_CROP:
                    if self.session.apply_crop(rect):
                        self.selection.bounds = QRectF(
                            self.session.scene.sceneRect())
                        self.selection.set_rect(self.session.scene.sceneRect())
                        self._apply_fit()
                else:
                    self.session.apply_region_effect(tool, rect)
        self._stroke = None
        self._position_toolbar()
        self.viewport().update()
        self._update_cursor_idle(pos)

    def _cancel_preview(self) -> None:
        if self._preview is not None:
            self.session.scene.removeItem(self._preview)
        self._preview = None
        self._stroke = None
        self._drag_mode = None
        self.viewport().update()

    # ---------- 键盘 ----------

    def keyPressEvent(self, event) -> None:
        if self._color_mode:
            if event.key() == Qt.Key.Key_Escape:
                self._finish()
                return
            if event.key() == Qt.Key.Key_C:
                self.copy_hover_color()
                return
            super().keyPressEvent(event)
            return

        if self.session.scene.focusItem() is not None:
            if event.key() == Qt.Key.Key_Escape:
                self._commit_text()
                event.accept()
                return
            super().keyPressEvent(event)
            return

        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Escape:
            if self._drag_mode == "draw":
                self._cancel_preview()
            else:
                self._finish()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ctrl:
            self.copy_to_clipboard()
            return
        if ctrl and key == Qt.Key.Key_C:
            self.copy_to_clipboard()
            return
        if ctrl and key == Qt.Key.Key_S:
            self.save_to_file()
            return
        if ctrl and key == Qt.Key.Key_Z and not (mods & Qt.KeyboardModifier.ShiftModifier):
            self.undo()
            return
        if (ctrl and key == Qt.Key.Key_Y) or (
                ctrl and key == Qt.Key.Key_Z
                and mods & Qt.KeyboardModifier.ShiftModifier):
            self.redo()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.session.delete_selected()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                   Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if mods & Qt.KeyboardModifier.ShiftModifier else 1
            dx = -step if key == Qt.Key.Key_Left else step if key == Qt.Key.Key_Right else 0
            dy = -step if key == Qt.Key.Key_Up else step if key == Qt.Key.Key_Down else 0
            self.selection.nudge(dx, dy)
            self._after_geometry_change()
            return
        quick = {
            Qt.Key.Key_V: TOOL_SELECT, Qt.Key.Key_R: TOOL_RECT,
            Qt.Key.Key_O: TOOL_ELLIPSE, Qt.Key.Key_A: TOOL_ARROW,
            Qt.Key.Key_P: TOOL_PEN, Qt.Key.Key_M: TOOL_MARKER,
            Qt.Key.Key_T: TOOL_TEXT, Qt.Key.Key_N: TOOL_NUMBER,
            Qt.Key.Key_G: TOOL_MOSAIC, Qt.Key.Key_B: TOOL_BLUR,
            Qt.Key.Key_C: TOOL_CROP,
        }
        if not ctrl and key in quick:
            self.set_tool(quick[key])
            return
        super().keyPressEvent(event)

    def _commit_text(self) -> None:
        focus = self.session.scene.focusItem()
        if isinstance(focus, TextItem):
            focus.stop_editing()

    # ---------- 工具/工具栏 ----------

    def _wire(self) -> None:
        self.toolbar.toolChanged.connect(self.set_tool)
        self.toolbar.colorChanged.connect(self.session.set_color)
        self.toolbar.widthChanged.connect(self.session.set_width)
        self.toolbar.undoRequested.connect(self.undo)
        self.toolbar.redoRequested.connect(self.redo)
        self.toolbar.copyRequested.connect(self.copy_to_clipboard)
        self.toolbar.saveRequested.connect(self.save_to_file)
        self.toolbar.pinRequested.connect(self.pin_to_screen)
        self.toolbar.ocrRequested.connect(self.run_ocr)
        self.toolbar.closeRequested.connect(self._finish)
        self.session._on_undo_changed = self._sync_undo
        self._sync_undo(False, False)

    def _sync_undo(self, can_undo: bool, can_redo: bool) -> None:
        self.toolbar.set_undo_enabled(bool(can_undo))
        self.toolbar.set_redo_enabled(bool(can_redo))

    def set_tool(self, tool: str) -> None:
        if self._drag_mode == "draw":
            self._cancel_preview()
        self._commit_text()
        self.session.set_tool(tool)
        self.toolbar.set_tool(tool)
        self._update_cursor_idle()

    def undo(self) -> bool:
        self._commit_text()
        changed = self.session.undo()
        if changed:
            self._after_scene_change()
        return changed

    def redo(self) -> bool:
        self._commit_text()
        changed = self.session.redo()
        if changed:
            self._after_scene_change()
        return changed

    def _after_scene_change(self) -> None:
        """裁剪/撤销可能改变 sceneRect：更新选区边界并重新适配视图。"""
        self.selection.bounds = QRectF(self.session.scene.sceneRect())
        if self.selection.has_selection() and not self.selection.rect.intersects(
                self.selection.bounds):
            self.selection.set_rect(self.selection.bounds)
        self._apply_fit()
        self._sync_toolbar_visibility()

    def _position_toolbar(self) -> None:
        if self._color_mode or not self.selection.has_selection():
            self.toolbar.hide()
            return
        sel = self.selection.rect
        self.toolbar.set_size_text(
            f"{int(round(sel.width()))} × {int(round(sel.height()))}")
        top_left = self.mapFromScene(sel.topLeft())
        bottom_right = self.mapFromScene(sel.bottomRight())
        view_rect = QRect(top_left, bottom_right).normalized()
        self.toolbar.adjustSize()
        width = max(self.toolbar.sizeHint().width(), self.toolbar.width())
        height = max(self.toolbar.sizeHint().height(), self.toolbar.height())
        viewport = self.viewport().size()
        x = view_rect.center().x() - width // 2
        x = max(4, min(x, viewport.width() - width - 4))
        y = view_rect.bottom() + 8
        if y + height > viewport.height() - 4:
            y = view_rect.top() - height - 8
        if y < 4:
            y = max(4, viewport.height() - height - 4)
        self.toolbar.move(x, y)
        self.toolbar.show()
        self.toolbar.raise_()

    def _sync_toolbar_visibility(self) -> None:
        self.toolbar.setVisible(not self._color_mode
                                and self.selection.has_selection())
        self._position_toolbar()

    # ---------- 动作 ----------

    def copy_to_clipboard(self) -> bool:
        if self._done or not self.selection.has_selection():
            return False
        image = self.session.render_region(self.selection.rect)
        if image.isNull():
            return False
        QGuiApplication.clipboard().setImage(image)
        self.copied.emit(image)
        self._finish()
        return True

    def save_to_file(self, path: str | None = None) -> bool:
        if self._done or not self.selection.has_selection():
            return False
        if path is None:
            name = time.strftime("墨页-%Y%m%d-%H%M%S.png")
            path, _selected = QFileDialog.getSaveFileName(
                self, "保存截图", str(Path.home() / "Pictures" / name),
                "PNG 图片 (*.png);;JPEG 图片 (*.jpg);;WebP 图片 (*.webp)")
            if not path:
                return False
        image = self.session.render_region(self.selection.rect)
        if not imageops.save_image(image, path):
            QMessageBox.warning(self, "保存截图", f"保存失败：{path}")
            return False
        self.saved.emit(image, path)
        self._finish()
        return True

    def pin_to_screen(self) -> bool:
        if self._done or not self.selection.has_selection():
            return False
        image = self.session.render_region(self.selection.rect)
        if image.isNull():
            return False
        self.pinned.emit(image)
        self._finish()
        return True

    def run_ocr(self) -> bool:
        """OCR 放到后台线程：这里只渲染选区并启动 worker，立即返回。"""
        if self._done or not self.selection.has_selection():
            return False
        if not self._ocr_enabled:
            QMessageBox.information(self, "OCR", "OCR 只在标准版可用。")
            return False
        if self._ocr_func is None:
            QMessageBox.warning(self, "OCR", "OCR 组件未就绪。")
            return False
        if self._ocr_busy:
            return False
        image = self.session.render_region(self.selection.rect)
        if image.isNull():
            return False
        self._set_ocr_busy(True)
        self._ocr_worker = start_ocr(image, self._ocr_func,
                                     self._on_ocr_done, self._on_ocr_failed)
        return True

    def ocr_busy(self) -> bool:
        return self._ocr_busy

    def _set_ocr_busy(self, busy: bool) -> None:
        self._ocr_busy = bool(busy)
        self.toolbar.set_ocr_busy(self._ocr_busy)
        self._position_toolbar()

    def _on_ocr_done(self, text: str) -> None:
        self._set_ocr_busy(False)
        text = (text or "").strip()
        if self._done:
            return
        if not text:
            QMessageBox.information(self, "OCR", "没有识别到文字。")
            return
        OCRResultDialog(text, self).exec()

    def _on_ocr_failed(self, message: str) -> None:
        self._set_ocr_busy(False)
        if self._done:
            return
        QMessageBox.warning(self, "OCR", f"识别失败：{message}")

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self.hide()
        self.close()

    def _emit_closed(self) -> None:
        if not self._closed_emitted:
            self._closed_emitted = True
            self.closed.emit()

    def closeEvent(self, event) -> None:
        self._emit_closed()
        super().closeEvent(event)
