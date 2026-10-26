"""离屏测试：标签拖拽状态机（6px 阈值 / 3s 合并预览 / 跨窗口 / 拖出）。

不跑真 ``QDrag.exec``：直接用 controller 的 ``press/move/release`` 测试缝，
``fire_merge_timer()`` 代替真实 3 秒等待。跨窗口判定依赖同进程几何命中，
两个标签栏用不同窗口坐标摆开。
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from sketchpad.workspace import WorkspaceTabs
from sketchpad.workspace.tabdrag import (
    DETACH_HINT_TEXT,
    DRAG_THRESHOLD,
    MIME_TYPE,
)

pytestmark = pytest.mark.usefixtures("qapp")


class _S:
    def __init__(self, title):
        self.tab_title = title
        self.pane = QWidget()
        QVBoxLayout(self.pane).addWidget(QLabel(title))


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def make_tabs(qapp):
    created = []

    def factory(x=0, y=0, window_id=0):
        tabs = WorkspaceTabs()
        tabs.set_window_id(window_id)
        tabs.setGeometry(x, y, 640, 360)
        tabs.show()
        qapp.processEvents()
        created.append(tabs)
        return tabs

    yield factory
    for tabs in created:
        tabs.drag_controller.cancel()
        tabs.close()
        tabs.deleteLater()
    qapp.processEvents()


def _doc(tabs, title):
    session = _S(title)
    entry = tabs.add_entry([session], session.pane)
    QApplication.processEvents()               # 让 QTabBar 完成布局，tabRect 才有尺寸
    return entry, session


def _center(tabs, index):
    bar = tabs.tabbar
    return bar.mapToGlobal(bar.tabRect(index).center())


def _right_edge(tabs, index):
    bar = tabs.tabbar
    rect = bar.tabRect(index)
    return bar.mapToGlobal(QPoint(rect.right() - 2, rect.center().y()))


def _left_edge(tabs, index):
    bar = tabs.tabbar
    rect = bar.tabRect(index)
    return bar.mapToGlobal(QPoint(rect.left() + 1, rect.center().y()))


def _empty_bar_point(tabs):
    bar = tabs.tabbar
    return bar.mapToGlobal(QPoint(bar.width() - 4, bar.height() // 2))


def _below_bar_point(tabs):
    bar = tabs.tabbar
    return bar.mapToGlobal(QPoint(bar.width() // 2, bar.height() + 30))


def _collect(controller):
    seen = {"reorder": [], "transfer": [], "preview": [], "merge": [], "detach": []}
    controller.reorderRequested.connect(
        lambda entry, index: seen["reorder"].append((entry, index)))
    controller.transferRequested.connect(
        lambda source, entry, target, index: seen["transfer"].append(
            (source, entry, target, index)))
    controller.mergePreviewChanged.connect(
        lambda source, target, text: seen["preview"].append((source, target, text)))
    controller.mergeRequested.connect(
        lambda source, entry, target, target_entry: seen["merge"].append(
            (source, entry, target, target_entry)))
    controller.detachRequested.connect(
        lambda source, entry, pos: seen["detach"].append(
            (source, entry, QPoint(pos))))
    return seen


# --- 阈值 / 重排 ---

def test_threshold_requires_six_pixels(make_tabs):
    tabs = make_tabs()
    entry, _ = _doc(tabs, "A")
    controller = tabs.drag_controller
    seen = _collect(controller)

    controller.press(entry, _center(tabs, 0))
    controller.move(_center(tabs, 0) + QPoint(DRAG_THRESHOLD - 1, 0))
    assert controller.is_dragging is False
    assert tabs.insertion_marker.isVisible() is False

    controller.release(_center(tabs, 0) + QPoint(DRAG_THRESHOLD - 1, 0))
    assert seen["reorder"] == []
    assert tabs.entries == [entry]


def test_reorder_request_between_tabs(make_tabs):
    tabs = make_tabs()
    a, _ = _doc(tabs, "A")
    b, _ = _doc(tabs, "B")
    c, _ = _doc(tabs, "C")
    controller = tabs.drag_controller
    seen = _collect(controller)

    controller.press(a, _center(tabs, 0))
    controller.move(_center(tabs, 0) + QPoint(DRAG_THRESHOLD, 0))
    assert controller.is_dragging is True
    assert tabs.insertion_marker.isVisible() is True

    controller.move(_right_edge(tabs, 2))
    controller.release(_right_edge(tabs, 2))

    assert seen["reorder"] == [(a, 2)]
    assert seen["transfer"] == [] and seen["merge"] == []
    assert tabs.entries == [b, c, a]           # controller 的重排信号已落到组件
    assert tabs.insertion_marker.isVisible() is False


def test_drop_on_empty_bar_area_reorders_to_end(make_tabs):
    tabs = make_tabs()
    a, _ = _doc(tabs, "A")
    b, _ = _doc(tabs, "B")
    c, _ = _doc(tabs, "C")
    controller = tabs.drag_controller
    seen = _collect(controller)

    controller.press(a, _center(tabs, 0))
    controller.move(_empty_bar_point(tabs))
    assert tabs.insertion_marker.isVisible() is True
    controller.release(_empty_bar_point(tabs))

    assert seen["reorder"] == [(a, 2)]
    assert tabs.entries == [b, c, a]


# --- 3 秒合并预览 ---

def test_merge_preview_after_three_seconds(make_tabs):
    tabs = make_tabs()
    a, session_a = _doc(tabs, "A")
    b, session_b = _doc(tabs, "B")
    controller = tabs.drag_controller
    seen = _collect(controller)
    entries_before = list(tabs.entries)
    sessions_before = list(a.sessions)

    controller.press(a, _center(tabs, 0))
    controller.move(_center(tabs, 1))
    controller.fire_merge_timer()

    assert controller.merge_preview_active is True
    assert tabs.preview_entry is b
    assert tabs.preview_text == "A | B"
    assert tabs.tabbar.tabText(1) == "A | B"
    assert tabs.preview_bar.isVisible() is True
    assert tabs.preview_bar.height() == 2
    assert seen["preview"] == [(a, b, "A | B")]
    # 预览只改渲染：session / page / 顺序都没动
    assert tabs.entries == entries_before
    assert a.sessions == sessions_before
    assert session_b.pane.parent() is not None
    assert seen["merge"] == [] and seen["reorder"] == []

    controller.release(_center(tabs, 1))
    assert seen["merge"] == [(tabs, a, tabs, b)]
    assert tabs.tabbar.tabText(1) == "B"
    assert tabs.preview_bar.isVisible() is False
    assert tabs.preview_entry is None
    assert tabs.insertion_marker.isVisible() is False
    assert controller.hint_visible is False
    assert tabs.entries == entries_before       # 合并由主窗执行，controller 只发信号


def test_preview_text_survives_title_refresh(make_tabs):
    tabs = make_tabs()
    a, _ = _doc(tabs, "A")
    b, session_b = _doc(tabs, "B")
    controller = tabs.drag_controller

    controller.press(a, _center(tabs, 0))
    controller.move(_center(tabs, 1))
    controller.fire_merge_timer()
    assert tabs.tabbar.tabText(1) == "A | B"

    session_b.tab_title = "● B"
    tabs.update_title(b)
    assert tabs.tabbar.tabText(1) == "A | B"     # 预览文字不被标题刷新冲掉

    tabs.clear_merge_preview()
    assert tabs.tabbar.tabText(1) == "● B"


def test_release_before_timeout_reorders_instead_of_merging(make_tabs):
    tabs = make_tabs()
    a, _ = _doc(tabs, "A")
    b, _ = _doc(tabs, "B")
    controller = tabs.drag_controller
    seen = _collect(controller)

    controller.press(a, _center(tabs, 0))
    controller.move(_right_edge(tabs, 1))
    controller.release(_right_edge(tabs, 1))

    assert seen["merge"] == []
    assert seen["preview"] == []
    assert seen["reorder"] == [(a, 1)]
    assert tabs.tabbar.tabText(1) == "A"           # 顺序真的换了
    assert tabs.entries == [b, a]


def test_moving_away_cancels_preview(make_tabs):
    tabs = make_tabs()
    a, _ = _doc(tabs, "A")
    b, _ = _doc(tabs, "B")
    controller = tabs.drag_controller
    seen = _collect(controller)

    controller.press(a, _center(tabs, 0))
    controller.move(_center(tabs, 1))
    controller.fire_merge_timer()
    assert tabs.tabbar.tabText(1) == "A | B"

    controller.move(_below_bar_point(tabs))
    assert tabs.preview_bar.isVisible() is False
    assert tabs.tabbar.tabText(1) == "B"
    assert seen["preview"][-1] == (a, b, "")

    controller.release(_below_bar_point(tabs))
    assert seen["merge"] == [] and seen["reorder"] == []
    assert tabs.entries == [a, b]


def test_escape_cancels_preview(make_tabs):
    tabs = make_tabs()
    a, _ = _doc(tabs, "A")
    b, _ = _doc(tabs, "B")
    controller = tabs.drag_controller
    seen = _collect(controller)

    controller.press(a, _center(tabs, 0))
    controller.move(_center(tabs, 1))
    controller.fire_merge_timer()
    assert controller.merge_preview_active is True

    controller.release(_center(tabs, 1), canceled=True)
    assert controller.state == "idle"
    assert controller.merge_preview_active is False
    assert tabs.tabbar.tabText(1) == "B"
    assert tabs.preview_bar.isVisible() is False
    assert tabs.insertion_marker.isVisible() is False
    assert seen["merge"] == [] and seen["reorder"] == []
    assert tabs.entries == [a, b]


# --- 草稿 / 分屏约束 ---

def test_draft_cannot_be_dragged(make_tabs):
    tabs = make_tabs()
    draft = tabs.add_draft(QWidget())
    a, _ = _doc(tabs, "A")
    controller = tabs.drag_controller
    seen = _collect(controller)

    assert controller.press(draft, _center(tabs, 0)) is False
    controller.move(_center(tabs, 1) + QPoint(20, 0))
    assert controller.is_dragging is False
    controller.release(_center(tabs, 1))

    assert seen == {"reorder": [], "transfer": [], "preview": [],
                    "merge": [], "detach": []}
    assert tabs.entries[0] is draft
    # 强行移动也钉回 0
    assert tabs.move_entry_to(draft, 1) is False
    assert tabs.entries[0] is draft


def test_split_tab_cannot_merge(make_tabs):
    tabs = make_tabs()
    s1, s2, s3 = _S("A"), _S("B"), _S("C")
    split = tabs.add_entry([s1, s2], QWidget())
    c, _ = _doc(tabs, "C")
    controller = tabs.drag_controller
    seen = _collect(controller)

    # 分屏标签作为源：悬停 3s 也不进预览
    controller.press(split, _center(tabs, 0))
    controller.move(_center(tabs, 1))
    controller.fire_merge_timer()
    assert controller.merge_preview_active is False
    assert seen["preview"] == []
    assert tabs.tabbar.tabText(1) == "C"
    controller.cancel()

    # 分屏标签作为目标：同样拒绝
    controller.press(c, _center(tabs, 1))
    controller.move(_center(tabs, 0))
    controller.fire_merge_timer()
    assert controller.merge_preview_active is False
    assert seen["preview"] == []
    controller.release(_center(tabs, 0), canceled=True)
    assert seen["merge"] == []


# --- 跨窗口 ---

def test_cross_window_transfer(make_tabs):
    source = make_tabs(0, 0, window_id=1)
    target = make_tabs(700, 40, window_id=2)
    a, _ = _doc(source, "A")
    x, _ = _doc(target, "X")
    y, _ = _doc(target, "Y")
    controller = source.drag_controller
    seen = _collect(controller)

    controller.press(a, _center(source, 0))
    controller.move(_center(source, 0) + QPoint(DRAG_THRESHOLD, 0))
    controller.move(_right_edge(target, 1))

    assert target.insertion_marker.isVisible() is True
    assert source.insertion_marker.isVisible() is False

    controller.release(_right_edge(target, 1))
    assert seen["transfer"] == [(source, a, target, 2)]
    assert seen["merge"] == []
    assert source.entries == [a]                # 落点由主窗执行，组件不自行搬
    assert target.entries == [x, y]
    assert target.insertion_marker.isVisible() is False


def test_cross_window_merge_preview(make_tabs):
    source = make_tabs(0, 0, window_id=1)
    target = make_tabs(700, 40, window_id=2)
    a, _ = _doc(source, "A")
    x, _ = _doc(target, "X")
    y, _ = _doc(target, "Y")
    controller = source.drag_controller
    seen = _collect(controller)

    controller.press(a, _center(source, 0))
    controller.move(_center(source, 0) + QPoint(DRAG_THRESHOLD, 0))
    controller.move(_center(target, 1))
    controller.fire_merge_timer()

    assert target.tabbar.tabText(1) == "A | Y"
    assert target.preview_bar.isVisible() is True
    assert seen["preview"] == [(a, y, "A | Y")]

    controller.release(_center(target, 1))
    assert seen["merge"] == [(source, a, target, y)]
    assert target.tabbar.tabText(1) == "Y"
    assert target.entries == [x, y]


# --- 拖出所有窗口 ---

def test_detach_outside_all_windows(make_tabs):
    tabs = make_tabs(0, 0, window_id=3)
    a, _ = _doc(tabs, "A")
    controller = tabs.drag_controller
    seen = _collect(controller)
    outside = QPoint(4000, 3000)

    controller.press(a, _center(tabs, 0))
    controller.move(outside)
    assert controller.hint_visible is True
    assert controller.detach_hint.text() == DETACH_HINT_TEXT
    assert tabs.insertion_marker.isVisible() is False

    controller.release(outside)
    assert len(seen["detach"]) == 1
    source, entry, pos = seen["detach"][0]
    assert source is tabs and entry is a and pos == outside
    assert controller.hint_visible is False
    assert tabs.entries == [a]


def test_detach_hint_hides_when_back_inside(make_tabs):
    tabs = make_tabs(0, 0, window_id=3)
    a, _ = _doc(tabs, "A")
    controller = tabs.drag_controller
    seen = _collect(controller)

    controller.press(a, _center(tabs, 0))
    controller.move(QPoint(4000, 3000))
    assert controller.hint_visible is True
    controller.move(_center(tabs, 0) + QPoint(DRAG_THRESHOLD, 0))
    assert controller.hint_visible is False
    controller.release(_center(tabs, 0) + QPoint(DRAG_THRESHOLD, 0))
    assert seen["detach"] == []


# --- 插入线 / MIME ---

def test_insertion_marker_visibility(make_tabs):
    tabs = make_tabs()
    a, _ = _doc(tabs, "A")
    b, _ = _doc(tabs, "B")
    controller = tabs.drag_controller

    controller.press(a, _center(tabs, 0))
    controller.move(_left_edge(tabs, 1))
    assert tabs.insertion_marker.isVisible() is True
    assert tabs.insertion_marker.width() == 2
    assert tabs.insertion_marker.geometry().x() == tabs.tabbar.tabRect(1).left()

    controller.cancel()
    assert tabs.insertion_marker.isVisible() is False


def test_mime_payload(make_tabs):
    tabs = make_tabs(window_id=5)
    a, _ = _doc(tabs, "A")
    controller = tabs.drag_controller

    mime = controller.mime_data(a)
    assert mime.hasFormat(MIME_TYPE)
    payload = json.loads(bytes(mime.data(MIME_TYPE)).decode("utf-8"))
    assert payload == {"window_id": 5, "entry_id": a.entry_id}
    assert tabs.entry_by_id(payload["entry_id"]) is a
