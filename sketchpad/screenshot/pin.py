"""贴图窗口：把截图钉在屏幕最上层。

- 左键拖动移动，右下角拖拽缩放，滚轮缩放；
- 右键：复制 / 另存 / 旋转 90° / 透明度 / 关闭；
- 双击关闭（PixPin 手感）；不抢焦点，支持同时贴多张。
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen, QTransform
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox, QWidget

from sketchpad.screenshot import imageops

GRIP = 16                    # 右下角缩放热区
MIN_SCALE = 0.1
MAX_SCALE = 8.0


class PinWindow(QWidget):
    closed = Signal(object)

    def __init__(self, image: QImage, position: QPoint | None = None,
                 parent=None) -> None:
        super().__init__(None)
        self.setWindowTitle("贴图")
        self.setWindowFlags(Qt.WindowType.Tool
                            | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._image = imageops.normalize(image)
        self._scale = 1.0
        self._opacity = 1.0
        self._dragging = False
        self._drag_offset = QPoint()
        self._resizing = False
        self._resize_anchor = QPoint()
        self._resize_scale = 1.0
        self._apply_size()
        self.move(self._default_position(position))
        self.show()

    # ---------- 尺寸/位置 ----------

    def _apply_size(self) -> None:
        width = max(16, round(self._image.width() * self._scale))
        height = max(16, round(self._image.height() * self._scale))
        self.setFixedSize(QSize(width, height))
        self.setWindowOpacity(self._opacity)
        self.update()

    def _default_position(self, position: QPoint | None) -> QPoint:
        screen = (QGuiApplication.screenAt(position or QCursor.pos())
                  or QGuiApplication.primaryScreen())
        avail = (screen.availableGeometry() if screen is not None
                 else QRect(0, 0, 1920, 1080))
        base = position or QCursor.pos()
        x = min(max(avail.left(), base.x() + 12), avail.right() - self.width())
        y = min(max(avail.top(), base.y() + 12), avail.bottom() - self.height())
        return QPoint(x, y)

    def image(self) -> QImage:
        return self._image

    def scale(self) -> float:
        return self._scale

    def set_scale(self, scale: float) -> None:
        self._scale = max(MIN_SCALE, min(MAX_SCALE, float(scale)))
        self._apply_size()

    def set_opacity_percent(self, percent: int) -> None:
        self._opacity = max(0.1, min(1.0, int(percent) / 100.0))
        self.setWindowOpacity(self._opacity)

    def opacity_percent(self) -> int:
        return round(self._opacity * 100)

    def rotate(self) -> None:
        self._image = self._image.transformed(QTransform().rotate(90))
        self._apply_size()

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(QRect(0, 0, self.width(), self.height()), self._image)
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        # 右下角两条短线当缩放把手
        painter.setPen(QPen(QColor(255, 255, 255, 160), 2))
        painter.drawLine(self.width() - 10, self.height() - 2,
                         self.width() - 2, self.height() - 10)
        painter.drawLine(self.width() - 15, self.height() - 2,
                         self.width() - 2, self.height() - 15)
        painter.end()

    # ---------- 鼠标 ----------

    def _in_grip(self, pos: QPoint) -> bool:
        return (pos.x() >= self.width() - GRIP
                and pos.y() >= self.height() - GRIP)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._in_grip(event.position().toPoint()):
                self._resizing = True
                self._resize_anchor = event.globalPosition().toPoint()
                self._resize_scale = self._scale
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self._dragging = True
                self._drag_offset = (event.globalPosition().toPoint()
                                     - self.frameGeometry().topLeft())
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._show_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_anchor
            grow = delta.x() + delta.y()
            base = max(1.0, min(self._image.width(), self._image.height()))
            self.set_scale(self._resize_scale + grow / base)
            event.accept()
            return
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        if self._in_grip(event.position().toPoint()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        self._resizing = False
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.close()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        step = 1.1 if event.angleDelta().y() > 0 else 0.9
        self.set_scale(self._scale * step)
        event.accept()

    # ---------- 菜单 ----------

    def _show_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        copy_action = menu.addAction("复制图片")
        save_action = menu.addAction("另存为…")
        menu.addSeparator()
        rotate_action = menu.addAction("旋转 90°")
        opacity_menu = menu.addMenu("不透明度")
        opacity_actions = []
        for percent in (100, 80, 60, 40):
            action = opacity_menu.addAction(f"{percent}%")
            action.setCheckable(True)
            action.setChecked(self.opacity_percent() == percent)
            action.triggered.connect(
                lambda _checked=False, p=percent: self.set_opacity_percent(p))
            opacity_actions.append(action)
        menu.addSeparator()
        close_action = menu.addAction("关闭")
        chosen = menu.exec(global_pos)
        if chosen is copy_action:
            self.copy_to_clipboard()
        elif chosen is save_action:
            self.save_to_file()
        elif chosen is rotate_action:
            self.rotate()
        elif chosen is close_action:
            self.close()

    def copy_to_clipboard(self) -> None:
        QGuiApplication.clipboard().setImage(self._image)

    def save_to_file(self, path: str | None = None) -> bool:
        if path is None:
            name = time.strftime("墨页-pin-%Y%m%d-%H%M%S.png")
            path, _selected = QFileDialog.getSaveFileName(
                self, "保存贴图", str(Path.home() / "Pictures" / name),
                "PNG 图片 (*.png);;JPEG 图片 (*.jpg)")
            if not path:
                return False
        if not imageops.save_image(self._image, path):
            QMessageBox.warning(self, "保存贴图", f"保存失败：{path}")
            return False
        return True

    def closeEvent(self, event) -> None:
        self.closed.emit(self)
        super().closeEvent(event)
