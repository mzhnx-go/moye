"""抓屏模式与取色：HiDPI 换算、上一区域、悬停颜色。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QPixmap
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from sketchpad.annotate.capture import CaptureOverlay
from sketchpad.screenshot import capture as capture_module


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class FakeScreen:
    def __init__(self, geometry: QRect):
        self._geometry = geometry

    def geometry(self) -> QRect:
        return self._geometry

    def availableGeometry(self) -> QRect:
        return self._geometry


def test_高DPI裁剪按图像与逻辑尺寸换算(monkeypatch):
    image = QImage(1600, 1200, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    for x in range(200, 600):
        for y in range(100, 300):
            image.setPixelColor(x, y, QColor("#000000"))
    screen = FakeScreen(QRect(0, 0, 800, 600))
    monkeypatch.setattr(capture_module, "screen_at", lambda point=None: screen)
    monkeypatch.setattr(capture_module, "grab_screen", lambda _screen: image)

    cropped = capture_module.crop_from_screen(QRect(100, 50, 200, 100))
    assert (cropped.width(), cropped.height()) == (400, 200)
    assert QColor(cropped.pixel(5, 5)) == QColor("#000000")
    assert QColor(cropped.pixel(395, 5)) == QColor("#000000")


def test_裁剪区域超出屏幕时取交集(monkeypatch):
    image = QImage(800, 600, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    screen = FakeScreen(QRect(0, 0, 800, 600))
    monkeypatch.setattr(capture_module, "screen_at", lambda point=None: screen)
    monkeypatch.setattr(capture_module, "grab_screen", lambda _screen: image)
    cropped = capture_module.crop_from_screen(QRect(700, 500, 300, 300))
    assert (cropped.width(), cropped.height()) == (100, 100)


def test_多屏按屏幕原点换算(monkeypatch):
    image = QImage(1600, 1200, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    for x in range(200, 400):
        for y in range(100, 300):
            image.setPixelColor(x, y, QColor("#ff0000"))
    screen = FakeScreen(QRect(1920, 0, 800, 600))
    monkeypatch.setattr(capture_module, "screen_at", lambda point=None: screen)
    monkeypatch.setattr(capture_module, "grab_screen", lambda _screen: image)
    # 屏幕左上角在 (1920,0)：全局 (2020,50) 对应局部 (100,50)
    cropped = capture_module.crop_from_screen(QRect(2020, 50, 100, 100))
    assert QColor(cropped.pixel(5, 5)) == QColor("#ff0000")


def test_框选悬停取色并复制(qapp):
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    image.setPixelColor(10, 10, QColor("#ff0000"))
    overlay = CaptureOverlay(image, QRect(0, 0, 100, 100), color_mode=True)
    try:
        overlay._hover = QPoint(10, 10)
        assert overlay.hover_hex() == "#FF0000"
        picked = QSignalSpy(overlay.colorPicked)
        cancelled = QSignalSpy(overlay.cancelled)
        assert overlay.copy_hover_color() is True
        assert picked.count() == 1
        assert picked.at(0)[0] == "#FF0000"
        assert QGuiApplication.clipboard().text() == "#FF0000"
        assert cancelled.count() == 1            # 取色模式拿到颜色就关闭
    finally:
        overlay.close()
        overlay.deleteLater()


def test_区域框选里按C取色但不关遮罩(qapp):
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    image.setPixelColor(10, 10, QColor("#00ff00"))
    overlay = CaptureOverlay(image, QRect(0, 0, 100, 100))
    try:
        overlay._hover = QPoint(10, 10)
        picked = QSignalSpy(overlay.colorPicked)
        cancelled = QSignalSpy(overlay.cancelled)
        assert overlay.copy_hover_color() is True
        assert picked.count() == 1
        assert cancelled.count() == 0             # 还要继续框选
    finally:
        overlay.close()
        overlay.deleteLater()


def test_框选悬停到图外返回None():
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    overlay = CaptureOverlay(image, QRect(0, 0, 100, 100))
    try:
        overlay._hover = None
        assert overlay.hover_hex() is None
        assert overlay.copy_hover_color() is False
    finally:
        overlay.close()
        overlay.deleteLater()


def test_窗口模式直接用Qt抓句柄避免DPI错位(monkeypatch):
    image = QImage(300, 200, QImage.Format.Format_ARGB32)
    image.fill(QColor("#123456"))
    seen: list[int] = []

    class GrabScreen:
        def grabWindow(self, handle):
            seen.append(int(handle))
            return QPixmap.fromImage(image)

    monkeypatch.setattr(capture_module, "_window_handle_at", lambda _p: 4321)
    monkeypatch.setattr(capture_module, "screen_at", lambda _p=None: GrabScreen())
    grabbed = capture_module.grab_window_at(QPoint(10, 10))
    assert seen == [4321]
    assert grabbed.size() == image.size()
    assert grabbed.devicePixelRatio() == 1.0


def test_窗口抓不到时退回矩形裁剪(monkeypatch):
    fallback = QImage(50, 40, QImage.Format.Format_ARGB32)
    fallback.fill(QColor("#ff0000"))

    class GrabScreen:
        def grabWindow(self, _handle):
            return QPixmap()

    monkeypatch.setattr(capture_module, "_window_handle_at", lambda _p: 99)
    monkeypatch.setattr(capture_module, "screen_at", lambda _p=None: GrabScreen())
    monkeypatch.setattr(capture_module, "window_rect_at",
                        lambda _p: QRect(1, 2, 50, 40))
    monkeypatch.setattr(capture_module, "crop_from_screen",
                        lambda rect, point=None: fallback)
    grabbed = capture_module.grab_window_at(QPoint(10, 10))
    assert grabbed.size() == fallback.size()


def test_取色模式点击即复制(qapp):
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    image.setPixelColor(12, 8, QColor("#00ff00"))
    overlay = CaptureOverlay(image, QRect(0, 0, 100, 100), color_mode=True)
    try:
        picked = QSignalSpy(overlay.colorPicked)
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(12, 8),
                            QPointF(12, 8), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        overlay.mousePressEvent(event)
        assert picked.count() == 1
        assert picked.at(0)[0] == "#00FF00"
        assert QGuiApplication.clipboard().text() == "#00FF00"
    finally:
        overlay.close()
        overlay.deleteLater()


def test_qapp_fixture(qapp):
    assert QApplication.instance() is qapp
