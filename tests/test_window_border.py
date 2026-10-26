"""无边框窗口的外边界：主窗与草稿窗四边必须有 1px 可见边框。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main
from sketchpad.core.settings import Settings
from sketchpad.ui import theme
from sketchpad.ui.scratch import ScratchWindow


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _edge_pixels(widget) -> tuple[str, str, str, str]:
    # 不 show：全量跑时前面用例留下的大量顶层窗口会让“等窗口暴露”卡死；
    # 隐藏控件也能 grab，先把样式/布局强制跑一遍即可。
    widget.ensurePolished()
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    QApplication.processEvents()
    QTest.qWait(20)
    img = widget.grab().toImage()
    w, h = img.width(), img.height()
    return (img.pixelColor(w // 2, 0).name(),
            img.pixelColor(w // 2, h - 1).name(),
            img.pixelColor(0, h // 2).name(),
            img.pixelColor(w - 1, h // 2).name())


def test_main_window_has_visible_outer_border(qapp, tmp_path):
    app = QApplication.instance()
    old = app.styleSheet()
    app.setStyleSheet(theme.QSS)
    win = main.MainWindow(main.AppController(tmp_path / "cfg"))
    try:
        edges = _edge_pixels(win)
        # QColor.name() 返回小写；theme.OUTER_BORDER 是写死的大写十六进制，比较时统一小写
        assert edges == (theme.OUTER_BORDER.lower(),) * 4
    finally:
        win._quitting = True
        win.close()
        app.setStyleSheet(old)


def test_scratch_window_has_visible_outer_border(qapp):
    app = QApplication.instance()
    old = app.styleSheet()
    app.setStyleSheet(theme.QSS)
    win = ScratchWindow(Settings())
    try:
        edges = _edge_pixels(win)
        # QColor.name() 返回小写；theme.OUTER_BORDER 是写死的大写十六进制，比较时统一小写
        assert edges == (theme.OUTER_BORDER.lower(),) * 4
    finally:
        win.hide()
        win.close()
        app.setStyleSheet(old)
