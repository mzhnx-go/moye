"""贴图窗口：置顶、缩放、旋转、透明度、复制与关闭。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from sketchpad.screenshot.pin import MAX_SCALE, MIN_SCALE, PinWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def make_image(width: int = 120, height: int = 80) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3366cc"))
    return image


@pytest.fixture
def pin(qapp):
    window = PinWindow(make_image())
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_置顶且无边框(pin):
    flags = pin.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.FramelessWindowHint
    assert (pin.width(), pin.height()) == (120, 80)


def test_缩放并钳制上下限(pin):
    pin.set_scale(2.0)
    assert (pin.width(), pin.height()) == (240, 160)
    pin.set_scale(1000)
    assert pin.scale() == MAX_SCALE
    pin.set_scale(0.0001)
    assert pin.scale() == MIN_SCALE


def test_滚轮缩放(pin):
    class Wheel:
        def angleDelta(self):
            class Delta:
                def y(self):
                    return 120
            return Delta()

        def accept(self):
            pass

    before = pin.scale()
    pin.wheelEvent(Wheel())
    assert pin.scale() > before


def test_旋转交换宽高(pin):
    pin.rotate()
    assert (pin.width(), pin.height()) == (80, 120)
    pin.rotate()
    assert (pin.width(), pin.height()) == (120, 80)


def test_透明度(pin):
    pin.set_opacity_percent(60)
    assert pin.opacity_percent() == 60
    assert abs(pin.windowOpacity() - 0.6) < 0.01
    pin.set_opacity_percent(5)
    assert pin.opacity_percent() == 10       # 下限 10%


def test_复制到剪贴板(pin):
    pin.copy_to_clipboard()
    image = QGuiApplication.clipboard().image()
    assert (image.width(), image.height()) == (120, 80)


def test_关闭发出closed(pin):
    spy = QSignalSpy(pin.closed)
    pin.close()
    assert spy.count() == 1
