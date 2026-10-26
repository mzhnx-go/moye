# tests/test_titlebar_tabs.py
"""标题栏挂标签条的离屏测试（方案书 2026-10-23 §3.1 / §4.1）。

覆盖：36px 固定高度、set_center_widget 的插入/替换/移除、
标签区点击不触发窗口拖动/最大化、窗口按钮几何与信号不变。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSizePolicy, QWidget
from PySide6.QtTest import QTest

from sketchpad.ui.titlebar import TITLE_BAR_HEIGHT, TitleBar


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _show_bar(width: int = 900, title: str = "PageFlow") -> TitleBar:
    bar = TitleBar(title)
    bar.resize(width, TITLE_BAR_HEIGHT)
    bar.show()
    QTest.qWait(20)
    return bar


def test_fixed_height_is_36_and_buttons_fit(qapp):
    bar = _show_bar()
    try:
        assert TITLE_BAR_HEIGHT == 36
        assert bar.height() == 36
        assert bar.minimumHeight() == bar.maximumHeight() == 36
        for button in (bar.settings_button, bar.pin_button, bar.min_button,
                       bar.max_button, bar.close_button):
            assert button.size() == button.minimumSize() == button.maximumSize()
            assert (button.width(), button.height()) == (32, 24)
            # 24px 按钮在 36px 栏里垂直居中（上下各留 6px，容 1px 误差）
            assert abs(button.y() - (36 - 24) // 2) <= 1
            assert 0 <= button.y() and button.y() + button.height() <= 36
    finally:
        bar.close()


def test_center_widget_sits_between_title_and_buttons_and_expands(qapp):
    bar = _show_bar(900)
    dummy = QWidget()
    dummy.setObjectName("DummyStrip")
    try:
        bar.set_center_widget(dummy)
        QTest.qWait(20)

        assert dummy.parent() is bar
        assert dummy.isVisible() is True
        assert (bar.layout().indexOf(bar.title_label)
                < bar.layout().indexOf(dummy)
                < bar.layout().indexOf(bar.pin_button))
        assert dummy.width() > 0
        # 窄窗口时允许被裁，不把窗口顶宽
        assert dummy.minimumWidth() == 0
        assert dummy.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
        # 标题 -> 中间控件 -> 设置按钮，顺序不能乱
        assert dummy.x() >= bar.title_label.x() + bar.title_label.width()
        assert dummy.x() + dummy.width() <= bar.settings_button.x()
        assert dummy.height() <= TITLE_BAR_HEIGHT

        wide = dummy.width()
        bar.resize(1300, TITLE_BAR_HEIGHT)
        QTest.qWait(20)
        assert dummy.width() > wide
        # 按钮仍然贴右，没被中间控件挤走
        assert bar.close_button.x() + bar.close_button.width() <= bar.width()
    finally:
        bar.set_center_widget(None)
        dummy.deleteLater()
        bar.close()


def test_replacing_and_removing_center_widget_keeps_old_alive(qapp):
    from shiboken6 import isValid

    bar = _show_bar(900)
    first = QWidget()
    first.setObjectName("FirstStrip")
    second = QWidget()
    second.setObjectName("SecondStrip")
    try:
        bar.set_center_widget(first)
        QTest.qWait(10)
        bar.set_center_widget(second)
        QTest.qWait(20)

        assert bar.center_widget is second
        assert second.parent() is bar
        assert (bar.layout().indexOf(bar.title_label)
                < bar.layout().indexOf(second)
                < bar.layout().indexOf(bar.pin_button))
        # 旧的只被 reparent 到 None，没有删除，也不在布局里
        assert isValid(first) is True
        assert first.parent() is None
        assert bar.layout().indexOf(first) == -1
        assert first.objectName() == "FirstStrip"      # 还能正常用

        bar.set_center_widget(None)
        QTest.qWait(20)
        assert bar.center_widget is None
        assert isValid(second) is True
        assert second.parent() is None
        assert bar.layout().indexOf(second) == -1
        # 移除后按钮仍贴右：中间是可伸缩弹簧
        assert bar.settings_button.x() > bar.title_label.x() + bar.title_label.width()
        assert bar.close_button.x() + bar.close_button.width() <= bar.width()
    finally:
        bar.set_center_widget(None)
        first.deleteLater()
        second.deleteLater()
        bar.close()


def test_tab_double_click_does_not_maximize_but_blank_does(qapp):
    from PySide6.QtWidgets import QHBoxLayout, QTabBar

    bar = _show_bar(900)
    strip = QWidget()
    layout = QHBoxLayout(strip)
    layout.setContentsMargins(0, 0, 0, 0)
    tabs = QTabBar(strip)
    tabs.setExpanding(False)
    tabs.addTab("笔记 1")
    layout.addWidget(tabs, 1)
    hit = []
    bar.maximizeRequested.connect(lambda: hit.append(1))
    try:
        bar.set_center_widget(strip)
        QTest.qWait(30)
        QTest.mouseDClick(tabs, Qt.LeftButton,
                          pos=tabs.tabRect(0).center())
        QTest.qWait(10)
        assert hit == []                     # 标签双击不最大化

        QTest.mouseDClick(tabs, Qt.LeftButton,
                          pos=QPoint(tabs.width() - 10, tabs.height() // 2))
        QTest.qWait(10)
        assert hit == [1]                    # 标签栏空白双击 = 最大化
    finally:
        strip.deleteLater()
        bar.close()


def test_center_widget_children_receive_their_own_mouse_events(qapp):
    bar = _show_bar(900)
    strip = QWidget()
    button = QPushButton("+", strip)
    button.setGeometry(10, 6, 40, 24)
    clicks = []
    button.clicked.connect(lambda: clicks.append(1))
    try:
        bar.set_center_widget(strip)
        QTest.qWait(20)
        QTest.mouseClick(button, Qt.LeftButton, pos=QPoint(20, 12))
        QTest.qWait(10)
        assert clicks == [1]                 # 子控件自己收到点击，没被标题栏吞掉
    finally:
        strip.deleteLater()
        bar.close()


def test_title_label_and_blank_space_still_start_window_drag(qapp):
    """标题文字 / 空白处按下 = 拖动窗口（沿用 startSystemMove），标签区不拖。"""
    class _FakeHandle:
        def __init__(self):
            self.moves = 0

        def startSystemMove(self):
            self.moves += 1
            return True

    class _FakeWindow:
        def __init__(self):
            self.handle = _FakeHandle()

        def windowHandle(self):
            return self.handle

    class _ProbeBar(TitleBar):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fake = _FakeWindow()

        def window(self):
            return self.fake

    bar = _ProbeBar("PageFlow")
    bar.resize(900, TITLE_BAR_HEIGHT)
    bar.show()
    strip = QWidget()
    try:
        bar.set_center_widget(strip)
        QTest.qWait(20)

        QTest.mouseClick(bar.title_label, Qt.LeftButton,
                         pos=QPoint(10, bar.title_label.height() // 2))
        assert bar.fake.handle.moves == 1      # 点标题文字仍然能拖

        # 去掉标签条后，标题与按钮之间是空白伸缩区
        bar.set_center_widget(None)
        QTest.qWait(20)
        QTest.mouseClick(bar, Qt.LeftButton, pos=QPoint(bar.width() // 2, 18))
        assert bar.fake.handle.moves == 2      # 空白处也能拖

        bar.set_center_widget(strip)
        QTest.qWait(20)
        QTest.mouseClick(strip, Qt.LeftButton,
                         pos=QPoint(strip.width() // 2, strip.height() // 2))
        assert bar.fake.handle.moves == 3      # 标签条空白按下也能拖窗口
    finally:
        strip.deleteLater()
        bar.close()


def test_window_buttons_keep_geometry_tooltips_and_signals(qapp):
    bar = _show_bar()
    got = {"settings": 0, "pin": [], "min": 0, "max": 0, "close": 0}
    bar.settingsRequested.connect(lambda: got.__setitem__("settings", got["settings"] + 1))
    bar.pinToggled.connect(lambda on: got["pin"].append(on))
    bar.minimizeRequested.connect(lambda: got.__setitem__("min", got["min"] + 1))
    bar.maximizeRequested.connect(lambda: got.__setitem__("max", got["max"] + 1))
    bar.closeRequested.connect(lambda: got.__setitem__("close", got["close"] + 1))
    try:
        assert bar.settings_button.toolTip() == "设置 (Ctrl+,)"
        assert bar.pin_button.toolTip() == "置顶"
        assert bar.min_button.toolTip() == "最小化"
        assert bar.max_button.toolTip() == "最大化"
        assert bar.close_button.toolTip() == "关闭（收到托盘）"

        bar.settings_button.click()
        bar.pin_button.click()
        bar.min_button.click()
        bar.max_button.click()
        bar.close_button.click()
        assert got == {"settings": 1, "pin": [True], "min": 1, "max": 1, "close": 1}

        bar.set_maximized(True)
        assert bar.max_button.toolTip() == "还原"
        bar.set_maximized(False)
        assert bar.max_button.toolTip() == "最大化"
    finally:
        bar.close()


def test_set_title_syncs_window_title(qapp):
    from PySide6.QtWidgets import QMainWindow

    host = QMainWindow()
    bar = TitleBar("PageFlow", host)
    host.setCentralWidget(bar)
    host.show()
    QTest.qWait(20)
    try:
        bar.set_title("sample.pdf - PageFlow")
        assert bar.title_label.text() == "sample.pdf - PageFlow"
        assert host.windowTitle() == "sample.pdf - PageFlow"
    finally:
        host.close()


def test_blank_tabbar_area_drags_window_but_tabs_and_buttons_do_not(qapp):
    from PySide6.QtWidgets import QHBoxLayout, QTabBar, QToolButton

    bar = _show_bar(760)
    strip = QWidget()
    layout = QHBoxLayout(strip)
    layout.setContentsMargins(0, 0, 0, 0)
    tabs = QTabBar(strip)
    tabs.setExpanding(False)
    tabs.addTab("笔记 1")
    tabs.addTab("讲义.pdf")
    button = QToolButton(strip)
    button.setText("+")
    layout.addWidget(tabs, 1)
    layout.addWidget(button)
    try:
        bar.set_center_widget(strip)
        QTest.qWait(30)

        tab_point = tabs.mapTo(bar, tabs.tabRect(0).center())
        assert bar._is_window_drag_area(tab_point) is False

        # 标签右侧的空隙（仍在 tabbar 内）= 可拖窗口
        empty_x = min(tabs.width() - 10, tabs.tabRect(1).right() + 20)
        empty_point = tabs.mapTo(bar, QPoint(empty_x, tabs.height() // 2))
        assert tabs.tabAt(QPoint(empty_x, tabs.height() // 2)) == -1
        assert bar._is_window_drag_area(empty_point) is True

        button_point = button.mapTo(bar, button.rect().center())
        assert bar._is_window_drag_area(button_point) is False

        title_point = bar.title_label.mapTo(bar, bar.title_label.rect().center())
        assert bar._is_window_drag_area(title_point) is True
    finally:
        bar.set_center_widget(None)
        strip.deleteLater()
        bar.close()
