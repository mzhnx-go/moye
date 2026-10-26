"""离屏测试：目录面板与查找条。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.ui.find_bar import FindBar
from sketchpad.ui.toc_panel import TocPanel

# 保住引用，防止被 GC
_ALIVE = []

ENTRIES = [
    (1, "第一章", 0),
    (2, "第一节", 1),
    (2, "第二节", 2),
    (1, "第二章", 5),
]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def toc(qapp):
    panel = TocPanel()
    _ALIVE.append(panel)
    panel.resize(260, 400)
    panel.show()
    qapp.processEvents()
    yield panel
    panel.close()


@pytest.fixture
def findbar(qapp):
    bar = FindBar()
    _ALIVE.append(bar)
    bar.resize(480, 40)
    bar.show()
    qapp.processEvents()
    yield bar
    bar.close()


def test_toc_shape(toc):
    assert toc.objectName() == "TocPanel"
    assert toc.width() == 260
    assert toc.title_label.text() == "目录"
    assert toc.empty_label.text() == "此文档没有目录"
    assert toc.tree.isHeaderHidden() is True


def test_toc_nested_build(toc):
    toc.set_entries(ENTRIES)
    tree = toc.tree
    assert tree.topLevelItemCount() == 2
    root = tree.topLevelItem(0)
    assert root.text(0) == "第一章"
    assert root.childCount() == 2
    assert [root.child(i).text(0) for i in range(2)] == ["第一节", "第二节"]
    assert root.child(1).data(0, Qt.UserRole) == 2
    assert tree.topLevelItem(1).data(0, Qt.UserRole) == 5


def test_toc_click_child_emits_page(toc, qapp):
    toc.set_entries(ENTRIES)
    pages = []
    toc.pageRequested.connect(pages.append)
    child = toc.tree.topLevelItem(0).child(1)
    toc.tree.scrollToItem(child)
    qapp.processEvents()
    rect = toc.tree.visualItemRect(child)
    assert rect.isValid() is True
    QTest.mouseClick(toc.tree.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert pages == [2]


def test_toc_empty_state_initial():
    panel = TocPanel()
    _ALIVE.append(panel)
    assert panel.tree.isHidden() is True
    assert panel.empty_label.isHidden() is False


def test_toc_empty_state_toggles(qapp):
    panel = TocPanel()
    _ALIVE.append(panel)
    panel.set_entries(ENTRIES)
    assert panel.tree.isHidden() is False
    assert panel.empty_label.isHidden() is True
    panel.clear_entries()
    assert panel.tree.topLevelItemCount() == 0
    assert panel.tree.isHidden() is True
    assert panel.empty_label.isHidden() is False


def test_findbar_shape(findbar):
    assert findbar.objectName() == "FindBar"
    assert findbar.input.placeholderText() == "在 PDF 中查找（回车搜索 / 下一个）"
    assert findbar.count_label.text() == "0/0"
    assert findbar.close_btn.text() == ""
    assert findbar.close_btn.icon().isNull() is False
    assert findbar.close_btn.toolTip() == "关闭查找"


def test_findbar_close_icon_not_clipped_by_global_button_padding(qapp):
    """全局 QPushButton 的 12px 横向内边距曾把 24px 关闭键挤成 24×32、图标只剩几像素。"""
    from sketchpad.ui import theme

    qapp.setStyleSheet(theme.QSS)
    bar = FindBar()
    _ALIVE.append(bar)
    try:
        bar.show()
        qapp.processEvents()
        assert (bar.close_btn.width(), bar.close_btn.height()) == (24, 24)
        image = bar.close_btn.grab().toImage()
        dark = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if max(image.pixelColor(x, y).red(),
                   image.pixelColor(x, y).green(),
                   image.pixelColor(x, y).blue()) < 120
        )
        assert dark >= 20, f"关闭图标被裁掉，仅剩 {dark} 个深色像素"
    finally:
        bar.close()
        qapp.setStyleSheet("")


def test_findbar_hidden_by_default(qapp):
    bar = FindBar()
    _ALIVE.append(bar)
    assert bar.isHidden() is True


def test_findbar_typing_does_not_search_enter_starts(findbar):
    """打字不搜；回车才用当前文本搜索，再回车 = 下一个。"""
    searched: list[str] = []
    steps: list[str] = []
    findbar.searchRequested.connect(searched.append)
    findbar.nextRequested.connect(lambda: steps.append("next"))
    findbar.prevRequested.connect(lambda: steps.append("prev"))
    findbar.input.setText("abc")
    assert searched == []               # 打字只记文本，不启动全文搜索
    QTest.keyClick(findbar.input, Qt.Key_Return)
    assert searched == ["abc"]          # 回车才搜
    searched.clear()
    QTest.keyClick(findbar.input, Qt.Key_Return)
    assert steps == ["next"]
    assert searched == []               # 同查询回车不重复搜索
    QTest.keyClick(findbar.input, Qt.Key_Return, Qt.ShiftModifier)
    assert steps == ["next", "prev"]


def test_findbar_typing_after_search_invalidates_once(findbar):
    """改词立即清旧结果（发一次空查询），但不搜新词；新词等回车。"""
    searched: list[str] = []
    findbar.searchRequested.connect(searched.append)
    findbar.input.setText("abc")
    findbar.start_search()
    searched.clear()

    findbar.input.setText("abcd")
    assert searched == [""]             # 清旧命中/取消在途搜索
    findbar.input.setText("abcde")
    assert searched == [""]             # 后续击键不重复发
    QTest.keyClick(findbar.input, Qt.Key_Return)
    assert searched == ["", "abcde"]


def test_findbar_duplicate_text_no_emit(findbar):
    got = []
    findbar.searchRequested.connect(got.append)
    findbar.input.setText("abc")
    findbar.input.setText("abc")
    findbar._on_text_changed("abc")     # 同一文本再进一次，仍不发
    assert got == []
    assert findbar.start_search() is True
    assert got == ["abc"]
    assert findbar.start_search() is False
    assert got == ["abc"]


def test_findbar_search_button_starts_current_query(findbar):
    searched: list[str] = []
    findbar.searchRequested.connect(searched.append)
    findbar.input.setText("函数")
    assert searched == []                # 打字不搜
    findbar.search_btn.click()
    assert searched == ["函数"]
    findbar.search_btn.click()           # 再点一次仍然明确发起查找
    assert searched == ["函数", "函数"]


def test_findbar_search_button_empty_does_nothing(findbar):
    searched: list[str] = []
    findbar.searchRequested.connect(searched.append)
    findbar.search_btn.click()
    assert searched == []
    assert findbar.count_label.text() == "0/0"


def test_findbar_next_prev_emit(findbar):
    hits = []
    findbar.nextRequested.connect(lambda: hits.append("next"))
    findbar.prevRequested.connect(lambda: hits.append("prev"))
    findbar.next_btn.click()
    findbar.prev_btn.click()
    assert hits == ["next", "prev"]


def test_findbar_close_button(findbar):
    fired = []
    findbar.closed.connect(lambda: fired.append(1))
    findbar.close_btn.click()
    assert fired == [1]


def test_findbar_escape_closes_and_clears(findbar):
    fired = []
    searched: list[str] = []
    findbar.closed.connect(lambda: fired.append(1))
    findbar.searchRequested.connect(searched.append)
    findbar.input.setText("abc")
    searched.clear()
    QTest.keyClick(findbar.input, Qt.Key_Escape)
    assert fired == [1]
    assert findbar.input.text() == ""
    assert searched == []               # Esc 关闭不触发搜索
    assert findbar.count_label.text() == "0/0"


def test_findbar_set_count(findbar):
    findbar.set_count(2, 5)
    assert findbar.count_label.text() == "2/5"
    findbar.set_count(0, 0)
    assert findbar.count_label.text() == "0/0"


def test_findbar_clear_emits_empty_search_once(findbar):
    """清空输入发一次 searchRequested("")，并重置 _last_query。"""
    got: list[str] = []
    findbar.searchRequested.connect(got.append)
    findbar.input.setText("abc")
    findbar.start_search()
    got.clear()
    findbar.input.setText("")
    assert got == [""]
    assert findbar._last_query == ""
    findbar._on_text_changed("")        # 再进一次空串，不重复发
    assert got == [""]


def test_findbar_close_resets_without_search(findbar):
    """✕ 关闭：清输入/计数、发 closed，但不发搜索。"""
    searched: list[str] = []
    closed: list[int] = []
    findbar.searchRequested.connect(searched.append)
    findbar.closed.connect(lambda: closed.append(1))
    findbar.input.setText("abc")
    findbar.set_count(3, 9)
    searched.clear()
    findbar._close()
    assert searched == []
    assert closed == [1]
    assert findbar.input.text() == ""
    assert findbar._last_query == ""
    assert findbar.count_label.text() == "0/0"
    assert findbar.isHidden() is True


def test_findbar_reset_silent(findbar):
    """reset() 清输入/上次查询/计数，不发任何信号，也不隐藏。"""
    searched: list[str] = []
    closed: list[int] = []
    findbar.searchRequested.connect(searched.append)
    findbar.closed.connect(lambda: closed.append(1))
    findbar.input.setText("abc")
    findbar.set_count(2, 4)
    searched.clear()
    findbar.reset()
    assert searched == []
    assert closed == []
    assert findbar.input.text() == ""
    assert findbar._last_query == ""
    assert findbar.count_label.text() == "0/0"
    assert findbar.isHidden() is False


def test_findbar_set_count_truncated(findbar):
    findbar.set_count(3, 500, truncated=True)
    assert findbar.count_label.text() == "3/500+"
    assert findbar.count_label.toolTip() != ""
    assert "500" in findbar.count_label.toolTip()
    findbar.set_count(3, 500, truncated=False)
    assert findbar.count_label.text() == "3/500"
    assert findbar.count_label.toolTip() == ""

    # 截断后再 reset/关闭：tooltip 也要收掉，不能留在 0/0 上
    findbar.set_count(3, 500, truncated=True)
    findbar.reset()
    assert findbar.count_label.text() == "0/0"
    assert findbar.count_label.toolTip() == ""
    findbar.set_count(3, 500, truncated=True)
    findbar._close()
    assert findbar.count_label.text() == "0/0"
    assert findbar.count_label.toolTip() == ""


def test_findbar_focus_input(findbar, qapp):
    findbar.focus_input()
    qapp.processEvents()
    assert findbar.input.hasFocus() is True
