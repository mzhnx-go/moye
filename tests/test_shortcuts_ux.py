"""v2.5 UX 批次：新增快捷键的离屏测试（缩放、查找步进、重做、全屏、首末页）。

样例 PDF 现造；对话框全部 monkeypatch，绝不弹真窗。
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import main

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


def _make_hits_pdf(path):
    """第 1 页放两个相同词：F3 / Shift+F3 才能真的步进。"""
    doc = pymupdf.open()
    for width, height in SIZES:
        doc.new_page(width=width, height=height)
    doc[0].insert_text((72.0, 72.0), "needle")
    doc[0].insert_text((72.0, 300.0), "needle")
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
    w.pdf_autosave.cancel()
    w._wait_find_workers()          # 异步搜索线程不允许活着离开用例
    w.pdf_view.close_document()
    w._quitting = True
    w.close()


def _wait_until(predicate, timeout_ms: int = 5000) -> bool:
    """轮询等条件成立（异步搜索落地等）；每步转一下事件循环让信号投递。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def _draw_draft_stroke(win):
    QTest.mousePress(win.view.viewport(), Qt.LeftButton, pos=QPoint(100, 100))
    QTest.mouseRelease(win.view.viewport(), Qt.LeftButton, pos=QPoint(200, 200))


def _focus(view):
    QTest.qWait(20)
    view.setFocus()
    QTest.qWait(20)


# ---------- 缩放 ----------

def test_ctrl_zoom_shortcuts_in_document_mode(win, pdf):
    win._enter_document(pdf)
    win.activateWindow()
    _focus(win.pdf_view)
    fit = win.pdf_view.zoom

    QTest.keyClick(win.pdf_view, Qt.Key_Equal, Qt.ControlModifier)
    assert win.pdf_view.zoom == pytest.approx(fit * main.MainWindow.ZOOM_STEP, rel=1e-6)

    QTest.keyClick(win.pdf_view, Qt.Key_Minus, Qt.ControlModifier)
    assert win.pdf_view.zoom == pytest.approx(fit, rel=1e-6)

    win.pdf_view.set_zoom(2.0)
    QTest.keyClick(win.pdf_view, Qt.Key_0, Qt.ControlModifier)
    assert win.pdf_view.zoom == pytest.approx(1.0)

    page = win.pdf_view.document.pages[win.pdf_view.current_page]
    QTest.keyClick(win.pdf_view, Qt.Key_9, Qt.ControlModifier)
    assert win.pdf_view.auto_fit is True
    assert win.pdf_view.zoom == pytest.approx(
        win.pdf_view.viewport().width() / page.width, rel=1e-6)


def test_ctrl_plus_is_an_alias_for_zoom_in(win, pdf):
    """美式键盘上 Ctrl++ 实际是 Ctrl+Shift+=；两个都给放。"""
    win._enter_document(pdf)
    win.activateWindow()
    _focus(win.pdf_view)
    win.pdf_view.set_zoom(1.0)
    QTest.keyClick(win.pdf_view, Qt.Key_Plus,
                   Qt.ControlModifier | Qt.ShiftModifier)
    assert win.pdf_view.zoom > 1.0


def test_ctrl_shift_equal_zooms_in(win, pdf):
    """Ctrl+Shift+= 曾是死键：事件过滤器按 Ctrl+Equal 收下，Shift 不参与判定。"""
    win._enter_document(pdf)
    win.activateWindow()
    _focus(win.pdf_view)
    win.pdf_view.set_zoom(1.0)

    QTest.keyClick(win.pdf_view, Qt.Key_Equal,
                   Qt.ControlModifier | Qt.ShiftModifier)
    assert win.pdf_view.zoom == pytest.approx(
        1.0 * main.MainWindow.ZOOM_STEP, rel=1e-6)


def test_zoom_shortcuts_are_document_only(win):
    win.activateWindow()
    _focus(win.view)
    before = win.pdf_view.zoom
    QTest.keyClick(win.view, Qt.Key_Equal, Qt.ControlModifier)
    QTest.keyClick(win.view, Qt.Key_Minus, Qt.ControlModifier)
    QTest.keyClick(win.view, Qt.Key_9, Qt.ControlModifier)
    assert win.pdf_view.zoom == before
    assert "文档" not in win.message_label.text()   # 快捷键根本没触发，不刷提示


# ---------- 查找步进 ----------

