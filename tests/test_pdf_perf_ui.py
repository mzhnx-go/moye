"""分屏切换与缩略图的性能收口：不写盘、不后台渲染隐藏侧栏。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path, pages=3):
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    doc.save(str(path))
    doc.close()
    return path


def _split_window(qapp, tmp_path):
    controller = main.AppController(tmp_path / "cfg")
    win = main.MainWindow(controller)
    win.resize(1200, 800)
    win.show()
    qapp.processEvents()
    first = _make_pdf(tmp_path / "一.pdf")
    second = _make_pdf(tmp_path / "二.pdf")
    assert win._enter_document(first) is True
    assert win._enter_document(second) is True
    qapp.processEvents()
    entries = [entry for entry in win.tabs.entries
               if entry.sessions and not win._is_note_session(entry.sessions[0])]
    win._merge_document_entries(entries[0], entries[1])
    qapp.processEvents()
    _ALIVE.append(win)
    return win


def test_split_switch_defers_settings_write(qapp, tmp_path):
    """切换分屏两侧只更新内存位置，不在点击路径同步写 settings.json。"""
    win = _split_window(qapp, tmp_path)
    try:
        calls = []
        win.controller.save_settings = lambda: calls.append(1)
        sessions = list(win._active_entry.sessions)
        other = next(s for s in sessions if s is not win._active_session)
        win._activate_session(other)
        qapp.processEvents()
        assert calls == []
        assert win._position_timer.isActive() is True
        win._position_timer.stop()
    finally:
        win._quitting = True
        win.close()


def test_hidden_thumb_panel_does_not_render_icons(qapp, tmp_path):
    """侧栏收起时只建页码占位，不在后台反复渲染缩略图拖慢分屏切换。"""
    controller = main.AppController(tmp_path / "cfg")
    win = main.MainWindow(controller)
    win.resize(1000, 700)
    win.show()
    qapp.processEvents()
    pdf = _make_pdf(tmp_path / "缩略图.pdf", pages=4)
    assert win._enter_document(pdf) is True
    qapp.processEvents()
    _ALIVE.append(win)
    try:
        assert win.side_panel.isVisible() is False
        assert win.thumb_panel._rendered == set()
        win.menubar.thumbs_action.setChecked(True)
        win._toggle_side_panel("thumbs")
        QTest.qWait(80)
        assert win.side_panel.isVisible() is True
        assert win.thumb_panel._rendered
    finally:
        win._quitting = True
        win.close()
