"""截图框选：选区换算、信号、误触取消。"""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent
from PySide6.QtTest import QSignalSpy

from sketchpad.annotate.capture import MIN_SELECTION, CaptureOverlay

GEOMETRY = QRect(0, 0, 800, 600)


def make_overlay() -> CaptureOverlay:
    image = QImage(GEOMETRY.size(), QImage.Format_ARGB32)
    image.fill(Qt.red)
    return CaptureOverlay(image, GEOMETRY)


def _mouse_event(type_, x: int, y: int, button) -> QMouseEvent:
    return QMouseEvent(type_, QPointF(x, y), QPointF(x, y), button, button,
                       Qt.KeyboardModifier.NoModifier)


def press(overlay: CaptureOverlay, x: int, y: int) -> None:
    overlay.mousePressEvent(
        _mouse_event(QEvent.Type.MouseButtonPress, x, y, Qt.MouseButton.LeftButton))


def move(overlay: CaptureOverlay, x: int, y: int) -> None:
    overlay.mouseMoveEvent(
        _mouse_event(QEvent.Type.MouseMove, x, y, Qt.MouseButton.LeftButton))


def release(overlay: CaptureOverlay, x: int, y: int) -> None:
    overlay.mouseReleaseEvent(
        _mouse_event(QEvent.Type.MouseButtonRelease, x, y, Qt.MouseButton.LeftButton))


def test_拖出选区后松手即发出finished():
    overlay = make_overlay()
    try:
        spy = QSignalSpy(overlay.finished)
        press(overlay, 100, 100)
        move(overlay, 300, 220)
        release(overlay, 300, 220)

        # PixPin 式：松手直接把选区交给截图编辑器，不再等“复制”按钮
        assert spy.count() == 1
        image = spy.at(0)[0]
        assert image.width() == 200 and image.height() == 120
    finally:
        overlay.close()
        overlay.deleteLater()


def test_选区太小当成误触取消():
    overlay = make_overlay()
    try:
        spy = QSignalSpy(overlay.cancelled)
        press(overlay, 100, 100)
        release(overlay, 100 + MIN_SELECTION - 1, 100)
        assert spy.count() == 1
    finally:
        overlay.close()
        overlay.deleteLater()


def test_Esc取消且不发finished():
    overlay = make_overlay()
    overlay.show()
    try:
        cancelled = QSignalSpy(overlay.cancelled)
        finished = QSignalSpy(overlay.finished)
        overlay.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert cancelled.count() == 1
        assert finished.count() == 0
    finally:
        overlay.close()
        overlay.deleteLater()


def test_选区会归一化反方向拖拽():
    overlay = make_overlay()
    try:
        press(overlay, 300, 220)
        move(overlay, 100, 100)
        assert overlay.selection() == QRect(100, 100, 200, 120)
    finally:
        overlay.close()
        overlay.deleteLater()


def test_未拖动时没有选区():
    overlay = make_overlay()
    try:
        assert overlay.selection() is None
    finally:
        overlay.close()
        overlay.deleteLater()


def test_控制器把截图复制进剪贴板(window, monkeypatch, controller_factory):
    """只测控制器这一段：不碰真机抓屏，直接喂一张图看它进不进剪贴板。"""
    from PySide6.QtGui import QGuiApplication

    controller = controller_factory(window, registered=True)
    image = QImage(10, 10, QImage.Format_ARGB32)
    image.fill(Qt.red)

    controller._on_captured(image)

    clipboard = QGuiApplication.clipboard()
    assert clipboard.image().width() == 10
    assert any("剪贴板" in text for text in window.notes)


def test_高DPI图像按窗口比例裁剪():
    """150%/200% 下截图是物理像素，选框是逻辑坐标；裁剪必须按比例放大。"""
    image = QImage(1600, 1200, QImage.Format_ARGB32)
    image.fill(Qt.red)
    overlay = CaptureOverlay(image, QRect(0, 0, 800, 600))
    try:
        spy = QSignalSpy(overlay.finished)
        press(overlay, 100, 100)
        move(overlay, 300, 220)
        release(overlay, 300, 220)
        overlay.copy_selection()
        assert spy.count() == 1
        cropped = spy.at(0)[0]
        assert (cropped.width(), cropped.height()) == (400, 240)
    finally:
        overlay.close()
        overlay.deleteLater()
