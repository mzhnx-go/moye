"""截图控制器：模式分派、剪贴板、编辑器、历史、贴图、收尾。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from sketchpad.screenshot import controller as controller_module
from sketchpad.screenshot import install
from sketchpad.screenshot.controller import ScreenshotController


class _FakeAppController:
    def __init__(self, settings_path):
        self.settings_path = settings_path


class _FakeWindow(QWidget):
    def __init__(self, tmp_path):
        super().__init__()
        self.controller = _FakeAppController(tmp_path / "data" / "settings.json")
        self.notes: list[str] = []

    def _note(self, text: str, error: bool = False) -> None:
        self.notes.append(text)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    return _FakeWindow(tmp_path)


@pytest.fixture
def controller(window, qapp):
    control = ScreenshotController(window)
    yield control
    control.shutdown()
    qapp.processEvents()


def make_image(width: int = 60, height: int = 40) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3366cc"))
    return image


def test_全屏模式抓图后进遮罩并复制写历史(controller, window, monkeypatch, qapp):
    image = make_image(80, 50)
    monkeypatch.setattr(controller_module, "grab_fullscreen", lambda: image)
    controller._capture_active = True
    controller._window_was_visible = False
    controller._editor_source = "fullscreen"
    finished = QSignalSpy(controller.finished)

    controller._grab("fullscreen")

    overlay = controller._editor
    assert overlay is not None
    assert overlay.selection_rect() == QRectF(0, 0, 80, 50)
    # 松手/打开遮罩不再自动复制
    assert controller.history.entries() == []
    assert overlay.copy_to_clipboard() is True
    qapp.processEvents()
    assert QGuiApplication.clipboard().image().size() == image.size()
    assert controller._editor is None
    entries = controller.history.entries()
    assert len(entries) == 1 and entries[0].source == "fullscreen"
    assert finished.count() == 1
    assert any("已复制" in text for text in window.notes)


def test_区域截图后记住上一区域(controller, qapp):
    controller._capture_active = True
    controller._window_was_visible = False
    controller._editor_source = "region"
    region = QRect(10, 20, 100, 80)
    controller._deliver(make_image(), "region", region)
    assert controller._last_region == region
    controller._editor.cancel()
    qapp.processEvents()


def test_上一区域直接用上次的矩形(controller, monkeypatch, qapp):
    image = make_image(200, 200)

    class FakeScreen:
        def geometry(self):
            return QRect(0, 0, 200, 200)

        def availableGeometry(self):
            return QRect(0, 0, 200, 200)

    monkeypatch.setattr(controller_module, "screen_at",
                        lambda point=None: FakeScreen())
    monkeypatch.setattr(controller_module, "grab_screen", lambda _screen: image)
    controller._capture_active = True
    controller._editor_source = "last"
    controller._last_region = QRect(20, 30, 60, 40)
    controller._grab("last")
    assert controller._editor is not None
    assert controller._editor.selection_rect() == QRectF(20, 30, 60, 40)
    controller._editor.cancel()
    qapp.processEvents()


def test_没有上一区域时退回区域框选(controller, monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(controller, "_grab_region", lambda: called.append(True))
    controller._capture_active = True
    controller._last_region = None
    controller._grab("last")
    assert called == [True]
    assert any("上一区域" in text for text in controller.window.notes)


def test_复制后把调整后的选区记为上一区域(controller, qapp):
    controller._capture_active = True
    controller._window_was_visible = False
    controller._editor_source = "region"
    controller.open_editor(make_image(200, 200), "region",
                          QRectF(20, 30, 60, 40))
    overlay = controller._editor
    assert overlay is not None
    expected = QRect(
        overlay.viewport().mapToGlobal(overlay.mapFromScene(QPointF(20, 30))),
        overlay.viewport().mapToGlobal(overlay.mapFromScene(QPointF(80, 70))),
    ).normalized()
    assert overlay.copy_to_clipboard() is True
    qapp.processEvents()
    assert controller._last_region == expected


def test_窗口模式初始选区等于窗口矩形(controller, monkeypatch, qapp):
    image = make_image(200, 200)

    class FakeScreen:
        def geometry(self):
            return QRect(0, 0, 200, 200)

        def availableGeometry(self):
            return QRect(0, 0, 200, 200)

    monkeypatch.setattr(controller_module, "screen_at",
                        lambda point=None: FakeScreen())
    monkeypatch.setattr(controller_module, "grab_screen", lambda _screen: image)
    monkeypatch.setattr(controller_module, "window_rect_logical_at",
                        lambda _point: QRect(20, 30, 100, 80))
    controller._capture_active = True
    controller._window_was_visible = False
    controller._grab("window")
    assert controller._editor is not None
    assert controller._editor.selection_rect() == QRectF(20, 30, 100, 80)
    controller._editor.cancel()
    qapp.processEvents()


def test_窗口模式找不到窗口时退回区域(controller, monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(controller_module, "window_rect_logical_at",
                        lambda _point: None)
    monkeypatch.setattr(controller, "_grab_region", lambda: called.append(True))
    controller._capture_active = True
    controller._grab("window")
    assert called == [True]
    assert any("窗口" in text for text in controller.window.notes)


def test_重开遮罩不会提前恢复主窗(controller, qapp):
    controller._capture_active = True
    controller._window_was_visible = False
    first = controller.open_editor(make_image(40, 30), "region")
    second = controller.open_editor(make_image(50, 40), "region")
    assert first is not second
    assert controller._capture_active is True
    assert controller._editor is second
    second.cancel()
    qapp.processEvents()
    assert controller._capture_active is False


def test_取消不写历史复制才写(controller, qapp):
    controller.open_editor(make_image(), "window")
    assert controller._editor is not None
    controller._editor.cancel()
    qapp.processEvents()
    assert controller.history.entries() == []

    controller.open_editor(make_image(), "window")
    assert controller._editor.copy_to_clipboard() is True
    qapp.processEvents()
    entries = controller.history.entries()
    assert len(entries) == 1 and entries[0].source == "window"


def test_遮罩贴图写历史并创建贴图(controller, qapp):
    controller.open_editor(make_image(80, 60), "region")
    assert controller._editor is not None
    assert controller._editor.pin_to_screen() is True
    qapp.processEvents()
    assert len(controller._pins) == 1
    assert len(controller.history.entries()) == 1
    controller.close_pins()
    qapp.processEvents()


def test_关窗确认时可暂停并恢复贴图(controller, qapp):
    pin = controller.pin(make_image())
    assert pin is not None and pin.isVisible() is True
    controller.suspend_topmost()
    assert pin.isVisible() is False
    controller.resume_topmost()
    qapp.processEvents()
    assert pin.isVisible() is True
    pin.close()
    qapp.processEvents()


def test_贴图创建窗口并在关闭时移除(controller, qapp):
    pin = controller.pin(make_image())
    assert pin is not None
    assert len(controller._pins) == 1
    pin.close()
    qapp.processEvents()
    assert controller._pins == []


def test_历史窗口列出条目(controller, qapp):
    controller.history.add(make_image(), "region")
    dialog = controller.show_history()
    assert dialog.list.count() == 1
    assert dialog.empty_label.isHidden() is True
    dialog.close()
    qapp.processEvents()


def test_历史窗口复制条目(controller, qapp):
    controller.history.add(make_image(23, 17), "region")
    dialog = controller.show_history()
    dialog.list.setCurrentRow(0)
    assert dialog._copy() is True
    assert QGuiApplication.clipboard().image().size().width() == 23
    dialog.close()
    qapp.processEvents()


def test_取色模式只复制颜色不打开编辑器(controller, monkeypatch, qapp):
    class FakeScreen:
        def geometry(self):
            return QRect(0, 0, 100, 100)

        def availableGeometry(self):
            return QRect(0, 0, 100, 100)

    image = make_image(100, 100)
    image.setPixelColor(10, 10, QColor("#ff0000"))
    monkeypatch.setattr(controller_module, "screen_at", lambda point=None: FakeScreen())
    monkeypatch.setattr(controller_module, "grab_screen", lambda _screen: image)
    controller._capture_active = True
    controller._window_was_visible = False
    controller._editor_source = "color"

    controller._grab("color")
    overlay = controller._editor
    assert overlay is not None
    from PySide6.QtCore import QPointF
    overlay._hover_scene = QPointF(10, 10)
    assert overlay.copy_hover_color() is True
    qapp.processEvents()

    assert QGuiApplication.clipboard().text() == "#FF0000"
    assert controller._editor is None
    assert controller._capture_active is False
    assert any("#FF0000" in text for text in controller.window.notes)


def test_上一区域跨控制器持久化(window, qapp):
    first = ScreenshotController(window)
    first._capture_active = True
    first._window_was_visible = False
    first._editor_source = "region"
    first._deliver(make_image(), "region", QRect(5, 6, 70, 80))
    first._editor.cancel()
    first.shutdown()
    qapp.processEvents()

    second = ScreenshotController(window)
    try:
        assert second._last_region == QRect(5, 6, 70, 80)
    finally:
        second.shutdown()


def test_install给托盘加截图入口(window, qapp):
    window.tray = QSystemTrayIcon(window)
    window.tray.setContextMenu(QMenu(window))
    control = install(window)
    try:
        labels = [action.text()
                  for action in window.tray.contextMenu().actions()]
        assert "截图（区域）" in labels
        assert "截图历史…" in labels
    finally:
        control.shutdown()
        window.tray = None


def test_shutdown收掉编辑器与贴图(controller, qapp):
    controller.open_editor(make_image())
    controller.pin(make_image())
    controller.shutdown()
    qapp.processEvents()
    assert controller._editor is None
    assert controller._pins == []
