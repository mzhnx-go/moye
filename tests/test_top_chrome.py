import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from sketchpad.ui import theme
from sketchpad.ui.titlebar import TitleBar
from sketchpad.ui.top_chrome import (
    HOUSE_MARGINS, PANEL_MARGINS, ROW_SPACING, TopChrome,
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _rows(chrome: TopChrome) -> list[QWidget]:
    return [chrome.rows.itemAt(i).widget() for i in range(chrome.rows.count())]


def _qss_block(qss: str, selector: str) -> str:
    return qss.split(selector, 1)[1].split("}", 1)[0]


def _rgb(name: str) -> tuple[int, int, int]:
    value = name.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        value = value / 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(item) for item in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    first, second = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (first + 0.05) / (second + 0.05)


def test_panel_geometry_and_rows(qapp):
    chrome = TopChrome()
    chrome.resize(1100, 130)
    first, second, third = QWidget(), QWidget(), QWidget()
    for widget in (first, second, third):
        widget.setFixedHeight(24)
    try:
        for widget in (first, second, third):
            chrome.add_row(widget)
        chrome.show()
        QTest.qWait(20)

        assert chrome.objectName() == "TopChrome"
        assert chrome.panel.objectName() == "TopChromePanel"
        assert _rows(chrome) == [first, second, third]
        assert chrome.rows.spacing() == ROW_SPACING

        outer = chrome.layout().contentsMargins()
        assert (outer.left(), outer.top(), outer.right(), outer.bottom()) == HOUSE_MARGINS
        inner = chrome.rows.contentsMargins()
        assert (inner.left(), inner.top(), inner.right(), inner.bottom()) == PANEL_MARGINS
        assert chrome.panel.x() == HOUSE_MARGINS[0]
        assert chrome.panel.y() == HOUSE_MARGINS[1]
        assert (chrome.panel.width()
                == chrome.width() - HOUSE_MARGINS[0] - HOUSE_MARGINS[2])
    finally:
        chrome.close()


def test_chrome_is_flush_with_window_edges(qapp):
    """顶栏是窗口自身的一部分：上面、左面、右面都不能留卡片式内缩。"""
    chrome = TopChrome()
    chrome.resize(1100, 130)
    try:
        chrome.show()
        QTest.qWait(20)
        assert HOUSE_MARGINS[0] == 0
        assert HOUSE_MARGINS[1] == 0
        assert HOUSE_MARGINS[2] == 0
        assert chrome.panel.x() == 0
        assert chrome.panel.y() == 0
        assert chrome.panel.width() == chrome.width()
        assert chrome.panel.height() == chrome.height()
    finally:
        chrome.close()


def test_panel_has_no_inset_border_or_corner_radius():
    block = _qss_block(theme.QSS, "QFrame#TopChromePanel")
    assert "border: none" in block
    assert "border-radius" not in block


def test_remove_row_keeps_widget_alive(qapp):
    from shiboken6 import isValid

    chrome = TopChrome()
    widget = QWidget()
    try:
        chrome.add_row(widget)
        assert chrome.has_row(widget) is True
        chrome.remove_row(widget)
        assert chrome.has_row(widget) is False
        assert isValid(widget) is True
    finally:
        widget.deleteLater()
        chrome.close()


def test_chrome_never_takes_keyboard_focus(qapp):
    chrome = TopChrome()
    try:
        assert chrome.focusPolicy() == Qt.NoFocus
        assert chrome.panel.focusPolicy() == Qt.NoFocus
    finally:
        chrome.close()


def test_panel_uses_flat_window_background():
    block = _qss_block(theme.QSS, "QFrame#TopChromePanel")
    assert theme.WINDOW_BG in block
    assert "rgba(" not in block


def test_flat_panel_text_contrast_is_at_least_aa():
    background = _rgb(theme.WINDOW_BG)
    assert _contrast(_rgb(theme.TEXT), background) >= 4.5


from sketchpad.ui.menubar import MENUBAR_QSS


def test_titlebar_has_no_divider_and_no_fill():
    # 主窗标题栏在玻璃面板里：只看更具体的 chrome 规则，基础规则留给草稿窗
    block = _qss_block(theme.QSS, "QFrame#TopChromePanel QWidget#TitleBar")
    assert "border-bottom" not in block
    assert "transparent" in block


def test_menubar_has_no_divider_and_no_fill():
    block = _qss_block(MENUBAR_QSS, "QMenuBar#Menubar")
    assert "border-bottom" not in block
    assert "transparent" in block


def test_titlebar_row_margins_are_compact(qapp):
    bar = TitleBar("墨页", compact=True)
    try:
        margins = bar.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (2, 0, 2, 0)
    finally:
        bar.close()


def test_scratch_titlebar_keeps_old_style_and_margins(qapp):
    """草稿窗不套玻璃面板：默认 TitleBar 仍是原来的底/底线与 10/0/4/0 缩进。"""
    bar = TitleBar("草稿纸")
    try:
        margins = bar.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (10, 0, 4, 0)
    finally:
        bar.close()
    base = _qss_block(theme.QSS, "QWidget#TitleBar {")
    assert "border-bottom" in base
    assert "rgba(255, 250, 252, 0.48)" in base


import main


@pytest.fixture
def win(qapp, tmp_path):
    controller = main.AppController(tmp_path / "cfg")
    window = main.MainWindow(controller)
    window.resize(1100, 760)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    window._quitting = True
    window.close()


def test_main_window_puts_three_rows_in_one_panel(win):
    assert win.top_chrome.rows.indexOf(win.titlebar) == 0
    assert win.top_chrome.rows.indexOf(win.menubar) == 1
    assert win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2
    assert win.titlebar.parent() is win.top_chrome.panel
    assert win.menubar.parent() is win.top_chrome.panel
    assert win.toolbar_scroll.parent() is win.top_chrome.panel
    assert win.centralWidget().layout().indexOf(win.top_chrome) == 0


def test_chrome_height_stays_in_budget(win):
    QTest.qWait(40)
    assert win.top_chrome.height() <= 126


def test_right_toolbar_leaves_chrome_with_two_rows(win):
    win._apply_toolbar_position("right")
    QTest.qWait(40)
    assert win.top_chrome.rows.count() == 2
    assert win.top_chrome.has_row(win.toolbar_scroll) is False
    assert win.body.indexOf(win.toolbar_scroll) == win.body.count() - 1

    win._apply_toolbar_position("top")
    QTest.qWait(40)
    assert win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2


def test_annotate_button_fits_at_792(win):
    win.resize(792, 760)
    QTest.qWait(60)
    button = win.toolbox.annotate_button
    assert button.isVisible() is True
    corner = button.mapTo(win.toolbar_scroll.viewport(), QPoint(0, 0))
    assert corner.x() >= 0
    assert corner.x() + button.width() <= win.toolbar_scroll.viewport().width()
