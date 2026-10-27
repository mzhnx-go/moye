"""离屏测试：多文档工作区的两个纯 UI 组件（标签栏 / 双文档分屏）。

只测组件本身：session 用假对象（鸭子类型 .pane / .tab_title），
不加载主窗、不碰真实文档。
"""
import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from sketchpad.ui import theme
from sketchpad.workspace import SplitWorkspace, TabEntry, WorkspaceTabs

# 保住引用，防止被 GC
_ALIVE = []


class _S:
    """假 session：组件只认 .pane 与 .tab_title。"""

    def __init__(self, title):
        self.tab_title = title
        self.pane = QWidget()
        QVBoxLayout(self.pane).addWidget(QLabel(title))


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_tabs(qapp) -> WorkspaceTabs:
    tabs = WorkspaceTabs()
    _ALIVE.append(tabs)
    tabs.resize(900, 540)
    tabs.show()
    qapp.processEvents()
    return tabs


def _close_button(tabs: WorkspaceTabs, index: int):
    return tabs.tabbar.tabButton(index, QTabBar.RightSide)


@pytest.fixture
def tabs(qapp):
    widget = _make_tabs(qapp)
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


# --- 1. 加草稿/加文档：数量、页、关闭按钮 ---

def test_add_draft_and_entry(tabs):
    draft = tabs.add_draft(QWidget())
    session = _S("A")
    entry = tabs.add_entry([session], session.pane)

    assert tabs.tabbar.count() == 2
    assert tabs.stack.count() == 2
    assert tabs.entries == [draft, entry]
    assert tabs.current_entry is entry          # 新加的标签自动选中
    assert tabs.stack.currentWidget() is entry.page
    assert tabs.tabbar.tabText(0) == "笔记 1"
    assert tabs.tabbar.tabText(1) == "A"

    # 草稿：固定第一页，也有自绘关闭按钮（关闭前由主窗确认）
    assert draft.draft is True
    assert draft.is_split is False
    draft_close = _close_button(tabs, 0)
    assert isinstance(draft_close, QToolButton)
    assert draft_close.objectName() == "TabClose"
    # 文档：有自绘关闭按钮
    button = _close_button(tabs, 1)
    assert isinstance(button, QToolButton)
    assert button.objectName() == "TabClose"


def test_tabs_carry_dark_qss(tabs):
    """theme.QSS 里没有 QTabBar 规则，组件自带局部 QSS（objectName 限定，不泄漏）。"""
    qss = tabs.styleSheet()
    assert "QTabBar#WorkspaceTabBar" in qss
    assert theme.WINDOW_BG in qss
    assert theme.ACCENT_SOFT in qss or theme.ACCENT in qss
    assert theme.TEXT in qss


def test_add_entry_rejects_wrong_session_count(tabs):
    with pytest.raises(ValueError):
        tabs.add_entry([], QWidget())
    with pytest.raises(ValueError):
        tabs.add_entry([_S("A"), _S("B"), _S("C")], QWidget())


# --- 2. 信号：新建 / 关闭 / 分屏 ---

def test_signals_new_close_split(tabs):
    draft = tabs.add_draft(QWidget())
    session = _S("A")
    entry = tabs.add_entry([session], session.pane)

    close_seen, split_seen = [], []
    tabs.closeRequested.connect(close_seen.append)
    tabs.splitRequested.connect(split_seen.append)

    # 草稿当前：分屏按钮禁用，点了也不发
    tabs.set_current_entry(draft)
    assert tabs.split_button.isEnabled() is False
    QTest.mouseClick(tabs.split_button, Qt.LeftButton)
    assert split_seen == []

    tabs.set_current_entry(entry)
    assert tabs.split_button.isEnabled() is True
    QTest.mouseClick(tabs.split_button, Qt.LeftButton)
    assert split_seen == [entry]

    QTest.mouseClick(_close_button(tabs, 1), Qt.LeftButton)
    assert close_seen == [entry]
    assert tabs.tabbar.count() == 2             # 组件自己不删，等主窗确认

    # 草稿的 × 也走 closeRequested，由主窗决定确认/清空
    QTest.mouseClick(_close_button(tabs, 0), Qt.LeftButton)
    assert close_seen == [entry, draft]


def test_context_menu_actions(tabs):
    draft = tabs.add_draft(QWidget())
    session = _S("A")
    entry = tabs.add_entry([session], session.pane)

    # 草稿也能"关闭"（主窗负责确认），仍保留"关闭其他"
    draft_texts = [action.text() for action in tabs._make_tab_menu(draft).actions()
                   if not action.isSeparator()]
    assert draft_texts == ["重命名…", "关闭", "关闭其他"]

    doc_texts = [action.text() for action in tabs._make_tab_menu(entry).actions()
                 if not action.isSeparator()]
    assert doc_texts == ["重命名…", "打开文件所在位置", "关闭", "分屏…", "关闭其他"]

    # 文档标签右键"打开文件所在位置"发信号；草稿没有这条
    revealed = []
    tabs.revealRequested.connect(revealed.append)
    session.path = Path("A.pdf")
    reveal = next(action for action in tabs._make_tab_menu(entry).actions()
                  if action.text() == "打开文件所在位置")
    assert reveal.isEnabled() is True
    reveal.trigger()
    assert revealed == [entry]

    # 右键"关闭其他"发信号
    seen = []
    tabs.closeOthersRequested.connect(seen.append)
    menu = tabs._make_tab_menu(draft)
    menu.actions()[-1].trigger()
    assert seen == [draft]

    # 草稿菜单里的"关闭"走同一条 closeRequested（主窗判断草稿分支）
    closed = []
    tabs.closeRequested.connect(closed.append)
    tabs._make_tab_menu(draft).actions()[1].trigger()      # [0]=重命名 [1]=关闭
    assert closed == [draft]


