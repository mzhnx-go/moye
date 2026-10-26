"""富文本 UI：投影字体、Ctrl+B/I/U、右键格式/字号、编辑器与格式保留。

多选底座来自 Phase A；这里只验证 Phase B 接进 PdfView 后的行为。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.core.notes import NoteImage, TextBlock
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.core.settings import Settings
from sketchpad.ui import pdfview as pdfview_module
from sketchpad.ui.pdfview import PdfView

W, H = 595.0, 842.0
_ALIVE: list = []


class _FakeSignal:
    def connect(self, *_args, **_kwargs) -> None:
        pass


class _FakeAction:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self._menu = None
        self.triggered = _FakeSignal()

    def text(self) -> str:
        return self._text

    def setCheckable(self, *_args) -> None:
        pass

    def setChecked(self, *_args) -> None:
        pass

    def menu(self):
        return self._menu


class _FakeMenu:
    """只记录菜单结构，绝不真弹菜单（真 exec 在离屏测试里会永久阻塞）。"""

    instances: list["_FakeMenu"] = []

    def __init__(self, *_args) -> None:
        self._actions: list[_FakeAction] = []
        _FakeMenu.instances.append(self)

    def addAction(self, text: str) -> _FakeAction:
        action = _FakeAction(text)
        self._actions.append(action)
        return action

    def addMenu(self, text: str) -> "_FakeMenu":
        action = _FakeAction(text)
        action._menu = _FakeMenu()
        self._actions.append(action)
        return action._menu

    def addSeparator(self) -> _FakeAction:
        action = _FakeAction("---")
        self._actions.append(action)
        return action

    def exec(self, *_args) -> None:
        return None

    def actions(self) -> list[_FakeAction]:
        return self._actions


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path):
    doc = pymupdf.open()
    doc.new_page(width=W, height=H)
    doc.save(str(path))
    doc.close()
    return path


def _document() -> PdfDocument:
    return PdfDocument([Page(
        W, H,
        texts=[
            TextBlock("t1", 60.0, 60.0, 160.0, "甲甲", 16.0, "#e8453c"),
            TextBlock("t2", 60.0, 180.0, 160.0, "乙乙", 16.0, "#2b7de9"),
        ],
        images=[NoteImage("i1", 300.0, 60.0, 120.0, 80.0, "missing.png")],
    )])


@pytest.fixture
def view(qapp, tmp_path):
    pdf = _make_pdf(tmp_path / "富文本.pdf")
    v = PdfView(Settings())
    v.resize(820, 620)
    v.show()
    qapp.processEvents()
    v.open(pdf, document_override=_document())
    qapp.processEvents()
    v.set_tool("select")
    qapp.processEvents()
    _ALIVE.append(v)
    yield v
    v.close_document()
    v.close()


def _block(view, obj_id: str) -> TextBlock:
    return next(b for b in view.document.pages[0].texts if b.id == obj_id)


def _text_item(view, obj_id: str):
    for item in view._layers[0].text_items:
        block = item.data(0)
        if isinstance(block, TextBlock) and block.id == obj_id:
            return item
    return None


def _select_text(view, *ids):
    keys = [(0, "text", obj_id) for obj_id in ids]
    view._set_selection(keys)


def _context_actions(view, scene_point: QPointF, monkeypatch) -> list[list[str]]:
    _FakeMenu.instances.clear()
    monkeypatch.setattr(pdfview_module, "QMenu", _FakeMenu)
    pos = view.mapFromScene(scene_point)
    event = QContextMenuEvent(QContextMenuEvent.Mouse, pos,
                              view.viewport().mapToGlobal(pos))
    QApplication.sendEvent(view.viewport(), event)

    menu = _FakeMenu.instances[0]          # addMenu 会追加子菜单，根菜单永远最先建
    seen = [[action.text() for action in menu.actions()]]
    for action in menu.actions():
        if action.menu() is not None:
            seen.append([child.text() for child in action.menu().actions()])
    return seen


# ---------- 投影 / 快捷键 ----------


def test_projection_uses_bold_italic_underline_font(view):
    block = _block(view, "t1")
    updated = TextBlock(block.id, block.x, block.y, block.w, block.text,
                        block.size, block.color, True, True, True)
    page = view.document.pages[0]
    page.texts[0] = updated
    view._rebind_page(0)

    item = _text_item(view, "t1")
    assert item is not None
    assert item.font().bold() and item.font().italic()
    assert item.font().underline()


def test_ctrl_b_toggles_single_text_with_one_undo(view, qapp):
    _select_text(view, "t1")
    view.setFocus()
    base = view.history.depth

    QTest.keyClick(view, Qt.Key_B, Qt.ControlModifier)
    qapp.processEvents()
    assert _block(view, "t1").bold is True
    assert view.history.depth == base + 1
    assert _text_item(view, "t1").font().bold() is True

    QTest.keyClick(view, Qt.Key_B, Qt.ControlModifier)
    qapp.processEvents()
    assert _block(view, "t1").bold is False
    assert view.history.depth == base + 2

    view.history.pop_undo()
    assert _block(view, "t1").bold is True


def test_ctrl_i_applies_to_multi_text_and_is_one_command(view, qapp):
    _select_text(view, "t1", "t2")
    view.setFocus()
    base = view.history.depth

    QTest.keyClick(view, Qt.Key_I, Qt.ControlModifier)
    qapp.processEvents()
    assert _block(view, "t1").italic is True
    assert _block(view, "t2").italic is True
    assert view.history.depth == base + 1

    view.history.pop_undo()
    assert _block(view, "t1").italic is False
    assert _block(view, "t2").italic is False


def test_format_ignores_image_in_mixed_selection(view, qapp):
    view._set_selection([(0, "text", "t1"), (0, "image", "i1")])
    view.setFocus()
    base = view.history.depth

    QTest.keyClick(view, Qt.Key_U, Qt.ControlModifier)
    qapp.processEvents()
    assert _block(view, "t1").underline is True
    assert view.history.depth == base + 1
    assert [image.id for image in view.document.pages[0].images] == ["i1"]


# ---------- 编辑器 ----------


def test_editor_ctrl_b_updates_font_and_commits_to_model(view, qapp):
    block = _block(view, "t1")
    view._begin_text_edit(0, block, QPointF(80.0, 80.0))
    qapp.processEvents()
    editor = view._text_editing
    assert editor is not None

    QTest.keyClick(view, Qt.Key_B, Qt.ControlModifier)
    qapp.processEvents()
    assert view._text_editing_bold is True
    cursor = editor.textCursor()
    cursor.select(QTextCursor.Document)
    assert cursor.charFormat().font().bold() is True

    view.commit_text_edit()
    qapp.processEvents()
    assert _block(view, "t1").bold is True
    assert _block(view, "t1").text == "甲甲"


def test_edit_commit_preserves_existing_format(view, qapp):
    block = _block(view, "t1")
    page = view.document.pages[0]
    page.texts[0] = TextBlock(block.id, block.x, block.y, block.w, block.text,
                              block.size, block.color, True, False, True)
    view._rebind_page(0)

    view._begin_text_edit(0, page.texts[0], QPointF(80.0, 80.0))
    view._text_editing.setPlainText("改过的文字")
    view.commit_text_edit()
    qapp.processEvents()

    written = _block(view, "t1")
    assert written.text == "改过的文字"
    assert written.bold is True and written.underline is True
    assert written.italic is False


# ---------- 字号 / 移动保留格式 ----------


def test_size_change_preserves_format(view):
    page = view.document.pages[0]
    block = page.texts[0]
    page.texts[0] = TextBlock(block.id, block.x, block.y, block.w, block.text,
                              block.size, block.color, True, True, False)
    view._rebind_page(0)

    _select_text(view, "t1")
    assert view._set_text_size([(0, "text", "t1")], 24.0) is True
    updated = _block(view, "t1")
    assert updated.size == pytest.approx(24.0)
    assert updated.bold is True and updated.italic is True


def test_nudge_preserves_format(view):
    page = view.document.pages[0]
    block = page.texts[0]
    page.texts[0] = TextBlock(block.id, block.x, block.y, block.w, block.text,
                              block.size, block.color, True, False, True)
    view._rebind_page(0)

    view._set_selection([(0, "text", "t1")])
    assert view._nudge_selected(1.0, 1.0) is True
    updated = _block(view, "t1")
    assert updated.bold is True and updated.underline is True
    assert updated.x == pytest.approx(block.x + 1.0)


# ---------- 右键菜单 ----------


def test_single_text_context_menu_has_format_and_size(view, monkeypatch):
    block = _block(view, "t1")
    center = view._object_scene_rect(0, "text", block).center()
    seen = _context_actions(view, center, monkeypatch)

    labels = seen[0]
    assert "编辑文字" in labels
    assert "粗体" in labels and "斜体" in labels and "下划线" in labels
    assert "字号" in labels
    assert any("12" in children and "32" in children for children in seen)


def test_multi_text_context_menu_has_format_and_batch_delete(view, monkeypatch):
    _select_text(view, "t1", "t2")
    block = _block(view, "t1")
    center = view._object_scene_rect(0, "text", block).center()
    seen = _context_actions(view, center, monkeypatch)

    labels = seen[0]
    assert "粗体" in labels and "删除选中项" in labels
