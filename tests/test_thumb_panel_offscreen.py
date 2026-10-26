# tests/test_thumb_panel_offscreen.py
"""缩略图侧栏离屏测试：占位项、窗口懒渲染、点击/定位、坏页跳过、预算封顶、清空。

样例 PDF 全在 tmp_path 现造；渲染契约见 sketchpad/pdf/document.py。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget

import sketchpad.ui.thumb_panel as thumb_panel_mod
from sketchpad.pdf.document import PdfError, PdfSource
from sketchpad.ui.thumb_panel import (MAX_PER_TICK, MAX_RENDERED_TOTAL, PANEL_WIDTH,
                                      SKIP_TIP, THUMB_H, THUMB_W, WINDOW_RADIUS, ThumbPanel)

PAGES = 12
BIG_PAGES = 350                 # 大文档：验证窗口懒渲染与总预算，别逼近 996 页真讲义
LANDSCAPE = (400.0, 300.0)      # 4:3，缩进 120×90 正好铺满
PORTRAIT = (300.0, 600.0)       # 1:2，等比后 45×90


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path, sizes):
    """按给定尺寸造 PDF，每页写一行字，方便肉眼核对缩略图。"""
    doc = pymupdf.open()
    for index, (width, height) in enumerate(sizes):
        page = doc.new_page(width=width, height=height)
        page.insert_text((20, 40), f"Page {index + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def pdf(tmp_path):
    return _make_pdf(tmp_path / "thumbs.pdf", [LANDSCAPE] * PAGES)


@pytest.fixture
def source(pdf):
    src = PdfSource(pdf)
    yield src
    src.close()


@pytest.fixture(scope="module")
def big_source(qapp, tmp_path_factory):
    """350 页大文档；窗口/预算类用例共用一份，避免重复造 PDF。"""
    path = _make_pdf(tmp_path_factory.mktemp("big") / "big.pdf",
                     [(120.0, 90.0)] * BIG_PAGES)
    src = PdfSource(path)
    yield src
    src.close()


@pytest.fixture
def panel(qapp):
    """每个用例一个真实控件；引用留在 fixture 里，别让 Python 回收。"""
    widget = ThumbPanel()
    yield widget
    widget.clear()
    widget.close()


def _drain(widget, limit=200):
    """反复跑 tick 直到 _render_batch 返回 0；超上限就当死循环。"""
    for _ in range(limit):
        if widget._render_batch() == 0:
            return
    raise AssertionError(f"{limit} 次 tick 还没排空，渲染没在收尾")


def test_widget_shape(panel):
    assert panel.objectName() == "ThumbPanel"
    assert panel.testAttribute(Qt.WA_StyledBackground) is True
    assert (panel.minimumWidth(), panel.maximumWidth()) == (PANEL_WIDTH, PANEL_WIDTH)
    assert PANEL_WIDTH == 150
    assert (THUMB_W, THUMB_H) == (120, 90)
    assert panel.title_label.text() == "页面"
    assert panel.list.viewMode() == QListWidget.IconMode
    assert panel.list.iconSize() == QSize(120, 90)
    assert panel.list.wordWrap() is False
    assert panel.list.uniformItemSizes() is True


def test_set_source_fills_placeholders_then_icons(panel, source):
    panel.set_source(source)
    assert panel.list.count() == PAGES == 12
    assert [panel.list.item(i).text() for i in range(PAGES)] == [
        str(i) for i in range(1, PAGES + 1)
    ]
    assert [panel.list.item(i).icon().isNull() for i in range(PAGES)] == [True] * PAGES

    _drain(panel)
    assert [panel.list.item(i).icon().isNull() for i in range(PAGES)] == [False] * PAGES
    assert panel.list.item(0).icon().availableSizes() == [QSize(120, 90)]


def test_one_tick_renders_at_most_four_pages(panel, source):
    panel.set_source(source)
    assert MAX_PER_TICK == 4
    assert panel._render_batch() == MAX_PER_TICK
    assert [panel.list.item(i).icon().isNull() for i in range(4)] == [False] * 4
    assert [panel.list.item(i).icon().isNull() for i in range(4, PAGES)] == [
        True
    ] * (PAGES - 4)


def test_click_emits_page_requested_and_set_current_is_silent(panel, source):
    panel.set_source(source)
    _drain(panel)
    got = []
    panel.pageRequested.connect(got.append)

    panel.list.itemClicked.emit(panel.list.item(2))
    assert got == [2]

    panel.set_current(4)
    assert panel.list.currentRow() == 4
    assert got == [2]


def test_mouse_click_on_thumbnail_emits_page_requested(qapp, source):
    widget = ThumbPanel()
    widget.resize(PANEL_WIDTH, 600)
    widget.show()
    try:
        widget.set_source(source)
        _drain(widget)
        got = []
        widget.pageRequested.connect(got.append)
        rect = widget.list.visualItemRect(widget.list.item(0))
        QTest.mouseClick(widget.list.viewport(), Qt.LeftButton,
                         Qt.NoModifier, rect.center())
        assert got == [0]
    finally:
        widget.clear()
        widget.close()


def test_set_current_out_of_range_is_silent_and_clears(panel, source):
    panel.set_source(source)
    _drain(panel)
    got = []
    panel.pageRequested.connect(got.append)
    panel.set_current(4)
    panel.set_current(PAGES + 9)
    assert panel.list.currentRow() == -1
    panel.set_current(-1)
    assert got == []


def test_clear_empties_and_stops_timer(panel, source):
    panel.set_source(source)
    assert panel.list.count() == PAGES
    assert panel._timer.isActive() is True

    panel.clear()
    assert panel.list.count() == 0
    assert panel._timer.isActive() is False
    assert panel._source is None


def test_clear_resets_render_state_and_set_source_starts_fresh(panel, source):
    panel.set_source(source)
    _drain(panel)
    assert panel._rendered == set(range(PAGES))
    assert len(panel._queue) == 0

    panel.clear()
    assert panel._rendered == set()          # 渲染集合/队列/计数全部归零
    assert len(panel._queue) == 0
    assert len(panel._pending) == 0
    assert panel._timer.isActive() is False

    panel.set_source(source)                 # 同一文档重新装载：当作全新开始
    assert panel._timer.isActive() is True
    assert [panel.list.item(i).icon().isNull() for i in range(PAGES)] == [True] * PAGES
    _drain(panel)
    assert panel._rendered == set(range(PAGES))
    assert [panel.list.item(i).icon().isNull() for i in range(PAGES)] == [False] * PAGES


def test_broken_page_is_skipped(panel, source, monkeypatch):
    real_render = PdfSource.render

    def broken_render(self, index, zoom):
        """第 2 页（0 起 1）渲染必炸，其余照旧。"""
        if index == 1:
            raise PdfError("坏页")
        return real_render(self, index, zoom)

    monkeypatch.setattr(PdfSource, "render", broken_render)
    panel.set_source(source)
    _drain(panel)

    assert panel.list.item(1).icon().isNull() is True
    assert [panel.list.item(i).icon().isNull() for i in (0, 2, 3, 11)] == [
        False,
        False,
        False,
        False,
    ]
    assert [panel.list.item(i).icon().isNull() for i in range(PAGES)].count(False) == (
        PAGES - 1
    )


def test_rendered_pages_are_not_rendered_again(panel, source, monkeypatch):
    """窗口已覆盖/重复 set_current 时，已有图标的页不重渲也不排队。"""
    calls = []
    real_render = PdfSource.render

    def counting_render(self, index, zoom):
        calls.append(index)
        return real_render(self, index, zoom)

    monkeypatch.setattr(PdfSource, "render", counting_render)
    panel.set_source(source)
    _drain(panel)
    assert calls == list(range(PAGES))

    panel.set_current(2)                     # 整个窗口都已渲过 → 无活可排
    panel.set_current(PAGES - 1)
    assert panel._timer.isActive() is False
    assert calls == list(range(PAGES))
    assert len(panel._rendered) == PAGES


def test_set_source_none_behaves_like_clear(panel, source):
    panel.set_source(source)
    _drain(panel)
    panel.set_source(None)
    assert panel.list.count() == 0
    assert panel._timer.isActive() is False
    assert panel._source is None
    assert panel._render_batch() == 0


def test_portrait_page_keeps_aspect(qapp, tmp_path):
    src = PdfSource(_make_pdf(tmp_path / "tall.pdf", [PORTRAIT]))
    widget = ThumbPanel()
    try:
        widget.set_source(src)
        _drain(widget)
        assert widget.list.item(0).icon().availableSizes() == [QSize(45, 90)]
    finally:
        widget.clear()
        widget.close()
        src.close()


def test_windowed_lazy_render_follows_current_page(qapp, big_source):
    """350 页：初始只渲首页窗口，跳到 300 后补渲 300 附近，总数不超预算，排空停表。"""
    widget = ThumbPanel()
    try:
        widget.set_source(big_source)
        assert widget.list.count() == BIG_PAGES
        _drain(widget)

        near_zero = range(0, WINDOW_RADIUS + 1)          # 首页 ±30 里能渲的全渲了
        assert [widget.list.item(i).icon().isNull() for i in near_zero] == [
            False
        ] * len(near_zero)
        assert len(widget._rendered) == WINDOW_RADIUS + 1
        for i in range(WINDOW_RADIUS + 1, BIG_PAGES):    # 远处仍是页码占位（无图标）
            item = widget.list.item(i)
            assert item.icon().isNull() is True
            assert item.text() == str(i + 1)

        widget.set_current(300)                          # 用户翻到第 301 页
        _drain(widget)
        near_300 = range(300 - WINDOW_RADIUS, 300 + WINDOW_RADIUS + 1)
        assert [widget.list.item(i).icon().isNull() for i in near_300] == [
            False
        ] * len(near_300)
        assert widget._rendered == set(near_zero) | set(near_300)
        assert len(widget._rendered) <= MAX_RENDERED_TOTAL
        assert widget._timer.isActive() is False         # 排空即停，不空转
        far = widget.list.item(100)                      # 没进过任何窗口的页不渲
        assert far.icon().isNull() is True
        assert far.toolTip() == "第 101 页"               # 预算没满，提示保持原样
    finally:
        widget.clear()
        widget.close()


def test_budget_cap_stops_queueing_and_marks_skipped(qapp, big_source, monkeypatch):
    """预算到顶：停表、不再排队，未渲页保留页码占位并给"已省略"提示。"""
    monkeypatch.setattr(thumb_panel_mod, "MAX_RENDERED_TOTAL", 10)  # 小预算，跑得快
    widget = ThumbPanel()
    try:
        widget.set_source(big_source)
        _drain(widget)

        rendered = {i for i in range(BIG_PAGES)
                    if not widget.list.item(i).icon().isNull()}
        assert rendered == set(range(10))
        assert widget._timer.isActive() is False
        for i in range(BIG_PAGES):
            item = widget.list.item(i)
            if i in rendered:
                assert item.toolTip() == f"第 {i + 1} 页"   # 已渲页提示不被改写
            else:
                assert item.toolTip() == SKIP_TIP

        widget.set_current(300)                 # 预算满后翻页：不排队、不启动定时器
        assert widget._timer.isActive() is False
        assert widget._render_batch() == 0
        assert len(widget._rendered) == 10
    finally:
        widget.clear()
        widget.close()


def test_overlapping_windows_do_not_queue_duplicates(qapp, big_source, monkeypatch):
    """先开首页窗口再跳第 21 页：重叠页只渲一次，队列无重复。"""
    calls = []
    real_render = PdfSource.render

    def counting_render(self, index, zoom):
        calls.append(index)
        return real_render(self, index, zoom)

    monkeypatch.setattr(PdfSource, "render", counting_render)
    widget = ThumbPanel()
    try:
        widget.set_source(big_source)           # 排 0..30
        widget.set_current(20)                  # 0..30 已在队/已渲，只补 31..50
        _drain(widget)
        assert sorted(calls) == list(range(51))
        assert len(calls) == len(set(calls))    # 每页至多渲一次
        assert widget._timer.isActive() is False
    finally:
        widget.clear()
        widget.close()
