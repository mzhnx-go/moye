"""主窗 ↔ 二级窗口：标签整体搬迁、分屏标签一起走、窗口关闭收口。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path, pages=6):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), "content", fontsize=24)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def primary(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    win = main.MainWindow(ctrl)
    win._confirm_unsaved = lambda action: QMessageBox.Discard
    win.resize(1000, 700)
    win.show()
    QTest.qWaitForWindowExposed(win)
    _ALIVE.append(win)
    yield win
    for window in list(win.window_registry.secondaries):
        window._confirm_unsaved = lambda action: QMessageBox.Discard
        window._quitting = True
        window.close()
        window.deleteLater()
    win.scratch.hide()
    win._quitting = True
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _open_two(win, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf")
    b = _make_pdf(tmp_path / "b.pdf")
    assert win._enter_document(a) is True
    assert win._enter_document(b) is True
    entries = [e for e in win.tabs.entries if not e.draft]
    by_name = {e.sessions[0].path.name: e for e in entries}
    return by_name["a.pdf"], by_name["b.pdf"]


def _secondary(primary):
    window = primary.window_registry.create_window(QPoint(220, 180))
    window._confirm_unsaved = lambda action: QMessageBox.Discard
    QTest.qWait(40)
    _ALIVE.append(window)
    return window


def test_move_single_entry_keeps_session_and_position(primary, tmp_path):
    entry_a, entry_b = _open_two(primary, tmp_path)
    primary.tabs.set_current_entry(entry_a)
    QTest.qWait(40)
    session = entry_a.sessions[0]
    view = session.view
    view.verticalScrollBar().setValue(320)
    QTest.qWait(40)
    before = view.current_position()
    target = _secondary(primary)

    moved = primary._move_entry_to_window(entry_a, target)

    assert moved is not None
    assert entry_a not in primary.tabs.entries
    assert [e.sessions[0].path.name for e in target.tabs.entries] == ["a.pdf"]
    assert moved.sessions[0] is session
    assert target.pdf_view is view
    assert view.window() is target
    assert target.stack.currentWidget() is session.pane
    QTest.qWait(60)
    after = view.current_position()
    assert after["page"] == before["page"]
    assert after["offset"] == pytest.approx(before["offset"], abs=0.02)
    assert primary.tabs.current_entry is entry_b


def test_move_back_to_primary(primary, tmp_path):
    entry_a, _ = _open_two(primary, tmp_path)
    target = _secondary(primary)

    moved = primary._move_entry_to_window(entry_a, target)
    back = target._move_entry_to_window(moved, primary)

    assert back is not None
    assert {e.sessions[0].path.name for e in primary.tabs.entries
            if not e.draft} == {"a.pdf", "b.pdf"}
    assert target.tabs.entries == []
    assert primary.pdf_view is back.sessions[0].view


def test_move_split_entry_as_a_whole(primary, tmp_path):
    entry_a, entry_b = _open_two(primary, tmp_path)
    primary._merge_document_entries(entry_a, entry_b)
    split_entry = primary.tabs.current_entry
    assert split_entry.is_split is True
    target = _secondary(primary)

    moved = primary._move_entry_to_window(split_entry, target)
    QTest.qWait(60)

    assert moved is not None and moved.is_split is True
    assert moved.page is split_entry.page
    assert len(target.tabs.entries) == 1
    assert [s.path.name for s in moved.sessions] == ["a.pdf", "b.pdf"]
    assert all(s.pane.isVisible() for s in moved.sessions)
    # 分屏容器在目标窗仍可取消分屏
    target._unsplit_document_entry(moved.page)
    QTest.qWait(40)
    assert [e.is_split for e in target.tabs.entries] == [False, False]


def test_registry_detach_creates_window_and_moves_entry(primary, tmp_path):
    entry_a, _ = _open_two(primary, tmp_path)

    target = primary.window_registry.detach(primary, entry_a, QPoint(300, 200))

    assert target is not None
    assert target in primary.window_registry.secondaries
    assert [e.sessions[0].path.name for e in target.tabs.entries] == ["a.pdf"]
    assert entry_a not in primary.tabs.entries


def test_secondary_closes_when_last_tab_moves_out(primary, tmp_path):
    entry_a, _ = _open_two(primary, tmp_path)
    target = _secondary(primary)
    moved = primary._move_entry_to_window(entry_a, target)
    assert moved is not None

    back = target._move_entry_to_window(moved, primary)
    assert back is not None
    QTest.qWait(60)

    assert target.isVisible() is False
    assert target not in primary.window_registry.secondaries


def test_secondary_has_no_draft_and_empty_hint(primary):
    target = _secondary(primary)
    assert target.tabs.entries == []
    # 标题栏已无「新建草稿」按钮（新建草稿只走菜单 / Ctrl+N）
    assert not hasattr(target.tabs, "draft_button")
    assert target._empty_page is not None
    assert target.stack.currentWidget() is target._empty_page


def _tab_center(win, entry):
    bar = win.tabs.tabbar
    index = win.tabs.index_of(entry)
    return bar.mapToGlobal(bar.tabRect(index).center())


def test_controller_merge_preview_then_release_merges(primary, tmp_path):
    entry_a, entry_b = _open_two(primary, tmp_path)
    QTest.qWait(30)
    controller = primary.tabs.drag_controller
    start = _tab_center(primary, entry_a)
    target = _tab_center(primary, entry_b)

    assert controller.press(entry_a, start) is True
    controller.move(start + QPoint(10, 0))
    controller.move(target)
    controller.fire_merge_timer()
    assert controller.merge_preview_active is True
    controller.release(target)

    split = primary.tabs.current_entry
    assert split.is_split is True
    assert [s.path.name for s in split.sessions] == ["a.pdf", "b.pdf"]


def test_controller_detach_outside_creates_window(primary, tmp_path):
    entry_a, _ = _open_two(primary, tmp_path)
    QTest.qWait(30)
    controller = primary.tabs.drag_controller
    start = _tab_center(primary, entry_a)
    outside = QPoint(-4000, -4000)

    controller.press(entry_a, start)
    controller.move(start + QPoint(10, 0))
    controller.move(outside)
    controller.release(outside)

    secondaries = primary.window_registry.secondaries
    assert len(secondaries) == 1
    assert [e.sessions[0].path.name for e in secondaries[0].tabs.entries] == ["a.pdf"]
    assert entry_a not in primary.tabs.entries
