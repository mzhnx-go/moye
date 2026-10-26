"""主窗模式接线离屏测试：草稿/文档切换、工具栏与菜单路由、状态栏、快捷键。

样例 PDF 在 tmp_path 现造（三页 A4 竖版，第一页够高，开局视口中心落在第 1 页）；
对话框全部 monkeypatch，绝不弹真窗。
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QInputDialog,
                               QMessageBox, QSplitter, QStackedWidget)

import main
from sketchpad.ui.toolbox import PALETTE
from sketchpad.ui import dialogs
from sketchpad.core import pdfstore
from sketchpad.services.hotkey import GlobalHotkey
from sketchpad.core.settings import WIDTHS, Settings
from sketchpad.ui.settings_dialog import SettingsDialog
from sketchpad.core.strokes import Stroke

# 三页竖版：第一页 842 pt 高，保证开局视口中心落在第 1 页
SIZES = ((595.0, 842.0), (500.0, 700.0), (420.0, 900.0))

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path, sizes=SIZES):
    doc = pymupdf.open()
    for width, height in sizes:
        doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def pdf(tmp_path):
    return _make_pdf(tmp_path / "sample.pdf")


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard   # 离屏不弹模态框
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    if w.settings_dialog is not None:
        w.settings_dialog.close()
    w.pdf_view.close_document()
    w._quitting = True
    w.close()


def _drag_on_page(view, start, end):
    """在 PDF 场景坐标里按下-抬起：抬笔时视图会补终点，得一条两点笔迹。"""
    press = view.mapFromScene(QPointF(*start))
    release = view.mapFromScene(QPointF(*end))
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=press)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=release)


def _draw_draft_stroke(win):
    """草稿画布：屏幕坐标直接落笔，得一条两点笔迹。"""
    QTest.mousePress(win.view.viewport(), Qt.LeftButton, pos=QPoint(100, 100))
    QTest.mouseRelease(win.view.viewport(), Qt.LeftButton, pos=QPoint(200, 200))


# ---------- 布局与开局 ----------

def test_starts_in_draft_with_both_views_in_stack(win):
    assert win.mode == "draft"
    assert isinstance(win.stack, QStackedWidget) is True
    assert win.stack.count() == 1                 # 启动只有草稿页
    assert win.stack.widget(0) is win.view
    assert win.stack.currentWidget() is win.view
    assert win.tabs.current_entry.draft is True
    assert win.menubar.mode == "draft"
    assert win.menubar.save_action.isEnabled() is False
    assert "第 " not in win.status_label.text()


def test_layout_order_chrome_find_body(win):
    column = win.centralWidget().layout()
    assert column.indexOf(win.top_chrome) == 0
    assert win.top_chrome.rows.indexOf(win.titlebar) == 0
    assert win.top_chrome.rows.indexOf(win.menubar) == 1
    assert win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2

    body = None
    body_index = -1
    for i in range(column.count()):
        item = column.itemAt(i)
        if item.layout() is not None:
            body = item.layout()
            body_index = i
            break
    assert body is not None
    assert column.indexOf(win.find_bar) < body_index
    assert body.indexOf(win.side_panel) >= 0
    assert body.indexOf(win.tabs) >= 0


def test_layout_with_right_toolbar_keeps_body_and_document_mode(win, pdf):
    """右排工具栏挂在 body 行最右：进文档模式、侧栏布局都不受影响。"""
    win._apply_toolbar_position("right")
    QTest.qWait(40)
    assert win.column.indexOf(win.toolbar_scroll) == -1
    assert win.top_chrome.has_row(win.toolbar_scroll) is False
    assert win.body.indexOf(win.toolbar_scroll) == win.body.count() - 1
    assert win.body.indexOf(win.tabs) >= 0

    assert win._enter_document(pdf) is True
    assert win.mode == "document"
    assert win.stack.currentWidget() is win._active_session.pane
    assert win.body.indexOf(win.toolbar_scroll) == win.body.count() - 1
    assert win.toolbox.vertical is True


# ---------- 模式切换 ----------

def test_enter_document_switches_stack_menubar_and_status(win, pdf):
    assert win._enter_document(pdf) is True
    assert win.mode == "document"
    assert win.stack.currentWidget() is win._active_session.pane
    assert win.menubar.mode == "document"
    assert win.menubar.save_action.isEnabled() is True
    assert win.menubar.goto_page_action.isEnabled() is True
    assert win.pdf_view.pdf_path == pdf
    assert win.status_label.text() == f"第 1/3 页 · 缩放 {round(win.pdf_view.zoom * 100)}% · 笔迹 0"


def test_enter_document_shows_page_top(win, pdf):
    """打开 PDF 落在页首：视图从隐藏变可见会再自适应一次，不能被推回页中间。"""
    assert win._enter_document(pdf) is True
    QTest.qWait(50)
    view = win.pdf_view
    assert view.verticalScrollBar().value() == 0
    assert view.mapToScene(QPoint(0, 0)).y() == pytest.approx(0.0, abs=24.0)
    assert view.current_page == 0


def test_enter_draft_closes_document_and_disables_actions(win, pdf):
    win._enter_document(pdf)
    win._enter_draft()
    assert win.mode == "draft"
    assert win.stack.currentWidget() is win.view
    assert win.menubar.mode == "draft"
    assert win.pdf_view.document is None
    assert win.pdf_view.pdf_path is None
    assert win.menubar.save_action.isEnabled() is False
    assert win.menubar.close_doc_action.isEnabled() is False
    assert win.status_label.text() == win.status_text()
    assert win.status_label.text().startswith("当前色")


def test_open_pdf_error_keeps_draft_and_writes_status(win, tmp_path, monkeypatch):
    bogus = tmp_path / "broken.pdf"
    bogus.write_bytes(b"not a pdf at all")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(bogus), "仅 PDF (*.pdf)")))

    assert win._open_pdf() is False
    assert win.mode == "draft"
    assert win.stack.currentWidget() is win.view
    assert "无法打开" in win.message_label.text()
    assert win.pdf_view.document is None


def test_open_pdf_uses_pdf_only_filter(win, pdf, monkeypatch):
    calls = []

    def fake(parent, caption, directory, filt):
        calls.append((caption, filt))
        return (str(pdf), filt)

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake))
    assert win._open_pdf() is True
    assert calls == [("打开 PDF", "仅 PDF (*.pdf)")]
    assert win.mode == "document"


# ---------- 保存 / 导出 ----------

def test_save_writes_sidecar_and_second_save_updates_it(win, pdf, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: pytest.fail("保存不该弹另存为")))
    win._enter_document(pdf)
    sidecar = pdfstore.sidecar_path(pdf)
    assert sidecar.exists() is False

    assert win._save_annotations() is True
    assert sidecar.exists() is True
    assert "已保存批注" in win.message_label.text()
    first = pdfstore.load_sidecar(pdf, 3)
    assert [len(page.strokes) for page in first.pages] == [0, 0, 0]

    win.pdf_view.document.pages[0].strokes.append(
        Stroke([(10.0, 20.0), (30.0, 40.0)], "#c0392b", 3.0))
    assert win._save_annotations() is True
    again = pdfstore.load_sidecar(pdf, 3)
    assert len(again.pages[0].strokes) == 1
    assert again.pages[0].strokes[0].points == [(10.0, 20.0), (30.0, 40.0)]


def test_save_without_document_reports_error_without_crash(win):
    assert win._save_annotations() is False
    assert "没有打开的 PDF" in win.message_label.text()


def test_ctrl_s_saves_only_in_document_mode(win, pdf):
    win.activateWindow()
    win.view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(win.view, Qt.Key_S, Qt.ControlModifier)    # 草稿模式：未绑定
    assert pdfstore.sidecar_path(pdf).exists() is False

    win._enter_document(pdf)
    win.pdf_view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(win.pdf_view, Qt.Key_S, Qt.ControlModifier)
    assert pdfstore.sidecar_path(pdf).exists() is True
    assert "已保存批注" in win.message_label.text()


def test_ctrl_o_opens_pdf_through_dialog(win, pdf, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(pdf), "仅 PDF (*.pdf)")))
    win.activateWindow()
    win.view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.view, Qt.Key_O, Qt.ControlModifier)
    assert win.mode == "document"
    assert win.pdf_view.pdf_path == pdf
    assert win.stack.currentWidget() is win._active_session.pane


def test_export_writes_new_pdf_and_leaves_source_bytes(win, pdf, tmp_path, monkeypatch):
    before = pdf.read_bytes()
    win._enter_document(pdf)
    win.pdf_view.document.pages[0].strokes.append(
        Stroke([(20.0, 20.0), (120.0, 80.0)], "#c0392b", 4.0))
    out = tmp_path / "out" / "result.pdf"
    calls = []

    def fake(parent, caption, directory, filt):
        calls.append((caption, directory, filt))
        return (str(out), filt)

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake))
    assert win._export_pdf() is True
    assert out.exists() is True
    assert pdf.read_bytes() == before
    assert str(out) in win.message_label.text()
    assert calls[0][1].endswith("sample-批注.pdf")
    assert calls[0][2] == "PDF (*.pdf)"

    with pymupdf.open(str(out)) as exported:
        assert exported.page_count == 3


# ---------- 工具栏路由 ----------

def test_toolbar_signals_apply_to_both_views_and_menubar(win):
    win.toolbox.color_buttons[1].click()
    assert (win.view.color, win.pdf_view.color) == ("#2980b9", "#2980b9")

    win.toolbox.eraser_button.click()
    assert (win.view.tool, win.pdf_view.tool) == ("eraser", "eraser")
    assert win.menubar.eraser_action.isChecked() is True

    win.toolbox.width_buttons[2].click()
    assert (win.view.width, win.pdf_view.width) == (4.0, 4.0)
    assert [action.isChecked() for action in win.menubar.width_actions] == [
        False, False, True, False]


def test_undo_button_routes_to_document_history(win, pdf):
    win._enter_document(pdf)
    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert len(win.pdf_view.document.pages[0].strokes) == 1
    assert win.pdf_view.history.depth == 1

    win.toolbox.undo_button.click()
    assert win.pdf_view.document.pages[0].strokes == []
    assert win.pdf_view.history.depth == 0
    assert win.view.history.depth == 0                 # 草稿的历史没被碰

    win.toolbox.redo_button.click()
    assert len(win.pdf_view.document.pages[0].strokes) == 1


def test_undo_button_routes_to_draft_history(win, pdf):
    win._enter_document(pdf)
    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert win.pdf_view.history.depth == 1
    win._enter_draft()                                 # 关文档：数据不再可访问
    assert win.pdf_view.document is None
    _draw_draft_stroke(win)
    assert len(win.controller.document.strokes) == 1
    assert win.view.history.depth == 1

    win.toolbox.undo_button.click()
    assert win.controller.document.strokes == []
    assert win.view.history.depth == 0
    # 关闭标签会销毁该文档会话，历史随之释放（不再保留已关闭文档的撤销栈）
    assert win.pdf_view.history.depth == 0


def test_ctrl_z_shortcut_works_on_pdf_view(win, pdf):
    win._enter_document(pdf)
    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert len(win.pdf_view.document.pages[0].strokes) == 1
    win.activateWindow()
    win.pdf_view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.pdf_view, Qt.Key_Z, Qt.ControlModifier)
    assert win.pdf_view.document.pages[0].strokes == []
    QTest.keyClick(win.pdf_view, Qt.Key_Y, Qt.ControlModifier)
    assert len(win.pdf_view.document.pages[0].strokes) == 1


def test_clear_routes_to_current_page_in_document_and_canvas_in_draft(win, pdf):
    win._enter_document(pdf)
    doc = win.pdf_view.document
    doc.pages[0].strokes.append(Stroke([(1.0, 1.0), (2.0, 2.0)], "#c0392b", 3.0))
    doc.pages[1].strokes.append(Stroke([(3.0, 3.0), (4.0, 4.0)], "#27ae60", 3.0))

    win.menubar.clear_canvas_action.trigger()
    assert doc.pages[0].strokes == []
    assert len(doc.pages[1].strokes) == 1

    win._enter_draft()
    _draw_draft_stroke(win)
    assert len(win.controller.document.strokes) == 1
    win.menubar.clear_canvas_action.trigger()
    assert win.controller.document.strokes == []


def test_trash_button_clears_only_current_page_in_document_mode(win, pdf):
    """文档模式：垃圾桶只清当前页，其它页的笔迹原样保留；可一步撤销。"""
    win._enter_document(pdf)
    doc = win.pdf_view.document
    for page in doc.pages:
        page.strokes.append(Stroke([(1.0, 1.0), (2.0, 2.0)], "#c0392b", 3.0))
    win.pdf_view.goto_page(1)
    assert win.pdf_view.current_page == 1

    win.toolbox.clear_button.click()
    assert doc.pages[1].strokes == []
    assert [len(page.strokes) for page in doc.pages] == [1, 0, 1]

    win.pdf_view.history.pop_undo()
    assert [len(page.strokes) for page in doc.pages] == [1, 1, 1]


# ---------- 导航 / 缩放 ----------

def test_page_down_moves_to_page_two_and_updates_status(win, pdf):
    win._enter_document(pdf)
    assert win.pdf_view.current_page == 0
    win.activateWindow()
    win.pdf_view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.pdf_view, Qt.Key_PageDown)
    assert win.pdf_view.current_page == 1
    assert win.status_label.text() == f"第 2/3 页 · 缩放 {round(win.pdf_view.zoom * 100)}% · 笔迹 0"

    QTest.keyClick(win.pdf_view, Qt.Key_PageUp)
    assert win.pdf_view.current_page == 0


def test_ctrl_g_jumps_to_typed_page(win, pdf, monkeypatch):
    win._enter_document(pdf)
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (3, True)))
    win.activateWindow()
    win.pdf_view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.pdf_view, Qt.Key_G, Qt.ControlModifier)
    assert win.pdf_view.current_page == 2
    assert win.status_label.text() == f"第 3/3 页 · 缩放 {round(win.pdf_view.zoom * 100)}% · 笔迹 0"


def test_zoom_actions_change_zoom_and_fit_width_uses_viewport(win, pdf):
    win._enter_document(pdf)
    page = win.pdf_view.document.pages[win.pdf_view.current_page]
    viewport_width = win.pdf_view.viewport().width()
    fit_zoom = viewport_width / page.width
    assert win.pdf_view.auto_fit is True
    assert win.pdf_view.zoom == pytest.approx(fit_zoom, abs=1e-6)

    win._on_menu_action("zoom_in")
    assert win.pdf_view.zoom > fit_zoom
    zoomed = win.pdf_view.zoom
    win._on_menu_action("zoom_out")
    assert win.pdf_view.zoom == pytest.approx(fit_zoom, abs=1e-6)

    win._on_menu_action("zoom_in")
    assert win.pdf_view.zoom == pytest.approx(zoomed, abs=1e-6)
    win._on_menu_action("actual_size")
    assert win.pdf_view.zoom == 1.0

    win._on_menu_action("fit_width")
    assert win.pdf_view.zoom == pytest.approx(viewport_width / page.width, abs=1e-6)
    assert win.pdf_view.auto_fit is True


def test_zoom_in_draft_only_writes_a_hint(win):
    before = win.pdf_view.zoom
    win._on_menu_action("zoom_in")
    assert win.pdf_view.zoom == before
    assert "文档" in win.message_label.text()


def test_help_actions_open_dialogs(win, monkeypatch):
    seen = []
    monkeypatch.setattr(dialogs, "show_shortcuts",
                        lambda parent: seen.append(("shortcuts", parent)))
    monkeypatch.setattr(dialogs, "show_about",
                        lambda parent: seen.append(("about", parent)))
    win._on_menu_action("shortcuts")
    win._on_menu_action("about")
    assert ("shortcuts", win) in seen
    assert ("about", win) in seen


# ---------- 标题栏：文件名 + 未保存圆点 ----------

def test_title_stays_app_name_and_tracks_dirty(win, pdf):
    """标题恒为应用名（2026-10-24 用户要求：文件名只看标签栏高亮）；脏标记 ● 保留。"""
    assert win.titlebar.title_label.text() == "墨页"
    assert win.windowTitle() == "墨页"

    assert win._enter_document(pdf) is True
    assert win.titlebar.title_label.text() == "墨页"
    assert win.windowTitle() == "墨页"

    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert len(win.pdf_view.document.pages[0].strokes) == 1
    assert "●" in win.windowTitle()

    assert win._save_annotations() is True
    assert "●" not in win.windowTitle()

    win._enter_draft()
    assert win.titlebar.title_label.text() == "墨页"
    assert win.windowTitle() == "墨页"


# ---------- 草稿空态提示 ----------

def test_empty_hint_shows_on_empty_draft_canvas_and_is_mouse_transparent(win):
    hint = win.empty_hint
    assert hint.text() == "打开 PDF（Ctrl+O） · 直接写 · B 画笔 / Q 橡皮"
    assert hint.isVisible() is True
    assert hint.testAttribute(Qt.WA_TransparentForMouseEvents) is True
    # 居中在草稿视口
    vp = win.view.viewport()
    assert abs(hint.geometry().center().x() - vp.rect().center().x()) <= 2
    assert abs(hint.geometry().center().y() - vp.rect().center().y()) <= 2

    # 窗口缩放后重新居中（视口 Resize 事件驱动）
    win.resize(620, 480)
    QTest.qWait(40)
    vp = win.view.viewport()
    assert abs(hint.geometry().center().x() - vp.rect().center().x()) <= 2
    assert abs(hint.geometry().center().y() - vp.rect().center().y()) <= 2


def test_empty_hint_hides_after_first_stroke_and_returns_after_undo(win):
    _draw_draft_stroke(win)
    assert win.empty_hint.isVisible() is False
    win.view.history.pop_undo()
    assert win.controller.document.strokes == []
    assert win.empty_hint.isVisible() is True


def test_empty_hint_hides_in_document_mode(win, pdf):
    assert win._enter_document(pdf) is True
    assert win.empty_hint.isVisible() is False
    win._enter_draft()
    assert win.empty_hint.isVisible() is True


# ---------- 保存状态角标 ----------

def test_save_chip_tracks_dirty_and_commit_time(win, pdf):
    assert win.save_label.text() == ""                 # 草稿模式不显示
    assert win._enter_document(pdf) is True
    assert win.save_label.text() == "已保存"

    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert win.save_label.text() == "● 未保存"
    assert win.save_label.toolTip() != ""

    assert win._save_annotations() is True
    text = win.save_label.text()
    assert text.startswith("已保存 ")
    assert len(text) == len("已保存 HH:MM:SS")
    assert win.save_label.toolTip() == ""

    win._enter_draft()
    assert win.save_label.text() == ""


def test_save_chip_clears_when_unsaved_changes_are_discarded(win, pdf):
    win._enter_document(pdf)
    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert win.save_label.text() == "● 未保存"
    win._confirm_unsaved = lambda action: QMessageBox.Discard
    win._enter_draft()
    assert win.mode == "draft"
    assert win.save_label.text() == ""


# ---------- 应用级按键兜底：焦点不在视图里也要能用 ----------

def test_hotkeys_reach_pdf_view_when_focus_is_on_a_scrollbar(win, pdf):
    """点过侧栏/滚动条后焦点不在视图上：Q/B/1-4/0 仍要落到文档视图。"""
    win._enter_document(pdf)
    win.activateWindow()
    win.resize(main.MIN_W, 520)                    # 窄窗：工具栏出现横向滚动条
    QTest.qWait(50)
    bar = win.toolbar_scroll.horizontalScrollBar()
    assert bar.isVisible() is True
    bar.setFocus()
    QTest.qWait(20)
    assert QApplication.focusWidget() is bar       # 焦点确实不在视图上
    assert win.pdf_view.hasFocus() is False

    QTest.keyClick(bar, Qt.Key_Q)
    assert win.pdf_view.tool == "eraser"
    QTest.keyClick(bar, Qt.Key_B)
    assert win.pdf_view.tool == "pen"

    for index, key in enumerate((Qt.Key_1, Qt.Key_2, Qt.Key_3)):
        QTest.keyClick(bar, key)
        assert win.pdf_view.color == PALETTE[index]

    win.pdf_view.set_zoom(2.0)
    QTest.keyClick(bar, Qt.Key_0)
    assert win.pdf_view.zoom == pytest.approx(1.0)


def test_typing_in_find_box_is_not_hijacked_by_hotkeys(win, pdf):
    """查找框里敲 B 必须是字母 b，不能切画笔。"""
    win._enter_document(pdf)
    win.pdf_view.set_tool("eraser")
    win.activateWindow()
    win._show_find()
    QTest.qWait(20)
    field = win.find_bar.input
    field.setFocus()
    QTest.qWait(20)
    assert QApplication.focusWidget() is field

    QTest.keyClick(field, Qt.Key_B)
    assert field.text() == "b"
    assert win.pdf_view.tool == "eraser"           # 工具没被抢走


# ---------- 设置对话框 ----------

def _stub_dialog(monkeypatch, values, code=QDialog.Accepted):
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: code)
    monkeypatch.setattr(SettingsDialog, "values", lambda self: values)


def test_settings_save_applies_color_width_and_clear_key_to_pdf_view(
        win, pdf, monkeypatch):
    prepared = Settings(clear_key="F9", color="#2980b9", width_index=2,
                        scratch_hotkey="Ctrl+Alt+D", width=9.5)
    monkeypatch.setattr(GlobalHotkey, "unregister", lambda self: None)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self, hwnd, sequence: True)
    _stub_dialog(monkeypatch, prepared)

    win._open_settings()

    assert win.pdf_view.color == "#2980b9"
    assert win.pdf_view.width == 9.5
    # 清屏键只有一条路径：配置记在 view.clear_key 上，由应用级过滤器分发；
    # 草稿画布内建的 QShortcut 必须保持停用，否则同一个键清两次
    assert win.view.clear_key == "F9"
    assert win.view._clear_shortcut is not None
    assert win.view._clear_shortcut.isEnabled() is False

    win._enter_document(pdf)
    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert len(win.pdf_view.document.pages[0].strokes) == 1
    depth = win.pdf_view.history.depth
    win.activateWindow()
    win.pdf_view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(win.pdf_view, Qt.Key_F9)
    assert win.pdf_view.document.pages[0].strokes == []
    assert win.pdf_view.history.depth == depth + 1      # 只压了一条 ClearAll
    win.pdf_view.history.pop_undo()                     # 一步撤销就全部回来
    assert len(win.pdf_view.document.pages[0].strokes) == 1
    assert win.pdf_view.history.depth == depth


# ---------- 侧栏：目录 / 缩略图可拖拽分割 ----------

def test_side_panels_live_in_a_vertical_splitter(win, pdf):
    win._enter_document(pdf)
    win.menubar.toc_action.trigger()
    win.menubar.thumbs_action.trigger()
    QTest.qWait(50)

    splitter = win.side_splitter
    assert isinstance(splitter, QSplitter) is True
    assert splitter.orientation() == Qt.Vertical
    assert splitter.count() == 2
    assert splitter.widget(0) is win.toc_panel
    assert splitter.widget(1) is win.thumb_panel
    assert splitter.childrenCollapsible() is False
    assert win.toc_panel.minimumHeight() >= 120
    assert win.thumb_panel.minimumHeight() >= 120
    # 两个面板都显示时手柄可见、两段高度都非零（可以拖拽分配）
    assert win.toc_panel.isVisible() is True
    assert win.thumb_panel.isVisible() is True
    assert splitter.handle(1).isVisible() is True
    sizes = splitter.sizes()
    assert sizes[0] > 0 and sizes[1] > 0

    # 关掉一个面板后边栏仍在，另一段自动占满
    win.menubar.thumbs_action.trigger()
    assert win.thumb_panel.isVisible() is False
    assert win.side_panel.isVisible() is True


# ---------- 文字选择（v2.10）----------

def _make_text_pdf(path, text="alpha beta gamma", size=(300.0, 200.0)):
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_text((40, 80), text, fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def _word_points(view):
    layer = view._layers[0]
    return [view.mapFromScene(QPointF(layer.x + (x0 + x1) / 2.0,
                                      layer.y + (y0 + y1) / 2.0))
            for _text, x0, y0, x1, y1, _block, _line in view._units(0)]


def test_v_enters_select_in_both_modes(win, pdf):
    assert win.toolbox.select_button.isVisible() is True

    win.view.setFocus()
    QTest.keyClick(win.view, Qt.Key_V)             # 草稿：V = 手形平移画布
    assert win.view.tool == "select"

    assert win._enter_document(pdf) is True
    assert win.toolbox.select_button.isVisible() is True

    win.pdf_view.setFocus()
    QTest.keyClick(win.pdf_view, Qt.Key_V)
    assert win.pdf_view.tool == "select"
    assert win.toolbox.select_button.isChecked() is True
    assert win.menubar.select_action.isChecked() is True

    # 工具栏切回画笔：两个视图都不再是选择模式
    win.toolbox.pen_button.click()
    assert win.pdf_view.tool == "pen"
    assert win.view.tool == "pen"
    win.toolbox.select_button.click()
    assert win.pdf_view.tool == "select"
    assert win.view.tool == "select"

    win._enter_draft()
    assert win.toolbox.select_button.isVisible() is True


def test_ctrl_c_copies_selected_text_and_reports_count(win, tmp_path):
    from PySide6.QtGui import QGuiApplication
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    assert win._enter_document(pdf) is True
    win.pdf_view.set_tool("select")
    points = _word_points(win.pdf_view)
    QTest.mousePress(win.pdf_view.viewport(), Qt.LeftButton, pos=points[0])
    QTest.mouseMove(win.pdf_view.viewport(), points[-1], delay=10)
    QTest.mouseRelease(win.pdf_view.viewport(), Qt.LeftButton, pos=points[-1])
    assert win.pdf_view.selected_text() == "alpha beta gamma"

    win.pdf_view.setFocus()
    QTest.keyClick(win.pdf_view, Qt.Key_C, Qt.ControlModifier)
    assert QGuiApplication.clipboard().text() == "alpha beta gamma"
    assert "已复制 16 个字符" in win.message_label.text()


def test_new_note_uses_configured_paper(win, pdf):
    win.controller.settings.note_paper = "light"
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    assert win.pdf_view.document.pages[0].notes[0].paper == "light"


def test_export_opens_folder_when_enabled(win, pdf, tmp_path, monkeypatch):
    import sketchpad.app as app_module

    opened = []

    class _Desk:
        @staticmethod
        def openUrl(url):
            opened.append(url.toLocalFile())

    assert win._enter_document(pdf) is True
    out = tmp_path / "out.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "PDF (*.pdf)")))
    monkeypatch.setattr(app_module, "QDesktopServices", _Desk)
    win.controller.settings.open_export_folder = True
    assert win._export_pdf() is True
    assert len(opened) == 1 and Path(opened[0]) == tmp_path


def test_open_pdf_dialog_starts_at_last_pdf_folder(win, tmp_path, monkeypatch):
    seen = []

    def fake_dialog(parent, title, start, filt):
        seen.append(start)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_dialog))
    pdf = tmp_path / "folder" / "lecture.pdf"
    pdf.parent.mkdir()
    import pymupdf
    doc = pymupdf.open()
    doc.new_page(width=300.0, height=400.0)
    doc.save(str(pdf))
    doc.close()

    win._enter_document(pdf)
    win._open_pdf()
    assert Path(seen[-1]) == tmp_path / "folder"

    # 关闭文档后仍按"最近打开"记住目录
    win._enter_draft()
    win._open_pdf()
    assert Path(seen[-1]) == tmp_path / "folder"


def test_open_pdf_dialog_falls_back_when_folder_missing(win, monkeypatch):
    seen = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a: (seen.append(a[2]), ("", ""))[1]))
    win.controller.settings.recent_files = ["C:/definitely-missing-dir/x.pdf"]
    win._open_pdf()
    # 不存在的目录不能作为起点；回退到程序根目录
    assert Path(seen[-1]).is_dir()