# --- 2b. TabStrip / 新建草稿按钮 / 窗口辅助接口 ---

def test_strip_contains_bar_and_buttons(tabs):
    strip = tabs.strip
    assert strip.tabbar is tabs.tabbar
    assert not hasattr(strip, "new_button")   # 打开文件 + 已移除，走菜单 / Ctrl+O
    assert strip.split_button is tabs.split_button
    # tabbar 自身不再处理拖动，交给 TabDragController
    assert tabs.tabbar.isMovable() is False

    # 「新建草稿」按钮已从标题栏移除（图标与工具栏草稿纸撞脸），走菜单 / Ctrl+N
    assert not hasattr(tabs, "draft_button")
    assert tabs.split_button.objectName() == "TabSplitButton"
    assert tabs.split_button.size().width() == 24


def test_window_helpers_and_reorder(tabs):
    draft = tabs.add_draft(QWidget())
    first, second = _S("A"), _S("B")
    entry_a = tabs.add_entry([first], first.pane)
    entry_b = tabs.add_entry([second], second.pane)

    assert tabs.window_id == 0
    tabs.set_window_id(7)
    assert tabs.window_id == 7
    assert tabs.index_of(entry_b) == 2
    assert tabs.entry_by_id(entry_a.entry_id) is entry_a
    assert tabs.entry_by_id(-1) is None

    # 草稿钉死 0，文档按最终下标移动
    assert tabs.move_entry_to(entry_a, 2) is True
    assert tabs.entries == [draft, entry_b, entry_a]
    assert tabs.move_entry_to(draft, 2) is False
    assert tabs.entries[0] is draft


# --- 3. 切当前页 / 移除（page 不删） ---

def test_set_current_and_remove(tabs):
    draft = tabs.add_draft(QWidget())
    first, second = _S("A"), _S("B")
    entry_a = tabs.add_entry([first], first.pane)
    entry_b = tabs.add_entry([second], second.pane)

    seen = []
    tabs.currentChanged.connect(seen.append)

    tabs.set_current_entry(draft)
    assert tabs.current_entry is draft
    assert tabs.stack.currentWidget() is draft.page
    assert seen == [draft]

    tabs.set_current_entry(entry_b)
    assert tabs.stack.currentWidget() is entry_b.page
    assert seen == [draft, entry_b]

    # 移除非当前标签：页数/tab 数都减一，page 还活着
    tabs.remove_entry(entry_a)
    assert tabs.tabbar.count() == 2
    assert tabs.stack.count() == 2
    assert entry_a not in tabs.entries
    assert isValid(first.pane) is True
    assert tabs.stack.indexOf(first.pane) == -1
    assert tabs.current_entry is entry_b
    assert seen == [draft, entry_b]              # 移除别的标签不改当前页

    # 移除当前标签：落到剩下的页上
    tabs.remove_entry(entry_b)
    assert tabs.tabbar.count() == 1
    assert isValid(second.pane) is True
    assert tabs.current_entry is draft
    assert tabs.stack.currentWidget() is draft.page


# --- 4. 拖动重排：草稿回第 0 位 ---

def test_drag_reorder_pins_draft_first(tabs):
    draft = tabs.add_draft(QWidget())
    first, second = _S("A"), _S("B")
    entry_a = tabs.add_entry([first], first.pane)
    entry_b = tabs.add_entry([second], second.pane)

    # 把草稿拖到最后：组件应当场纠回 0
    tabs.tabbar.moveTab(0, 2)
    assert tabs.tabbar.count() == 3
    assert tabs.entries == [draft, entry_a, entry_b]
    assert tabs.entries[0].draft is True
    assert tabs.tabbar.tabText(0) == "笔记 1"

    # 文档之间可以正常换位，草稿仍留在 0
    tabs.tabbar.moveTab(1, 2)
    assert tabs.entries == [draft, entry_b, entry_a]
    assert tabs.tabbar.tabText(0) == "笔记 1"
    assert tabs.tabbar.tabText(1) == "B"
    assert tabs.tabbar.tabText(2) == "A"

    # 换位后页映射还是对的
    tabs.set_current_entry(entry_a)
    assert tabs.stack.currentWidget() is entry_a.page


# --- 5. 标题与脏标记 ---