def test_f3_without_hits_opens_find_bar_and_focuses_input(win, pdf):
    win._enter_document(pdf)
    win.activateWindow()
    _focus(win.pdf_view)
    assert win.find_bar.isVisible() is False

    QTest.keyClick(win.pdf_view, Qt.Key_F3)
    assert win.find_bar.isVisible() is True
    assert QApplication.focusWidget() is win.find_bar.input


def test_f3_with_typed_query_starts_search(win, tmp_path):
    """F3 在无命中时：查找条里已有未搜的词，要一并开搜，而不是只聚焦。"""
    pdf = _make_hits_pdf(tmp_path / "hits.pdf")
    win._enter_document(pdf)
    win.find_bar.input.setText("needle")
    assert win._find_hits == []                 # 只输入，尚未搜索
    win.activateWindow()
    _focus(win.pdf_view)

    QTest.keyClick(win.pdf_view, Qt.Key_F3)
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/2")
    assert win._find_hits


def test_f3_and_shift_f3_step_through_hits(win, tmp_path):
    pdf = _make_hits_pdf(tmp_path / "hits.pdf")
    win._enter_document(pdf)
    win.find_bar.input.setText("needle")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)   # 回车才搜索
    # 异步搜索：轮询等结果落地（不再死等固定 320ms）
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/2")

    win.activateWindow()
    _focus(win.pdf_view)
    QTest.keyClick(win.pdf_view, Qt.Key_F3)
    assert win.find_bar.count_label.text() == "2/2"
    QTest.keyClick(win.pdf_view, Qt.Key_F3, Qt.ShiftModifier)
    assert win.find_bar.count_label.text() == "1/2"


def test_f3_in_draft_is_inactive(win):
    win.activateWindow()
    _focus(win.view)
    QTest.keyClick(win.view, Qt.Key_F3)
    assert win.find_bar.isVisible() is False


# ---------- 重做别名 ----------

