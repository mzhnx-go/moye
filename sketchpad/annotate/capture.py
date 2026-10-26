"""全屏截图框选：抓整屏做背景，拖出选区后交给截图编辑器继续标注。

为什么单独一个窗口：批注层自己是全屏的，截图时必须把它藏掉再抓屏，
否则会把自己的工具栏一起截进去；框选阶段又需要一个能收键盘（Esc 取消）
的全屏窗口，而批注层在穿透模式下整层点穿、收不到事件。

悬停时显示鼠标下像素的颜色，按 C 复制 HEX（PixPin 式取色）。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

MIN_SELECTION = 4          # 小于这个尺寸当成误触，直接取消


class CaptureOverlay(QWidget):
    """全屏截图遮罩；`finished` 交出裁剪好的图，`cancelled` 表示用户放弃。

    松手即发 `finished`：调用方打开截图编辑器，不再在这里复制/保存。
    """

    finished = Signal(QImage)
    cancelled = Signal()
    colorPicked = Signal(str)

    def __init__(self, shot: QImage, geometry, parent=None,
                 color_mode: bool = False) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)   # 要收键盘，得激活
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setWindowTitle("屏幕取色" if color_mode else "选择截图区域")
        self._color_mode = bool(color_mode)
        self._shot = shot
        self._origin = geometry.topLeft()      # 窗口左上角的屏幕坐标
        self.setGeometry(QRect(geometry.topLeft(), geometry.size()))
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._hover: QPoint | None = None
        self._sent = False

    # ---------- 选区 ----------

    def selection(self) -> QRect | None:
        """当前选框（窗口坐标，不含右下边界那一个像素）；没开始拖就返回 None。"""
        if self._start is None or self._end is None:
            return None
        # 刻意不用 QRect(start, end)：那个构造把右下角算成"包含"，宽高会多 1 像素
        dx = self._end.x() - self._start.x()
        dy = self._end.y() - self._start.y()
        return QRect(self._start.x(), self._start.y(), dx, dy).normalized()

    def _image_rect(self, selection: QRect) -> QRect:
        """窗口坐标 → 截图图像坐标：按图像/窗口比例缩放。

        `QScreen.grabWindow(0)` 给的是这块屏的物理像素图（150%/200% 时比
        geometry 大），不能直接把逻辑坐标当像素用，也不能加屏幕原点
        （图像本身从 0 开始）。
        """
        sx = self._shot.width() / max(1, self.width())
        sy = self._shot.height() / max(1, self.height())
        return QRect(round(selection.x() * sx), round(selection.y() * sy),
                     max(1, round(selection.width() * sx)),
                     max(1, round(selection.height() * sy)))

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        target = self.rect()
        painter.drawImage(target, self._shot)
        # 压暗整屏，选区留原样——一眼看出"要的就是这块"
        painter.fillRect(target, QColor(0, 0, 0, 110))

        selection = self.selection()
        if selection is not None and selection.width() > 1 and selection.height() > 1:
            painter.drawImage(selection, self._shot,
                              self._image_rect(selection))
            painter.setPen(QPen(QColor("#ffffff"), 1, Qt.SolidLine))
            painter.drawRect(selection)
            label = f"{selection.width()} × {selection.height()}"
            painter.setPen(QPen(QColor("#ffffff")))
            text_point = selection.bottomRight() + QPoint(0, 16)
            if text_point.y() > target.bottom():
                text_point = selection.bottomRight() + QPoint(0, -8)
            painter.drawText(text_point, label)
        elif self._hover is not None:
            self._paint_color_chip(painter)
        if self._color_mode:
            hint = "点击复制颜色 · 按 C 复制 · Esc 取消"
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(QRect(0, 12, self.width(), 28),
                             Qt.AlignCenter, hint)
        painter.end()

    def _paint_color_chip(self, painter: QPainter) -> None:
        hex_value = self.hover_hex()
        if hex_value is None:
            return
        chip = QRect(self._hover.x() + 18, self._hover.y() + 18, 86, 26)
        if chip.right() > self.width() - 4:
            chip.moveRight(self._hover.x() - 8)
        if chip.bottom() > self.height() - 4:
            chip.moveBottom(self._hover.y() - 8)
        color = QColor(hex_value)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.setBrush(color)
        painter.drawRoundedRect(chip, 4, 4)
        luminance = (color.red() * 299 + color.green() * 587
                     + color.blue() * 114) / 1000
        painter.setPen(QPen(QColor("#000000" if luminance > 140 else "#ffffff")))
        painter.drawText(chip, Qt.AlignCenter, hex_value)

    # ---------- 取色 ----------

    def _point_to_image(self, point: QPoint) -> QPoint | None:
        if self._shot is None or self._shot.isNull():
            return None
        sx = self._shot.width() / max(1, self.width())
        sy = self._shot.height() / max(1, self.height())
        return QPoint(min(self._shot.width() - 1, max(0, round(point.x() * sx))),
                      min(self._shot.height() - 1, max(0, round(point.y() * sy))))

    def hover_hex(self) -> str | None:
        point = self._point_to_image(self._hover) if self._hover is not None else None
        if point is None:
            return None
        return QColor(self._shot.pixel(point)).name().upper()

    def copy_hover_color(self) -> bool:
        hex_value = self.hover_hex()
        if hex_value is None:
            return False
        QGuiApplication.clipboard().setText(hex_value)
        self.colorPicked.emit(hex_value)
        if self._color_mode:
            self._cancel()                 # 取色器：拿到颜色就收
        return True

    # ---------- 鼠标 ----------

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            if self._color_mode:
                self._hover = ev.position().toPoint()
                self.copy_hover_color()
                ev.accept()
                return
            self._start = ev.position().toPoint()
            self._end = self._start
            self.update()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._start is not None:
            self._end = ev.position().toPoint()
            self.update()
            ev.accept()
            return
        self._hover = ev.position().toPoint()
        self.update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = None
        self.update()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._color_mode or self._start is None or ev.button() != Qt.LeftButton:
            super().mouseReleaseEvent(ev)
            return
        self._end = ev.position().toPoint()
        selection = self.selection()
        if (selection is None or selection.width() < MIN_SELECTION
                or selection.height() < MIN_SELECTION):
            self._cancel()                 # 误触：当成取消，不要复制一张空图
            return
        self._commit(selection)

    def _crop(self, selection: QRect) -> QImage:
        return self._shot.copy(self._image_rect(selection))

    # ---------- 键盘 ----------

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape:
            self._cancel()
            ev.accept()
            return
        if ev.key() == Qt.Key_C and self.selection() is None:
            self.copy_hover_color()
            ev.accept()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self.selection() is not None:
            self.copy_selection()
            ev.accept()
            return
        super().keyPressEvent(ev)

    # ---------- 选区确认 ----------

    def _commit(self, selection: QRect) -> None:
        if self._sent:
            return
        self._sent = True
        self.finished.emit(self._crop(selection))
        self.close()

    def copy_selection(self) -> None:
        selection = self.selection()
        if selection is None:
            return
        self._commit(selection)

    def _cancel(self) -> None:
        if self._sent:
            return
        self._sent = True
        self.cancelled.emit()
        self.close()

    def closeEvent(self, event) -> None:
        self._start = None
        self._end = None
        super().closeEvent(event)
        if not self._sent:
            self._sent = True
            self.cancelled.emit()
