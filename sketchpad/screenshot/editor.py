"""旧版独立截图编辑器（保留给历史测试/兼容，生产已改用全屏 `overlay.py`）。

生产截图流程：`ScreenshotOverlay` 在冻结屏幕上直接框选/标注；本模块的
`ScreenshotEditor` 是一个独立小窗口，不再被控制器打开。`OCRResultDialog`
仍被 overlay 复用。
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QImage, QPainter,
                           QPen)
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QGraphicsItem,
                               QGraphicsRectItem, QGraphicsView, QHBoxLayout,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from sketchpad.screenshot import imageops
from sketchpad.screenshot.constants import (DEFAULT_COLOR, DEFAULT_WIDTH,
                                            SHAPE_TOOLS, TEXT_SIZE,
                                            TOOL_ARROW, TOOL_BLUR, TOOL_CROP,
                                            TOOL_ELLIPSE, TOOL_MARKER,
                                            TOOL_MOSAIC, TOOL_NUMBER, TOOL_PEN,
                                            TOOL_RECT, TOOL_SELECT, TOOL_TEXT)
from sketchpad.screenshot.items import (NumberItem, RegionEffectItem, ShapeItem,
                                        StrokeItem, TextItem)
from sketchpad.screenshot.scene import EditorScene
from sketchpad.screenshot.toolbar import EditorToolbar

EDITOR_QSS = """
QWidget#ScreenshotEditor { background: #202024; }
QGraphicsView { border: none; background: #141416; }
QLabel { color: #9a9aa0; }
"""


class EditorView(QGraphicsView):
    """画布：绘制工具由它收鼠标，指针工具交给 QGraphicsView 原生选择/拖动。"""

    def __init__(self, editor: "ScreenshotEditor") -> None:
        super().__init__(editor)
        self.editor = editor
        self.setObjectName("ScreenshotCanvas")
        self.setScene(editor.scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.TextAntialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._drawing = False
        self._start = QPointF()
        self._preview = None
        self._stroke: StrokeItem | None = None

    # ---------- 坐标 ----------

    def _scene_pos(self, event) -> QPointF:
        return self.mapToScene(event.position().toPoint())

    def _clamped(self, point: QPointF) -> QPointF:
        rect = self.scene().sceneRect()
        return QPointF(min(max(point.x(), rect.left()), rect.right()),
                       min(max(point.y(), rect.top()), rect.bottom()))

    # ---------- 鼠标 ----------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        tool = self.editor.tool
        if tool == TOOL_SELECT:
            super().mousePressEvent(event)
            return
        if self.editor.is_editing_text():
            self.editor.commit_text()
        pos = self._clamped(self._scene_pos(event))
        if tool == TOOL_TEXT:
            self.editor.place_text(pos)
            event.accept()
            return
        if tool == TOOL_NUMBER:
            self.editor.place_number(pos)
            event.accept()
            return
        self._begin_draw(tool, pos)
        event.accept()

    def _begin_draw(self, tool: str, pos: QPointF) -> None:
        self._drawing = True
        self._start = pos
        if tool in SHAPE_TOOLS:
            item = ShapeItem(tool, pos, pos, self.editor.color, self.editor.pen_width)
            self.scene().addItem(item)
            self._preview = item
        elif tool in (TOOL_PEN, TOOL_MARKER):
            item = StrokeItem([pos], self.editor.color, self.editor.pen_width,
                              marker=tool == TOOL_MARKER)
            self.scene().addItem(item)
            self._stroke = item
            self._preview = item
        elif tool in (TOOL_MOSAIC, TOOL_BLUR, TOOL_CROP):
            rect_item = QGraphicsRectItem(QRectF(pos, pos))
            rect_item.setPen(QPen(QColor("#ffffff"), 1, Qt.PenStyle.DashLine))
            rect_item.setBrush(Qt.BrushStyle.NoBrush)
            rect_item.setZValue(10)
            self.scene().addItem(rect_item)
            self._preview = rect_item

    def mouseMoveEvent(self, event) -> None:
        if not self._drawing:
            super().mouseMoveEvent(event)
            return
        pos = self._clamped(self._scene_pos(event))
        tool = self.editor.tool
        if tool in SHAPE_TOOLS and isinstance(self._preview, ShapeItem):
            self._preview.set_end(pos)
        elif self._stroke is not None:
            self._stroke.add_point(pos)
        elif isinstance(self._preview, QGraphicsRectItem):
            self._preview.setRect(QRectF(self._start, pos).normalized())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if not self._drawing or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self._drawing = False
        pos = self._clamped(self._scene_pos(event))
        tool = self.editor.tool
        preview = self._preview
        self._preview = None
        if isinstance(preview, ShapeItem):
            preview.set_end(pos)
            if (abs(pos.x() - self._start.x()) < 3
                    and abs(pos.y() - self._start.y()) < 3):
                self.scene().removeItem(preview)
            else:
                self.editor.commit_item(preview)
        elif isinstance(preview, StrokeItem):
            if preview.point_count() < 2:
                self.scene().removeItem(preview)
            else:
                self.editor.commit_item(preview)
        elif isinstance(preview, QGraphicsRectItem):
            rect = preview.rect()
            self.scene().removeItem(preview)
            if rect.width() >= 4 and rect.height() >= 4:
                if tool == TOOL_CROP:
                    self.editor.apply_crop(rect)
                else:
                    self.editor.apply_region_effect(tool, rect)
        self._stroke = None
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.editor.tool == TOOL_SELECT:
            item = self.itemAt(event.position().toPoint())
            if isinstance(item, TextItem):
                item.start_editing()
                event.accept()
                return
            if item is None or item is self.editor.scene.base_item():
                self.editor.copy_to_clipboard()   # 双击空白=复制（PixPin 手感）
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    # ---------- 键盘 ----------

    def keyPressEvent(self, event) -> None:
        if self.editor.scene.focusItem() is not None:
            # 文字输入中：Esc 结束编辑，其余按键（含 Ctrl+Enter）归文本框
            if event.key() == Qt.Key.Key_Escape:
                self.editor.commit_text()
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if self.editor.handle_key(event):
            event.accept()
            return
        super().keyPressEvent(event)


class OCRResultDialog(QDialog):
    """OCR 结果：只读文本 + 全选/复制；不自动替换截图。"""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OCR 识别结果")
        self.resize(560, 420)
        self.setStyleSheet("QDialog { background: #202024; } QPlainTextEdit {"
                           " background: #141416; color: #e8eaed; border: 1px solid"
                           " #3a3a3e; } QPushButton { color: #e8eaed; background:"
                           " #3a3a3c; border: none; padding: 5px 14px; border-radius:"
                           " 5px; }")
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(text, self)
        self.text.setReadOnly(True)
        layout.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch(1)
        copy_button = QPushButton("复制全部", self)
        copy_button.clicked.connect(self.copy_all)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.accept)
        row.addWidget(copy_button)
        row.addWidget(close_button)
        layout.addLayout(row)

    def copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())


class ScreenshotEditor(QWidget):
    copied = Signal(QImage)
    saved = Signal(QImage, str)
    pinned = Signal(QImage)
    finished = Signal(QImage)          # 任何方式关闭都会发一次（用于历史）
    closed = Signal()

    def __init__(self, image: QImage, ocr_enabled: bool = False,
                 ocr_func=None, parent=None) -> None:
        super().__init__(None)
        self.setObjectName("ScreenshotEditor")
        self.setWindowTitle("截图编辑")
        self.setWindowFlags(Qt.WindowType.Tool
                            | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(EDITOR_QSS)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._done = False
        self._tool = TOOL_SELECT
        self._color = DEFAULT_COLOR
        self._width = DEFAULT_WIDTH
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._number = 0
        self._scale = 1.0
        self._ocr_enabled = bool(ocr_enabled)
        self._ocr_func = ocr_func
        self._positioned = False

        self.scene = EditorScene(imageops.normalize(image), self)
        self.view = EditorView(self)
        self.toolbar = EditorToolbar(ocr_enabled=self._ocr_enabled, parent=self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.view, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._wire()
        self._apply_sizing()
        self._place_window()

    # ---------- 接线 ----------

    def _wire(self) -> None:
        self.toolbar.toolChanged.connect(self.set_tool)
        self.toolbar.colorChanged.connect(self.set_color)
        self.toolbar.widthChanged.connect(self.set_width)
        self.toolbar.undoRequested.connect(self.undo)
        self.toolbar.redoRequested.connect(self.redo)
        self.toolbar.copyRequested.connect(self.copy_to_clipboard)
        self.toolbar.saveRequested.connect(self.save_to_file)
        self.toolbar.pinRequested.connect(self.pin_to_screen)
        self.toolbar.ocrRequested.connect(self.run_ocr)
        self.toolbar.closeRequested.connect(self.cancel)

    # ---------- 状态 ----------

    @property
    def tool(self) -> str:
        return self._tool

    @property
    def color(self) -> str:
        return self._color

    @property
    def pen_width(self) -> int:
        return self._width

    def set_tool(self, tool: str) -> None:
        self.commit_text()
        self._tool = tool
        select = tool == TOOL_SELECT
        self.view.setDragMode(
            QGraphicsView.DragMode.RubberBandDrag if select
            else QGraphicsView.DragMode.NoDrag)
        buttons = (Qt.MouseButton.LeftButton if select
                   else Qt.MouseButton.NoButton)
        for item in self.scene.annotation_items():
            item.setAcceptedMouseButtons(buttons)
        self.toolbar.set_tool(tool)

    def set_color(self, color: str) -> None:
        self._color = color
        for item in self.scene.selectedItems():
            if isinstance(item, TextItem):
                item.setDefaultTextColor(QColor(color))
                item.update()
            elif hasattr(item, "color"):
                item.color = QColor(color)
                item.update()

    def set_width(self, width: int) -> None:
        self._width = int(width)
        for item in self.scene.selectedItems():
            if isinstance(item, TextItem):
                font = item.font()
                font.setPixelSize(max(8, round(TEXT_SIZE * width / DEFAULT_WIDTH)))
                item.setFont(font)
            elif hasattr(item, "width") and not callable(getattr(item, "width")):
                item.prepareGeometryChange()
                item.width = int(width) * (2 if getattr(item, "marker", False) else 1)
                item.update()

    def size_text(self) -> str:
        rect = self.scene.sceneRect()
        return f"{int(rect.width())} × {int(rect.height())}"

    # ---------- 图元 ----------

    def commit_item(self, item) -> None:
        command = (lambda: self.scene.removeItem(item),
                   lambda: self.scene.addItem(item))
        item._undo_command = command
        self._push(*command)
        self._bind_mouse(item)

    def _bind_mouse(self, item) -> None:
        item.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton if self._tool == TOOL_SELECT
            else Qt.MouseButton.NoButton)
        if isinstance(item, TextItem) and self._tool == TOOL_SELECT:
            item.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def place_text(self, pos: QPointF) -> TextItem:
        item = TextItem("", self._color, TEXT_SIZE,
                        on_finish=self._text_finished)
        item.setPos(pos)
        self.scene.addItem(item)
        self.commit_item(item)
        item.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        item.start_editing()
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        return item

    def _text_finished(self, item: TextItem) -> None:
        if not item.toPlainText().strip():
            self.scene.removeItem(item)      # 空文本不留在图上
            # 顺手撤掉它的“加入”记录，否则重做会加回一个看不见的空文本
            command = getattr(item, "_undo_command", None)
            if command is not None and self._undo and self._undo[-1] is command:
                self._undo.pop()
                self._redo.clear()
        else:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self._sync_undo_buttons()

    def is_editing_text(self) -> bool:
        return isinstance(self.scene.focusItem(), TextItem)

    def commit_text(self) -> None:
        focus = self.scene.focusItem()
        if isinstance(focus, TextItem):
            focus.stop_editing()

    def place_number(self, pos: QPointF) -> NumberItem:
        self._number += 1
        item = NumberItem(self._number, pos, self._color)
        self.scene.addItem(item)
        self.commit_item(item)
        return item

    def apply_region_effect(self, tool: str, rect: QRectF) -> None:
        rect_i = rect.toAlignedRect().intersected(
            self.scene.sceneRect().toAlignedRect())
        if rect_i.width() < 4 or rect_i.height() < 4:
            return
        base = self.scene.base_image()
        image = (imageops.pixelate(base, rect_i) if tool == TOOL_MOSAIC
                 else imageops.blur_region(base, rect_i))
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
        cropped = imageops.crop(base, rect)
        if cropped.isNull():
            return
        self.scene.set_base(cropped)
        offset = QPointF(rect.topLeft())
        for item in self.scene.annotation_items():
            item.setPos(item.pos() - offset)
        self._apply_sizing()
        self._place_window()

    def _snapshot(self) -> tuple[QImage, list[tuple]]:
        return (self.scene.base_image().copy(),
                [(item, QPointF(item.pos())) for item in self.scene.annotation_items()])

    def _restore_snapshot(self, snapshot: tuple[QImage, list[tuple]]) -> None:
        image, positions = snapshot
        self.scene.set_base(image.copy())
        for item, pos in positions:
            item.setPos(pos)
        self._apply_sizing()
        self._place_window()

    # ---------- 撤销 ----------

    def _push(self, undo, redo) -> None:
        self._undo.append((undo, redo))
        self._redo.clear()
        self._sync_undo_buttons()

    def _sync_undo_buttons(self) -> None:
        self.toolbar.set_undo_enabled(bool(self._undo))
        self.toolbar.set_redo_enabled(bool(self._redo))

    def undo(self) -> bool:
        if not self._undo:
            return False
        undo, redo = self._undo.pop()
        undo()
        self._redo.append((undo, redo))
        self._sync_undo_buttons()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        undo, redo = self._redo.pop()
        redo()
        self._undo.append((undo, redo))
        self._sync_undo_buttons()
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

    def render_output(self) -> QImage:
        self.commit_text()
        return self.scene.render_output()

    def copy_to_clipboard(self) -> bool:
        image = self.render_output()
        QGuiApplication.clipboard().setImage(image)
        self.copied.emit(image)
        self._finish(image)
        return True

    def save_to_file(self, path: str | None = None) -> bool:
        image = self.render_output()
        if path is None:
            name = time.strftime("墨页-%Y%m%d-%H%M%S.png")
            path, _selected = QFileDialog.getSaveFileName(
                self, "保存截图", str(Path.home() / "Pictures" / name),
                "PNG 图片 (*.png);;JPEG 图片 (*.jpg);;WebP 图片 (*.webp)")
            if not path:
                return False
        if not imageops.save_image(image, path):
            QMessageBox.warning(self, "保存截图", f"保存失败：{path}")
            return False
        self.saved.emit(image, path)
        self._finish(image)
        return True

    def pin_to_screen(self) -> bool:
        image = self.render_output()
        self.pinned.emit(image)
        self._finish(image)
        return True

    def run_ocr(self) -> bool:
        if not self._ocr_enabled:
            QMessageBox.information(self, "OCR", "OCR 只在标准版可用。")
            return False
        if self._ocr_func is None:
            QMessageBox.warning(self, "OCR", "OCR 组件未就绪。")
            return False
        image = self.render_output()
        try:
            text = self._ocr_func(image)
        except Exception as exc:                 # 识别引擎的异常统一提示
            QMessageBox.warning(self, "OCR", f"识别失败：{exc}")
            return False
        text = (text or "").strip()
        if not text:
            QMessageBox.information(self, "OCR", "没有识别到文字。")
            return False
        OCRResultDialog(text, self).exec()
        return True

    # ---------- 键盘 ----------

    def handle_key(self, event) -> bool:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Escape:
            self.cancel()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ctrl:
            self.copy_to_clipboard()
            return True
        if ctrl and key == Qt.Key.Key_C:
            self.copy_to_clipboard()
            return True
        if ctrl and key == Qt.Key.Key_S:
            self.save_to_file()
            return True
        if ctrl and key == Qt.Key.Key_Z and not (mods & Qt.KeyboardModifier.ShiftModifier):
            self.undo()
            return True
        if (ctrl and key == Qt.Key.Key_Y) or (ctrl and key == Qt.Key.Key_Z
                                              and mods & Qt.KeyboardModifier.ShiftModifier):
            self.redo()
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return True
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
            return True
        return False

    # ---------- 窗口尺寸 ----------

    def _apply_sizing(self) -> None:
        rect = self.scene.sceneRect()
        width = max(1, int(rect.width()))
        height = max(1, int(rect.height()))
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            scale = 1.0
            avail = QRect(0, 0, 1920, 1080)
        else:
            avail = screen.availableGeometry()
            scale = min(1.0, (avail.width() - 48) / width,
                        (avail.height() - 120) / height)
        self._scale = max(0.12, scale)
        view_w = max(80, round(width * self._scale))
        view_h = max(60, round(height * self._scale))
        self.view.setFixedSize(view_w, view_h)
        self.view.resetTransform()
        self.view.scale(self._scale, self._scale)
        self.view.setSceneRect(QRectF(rect))
        self.toolbar.set_size_text(f"{width} × {height}")
        self.toolbar.adjustSize()
        window_w = max(view_w, self.toolbar.sizeHint().width())
        self.setFixedSize(window_w, view_h + self.toolbar.sizeHint().height())

    def _place_window(self) -> None:
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        avail = (screen.availableGeometry() if screen is not None
                 else QRect(0, 0, 1920, 1080))
        if self._positioned:
            pos = self.pos()
        else:
            cursor = QCursor.pos()
            pos = QPoint(cursor.x() + 16, cursor.y() + 16)
        x = min(max(avail.left() + 8, pos.x()),
                avail.right() - self.width() - 8)
        y = min(max(avail.top() + 8, pos.y()),
                avail.bottom() - self.height() - 8)
        self.move(x, y)
        self._positioned = True

    # ---------- 结束 ----------

    def cancel(self) -> None:
        self._finish(self.render_output())

    def _finish(self, image: QImage) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(image)
        self.closed.emit()
        self.hide()
        self.deleteLater()

    def closeEvent(self, event) -> None:
        if not self._done:
            self._done = True
            self.finished.emit(self.render_output())
            self.closed.emit()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