def test_undo_redo_route_to_draft_history(win):
    """Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z 只走应用级过滤器一条路径，作用于草稿视图。

    映射按约定：Ctrl+Z = 撤销，Ctrl+Y 与 Ctrl+Shift+Z 都是重做（别名）。
    """
    win.activateWindow()
    _focus(win.view)
    _draw_draft_stroke(win)
    _draw_draft_stroke(win)
    assert len(win.controller.document.strokes) == 2

    QTest.keyClick(win.view, Qt.Key_Z, Qt.ControlModifier)
    assert len(win.controller.document.strokes) == 1
    QTest.keyClick(win.view, Qt.Key_Y, Qt.ControlModifier)
    assert len(win.controller.document.strokes) == 2
    QTest.keyClick(win.view, Qt.Key_Z, Qt.ControlModifier)
    assert len(win.controller.document.strokes) == 1
    QTest.keyClick(win.view, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert len(win.controller.document.strokes) == 2      # Ctrl+Shift+Z = 重做


def test_clear_key_is_a_single_undoable_command(win):
    """清屏键只压一条 ClearAll：视图内建的 QShortcut 已停用，不会清两次。"""
    win.activateWindow()
    _focus(win.view)
    assert win.view.clear_key == "E"                 # 默认清屏键
    _draw_draft_stroke(win)
    _draw_draft_stroke(win)
    assert len(win.controller.document.strokes) == 2
    depth = win.view.history.depth

    QTest.keyClick(win.view, Qt.Key_E)
    assert win.controller.document.strokes == []
    assert win.view.history.depth == depth + 1

    QTest.keyClick(win.view, Qt.Key_Z, Qt.ControlModifier)   # 一步撤销全部回来
    assert len(win.controller.document.strokes) == 2
    assert win.view.history.depth == depth


def test_ctrl_z_in_find_box_undoes_text_not_canvas(win, pdf):
    """查找框里的 Ctrl+Z 必须是撤销文字；画布笔迹一条都不能少。"""
    from PySide6.QtCore import QPointF

    win._enter_document(pdf)
    press = win.pdf_view.mapFromScene(QPointF(100.0, 100.0))
    release = win.pdf_view.mapFromScene(QPointF(200.0, 200.0))
    QTest.mousePress(win.pdf_view.viewport(), Qt.LeftButton, pos=press)
    QTest.mouseRelease(win.pdf_view.viewport(), Qt.LeftButton, pos=release)
    assert len(win.pdf_view.document.pages[0].strokes) == 1

    win.activateWindow()
    win._show_find()
    QTest.qWait(20)
    field = win.find_bar.input
    field.clear()
    field.setFocus()
    QTest.qWait(20)
    assert QApplication.focusWidget() is field

    QTest.keyClicks(field, "abc")
    assert field.text() == "abc"
    QTest.keyClick(field, Qt.Key_Z, Qt.ControlModifier)
    assert field.text() != "abc"                      # 文字被撤销
    assert len(win.pdf_view.document.pages[0].strokes) == 1   # 画布没动


def test_ctrl_shift_z_redoes_in_draft(win):
    win.activateWindow()
    _focus(win.view)
    _draw_draft_stroke(win)
    assert len(win.controller.document.strokes) == 1

    QTest.keyClick(win.view, Qt.Key_Z, Qt.ControlModifier)
    assert win.controller.document.strokes == []
    QTest.keyClick(win.view, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert len(win.controller.document.strokes) == 1


def test_ctrl_shift_z_redoes_in_document_mode(win, pdf):
    from PySide6.QtCore import QPointF

    win._enter_document(pdf)
    win.activateWindow()
    _focus(win.pdf_view)
    press = win.pdf_view.mapFromScene(QPointF(100.0, 100.0))
    release = win.pdf_view.mapFromScene(QPointF(200.0, 200.0))
    QTest.mousePress(win.pdf_view.viewport(), Qt.LeftButton, pos=press)
    QTest.mouseRelease(win.pdf_view.viewport(), Qt.LeftButton, pos=release)
    assert len(win.pdf_view.document.pages[0].strokes) == 1

    QTest.keyClick(win.pdf_view, Qt.Key_Z, Qt.ControlModifier)
    assert win.pdf_view.document.pages[0].strokes == []
    QTest.keyClick(win.pdf_view, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert len(win.pdf_view.document.pages[0].strokes) == 1


# ---------- 全屏 ----------

def test_f11_fullscreen_and_esc_leaves(win):
    win.activateWindow()
    _focus(win.view)
    QTest.keyClick(win.view, Qt.Key_F11)
    QTest.qWait(50)
    assert win.isFullScreen() is True
    assert win._esc_shortcut.isEnabled() is True

    QTest.keyClick(win.view, Qt.Key_Escape)
    QTest.qWait(50)
    assert win.isFullScreen() is False
    assert win._esc_shortcut.isEnabled() is False


def test_escape_without_fullscreen_is_not_bound(win):
    win.activateWindow()
    _focus(win.view)
    assert win._esc_shortcut.isEnabled() is False
    QTest.keyClick(win.view, Qt.Key_Escape)
    assert win.isFullScreen() is False


# ---------- 首末页 ----------

def test_home_end_jump_to_first_and_last_page(win, pdf):
    win._enter_document(pdf)
    win.activateWindow()
    _focus(win.pdf_view)
    assert win.pdf_view.current_page == 0

    QTest.keyClick(win.pdf_view, Qt.Key_End)
    assert win.pdf_view.current_page == 2
    QTest.keyClick(win.pdf_view, Qt.Key_Home)
    assert win.pdf_view.current_page == 0


def test_home_end_do_not_steal_text_editing_keys(win, pdf):
    """查找框里 Home/End 必须是移动光标，不能翻页。"""
    win._enter_document(pdf)
    win.pdf_view.goto_page(1)
    win._show_find()
    QTest.qWait(20)
    field = win.find_bar.input
    field.setText("abc")
    field.setFocus()
    QTest.qWait(20)
    assert QApplication.focusWidget() is field

    QTest.keyClick(field, Qt.Key_Home)
    assert win.pdf_view.current_page == 1            # 没被当成翻页
    QTest.keyClick(field, Qt.Key_End)
    assert win.pdf_view.current_page == 1


# ---------- 文档专属快捷键不再在草稿模式开火 ----------

def test_page_export_shortcuts_are_document_only(win):
    always = {shortcut.key().toString() for shortcut in win._shortcuts}
    assert {"PgUp", "PgDown", "Ctrl+Shift+E"}.isdisjoint(always)
    document = {shortcut.key().toString() for shortcut in win._document_shortcuts}
    assert {"PgUp", "PgDown", "Ctrl+Shift+E"} <= document

    win.activateWindow()
    _focus(win.view)
    baseline = win.message_label.text()
    QTest.keyClick(win.view, Qt.Key_PageDown)
    QTest.keyClick(win.view, Qt.Key_E, Qt.ControlModifier | Qt.ShiftModifier)
    assert win.message_label.text() == baseline
    assert win.mode == "draft"
