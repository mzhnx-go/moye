"""分屏容器（QSplitter）与笔记卡紧凑模式的离屏测试。

不建主窗：PDF 视图/卡片都用普通 QWidget 替身，只验几何、信号与最小宽度。
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.ui.note_card import NoteCard
from sketchpad.workspace.note_split import (DEFAULT_RATIO, NOTE_MIN_WIDTH,
                                            PDF_MIN_WIDTH, NoteSplitPane)
from sketchpad.workspace.session import DocumentSession, make_session_pane

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _FakeViewer(QWidget):
    """PDF 视图替身：只记 fit_width 有没有被调用。"""

    def __init__(self, name: str = "讲义.pdf") -> None:
        super().__init__()
        self.pdf_path = Path(name)
        self.fit_calls = 0

    def fit_width(self) -> None:
        self.fit_calls += 1


class _FakeSaver:
    def cancel(self) -> None:
        pass


def _show_pane(width: int = 1200, height: int = 760) -> NoteSplitPane:
    pane = NoteSplitPane(_FakeViewer())
    pane.resize(width, height)
    pane.show()
    QTest.qWait(30)
    _ALIVE.append(pane)
    return pane


def _card() -> QWidget:
    card = QWidget()
    card.setObjectName("FakeNoteCard")
    return card


def _send_mouse(widget: QWidget, kind, pos: QPoint,
                button, buttons) -> None:
    global_pos = QPointF(widget.mapToGlobal(pos))
    ev = QMouseEvent(kind, QPointF(pos), global_pos, button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def _drag_handle(pane: NoteSplitPane, dx: int) -> None:
    """把分隔条水平拖动 dx（负=PDF 变窄，正=PDF 变宽）。"""
    handle = pane.splitter.handle(1)
    start = handle.rect().center()
    _send_mouse(handle, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
    end = QPoint(start.x() + dx, start.y())
    _send_mouse(handle, QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton)
    _send_mouse(handle, QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)
    QApplication.processEvents()


# --- 结构 ---

def test_splitter_structure_matches_spec(qapp):
    viewer = _FakeViewer()
    pane = NoteSplitPane(viewer)
    _ALIVE.append(pane)
    splitter = pane.splitter
    assert splitter.objectName() == "NoteSplitSplitter"
    assert splitter.handleWidth() == 6
    assert splitter.childrenCollapsible() is False
    assert splitter.opaqueResize() is True
    assert splitter.count() == 2
    assert splitter.widget(0).objectName() == "NoteSplitPdfFrame"
    assert splitter.widget(1).objectName() == "NoteSplitNoteFrame"
    assert pane.view is viewer
    assert pane.is_note_open is False
    assert pane.note_frame.isHidden()
    assert pane.title_row.isHidden()
    assert pane.title_row.height() == 26
    assert pane.fit_button.text() == "适应分屏"
    assert pane.fit_button.toolTip() == "按分屏宽度适应（Ctrl+9）"


def test_show_note_adds_right_side_and_title_then_hide_returns_card(qapp):
    pane = _show_pane()
    card = _card()
    pane.show_note(card)
    QApplication.processEvents()
    QTest.qWait(30)

    assert pane.is_note_open is True
    assert pane.note_frame.isVisible()
    assert pane.title_row.isVisible()
    assert card.parent() is pane.note_frame
    assert pane.note_frame.rect().contains(card.geometry())

    returned = pane.hide_note()
    QApplication.processEvents()
    assert returned is card
    assert pane.is_note_open is False
    assert card.parent() is None                  # 摘父但不删除
    assert pane.note_frame.isHidden()
    assert pane.title_row.isHidden()
    card.objectName()                              # 仍可访问（未被 deleteLater）


def test_set_note_title_updates_file_label(qapp):
    pane = _show_pane()
    pane.show_note(_card())
    pane.set_note_title("第二章.pdf")
    assert pane.file_label.text() == "第二章.pdf"


# --- 比例 ---

def test_ratio_applied_with_real_width_minus_handle(qapp):
    pane = _show_pane(1200, 760)
    pane.show_note(_card())
    QApplication.processEvents()
    QTest.qWait(30)

    sizes = pane.splitter.sizes()
    available = pane.splitter.width() - pane.splitter.handleWidth()
    assert sum(sizes) == pytest.approx(available, abs=2)
    assert pane.ratio == pytest.approx(DEFAULT_RATIO, abs=0.005)
    assert sizes[0] / float(sum(sizes)) == pytest.approx(DEFAULT_RATIO, abs=0.005)

    pane.resize(1000, 760)                         # 窗口变窄，比例不丢
    QApplication.processEvents()
    QTest.qWait(30)
    assert pane.ratio == pytest.approx(DEFAULT_RATIO, abs=0.01)


def test_show_note_before_width_defers_ratio_to_event_loop(qapp):
    pane = NoteSplitPane(_FakeViewer())
    _ALIVE.append(pane)
    pane.show_note(_card())                        # 还没 show，没有真实宽度
    pane.resize(1200, 760)
    pane.show()
    QApplication.processEvents()
    QTest.qWait(50)
    assert pane.ratio == pytest.approx(DEFAULT_RATIO, abs=0.01)


def test_min_widths_hold_when_pane_is_narrow(qapp):
    pane = _show_pane(500, 600)
    pane.show_note(_card())
    QApplication.processEvents()
    QTest.qWait(30)

    sizes = pane.splitter.sizes()
    assert sizes[0] >= PDF_MIN_WIDTH
    assert sizes[1] >= NOTE_MIN_WIDTH
    # 即使分屏溢出到 pane 之外，两个最小宽度也不能互相压掉
    assert pane.splitter.minimumWidth() >= PDF_MIN_WIDTH + NOTE_MIN_WIDTH


def test_set_ratio_applies_and_emits(qapp):
    pane = _show_pane(1200, 760)
    pane.show_note(_card())
    QApplication.processEvents()
    QTest.qWait(30)
    seen: list[float] = []
    pane.ratioChanged.connect(seen.append)

    pane.set_ratio(0.50)
    QApplication.processEvents()
    QTest.qWait(20)
    assert pane.ratio == pytest.approx(0.50, abs=0.01)
    assert seen and seen[-1] == pytest.approx(pane.ratio, abs=0.01)


def test_drag_handle_changes_ratio_and_emits(qapp):
    pane = _show_pane(1200, 760)
    pane.show_note(_card())
    QApplication.processEvents()
    QTest.qWait(30)
    before = pane.ratio
    seen: list[float] = []
    pane.ratioChanged.connect(seen.append)

    _drag_handle(pane, -120)
    QTest.qWait(20)
    assert pane.ratio < before
    assert seen and seen[-1] == pytest.approx(pane.ratio, abs=0.01)


def test_double_click_handle_resets_to_remembered_ratio(qapp):
    pane = _show_pane(1200, 760)
    pane.show_note(_card())                       # 记忆比例 = 0.62
    QApplication.processEvents()
    QTest.qWait(30)

    _drag_handle(pane, 180)
    assert pane.ratio > 0.7

    handle = pane.splitter.handle(1)
    center = handle.rect().center()
    _send_mouse(handle, QEvent.MouseButtonDblClick, center,
                Qt.LeftButton, Qt.LeftButton)
    QApplication.processEvents()
    QTest.qWait(20)
    assert pane.ratio == pytest.approx(DEFAULT_RATIO, abs=0.01)


# --- 适应分屏按钮 ---

def test_fit_button_only_requests_fit(qapp):
    viewer = _FakeViewer()
    pane = NoteSplitPane(viewer)
    _ALIVE.append(pane)
    hits: list[int] = []
    pane.fitRequested.connect(lambda: hits.append(1))

    pane.fit_button.click()
    assert hits == [1]
    assert viewer.fit_calls == 0                  # 组件绝不自己调 fit_width


# --- 会话接线 ---

def test_make_session_pane_is_note_split_and_session_carries_ratio(qapp):
    view = _FakeViewer()
    pane = make_session_pane(view)
    _ALIVE.append(pane)
    assert isinstance(pane, NoteSplitPane)
    assert pane.view is view

    session = DocumentSession(Path("a.pdf"), view, _FakeSaver(), pane)
    assert session.note_split_ratio == pytest.approx(DEFAULT_RATIO)
    session.note_split_ratio = 0.5
    assert session.note_split_ratio == pytest.approx(0.5)


# --- 笔记卡紧凑模式 ---

def _note_card() -> NoteCard:
    card = NoteCard(Note("n-split", 10, 20), Settings())
    _ALIVE.append(card)
    return card


def test_card_split_mode_changes_hints_and_default_behavior(qapp):
    card = _note_card()
    try:
        assert card.minimumSizeHint() == QSize(420, 540)
        assert card.sizeHint() == QSize(560, 720)

        card.set_split_mode(True)
        assert card.minimumSizeHint() == QSize(340, 300)
        assert card.sizeHint() == QSize(340, 400)
        assert card.minimumSizeHint().width() == 340

        card.set_split_mode(False)
        assert card.minimumSizeHint() == QSize(420, 540)
        assert card.sizeHint() == QSize(560, 720)
    finally:
        card.close()


def test_tools_live_in_horizontal_scroll_area(qapp):
    card = _note_card()
    try:
        scroll = card.tools_scroll
        assert isinstance(scroll, QScrollArea)
        assert scroll.objectName() == "NoteToolsScroll"
        assert scroll.widget() is card.tools
        assert scroll.widgetResizable() is False
        assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        # 工具行控件仍在，现有测试/接线可以照旧访问
        for widget in (card.pen_button, card.color_buttons[0],
                       card.width_buttons[0], card.undo_button):
            assert widget is not None
            assert card.tools.isAncestorOf(widget)

        card.set_split_mode(True)
        card.resize(340, 400)
        card.show()
        QApplication.processEvents()
        QTest.qWait(50)
        assert card.size().width() == 340
        assert card.minimumSizeHint().width() == 340
        bar = scroll.horizontalScrollBar()
        assert bar.maximum() > bar.minimum()      # 340px 下工具行可横向滚动
        assert scroll.height() > card.tools.sizeHint().height()   # 滚动条高度让出来了
    finally:
        card.close()


def test_real_split_card_fits_note_column(qapp):
    """真实 NoteCard 放进右栏：340px 也能放下，工具行横向滚动，摘下来仍存活。"""
    pane = _show_pane(760, 720)
    card = _note_card()
    card.set_split_mode(True)
    card.set_resize_enabled(False)
    pane.show_note(card)
    QApplication.processEvents()
    QTest.qWait(80)

    assert pane.is_note_open is True
    assert card.parent() is pane.note_frame
    assert pane.splitter.sizes()[1] >= NOTE_MIN_WIDTH
    assert card.width() >= 340
    assert card.tools_scroll.horizontalScrollBar().maximum() > 0

    returned = pane.hide_note()
    assert returned is card                    # 只摘父，不删除
    assert card.parent() is None
    card.close()

