"""启动动画：离屏渲染与淡出时序。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.ui import icons
from sketchpad.ui import splash as splash_module
from sketchpad.ui.splash import LaunchSplash


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_splash_uses_icon_art_and_fixed_size(qapp):
    splash = LaunchSplash()
    try:
        assert splash.width() == splash_module.SPLASH_W
        assert splash.height() == splash_module.SPLASH_H
        assert not splash._art.isNull(), "启动卡必须使用 splash_girl.png"
        assert icons._asset_path("splash_girl.png") is not None
        assert splash._alpha() == 0.0, "首帧从透明淡入"
    finally:
        splash._timer.stop()
        splash.close()


def test_splash_paints_rounded_card(qapp):
    splash = LaunchSplash()
    try:
        splash._elapsed = 300
        image = QImage(splash.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        splash.render(image)
        assert image.pixelColor(280, 165).alpha() == 255   # 卡片实体
        assert image.pixelColor(0, 0).alpha() == 0         # 圆角外透明
    finally:
        splash._timer.stop()
        splash.close()


def test_finish_respects_minimum_visible_time(qapp, monkeypatch):
    splash = LaunchSplash(min_visible_ms=180, fade_ms=0)
    done: list[bool] = []
    monkeypatch.setattr(splash, "_complete", lambda: done.append(True))
    try:
        splash.finish()
        QTest.qWait(60)
        assert done == []
        QTest.qWait(180)
        assert done == [True]
    finally:
        splash._timer.stop()
        splash.close()


def test_finish_without_wait_fades_immediately(qapp, monkeypatch):
    splash = LaunchSplash(min_visible_ms=0, fade_ms=0)
    done: list[bool] = []
    monkeypatch.setattr(splash, "_complete", lambda: done.append(True))
    try:
        splash.finish()
        assert done == [True]
        splash.finish()                 # 重复调用不再触发
        assert done == [True]
    finally:
        splash._timer.stop()
        splash.close()


def test_main_shows_splash_then_finishes_after_window(qapp, monkeypatch):
    import sketchpad.app as app_module

    events: list[str] = []

    class FakeSplash:
        def finish(self) -> None:
            events.append("splash.finish")

    class FakeWindow:
        def __init__(self, *args, **kwargs) -> None:
            events.append("window.new")

        def show(self) -> None:
            events.append("window.show")

        def move(self, *_args) -> None:
            pass

        def width(self) -> int:
            return 800

        def height(self) -> int:
            return 600

    monkeypatch.setattr(app_module, "_set_taskbar_app_id", lambda: None)
    monkeypatch.setattr(app_module, "_QUIT_BROADCAST_ENABLED", False)
    monkeypatch.setattr(app_module.tooltips, "install", lambda _app: None)
    monkeypatch.setattr(
        app_module, "show_launch_splash",
        lambda: events.append("splash.show") or FakeSplash())
    monkeypatch.setattr(app_module, "AppController", lambda *_a, **_k: object())
    monkeypatch.setattr(app_module, "MainWindow", FakeWindow)
    monkeypatch.setattr(
        app_module.single_instance, "InstanceLock",
        lambda: type("Lock", (), {"acquire": lambda _self: True})())
    monkeypatch.setattr(
        app_module, "apply_instance_features",
        lambda _window, _primary: events.append("features"))
    monkeypatch.setattr(
        QApplication, "exec", lambda _self: events.append("exec") or 0)

    assert app_module.main([]) == 0
    assert events == ["splash.show", "window.new", "window.show",
                      "features", "splash.finish", "exec"]

