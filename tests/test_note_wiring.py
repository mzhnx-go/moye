# tests/test_note_wiring.py
"""主窗笔记接线（任务 5）：工具显隐、N 键、新建/展开/删除卡片、焦点分流与关卡竞态。

全部离屏运行；样例 PDF 在 tmp_path 现造，对话框一律 monkeypatch，不弹真窗。
"""
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QMenu,
                               QMessageBox)

import main
import sketchpad.app as app_module
from sketchpad.core.strokes import Stroke

# 三页竖版：与 test_app_modes_offscreen 同一份布局，文档模式行为保持一致
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


def _wait_until(predicate, timeout_ms: int = 3000) -> None:
    """轮询等待异步动作（关闭动画/closed 信号）；固定等待在整套测试一起跑时会不够。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if predicate():
            return
        QTest.qWait(20)
    assert predicate()


def _create_and_open(win, x=40.0, y=50.0):
    """建一条笔记并点图钉打开它（打开 = 独立小窗口），返回 (note, window)。"""
    win._create_note(0, x, y)
    note = win.pdf_view.document.pages[0].notes[-1]
    win._open_note(0, note.id)
    return note, win._note_windows.get((0, note.id))


def _drag_pin_on_view(win, start: QPoint, end: QPoint) -> None:
    """在主窗的 PDF 视图上真按一下图钉再拖到 end（含落在终点上的 move）。"""
    view = win.pdf_view
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    for step in range(1, 4):
        ratio = step / 4
        local = QPointF(start.x() + (end.x() - start.x()) * ratio,
                        start.y() + (end.y() - start.y()) * ratio)
        ev = QMouseEvent(QEvent.MouseMove, local,
                         QPointF(view.viewport().mapToGlobal(local.toPoint())),
                         Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(view.viewport(), ev)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)


# ---------- 工具显隐 ----------

def test_note_tool_visibility_follows_mode(win, pdf):
    assert win.mode == "draft"
    assert win.toolbox.note_button.isVisible() is False
    assert win.menubar.note_action.isEnabled() is False

    assert win._enter_document(pdf) is True
    assert win.toolbox.note_button.isVisible() is True
    assert win.menubar.note_action.isEnabled() is True

    win._enter_draft()
    assert win.toolbox.note_button.isVisible() is False
    assert win.menubar.note_action.isEnabled() is False


def test_n_key_switches_to_note_only_in_document_mode(win, pdf):
    assert win._enter_document(pdf) is True
    win.activateWindow()
    win.pdf_view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.pdf_view, Qt.Key_N)
    assert win.pdf_view.tool == "note"
    assert win.toolbox.note_button.isChecked() is True
    assert win.menubar.note_action.isChecked() is True

    win._enter_draft()
    win.view.setFocus()
    QTest.qWait(20)
    before = (win.view.tool, win.pdf_view.tool)
    QTest.keyClick(win.view, Qt.Key_N)                 # 草稿模式：N 不生效
    assert (win.view.tool, win.pdf_view.tool) == before
    assert win.toolbox.note_button.isChecked() is False


# ---------- 新建笔记 ----------

def test_create_note_clamps_anchor_adds_pin_and_marks_dirty(win, pdf):
    assert win._enter_document(pdf) is True
    win._dirty = False

    win._create_note(0, 40.0, 50.0)
    page = win.pdf_view.document.pages[0]
    assert len(page.notes) == 1
    note = page.notes[0]
    assert (note.x, note.y) == (40.0, 50.0)
    assert note.paper == "dark"
    assert (0, note.id) in win.pdf_view._note_pins
    assert win.pdf_view._note_pins[(0, note.id)].scene() is not None
    assert win._dirty is True
    assert "已插入笔记 1" in win.message_label.text()
    # 放完笔记自动切回鼠标，避免连续落图钉
    assert win.pdf_view.tool == "select"
    assert win.view.tool == "select"
    assert win.toolbox.select_button.isChecked() is True

    # 越界锚点钳回页面矩形；同页 id 递增
    win._create_note(0, -10.0, 99999.0)
    second = page.notes[1]
    assert (second.x, second.y) == (0.0, page.height)
    assert second.id != note.id

    # 坏页号一律忽略，不抛也不加笔记
    win._create_note(99, 10.0, 10.0)
    assert len(page.notes) == 2


# ---------- 展开 / 内容 / 关闭 ----------

def test_pdf_note_window_ctrlv_pastes_image(win, pdf):
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._open_note(0, note.id)
    QApplication.processEvents()
    window = win._note_windows[(0, note.id)]
    image = QImage(90, 60, QImage.Format.Format_ARGB32)
    image.fill(0xFF3366CC)
    QGuiApplication.clipboard().setImage(image)
    window.card.view.setFocus()
    QTest.keyClick(window.card.view, Qt.Key.Key_V,
                   Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert len(note.images) == 1
    from sketchpad.core import noteassets
    assert noteassets.asset_path(pdf, note.images[0].asset).exists()


def test_open_note_window_content_and_close_clears_reference(win, pdf):
    assert win._enter_document(pdf) is True
    note, window = _create_and_open(win)
    assert window is not None
    assert window.card.note is note

    win._dirty = False
    view = window.card.view
    view.document.strokes.append(Stroke([(10.0, 10.0), (30.0, 30.0)],
                                        "#e8eaed", 2.0))
    view.scene.rebind()
    view.changed.emit()                                # 卡片内容变化 → 自动暂存路径
    assert note.strokes and win._dirty is True

    window.close()
    _wait_until(lambda: (0, note.id) not in win._note_windows)
    assert (0, note.id) not in win._note_windows


# ---------- 删除 ----------

def test_delete_requested_removes_note_and_pin(win, pdf):
    assert win._enter_document(pdf) is True
    note, window = _create_and_open(win)
    assert (0, note.id) in win.pdf_view._note_pins

    window.deleteRequested.emit()                      # 窗口确认删除后的信号
    page = win.pdf_view.document.pages[0]
    assert page.notes == []
    assert (0, note.id) not in win.pdf_view._note_pins
    _wait_until(lambda: (0, note.id) not in win._note_windows)
    assert (0, note.id) not in win._note_windows


# ---------- 图钉 × 删除（需求 B：图标右上角关闭键） ----------

def test_pin_x_delete_confirmed_removes_note_and_pin(win, pdf, monkeypatch):
    """点图钉 × → 确认框答"是"：笔记与图钉一起没了，并标脏走自动暂存路径。"""
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    asked: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.Yes))

    win._dirty = False
    win.pdf_view.noteDeleteRequested.emit(0, note.id)

    assert len(asked) == 1
    assert note.id in asked[0][2]                     # 问话里点名是哪张笔记
    page = win.pdf_view.document.pages[0]
    assert page.notes == []
    assert (0, note.id) not in win.pdf_view._note_pins
    assert win._dirty is True
    assert "已删除笔记" in win.message_label.text()


def test_pin_x_delete_cancelled_keeps_everything(win, pdf, monkeypatch):
    """确认框答"否"（默认按钮）：笔记与图钉原封不动，也不标脏。"""
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))

    win._dirty = False
    win.pdf_view.noteDeleteRequested.emit(0, note.id)

    assert win.pdf_view.document.pages[0].notes == [note]
    assert (0, note.id) in win.pdf_view._note_pins
    assert win._dirty is False


def test_pin_x_click_through_view_reaches_confirm(win, pdf, monkeypatch):
    """从视图上真点一下 ×：走的是 noteDeleteRequested，不是开卡（不弹笔记卡）。"""
    assert win._enter_document(pdf) is True
    win._create_note(0, 60.0, 70.0)
    note = win.pdf_view.document.pages[0].notes[0]
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    anchor = win.pdf_view.note_anchor_view_pos(0, note.id)
    close = QPoint(anchor.x() + 7, anchor.y() - 7)    # × 圆心在图钉坐标 (7.5, -7.5)

    QTest.mouseClick(win.pdf_view.viewport(), Qt.LeftButton, pos=close)

    assert win._note_windows == {}                    # 只删不开窗
    assert win.pdf_view.document.pages[0].notes == []


# ---------- 拖图钉移动（需求 B：图标可移动） ----------

def test_pin_drag_through_view_updates_anchor_and_marks_dirty(win, pdf):
    """在主窗里拖图钉：Note.x/y 写回、图钉跟着挪、dirty 与自动暂存路径被触发。"""
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    page = win.pdf_view.document.pages[0]
    win._dirty = False
    start = win.pdf_view.note_anchor_view_pos(0, note.id)
    end = QPoint(start.x() + 25, start.y() + 35)

    _drag_pin_on_view(win, start, end)

    assert (note.x, note.y) != (40.0, 50.0)
    assert 0.0 <= note.x <= page.width and 0.0 <= note.y <= page.height
    layer = win.pdf_view._layers[0]
    pin = win.pdf_view._note_pins[(0, note.id)]
    assert pin.scenePos() == QPointF(layer.x + note.x, layer.y + note.y)
    assert win._dirty is True                         # notesChanged → _on_notes_changed
    assert win._note_windows == {}                    # 拖完不开窗


# ---------- 模式切换先关卡片 ----------

def test_mode_switches_close_open_window_first(win, pdf, tmp_path):
    assert win._enter_document(pdf) is True
    note, window = _create_and_open(win)
    assert window is not None

    second = _make_pdf(tmp_path / "second.pdf", ((300.0, 400.0),))
    assert win._enter_document(second) is True
    # 多标签：旧文档的窗口留着（内容在共享模型里），切走时不销毁
    assert window.isVisible() is True
    assert win._note_windows == {}                     # 新标签没有窗口
    assert win.pdf_view.pdf_path == second
    first_entry = next(e for e in win.tabs.entries
                       if not e.draft and e.sessions[0].path == pdf)
    win.tabs.set_current_entry(first_entry)
    _wait_until(lambda: (0, note.id) in win._note_windows)
    assert win._note_windows[(0, note.id)] is window
    assert win.pdf_view.pdf_path == pdf

    note2, window2 = _create_and_open(win, 20.0, 30.0)
    assert window2 is not None
    win._enter_draft()                                 # 兼容“关闭所有文档”
    assert win.mode == "draft"


# ---------- 焦点分流 ----------

def test_card_focus_routes_b_to_note_view_not_pdf(win, pdf):
    """焦点在笔记内（主窗分屏那张）：B/Q 归 NoteView，不落到 PDF 视图。"""
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._open_note_split(0, note.id)
    _wait_until(lambda: win._note_split_card is not None)
    win.pdf_view.set_tool("eraser")
    card = win._note_split_card
    card.view.set_tool("eraser")
    card.view.setFocus()
    QTest.qWait(20)
    focus = QApplication.focusWidget()
    assert focus is not None and card.isAncestorOf(focus)

    QTest.keyClick(card.view, Qt.Key_B)                # 放行给 NoteView，不落到 PDF
    assert card.view.tool == "pen"
    assert win.pdf_view.tool == "eraser"


# ---------- 竞态：旧卡片迟到的 closed 不能清掉新卡片 ----------

def test_two_note_windows_are_independent(win, pdf):
    """两张笔记各开各的窗口：关掉一张不影响另一张（旧浮层的竞态随形态取消而消失）。"""
    assert win._enter_document(pdf) is True
    win._create_note(0, 20.0, 30.0)
    win._create_note(0, 60.0, 70.0)
    first, second = win.pdf_view.document.pages[0].notes

    win._open_note(0, first.id)
    win._open_note(0, second.id)
    w1 = win._note_windows[(0, first.id)]
    w2 = win._note_windows[(0, second.id)]
    assert w1 is not w2

    w1.close()
    _wait_until(lambda: (0, first.id) not in win._note_windows)
    assert (0, second.id) in win._note_windows
    assert win._note_windows[(0, second.id)] is w2


# ---------- 导出接线：有笔记走插页导出，无笔记保持原路径 ----------

def test_export_uses_note_pages_when_notes_exist(win, pdf, tmp_path, monkeypatch):
    assert win._enter_document(pdf) is True
    out = tmp_path / "with-notes.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "PDF (*.pdf)")))
    calls = []
    monkeypatch.setattr(app_module, "export_with_notes",
                        lambda src, strokes, notes, path, **kwargs: calls.append(
                            ("notes", strokes, notes, path)) or Path(path))
    monkeypatch.setattr(app_module, "export_with_strokes",
                        lambda src, strokes, path: calls.append(
                            ("strokes", strokes, path)) or Path(path))

    assert win._export_pdf() is True                 # 无笔记：老路径
    assert calls[-1][0] == "strokes"

    win._create_note(0, 40.0, 50.0)
    assert win._export_pdf() is True                 # 有笔记：插页路径
    kind, strokes, notes, path = calls[-1]
    assert kind == "notes"
    assert Path(path) == out
    assert len(strokes) == len(notes) == len(win.pdf_view.document.pages)
    assert len(notes[0]) == 1 and notes[0][0].id == win.pdf_view.document.pages[0].notes[0].id


def test_export_with_notes_end_to_end(win, pdf, tmp_path, monkeypatch):
    """真实导出：源 3 页 + 第 1 页一张笔记 → 4 页，末页是笔记页。"""
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    note.strokes.append(Stroke([(10.0, 10.0), (80.0, 60.0)], "#e8eaed", 2.0))

    out = tmp_path / "exported.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "PDF (*.pdf)")))
    assert win._export_pdf() is True
    assert out.exists()

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 4
        # 页序：源页 0 → 页 0 的笔记页 → 源页 1 → 源页 2
        assert doc[1].rect.width == pytest.approx(595.0)
        assert doc[1].rect.height == pytest.approx(595.0 * 720.0 / 560.0)
        assert doc[1].get_drawings()          # 纸面横线 + 笔记笔迹
        assert doc[0].get_drawings()          # 锚点页的序号标记
        assert doc[3].rect.width == pytest.approx(420.0)   # 末页仍是源页
    finally:
        doc.close()


# ---------- 命名 / 独立窗口 / 右键菜单 ----------

def test_rename_note_updates_model_pin_and_open_card(win, pdf, monkeypatch):
    assert win._enter_document(pdf) is True
    note, window = _create_and_open(win)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("函数笔记", True)))

    win._rename_note(0, note.id)

    assert note.name == "函数笔记"
    assert window.card.title_label.text() == "函数笔记"
    assert win.pdf_view._note_pins[(0, note.id)].name == "函数笔记"
    assert win._dirty is True


def test_rename_cancel_keeps_old_name(win, pdf, monkeypatch):
    assert win._enter_document(pdf) is True
    note, _overlay = _create_and_open(win)
    note.name = "旧名字"
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("新名字", False)))
    win._rename_note(0, note.id)
    assert note.name == "旧名字"


def test_note_window_toggle_and_close(win, pdf):
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]

    win._toggle_note_window(0, note.id)
    key = (0, note.id)
    window = win._note_windows[key]
    assert window.isWindow() is True
    assert window.card.grip.isVisible() is False
    assert "笔记" in window.windowTitle()

    # 独立窗口里的编辑内容同样走 dirty + 自动暂存
    win._dirty = False
    window.contentChanged.emit()
    assert win._dirty is True

    # 再点同一项 = 收回；closed 后从字典摘掉
    win._toggle_note_window(0, note.id)
    _wait_until(lambda: key not in win._note_windows)
    assert key not in win._note_windows


def test_open_note_focuses_existing_window(win, pdf):
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._toggle_note_window(0, note.id)
    window = win._note_windows[(0, note.id)]
    win._open_note(0, note.id)                     # 已开独立窗口：聚焦，不新开
    assert win._note_windows[(0, note.id)] is window


def test_delete_note_closes_its_window(win, pdf, monkeypatch):
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._toggle_note_window(0, note.id)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    win._delete_note_requested(0, note.id)
    _wait_until(lambda: (0, note.id) not in win._note_windows)
    assert win.pdf_view.document.pages[0].notes == []


def test_note_context_menu_contents_and_dispatch(win, pdf, monkeypatch):
    """用假 QMenu 抓菜单项；真实 QMenu.exec 在离屏下会阻塞，不能直接测。"""
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]

    class FakeMenu:
        last = None
        pick = None

        def __init__(self, parent=None):
            FakeMenu.last = self
            self._actions = []

        def addAction(self, text):
            from PySide6.QtGui import QAction
            action = QAction(text)
            self._actions.append(action)
            return action

        def addSeparator(self):
            pass

        def actions(self):
            return list(self._actions)

        def exec(self, pos):
            if not FakeMenu.pick:
                return None
            return next(action for action in self._actions
                        if action.text().startswith(FakeMenu.pick))

    monkeypatch.setattr(app_module, "QMenu", FakeMenu)

    FakeMenu.pick = None
    win._show_note_menu(0, note.id, QPoint(10, 10))
    texts = [action.text() for action in FakeMenu.last.actions()]
    for label in ("打开笔记", "分屏打开", "在独立窗口打开", "重命名…", "切换纸张深浅", "删除笔记…"):
        assert label in texts

    called = []
    monkeypatch.setattr(win, "_toggle_note_window",
                        lambda p, i: called.append((p, i)))
    FakeMenu.pick = "在独立窗口"
    win._show_note_menu(0, note.id, QPoint(10, 10))
    assert called == [(0, note.id)]


# ---------- 主窗内分屏：不改 PDF 缩放 ----------

def test_split_keeps_pdf_zoom_and_restores_fit(win, pdf):
    assert win._enter_document(pdf) is True
    QTest.qWait(40)
    zoom_before = win.pdf_view.zoom
    assert win.pdf_view.auto_fit is True
    win.pdf_view.verticalScrollBar().setValue(180)
    QTest.qWait(40)
    position_before = win.pdf_view.current_position()

    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._open_note_split(0, note.id)
    QTest.qWait(80)

    assert win._note_split_card is not None
    pane = win._note_split_pane
    assert pane is not None
    QTest.qWait(30)
    assert pane.is_note_open is True
    assert pane.splitter.count() == 2
    assert pane.pdf_frame.isAncestorOf(win.pdf_view)
    assert pane.note_frame.isAncestorOf(win._note_split_card)
    assert pane.title_row.isVisible() is True
    assert win.pdf_view.zoom == pytest.approx(zoom_before)
    assert win.pdf_view.auto_fit is False       # 分屏期间冻结缩放
    position_split = win.pdf_view.current_position()
    assert position_split["page"] == position_before["page"]
    assert position_split["offset"] == pytest.approx(position_before["offset"], abs=0.02)

    win._close_note_split()
    _wait_until(lambda: win._note_split_pane is None)
    QTest.qWait(80)
    assert win.pdf_view.auto_fit is True        # 退出分屏恢复适应页宽
    position_after = win.pdf_view.current_position()
    assert position_after["page"] == position_before["page"]
    assert position_after["offset"] == pytest.approx(position_before["offset"], abs=0.02)


def test_window_split_button_moves_note_to_split(win, pdf):
    """独立窗口里的「分屏」：收回窗口、笔记落到主窗分屏（两种形态互斥）。"""
    assert win._enter_document(pdf) is True
    note, window = _create_and_open(win)
    window.card.split_button.click()
    _wait_until(lambda: win._note_split_card is not None
                and (0, note.id) not in win._note_windows)
    assert win._note_split_key == (0, note.id)


def test_window_split_returns_to_main_split(win, pdf):
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._toggle_note_window(0, note.id)
    window = win._note_windows[(0, note.id)]

    win._move_note_to_split(0, note.id, window)
    _wait_until(lambda: (0, note.id) not in win._note_windows)
    assert win._note_split_key == (0, note.id)
    assert win._note_split_card is not None


def test_delete_note_closes_split(win, pdf, monkeypatch):
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._open_note_split(0, note.id)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    win._delete_note_requested(0, note.id)
    assert win._note_split_card is None
    assert win._note_split_pane is None


def test_split_card_rich_text_persists_to_sidecar(win, pdf):
    from sketchpad.core import pdfstore

    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._open_note_split(0, note.id)
    view = win._note_split_card.view
    block = view.add_text_block(30.0, 40.0, "函数笔记")
    assert block.text == "函数笔记"
    view.notify_changed()

    assert win._save_annotations() is True
    loaded = pdfstore.load_sidecar(pdf, 3)
    assert loaded is not None
    saved = loaded.pages[0].notes[0]
    assert saved.texts[0].text == "函数笔记"


def test_note_window_geometry_is_remembered_within_session(win, pdf):
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._toggle_note_window(0, note.id)
    window = win._note_windows[(0, note.id)]
    target = QRect(120, 140, 680, 760)
    window.setGeometry(target)
    QTest.qWait(20)

    win._close_note_windows()
    _wait_until(lambda: (0, note.id) not in win._note_windows)
    assert win._active_session.note_window_geometry[note.id] == target

    win._toggle_note_window(0, note.id)
    reopened = win._note_windows[(0, note.id)]
    assert reopened.geometry() == target


def test_split_card_focus_gets_keys_not_pdf(win, pdf):
    assert win._enter_document(pdf) is True
    win._create_note(0, 40.0, 50.0)
    note = win.pdf_view.document.pages[0].notes[0]
    win._open_note_split(0, note.id)
    card = win._note_split_card
    win.pdf_view.set_tool("eraser")
    card.view.set_tool("eraser")
    card.view.setFocus()
    QTest.qWait(20)
    focus = QApplication.focusWidget()
    assert focus is not None and card.isAncestorOf(focus)
    QTest.keyClick(card.view, Qt.Key_B)
    assert card.view.tool == "pen"          # 放行给笔记画布
    assert win.pdf_view.tool == "eraser"    # 不落到 PDF