def test_titles_and_dirty_marker(tabs):
    first, second = _S("A"), _S("B")
    entry = tabs.add_entry([first], first.pane)
    assert entry.title == "A"
    assert entry.is_split is False
    assert tabs.tabbar.tabText(0) == "A"
    assert tabs.tabbar.tabToolTip(0) == "A"       # 默认提示跟标题

    # 脏标记由主窗塞进 tab_title（组件不解析，只重读）
    first.tab_title = "● A"
    tabs.update_title(entry)
    assert entry.title == "● A"
    assert tabs.tabbar.tabText(0) == "● A"
    assert tabs.tabbar.tabToolTip(0) == "● A"

    # 分屏 entry：两个 session 用 " | " 拼
    split_page = QWidget()
    split_entry = tabs.add_entry([first, second], split_page)
    assert split_entry.is_split is True
    assert split_entry.sessions == [first, second]
    assert split_entry.title == "● A | B"
    assert tabs.tabbar.tabText(1) == "● A | B"

    # 显式 tooltip 优先；空串恢复默认
    tabs.set_tab_tooltip(entry, "D:/docs/a.pdf")
    assert tabs.tabbar.tabToolTip(0) == "D:/docs/a.pdf"
    tabs.set_tab_tooltip(entry, "")
    assert tabs.tabbar.tabToolTip(0) == "● A"


# --- 6. 分屏容器 ---

def test_split_workspace(qapp):
    workspace = SplitWorkspace()
    _ALIVE.append(workspace)
    workspace.resize(640, 420)
    workspace.show()
    qapp.processEvents()

    first, second = _S("A"), _S("B")
    workspace.set_sessions([first, second])

    assert workspace.splitter.count() == 2
    assert first.pane.parent() is not None
    assert second.pane.parent() is not None
    assert workspace.sessions == [first, second]
    assert workspace.title == "A | B"
    assert workspace.active_session is first

    sizes = workspace.splitter.sizes()
    assert abs(sizes[0] - sizes[1]) <= 1          # 初始各 50%

    seen = []
    workspace.activeChanged.connect(seen.append)

    # 点右侧 pane（QFrame 容器本身）
    right = workspace.splitter.widget(1)
    QTest.mouseClick(right, Qt.LeftButton, pos=right.rect().center())
    assert seen == [second]
    assert workspace.active_session is second

    # 点在左侧 pane 内部的子控件上也算激活这一侧
    QTest.mouseClick(first.pane, Qt.LeftButton, pos=first.pane.rect().center())
    assert seen == [second, first]
    assert workspace.active_session is first

    # 直接 set_active 也行；已经是活动侧就不重复发
    workspace.set_active(second)
    assert seen == [second, first, second]
    workspace.set_active(second)
    assert seen == [second, first, second]

    # 取消分屏按钮 / 右键菜单项
    unsplit = []
    workspace.unsplitRequested.connect(lambda: unsplit.append(True))
    QTest.mouseClick(workspace.unsplit_button, Qt.LeftButton)
    assert unsplit == [True]

    # 拆开后 pane 仍存活、parent 置 None、可以挂回普通标签页
    detached = workspace.detach_sessions()
    assert detached == [first, second]
    assert workspace.splitter.count() == 0
    assert isValid(first.pane) is True
    assert isValid(second.pane) is True
    assert first.pane.parent() is None
    assert second.pane.parent() is None
    assert workspace.unsplit_button.isVisible() is False
    assert workspace.title == ""


def test_split_workspace_requires_two_sessions(qapp):
    workspace = SplitWorkspace()
    _ALIVE.append(workspace)
    with pytest.raises(ValueError):
        workspace.set_sessions([_S("A")])


# --- 7. 不依赖主窗 ---

def test_components_do_not_import_main_window():
    root = Path(__file__).resolve().parents[1] / "sketchpad" / "workspace"
    forbidden_names = {"PdfView", "DocumentSession"}
    for name in ("__init__.py", "tabs.py", "split.py"):
        source = (root / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("sketchpad.app"), (name, alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith("sketchpad.app"), (name, module)
                for alias in node.names:
                    assert alias.name not in forbidden_names, (name, alias.name)
        assert "import sketchpad.app" not in source


def test_tab_entry_dataclass_shape():
    page = QWidget()
    entry = TabEntry(sessions=[_S("A")], page=page)
    assert entry.draft is False
    assert entry.title == "A"
    assert entry.is_split is False
    assert TabEntry(sessions=[], page=page, draft=True).title == "笔记 1"


def test_custom_title_wins_and_relabels_tab(tabs):
    """重命名后标签显示自定义名；清空则回退默认（文件名 / 笔记 1）。"""
    entry = tabs.add_entry([_S("A")], QWidget())
    default = entry.title
    entry.custom_title = "讲义 2026"
    tabs.update_title(entry)
    assert entry.title == "讲义 2026"
    assert tabs.tabbar.tabText(tabs._index_of(entry)) == "讲义 2026"

    entry.custom_title = None
    tabs.update_title(entry)
    assert tabs.tabbar.tabText(tabs._index_of(entry)) == default
