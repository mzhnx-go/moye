# tests/test_menubar_offscreen.py
"""菜单栏离屏测试：菜单顺序、条目文案、模式启停、工具联动（v2 设计文档 §6）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu

from sketchpad.ui import theme
from sketchpad.ui.menubar import Menubar

MENU_TITLES = ["文件", "编辑", "视图", "导航", "工具", "设置", "帮助"]
WIDTH_TEXTS = ["细", "中", "粗", "特粗"]

# 只在文档模式可用的条目（缩放三项也只服务 PDF）
DOCUMENT_ONLY = [
    "save_action", "export_action", "close_doc_action",
    "prev_page_action", "next_page_action", "goto_page_action",
    "fit_width_action", "zoom_in_action", "zoom_out_action", "actual_size_action",
    "find_action", "toc_action", "thumbs_action", "note_action",
]

# 其余条目在两种模式下都可用
ALWAYS_ENABLED = [
    "open_pdf_action", "new_draft_action", "undo_action", "redo_action", "fullscreen_action",
    "pen_action", "highlighter_action", "eraser_action", "clear_canvas_action",
    "settings_action", "quit_action", "shortcuts_action", "about_action",
]

ACTION_TEXTS = {
    "open_pdf_action": "打开 PDF…",
    "new_draft_action": "新建笔记",
    "save_action": "保存批注",
    "export_action": "导出 PDF…",
    "close_doc_action": "关闭文档",
    "settings_action": "设置",
    "quit_action": "退出全部实例",
    "undo_action": "撤销",
    "redo_action": "重做",
    "find_action": "查找…",
    "zoom_in_action": "放大",
    "zoom_out_action": "缩小",
    "actual_size_action": "实际大小",
    "fit_width_action": "适应页宽",
    "fullscreen_action": "全屏",
    "toc_action": "目录",
    "thumbs_action": "页面缩略图",
    "prev_page_action": "上一页",
    "next_page_action": "下一页",
    "goto_page_action": "跳转到页…",
    "pen_action": "画笔",
    "highlighter_action": "荧光笔",
    "select_action": "鼠标（平移/选字）",
    "note_action": "插入笔记",
    "eraser_action": "橡皮",
    "clear_canvas_action": "清空画布",
    "shortcuts_action": "快捷键一览",
    "about_action": "关于",
}

# 发出 actionTriggered 的稳定 id，顺序即触发顺序
ID_ACTIONS = [
    ("open_pdf_action", "open_pdf"),
    ("new_draft_action", "new_draft"),
    ("save_action", "save"),
    ("export_action", "export"),
    ("close_doc_action", "close_doc"),
    ("settings_action", "settings"),
    ("quit_action", "quit"),
    ("undo_action", "undo"),
    ("redo_action", "redo"),
    ("find_action", "find"),
    ("zoom_in_action", "zoom_in"),
    ("zoom_out_action", "zoom_out"),
    ("actual_size_action", "actual_size"),
    ("fit_width_action", "fit_width"),
    ("fullscreen_action", "fullscreen"),
    ("toc_action", "toc"),
    ("thumbs_action", "thumbs"),
    ("prev_page_action", "prev_page"),
    ("next_page_action", "next_page"),
    ("goto_page_action", "goto_page"),
    ("clear_canvas_action", "clear_canvas"),
    ("shortcuts_action", "shortcuts"),
    ("about_action", "about"),
]


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def bar(qapp):
    """每个用例一个新菜单栏；不显示也能触发动作与读状态。

    收尾必须 close：离屏平台对活动窗口很敏感，遗留的顶层菜单栏会让后面的
    Ctrl+Z 用例收不到快捷键（实测全量跑时两条用例因此变红）。
    """
    widget = Menubar()
    yield widget
    widget.close()
    qapp.processEvents()


def test_menu_titles_and_order(bar):
    titles = [action.text() for action in bar.actions()]
    assert titles == MENU_TITLES
    assert "插件" not in titles


def test_every_action_has_its_chinese_text(bar):
    assert {name: getattr(bar, name).text() for name in ACTION_TEXTS} == ACTION_TEXTS
    assert [action.text() for action in bar.width_actions] == WIDTH_TEXTS


def test_recent_menu_is_an_empty_menu_in_file_menu(bar):
    assert isinstance(bar.recent_menu, QMenu) is True
    assert bar.recent_menu.title() == "最近打开"
    assert bar.recent_menu.actions() == []
    assert bar.recent_menu.menuAction() in bar.file_menu.actions()


def test_dead_page_menu_is_gone(bar):
    """「页面」菜单只有一个永远置灰的条目，整条已移除。"""
    assert "页面" not in [action.text() for action in bar.actions()]
    assert getattr(bar, "page_menu", None) is None
    assert getattr(bar, "page_info_action", None) is None
    assert not hasattr(bar, "clear_page_action")


def test_clear_action_lives_only_in_tools_and_follows_mode(bar):
    """清空只有一个入口：工具菜单；文案按模式切换。"""
    assert bar.clear_canvas_action in bar.tools_menu.actions()
    assert bar.clear_canvas_action not in bar.edit_menu.actions()
    assert bar.clear_canvas_action.isEnabled() is True

    bar.set_mode("document")
    assert bar.clear_canvas_action.text() == "清空本页"
    assert bar.clear_canvas_action.isEnabled() is True

    bar.set_mode("draft")
    assert bar.clear_canvas_action.text() == "清空画布"

    labels = [action.text() for action in bar.findChildren(QAction)]
    assert labels.count("清空画布") + labels.count("清空本页") == 1


def test_find_action_closes_the_edit_menu(bar):
    """编辑菜单：撤销 / 重做 / 查找，查找后面不再挂清空。"""
    actions = bar.edit_menu.actions()
    assert [a.text() for a in actions if not a.isSeparator()] == [
        "撤销", "重做", "查找…"]
    assert actions.index(bar.find_action) == len(actions) - 1


def test_thumbs_opens_the_view_menu_as_checkable(bar):
    """页面缩略图在视图菜单最前，与缩放组之间隔一条分隔线，默认不勾。"""
    actions = bar.view_menu.actions()
    assert [a.text() for a in actions if not a.isSeparator()] == [
        "页面缩略图", "放大", "缩小", "实际大小", "适应页宽", "阅读模式", "全屏"]
    assert actions.index(bar.thumbs_action) == 0
    assert actions[1].isSeparator() is True
    assert bar.thumbs_action.isCheckable() is True
    assert bar.thumbs_action.isChecked() is False


def test_toc_opens_the_navigate_menu_as_checkable(bar):
    """目录已移到导航菜单最前，后面才是上一页/下一页。"""
    actions = bar.navigate_menu.actions()
    assert [a.text() for a in actions if not a.isSeparator()] == [
        "目录", "上一页", "下一页", "跳转到页…"]
    assert actions.index(bar.toc_action) == 0
    assert actions[1].isSeparator() is True
    assert bar.toc_action not in bar.view_menu.actions()
    assert bar.toc_action.isCheckable() is True
    assert bar.toc_action.isChecked() is False


def test_toc_and_thumbs_toggle_and_emit_ids(bar):
    bar.set_mode("document")
    got = []
    bar.actionTriggered.connect(got.append)
    bar.toc_action.trigger()
    bar.thumbs_action.trigger()
    assert [bar.toc_action.isChecked(), bar.thumbs_action.isChecked()] == [True, True]
    bar.toc_action.trigger()
    assert [bar.toc_action.isChecked(), bar.thumbs_action.isChecked()] == [False, True]
    assert got == ["toc", "thumbs", "toc"]


def test_document_mode_enables_document_actions(bar):
    bar.set_mode("document")
    assert [getattr(bar, name).isEnabled() for name in DOCUMENT_ONLY] == [True] * len(DOCUMENT_ONLY)
    assert [getattr(bar, name).isEnabled() for name in ALWAYS_ENABLED] == [True] * len(ALWAYS_ENABLED)
    assert bar.navigate_menu.isEnabled() is True


def test_draft_mode_disables_exactly_document_actions(bar):
    bar.set_mode("document")
    bar.set_mode("draft")
    assert [getattr(bar, name).isEnabled() for name in DOCUMENT_ONLY] == [False] * len(DOCUMENT_ONLY)
    assert [getattr(bar, name).isEnabled() for name in ALWAYS_ENABLED] == [True] * len(ALWAYS_ENABLED)
    assert bar.navigate_menu.isEnabled() is False


def test_switching_back_to_document_restores_actions(bar):
    bar.set_mode("draft")
    bar.set_mode("document")
    assert [getattr(bar, name).isEnabled() for name in DOCUMENT_ONLY] == [True] * len(DOCUMENT_ONLY)


def test_invalid_mode_raises_value_error(bar):
    with pytest.raises(ValueError):
        bar.set_mode("bogus")


def test_non_tool_actions_emit_their_stable_ids(bar):
    bar.set_mode("document")
    got = []
    bar.actionTriggered.connect(got.append)
    for name, _ in ID_ACTIONS:
        getattr(bar, name).trigger()
    assert got == [key for _, key in ID_ACTIONS]


def test_pen_eraser_emit_action_id_and_tool(bar):
    assert [action.isCheckable() for action in (bar.pen_action, bar.eraser_action)] == [True, True]
    triggered, tools = [], []
    bar.actionTriggered.connect(triggered.append)
    bar.toolChosen.connect(tools.append)
    bar.eraser_action.trigger()
    assert (bar.pen_action.isChecked(), bar.eraser_action.isChecked()) == (False, True)
    bar.pen_action.trigger()
    assert (bar.pen_action.isChecked(), bar.eraser_action.isChecked()) == (True, False)
    assert triggered == ["eraser", "pen"]
    assert tools == ["eraser", "pen"]


def test_width_actions_emit_action_id_and_float(bar):
    assert [action.isCheckable() for action in bar.width_actions] == [True] * 4
    triggered, widths = [], []
    bar.actionTriggered.connect(triggered.append)
    bar.widthChosen.connect(widths.append)
    for action in bar.width_actions:
        action.trigger()
    assert triggered == ["width_0", "width_1", "width_2", "width_3"]
    assert widths == [1.5, 2.0, 4.0, 8.0]


def test_set_tool_checks_only_that_tool_and_stays_silent(bar):
    got = []
    bar.actionTriggered.connect(got.append)
    bar.set_tool("eraser")
    assert (bar.pen_action.isChecked(), bar.eraser_action.isChecked()) == (False, True)
    bar.set_tool("pen")
    assert (bar.pen_action.isChecked(), bar.eraser_action.isChecked()) == (True, False)
    assert got == []


def test_set_width_index_checks_only_the_third_and_stays_silent(bar):
    got = []
    bar.actionTriggered.connect(got.append)
    bar.widthChosen.connect(got.append)
    bar.set_width_index(2)
    assert [action.isChecked() for action in bar.width_actions] == [False, False, True, False]
    assert got == []


def test_set_tool_and_width_reject_unknown_values(bar):
    with pytest.raises(ValueError):
        bar.set_tool("brush")
    with pytest.raises(ValueError):
        bar.set_width_index(4)


def test_scoped_qss_styles_the_bar_and_its_menus_for_dark_theme(bar):
    qss = bar.styleSheet()
    assert bar.objectName() == "Menubar"
    assert "QMenuBar#Menubar" in qss
    assert "QMenuBar#Menubar QMenu" in qss
    assert theme.TEXT in qss
    assert "background: transparent" in qss
    assert "border-bottom" not in qss.split("QMenuBar#Menubar {", 1)[1].split("}", 1)[0]
    assert theme.ACCENT_SOFT in qss


def test_shortcuts_follow_the_v2_spec(bar):
    """菜单里展示的组合键（真正绑定在 app._install_shortcuts）；单键留在画布层。"""
    expected = {
        "open_pdf_action": "Ctrl+O",
        "save_action": "Ctrl+S",
        "export_action": "Ctrl+Shift+E",
        "undo_action": "Ctrl+Z",
        "redo_action": "Ctrl+Y",
        "find_action": "Ctrl+F",
        "prev_page_action": "PgUp",
        "next_page_action": "PgDown",   # Qt 的键名是 PgDown，不是 PgDn
        "goto_page_action": "Ctrl+G",
        "zoom_in_action": "Ctrl+=",
        "zoom_out_action": "Ctrl+-",
        "actual_size_action": "Ctrl+0",
        "fit_width_action": "Ctrl+9",
        "fullscreen_action": "F11",
    }
    assert {name: getattr(bar, name).shortcut().toString() for name in expected} == expected
    # 重做同时挂 Ctrl+Shift+Z（与 Ctrl+Y 等价）
    assert [sequence.toString() for sequence in bar.redo_action.shortcuts()] == [
        "Ctrl+Y", "Ctrl+Shift+Z"]



def test_select_action_is_document_only_and_emits_select(bar):
    """「选择文字」已升级成鼠标工具：文案带「鼠标」、键位仍是 V、只在文档模式可用。"""
    assert "鼠标" in bar.select_action.text()
    assert bar.select_action.shortcut().toString() == "V"
    bar.set_mode("draft")
    assert bar.select_action.isEnabled() is False
    bar.set_mode("document")
    assert bar.select_action.isEnabled() is True
    assert bar.select_action.isCheckable() is True

    triggered, tools = [], []
    bar.actionTriggered.connect(triggered.append)
    bar.toolChosen.connect(tools.append)
    bar.select_action.trigger()
    assert triggered == ["select"]
    assert tools == ["select"]
    assert bar.select_action.isChecked() is True
    assert (bar.pen_action.isChecked(), bar.eraser_action.isChecked()) == (False, False)


def test_set_tool_select_checks_only_it_and_stays_silent(bar):
    got = []
    bar.actionTriggered.connect(got.append)
    bar.set_tool("select")
    assert (bar.pen_action.isChecked(), bar.select_action.isChecked(),
            bar.eraser_action.isChecked()) == (False, True, False)
    assert got == []


def test_note_action_is_document_only_and_emits_note(bar):
    """插入笔记：工具菜单可勾选、快捷键 N，草稿模式禁用；点一下发 note。"""
    assert bar.note_action.text() == "插入笔记"
    assert bar.note_action in bar.tools_menu.actions()
    assert bar.note_action.isCheckable() is True
    assert bar.note_action.shortcut().toString() == "N"

    bar.set_mode("draft")
    assert bar.note_action.isEnabled() is False
    bar.set_mode("document")
    assert bar.note_action.isEnabled() is True

    triggered, tools = [], []
    bar.actionTriggered.connect(triggered.append)
    bar.toolChosen.connect(tools.append)
    bar.note_action.trigger()
    assert triggered == ["note"]
    assert tools == ["note"]
    assert bar.note_action.isChecked() is True

    got = []
    bar.actionTriggered.connect(got.append)
    bar.set_tool("pen")
    bar.set_tool("note")
    assert bar.note_action.isChecked() is True
    assert bar.pen_action.isChecked() is False
    assert got == []


def test_tools_menu_exposes_fullscreen_annotate_and_screenshot(qapp):
    bar = Menubar()
    try:
        assert bar.fullscreen_annotate_action.text() == "全屏批注"
        assert bar.screenshot_action.text().startswith("截图")
        fired = []
        bar.actionTriggered.connect(fired.append)
        bar.fullscreen_annotate_action.trigger()
        bar.screenshot_action.trigger()
        assert fired == ["annotate", "screenshot"]
    finally:
        bar.close()
