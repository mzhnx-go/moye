"""二级窗口登记处：登记、命中、移入、合并、退出收口（用假窗口，不建真主窗）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget

from sketchpad.workspace.windows import WindowRegistry

_ALIVE: list = []


class FakeEntry:
    def __init__(self, name):
        self.name = name
        self.draft = False
        self.sessions = [name]
        self.page = QWidget()
        self.is_split = False


class FakeTabs:
    def __init__(self):
        self.entries = []
        self.window_id = 0

    def set_window_id(self, value):
        self.window_id = value

    def add_entry(self, sessions, page):
        entry = FakeEntry(sessions[0])
        entry.sessions = list(sessions)
        entry.page = page
        self.entries.append(entry)
        return entry

    def remove_entry(self, entry):
        if entry in self.entries:
            self.entries.remove(entry)

    def move_entry_to(self, entry, index):
        self.entries.remove(entry)
        self.entries.insert(index, entry)


class FakeWindow(QWidget):
    def __init__(self, name="win"):
        super().__init__()
        self.name = name
        self.window_id = 0
        self.tabs = FakeTabs()
        self.moved = []
        self.close_result = True

    def _move_entry_to_window(self, entry, target, index=None):
        self.tabs.remove_entry(entry)
        adopted = target.tabs.add_entry(entry.sessions, entry.page)
        if index is not None:
            target.tabs.move_entry_to(adopted, index)
        self.moved.append((entry, target))
        return adopted

    def _merge_document_entries(self, first, second):
        self.tabs.remove_entry(first)
        self.tabs.remove_entry(second)
        merged = FakeEntry(f"{first.name} | {second.name}")
        self.tabs.entries.append(merged)
        return merged

    def close(self):
        self.closed = True
        return self.close_result


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _registry(qapp):
    primary = FakeWindow("primary")
    windows = []

    def factory():
        window = FakeWindow(f"secondary{len(windows)}")
        windows.append(window)
        _ALIVE.append(window)
        return window

    _ALIVE.extend([primary])
    return WindowRegistry(primary, factory), windows


def test_register_assigns_ids_and_lookup(qapp):
    registry, _ = _registry(qapp)
    primary = registry.primary
    assert primary.window_id == 1
    assert primary.tabs.window_id == 1
    assert registry.find(1) is primary
    assert registry.window_for_tabs(primary.tabs) is primary
    assert registry.secondaries == []


def test_detach_creates_secondary_and_moves_entry(qapp):
    registry, windows = _registry(qapp)
    primary = registry.primary
    entry = primary.tabs.add_entry(["a"], QWidget())

    target = registry.detach(primary, entry, QPoint(400, 300))

    assert target is windows[0]
    assert registry.find(target.window_id) is target
    assert primary.tabs.entries == []
    assert [e.name for e in target.tabs.entries] == ["a"]
    assert primary.moved and primary.moved[0][1] is target


def test_detach_ignores_draft(qapp):
    registry, windows = _registry(qapp)
    entry = FakeEntry("draft")
    entry.draft = True
    assert registry.detach(registry.primary, entry, QPoint(10, 10)) is None
    assert windows == []


def test_transfer_cross_window_and_same_window(qapp):
    registry, _ = _registry(qapp)
    primary = registry.primary
    target = registry.create_window(QPoint(100, 100))
    entry = primary.tabs.add_entry(["a"], QWidget())

    moved = registry.transfer(primary, entry, target)
    assert moved is not None
    assert [e.name for e in target.tabs.entries] == ["a"]
    assert primary.tabs.entries == []

    first = target.tabs.add_entry(["b"], QWidget())
    second = target.tabs.add_entry(["c"], QWidget())
    registry.transfer(target, second, target, 0)
    assert [e.name for e in target.tabs.entries][:2] == ["c", "a"]
    assert first in target.tabs.entries


def test_merge_same_window_and_cross_window(qapp):
    registry, _ = _registry(qapp)
    primary = registry.primary
    a = primary.tabs.add_entry(["a"], QWidget())
    b = primary.tabs.add_entry(["b"], QWidget())
    assert registry.merge(primary, a, primary, b) is True
    assert len(primary.tabs.entries) == 1

    target = registry.create_window(QPoint(100, 100))
    other = target.tabs.add_entry(["c"], QWidget())
    cross = primary.tabs.add_entry(["d"], QWidget())
    assert registry.merge(primary, cross, target, other) is True
    assert target.tabs.entries[0].name == "d | c"
    assert [e.name for e in primary.tabs.entries] == ["a | b"]


def test_window_at_uses_frame_geometry(qapp):
    registry, _ = _registry(qapp)
    primary = registry.primary
    primary.setGeometry(QRect(0, 0, 500, 400))
    primary.show()
    qapp.processEvents()
    assert registry.window_at(QPoint(100, 100)) is primary
    assert registry.window_at(QPoint(2000, 2000)) is None
    assert registry.inside_any(QPoint(498, 398)) is True
    assert registry.inside_any(QPoint(505, 400)) is False
    assert registry.inside_any(QPoint(505, 400), margin=8) is True


def test_close_all_stops_on_cancel(qapp):
    registry, windows = _registry(qapp)
    first = registry.create_window(QPoint(100, 100))
    second = registry.create_window(QPoint(200, 200))
    first.close_result = False

    assert registry.close_all() is False
    assert getattr(first, "closed", False) is True
    assert getattr(second, "closed", False) is False

    first.close_result = True
    assert registry.close_all() is True
    assert getattr(second, "closed", False) is True
