"""独立笔记窗口的自绘外壳（离屏）：无边框标志、28px 标题行、拖动/双击最大化、
三个窗口按钮、边缘缩放过滤器与外框，以及卡片在标题行以下铺满。

窗口里的 `NoteCard` 仍是原组件：只多隐藏了重复的关闭键，其余按钮/信号不变。
真实的系统拖动与四边缩放（`startSystemMove` / `startSystemResize`）由 Windows
窗口管理器执行，离屏平台只能验证调用是否发出。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6
import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.ui.frameless import EdgeResizeFilter, WindowBorder
from sketchpad.ui.note_card import (NOTE_WINDOW_TITLE_HEIGHT, NoteWindow,
                                    NoteWindowTitleBar)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    win = NoteWindow(Note("n-win", 10, 20), Settings(), display_name="数学")
    win.show()
    QTest.qWait(40)
    yield win
    if shiboken6.isValid(win):
        win.close()


class FakeWindowHandle:
    """替身 QWindow：记录 startSystemMove/Resize 被调用（真实移动只能在 Windows 上看）。"""

    def __init__(self) -> None:
        self.moves = 0
        self.resizes: list = []

    def startSystemMove(self) -> bool:
        self.moves += 1
        return True

    def startSystemResize(self, edges) -> bool:
        self.resizes.append(edges)
        return True


def test_frameless_flags_without_translucent_background(window):
    flags = window.windowFlags()
    assert flags & Qt.Window
    assert flags & Qt.FramelessWindowHint
    # 不透明自绘底色：不要半透明窗口（否则会透出桌面/拖影）
    assert window.testAttribute(Qt.WA_TranslucentBackground) is False
    assert window.objectName() == "NoteWindow"


def test_titlebar_is_28px_with_label_and_window_buttons(window):
    bar = window.titlebar
    assert isinstance(bar, NoteWindowTitleBar)
    assert bar.objectName() == "NoteWindowTitleBar"
    assert bar.height() == NOTE_WINDOW_TITLE_HEIGHT == 28
    assert bar.title_label.text() == "笔记 · 数学"

    window.set_display_name("物理")
    assert bar.title_label.text() == "笔记 · 物理"
    assert window.windowTitle() == "笔记 · 物理"
    assert window.card.title_label.text() == "物理"

    for button, tip in ((bar.min_button, "最小化"),
                        (bar.max_button, "最大化"),
                        (bar.close_button, "关闭")):
        assert button.toolTip() == tip
        assert button.focusPolicy() == Qt.NoFocus
        assert button.window() is window
        assert 0 < button.width() <= 32
        assert 0 < button.height() <= 24


def test_close_button_closes_window_and_emits_closed_once(window):
    closed: list[int] = []
    window.closed.connect(lambda: closed.append(1))

    window.titlebar.close_button.click()
    assert closed == [1]
    assert window.isVisible() is False

    window.close()                      # 重复关闭不再发 closed
    assert closed == [1]


def test_minimize_button_calls_show_minimized(window, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(window, "showMinimized", lambda: calls.append(1))
    window.titlebar.min_button.click()
    assert calls == [1]


def test_maximize_button_toggles_state_and_icon(window):
    bar = window.titlebar
    assert window.isMaximized() is False
    assert bar.max_button.toolTip() == "最大化"

    bar.max_button.click()
    QTest.qWait(50)
    assert window.isMaximized() is True
    assert bar.max_button.toolTip() == "还原"

    bar.max_button.click()
    QTest.qWait(50)
    assert window.isMaximized() is False
    assert bar.max_button.toolTip() == "最大化"


def test_title_press_starts_system_move_and_double_click_maximizes(window, monkeypatch):
    bar = window.titlebar
    fake = FakeWindowHandle()
    monkeypatch.setattr(window, "windowHandle", lambda: fake)

    # 标题文字上按下：窗口拖动
    QTest.mouseClick(bar, Qt.LeftButton, pos=bar.title_label.geometry().center())
    assert fake.moves == 1

    # 直接点在 QLabel 上（QLabel 不处理鼠标，事件冒泡到标题行）：同样是拖动
    QTest.mouseClick(bar.title_label, Qt.LeftButton)
    assert fake.moves == 2

    # 标题行空白处按下：同样是窗口拖动
    QTest.mouseClick(bar, Qt.LeftButton, pos=QPoint(bar.width() // 2, 14))
    assert fake.moves == 3

    # 双击标题行空白处：最大化（不是拖动）
    QTest.mouseDClick(bar, Qt.LeftButton, pos=QPoint(bar.width() // 2, 14))
    QTest.qWait(50)
    assert window.isMaximized() is True
    assert bar.max_button.toolTip() == "还原"


def test_button_clicks_do_not_drag_or_maximize(window, monkeypatch):
    bar = window.titlebar
    fake = FakeWindowHandle()
    monkeypatch.setattr(window, "windowHandle", lambda: fake)
    called: list[int] = []
    monkeypatch.setattr(window, "showMinimized", lambda: called.append(1))

    QTest.mouseClick(bar.min_button, Qt.LeftButton)
    assert fake.moves == 0
    assert called == [1]

    QTest.mouseDClick(bar.min_button, Qt.LeftButton)
    assert fake.moves == 0
    assert window.isMaximized() is False

    # 按钮区域也不属于拖动区（坐标命中防御）
    center = bar.min_button.mapTo(bar, bar.min_button.rect().center())
    assert bar._is_window_drag_area(center) is False


def test_edge_resize_filter_and_border_fill_window(window):
    assert isinstance(window._edge_resize_filter, EdgeResizeFilter)
    assert isinstance(window._window_border, WindowBorder)
    assert window._window_border.geometry() == window.rect()

    window.resize(700, 900)
    QTest.qWait(40)
    assert window.size() == QSize(700, 900)
    assert window._window_border.geometry() == window.rect()


def test_card_fills_below_title_and_hides_duplicate_close(window):
    card = window.card
    assert card.parent() is window
    assert card.y() == NOTE_WINDOW_TITLE_HEIGHT
    assert card.geometry() == QRect(0, NOTE_WINDOW_TITLE_HEIGHT,
                                    window.width(),
                                    window.height() - NOTE_WINDOW_TITLE_HEIGHT)
    # 窗口标题行已有名称：卡片整行标题收起（不再"标题套标题"）
    assert card.header.isHidden()
    assert card.close_button.isHidden()
    assert card.expand_button.isHidden()
    assert card.window_button.isHidden()
    assert card.resize_enabled() is False

    # 2026-10-24 用户要求能缩得更小：窗口下限不再钳到卡片的最小可用尺寸
    assert window.minimumSize() == QSize(260, 300)
    assert window.card.minimumSize() == QSize(0, 0)
