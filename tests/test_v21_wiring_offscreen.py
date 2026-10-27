"""v2.1 接线离屏测试：草稿纸按钮、侧栏、查找条、帮助对话框、最近打开、自动保存。

自包含（不 import 别的测试模块）；offscreen 环境，对话框全部 monkeypatch，绝不弹真窗。
样例 PDF 在 tmp_path 现造（三页 A4 竖版）。
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, QProcess, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.ui import dialogs
from sketchpad.core import pdfstore
from sketchpad.core.strokes import Stroke
from sketchpad.services.pdfautosave import cache_path_for, write_cache

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


def _make_toc_pdf(path, toc, sizes=SIZES):
    doc = pymupdf.open()
    for width, height in sizes:
        doc.new_page(width=width, height=height)
    doc.set_toc(toc)
    doc.save(str(path))
    doc.close()
    return path


def _make_text_pdf(path, text, sizes=SIZES):
    doc = pymupdf.open()
    for width, height in sizes:
        doc.new_page(width=width, height=height)
    doc[0].insert_text((72.0, 72.0), text)
    doc.save(str(path))
    doc.close()
    return path


def _make_two_hit_pdf(path, sizes=SIZES):
    """第 1 页放两个相同词，供回车循环上一个/下一个用。"""
    doc = pymupdf.open()
    for width, height in sizes:
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


def _drag_on_page(view, start, end):
    """在 PDF 场景坐标里按下-抬起：抬笔时视图会补终点，得一条两点笔迹。"""
    press = view.mapFromScene(QPointF(*start))
    release = view.mapFromScene(QPointF(*end))
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=press)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=release)


def _count_toc_items(tree) -> int:
    total = 0
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        total += 1 + item.childCount()
    return total


# ---------- 1. 工具栏草稿纸按钮 ----------

def test_scratch_button_toggles_scratch_window(win):
    assert win.scratch.is_visible() is False
    win.toolbox.scratch_button.click()
    assert win.scratch.is_visible() is True
    win.toolbox.scratch_button.click()
    assert win.scratch.is_visible() is False


# ---------- 2. 打开 PDF：目录 + 缩略图 ----------

def test_open_pdf_populates_toc_and_thumbs(win, tmp_path):
    pdf = _make_toc_pdf(tmp_path / "toc.pdf",
                        [[1, "第一章", 1], [2, "小节", 3]])
    assert win._enter_document(pdf) is True
    assert _count_toc_items(win.toc_panel.tree) > 0
    assert win.thumb_panel.list.count() == 3


# ---------- 3. 目录 / 缩略图切换 ----------

def test_toc_thumbs_toggle_panel_and_action_state(win, pdf):
    win._enter_document(pdf)
    # 必须走 action.trigger() 而不是 _on_menu_action：真实菜单点击时 Qt 先翻
    # 勾选态再发 triggered，处理器看到的就是新状态——按真实路径才抓得到回归
    win.menubar.toc_action.trigger()
    assert win.menubar.toc_action.isChecked() is True
    assert win.toc_panel.isVisible() is True
    assert win.side_panel.isVisible() is True
    # 再唤起缩略图，边栏仍显示
    win.menubar.thumbs_action.trigger()
    assert win.menubar.thumbs_action.isChecked() is True
    assert win.thumb_panel.isVisible() is True
    assert win.side_panel.isVisible() is True
    # 关掉目录：缩略图还在，边栏仍在
    win.menubar.toc_action.trigger()
    assert win.menubar.toc_action.isChecked() is False
    assert win.toc_panel.isVisible() is False
    assert win.side_panel.isVisible() is True
    # 关掉缩略图：边栏收起
    win.menubar.thumbs_action.trigger()
    assert win.menubar.thumbs_action.isChecked() is False
    assert win.thumb_panel.isVisible() is False
    assert win.side_panel.isVisible() is False


def test_enter_draft_hides_side_panel_and_unchecks_actions(win, pdf):
    win._enter_document(pdf)
    win.menubar.toc_action.trigger()
    win.menubar.thumbs_action.trigger()
    assert win.side_panel.isVisible() is True
    win._enter_draft()
    assert win.toc_panel.isVisible() is False
    assert win.thumb_panel.isVisible() is False
    assert win.side_panel.isVisible() is False
    assert win.menubar.toc_action.isChecked() is False
    assert win.menubar.thumbs_action.isChecked() is False


# ---------- 4. 缩略图跳转 ----------

def test_thumb_page_requested_moves_pdf_view(win, pdf):
    win._enter_document(pdf)
    win.thumb_panel.pageRequested.emit(2)
    QTest.qWait(30)
    assert win.pdf_view.current_page == 2


# ---------- 5. 查找条 ----------

def test_ctrl_f_shows_find_bar_and_search_works(win, tmp_path):
    pdf = _make_text_pdf(tmp_path / "needle.pdf", "needle")
    win._enter_document(pdf)
    win.activateWindow()
    win.pdf_view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.pdf_view, Qt.Key_F, Qt.ControlModifier)
    assert win.find_bar.isVisible() is True

    win.find_bar.input.setText("needle")
    assert win.find_bar.count_label.text() == "0/0"     # 打字不再即时搜索
    assert win._find_worker is None
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)   # 回车才启动搜索
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/1")
    assert len(win.pdf_view._hit_items) > 0

    # 循环不越界、不报错
    win._find_step(1)
    win._find_step(-1)
    assert win.find_bar.count_label.text() == "1/1"

    # 关闭清空高亮
    win.find_bar.closed.emit()
    assert win.pdf_view._hit_items == []


def test_enter_steps_to_next_hit_and_shift_enter_previous(win, tmp_path):
    """回车 = 下一个命中（循环），Shift+回车 = 上一个；回车不再触发重复搜索。"""
    pdf = _make_two_hit_pdf(tmp_path / "two.pdf")
    win._enter_document(pdf)
    QTest.keyClick(win.pdf_view, Qt.Key_F, Qt.ControlModifier)
    win.find_bar.input.setText("needle")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/2")

    QTest.keyClick(win.find_bar.input, Qt.Key_Return)
    assert win.find_bar.count_label.text() == "2/2"
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)   # 循环回第一个
    assert win.find_bar.count_label.text() == "1/2"
    QTest.keyClick(win.find_bar.input, Qt.Key_Return, Qt.ShiftModifier)
    assert win.find_bar.count_label.text() == "2/2"


def test_find_empty_query_clears(win, pdf):
    win._enter_document(pdf)
    gen = win._find_generation
    win.find_bar.searchRequested.emit("zzz-no-such-text")
    # 无命中也是异步结果：等搜索真正收口（代号已推进且 worker 退场）再断言
    assert _wait_until(lambda: win._find_generation > gen
                       and win._find_worker is None)
    assert win.find_bar.count_label.text() == "0/0"
    assert win.pdf_view._hit_items == []


# ---------- 6. 帮助菜单用真对话框 ----------

def test_help_menu_opens_dialogs(win, monkeypatch):
    seen = []
    monkeypatch.setattr(dialogs, "show_shortcuts",
                        lambda parent: seen.append(("shortcuts", parent)))
    monkeypatch.setattr(dialogs, "show_about",
                        lambda parent: seen.append(("about", parent)))
    win._on_menu_action("shortcuts")
    win._on_menu_action("about")
    assert ("shortcuts", win) in seen
    assert ("about", win) in seen


# ---------- 7. 最近打开 ----------

def test_recent_files_land_in_settings_and_menu(win, pdf):
    assert win._enter_document(pdf) is True
    assert str(pdf) in win.controller.settings.recent_files
    actions = win.menubar.recent_menu.actions()
    assert actions[0].text() == "批量管理…"
    file_actions = [action for action in actions if action.data()]
    assert len(file_actions) == 1
    assert file_actions[0].text() == pdf.name
    # 点菜单能重新打开
    file_actions[0].trigger()
    QTest.qWait(20)
    assert win.mode == "document"
    assert win.pdf_view.pdf_path == pdf


def test_recent_file_missing_is_disabled_with_full_path_tooltip(win, tmp_path):
    """失效条目直接置灰（tooltip 带全路径），不再让用户点了才发现。"""
    missing = str(tmp_path / "gone.pdf")
    win.controller.settings.recent_files = [missing]
    win._rebuild_recent_menu()
    action = next(action for action in win.menubar.recent_menu.actions()
                  if action.data() == missing)
    assert action.isEnabled() is False
    assert missing in action.toolTip()
    action.trigger()                            # 置灰条目点了也不该改状态
    assert win.mode == "draft"


# ---------- 8. PDF 批注自动保存（缓存 + 提交） ----------

def test_pdf_autosave_writes_cache_then_commit_writes_sidecar(win, pdf):
    win._enter_document(pdf)
    win.controller.settings.pdf_autosave = True
    win._configure_autosave()
    win.pdf_autosave.set_interval(50)           # 缩短间隔便于测试
    cache = cache_path_for(pdf, win._autosave_base)
    sidecar = pdfstore.sidecar_path(pdf)
    assert cache.exists() is False and sidecar.exists() is False

    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    QTest.qWait(150)
    assert cache.exists() is True               # 自动暂存只写缓存
    assert sidecar.exists() is False
    assert win._dirty is True

    win._confirm_unsaved = lambda action: QMessageBox.Save
    win._enter_draft()                          # 正常切走：提交 sidecar + 清缓存
    assert sidecar.exists() is True
    assert cache.exists() is False
    assert win._dirty is False


def test_tab_reveal_file_selects_in_explorer(win, pdf, monkeypatch):
    win._enter_document(pdf)
    calls = []
    monkeypatch.setattr(
        QProcess, "startDetached",
        staticmethod(lambda program, args: calls.append((program, args))
                     or (True, 0)))
    entry = win._active_entry
    win._reveal_entry_file(entry)
    assert calls == [("explorer.exe", ["/select,", str(pdf)])]


def test_pdf_autosave_interval_comes_from_settings(win, pdf):
    win._enter_document(pdf)
    win.controller.settings.pdf_autosave_interval_min = 20
    win._configure_autosave()
    assert win.pdf_autosave.interval_ms == 20 * 60 * 1000


def test_pdf_autosave_default_off_and_can_be_enabled(win, pdf):
    win._enter_document(pdf)
    assert win.controller.settings.pdf_autosave is False

    win.controller.settings.pdf_autosave = True
    win._configure_autosave()
    win.pdf_autosave.set_interval(50)
    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    QTest.qWait(150)
    assert cache_path_for(pdf, win._autosave_base).exists() is True
    assert pdfstore.sidecar_path(pdf).exists() is False


def test_dirty_switch_cancel_keeps_document_then_save_commits(win, pdf):
    win._enter_document(pdf)
    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert win._dirty is True

    win._confirm_unsaved = lambda action: QMessageBox.Cancel
    win._enter_draft()
    assert win.mode == "document"               # 取消：留在文档模式
    assert pdfstore.sidecar_path(pdf).exists() is False

    win._confirm_unsaved = lambda action: QMessageBox.Save
    win._enter_draft()
    assert win.mode == "draft"
    assert pdfstore.sidecar_path(pdf).exists() is True
    assert win._dirty is False


def test_recover_cached_annotations_on_reopen(win, pdf):
    win._enter_document(pdf)
    doc = win.pdf_view.document
    doc.pages[0].strokes.append(Stroke([(1.0, 1.0), (2.0, 2.0)], "#c0392b", 3.0))
    write_cache(pdf, doc, win._autosave_base)
    win._enter_draft()                          # dirty=False：不弹框，缓存留在磁盘

    win._ask_recover_cache = lambda saved_at: True
    assert win._enter_document(pdf) is True
    assert len(win.pdf_view.document.pages[0].strokes) == 1
    assert win._dirty is True                   # 恢复的批注尚未写进 sidecar


def test_corrupt_sidecar_can_be_ignored_with_backup(win, pdf):
    sidecar = pdfstore.sidecar_path(pdf)
    sidecar.write_text("{not json", encoding="utf-8")
    win._ask_ignore_sidecar = lambda exc: True
    assert win._enter_document(pdf) is True
    assert win.pdf_view.document.pages[0].strokes == []
    assert sidecar.exists() is False
    backups = list(pdf.parent.glob("*.sketch.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_corrupt_sidecar_can_be_cancelled(win, pdf):
    sidecar = pdfstore.sidecar_path(pdf)
    sidecar.write_text("{not json", encoding="utf-8")
    win._ask_ignore_sidecar = lambda exc: False
    assert win._enter_document(pdf) is False
    assert win.mode == "draft"
    assert sidecar.exists() is True


def test_manual_save_cancels_pending_autosave(win, pdf):
    """Ctrl+S 提交后，待定的 debounce 不能再写出旧缓存。"""
    win._enter_document(pdf)
    win.controller.settings.pdf_autosave = True
    win._configure_autosave()
    win.pdf_autosave.set_interval(50)
    _drag_on_page(win.pdf_view, (100.0, 100.0), (200.0, 200.0))
    assert win.pdf_autosave.pending is True

    assert win._save_annotations() is True
    QTest.qWait(150)
    assert pdfstore.sidecar_path(pdf).exists() is True
    assert cache_path_for(pdf, win._autosave_base).exists() is False


def test_stale_cache_does_not_override_newer_sidecar(win, pdf):
    """sidecar 比缓存新时，旧缓存直接丢弃，不弹恢复框。"""
    sidecar_doc = pdfstore.PdfDocument(
        [pdfstore.Page(595.0, 842.0, 0, []) for _ in range(3)])
    sidecar_doc.pages[0].strokes.append(
        Stroke([(1.0, 1.0), (2.0, 2.0)], "#ff0000", 3.0))
    pdfstore.save_sidecar(pdf, sidecar_doc)

    cached_doc = pdfstore.PdfDocument(
        [pdfstore.Page(595.0, 842.0, 0, []) for _ in range(3)])
    cached_doc.pages[0].strokes.append(
        Stroke([(5.0, 5.0), (6.0, 6.0)], "#00ff00", 3.0))
    write_cache(pdf, cached_doc, win._autosave_base)

    future = time.time() + 60
    os.utime(pdfstore.sidecar_path(pdf), (future, future))
    called: list = []
    win._ask_recover_cache = lambda saved_at: called.append(saved_at) or True

    assert win._enter_document(pdf) is True
    assert called == []
    assert cache_path_for(pdf, win._autosave_base).exists() is False
    strokes = win.pdf_view.document.pages[0].strokes
    assert len(strokes) == 1 and strokes[0].color == "#ff0000"


def test_startup_notes_pending_autosave(qapp, tmp_path, pdf):
    """启动时如果 autosave 目录有残留，状态栏先提示一句。"""
    ctrl = main.AppController(tmp_path / "cfg")
    doc = pdfstore.PdfDocument([pdfstore.Page(595.0, 842.0, 0, []) for _ in range(3)])
    write_cache(pdf, doc, ctrl.settings_path.parent)

    w = main.MainWindow(ctrl)
    try:
        assert "未恢复" in w.message_label.text()
    finally:
        w.pdf_view.close_document()
        w._quitting = True
        w.close()


# ---------- 9. 查找状态收口 ----------

def test_typing_after_search_clears_hits_without_searching(win, tmp_path):
    pdf = _make_two_hit_pdf(tmp_path / "two.pdf")
    win._enter_document(pdf)
    win._show_find()
    win.find_bar.input.setText("needle")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/2")

    win.find_bar.input.setText("needl")         # 改词：立即作废旧结果
    assert win._find_hits == []
    assert win.find_bar.count_label.text() == "0/0"
    assert win._find_worker is None             # 但绝不启动新搜索
    QTest.qWait(100)
    assert win._find_hits == []                 # 停留期间没有幽灵结果


def test_editing_after_search_does_not_step_old_hits(win, tmp_path):
    pdf = _make_two_hit_pdf(tmp_path / "two.pdf")
    win._enter_document(pdf)
    win._show_find()
    win.find_bar.input.setText("needle")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/2")

    win.find_bar.input.setText("zzz-none")
    assert win.find_bar.count_label.text() == "0/0"   # 旧命中已清，不会被回车步进
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)  # 回车才搜新词
    assert _wait_until(lambda: win._find_worker is None)
    assert win.find_bar.count_label.text() == "0/0"
    assert win._find_hits == []


def test_find_close_resets_input_and_count(win, tmp_path):
    pdf = _make_text_pdf(tmp_path / "needle.pdf", "needle")
    win._enter_document(pdf)
    win._show_find()
    win.find_bar.input.setText("needle")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/1")

    win.find_bar._close()
    assert win.find_bar.input.text() == ""
    assert win.find_bar.count_label.text() == "0/0"


def test_opening_new_pdf_clears_find_state(win, tmp_path):
    first = _make_text_pdf(tmp_path / "a.pdf", "needle")
    second = _make_text_pdf(tmp_path / "b.pdf", "other")
    win._enter_document(first)
    win.find_bar.input.setText("needle")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)
    assert _wait_until(lambda: bool(win._find_hits))

    assert win._enter_document(second) is True
    assert win._find_hits == []
    assert win.find_bar.input.text() == ""
    assert win.find_bar.count_label.text() == "0/0"
    assert win.pdf_view._hit_items == []


def test_find_query_is_trimmed(win, tmp_path):
    pdf = _make_text_pdf(tmp_path / "needle.pdf", "needle")
    win._enter_document(pdf)
    win.find_bar.input.setText("  needle  ")
    QTest.keyClick(win.find_bar.input, Qt.Key_Return)
    assert _wait_until(lambda: win.find_bar.count_label.text() == "1/1")


def test_find_caps_at_500_with_plus_indicator(win, pdf, monkeypatch):
    win._enter_document(pdf)
    fake = [(0, (0.0, 0.0, 1.0, 1.0)) for _ in range(501)]
    monkeypatch.setattr(win.pdf_view.source, "search_document",
                        lambda text, max_hits=500: fake[:max_hits])
    win._find_search("x")
    assert len(win._find_hits) == 500
    assert win.find_bar.count_label.text() == "1/500+"
