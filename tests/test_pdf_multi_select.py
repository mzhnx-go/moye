"""PDF 页面图文对象多选：Ctrl/Shift 点选、框选、批量移动/微调/删除、撤销与手柄回归。

对象用 `document_override` 直接喂给视图（与崩溃恢复同一条路径），坐标固定，
测试不依赖插入图片时的视口中心，也不碰真实用户文件。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.core.notes import NoteImage, TextBlock
from sketchpad.core.pdfcommands import ReplaceMany
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.core.settings import Settings
from sketchpad.ui.pdfview import PdfView

W, H = 595.0, 842.0
_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path):
    doc = pymupdf.open()
    doc.new_page(width=W, height=H)
    doc.save(str(path))
    doc.close()
    return path


def _objects_document() -> PdfDocument:
    """三块文字 + 一张图片（资源缺失走灰色占位，位置仍由模型决定）。"""
    return PdfDocument([Page(
        W, H,
        texts=[
            TextBlock("t1", 60.0, 60.0, 160.0, "甲甲", 16.0, "#e8453c"),
            TextBlock("t2", 60.0, 180.0, 160.0, "乙乙", 16.0, "#2b7de9"),
            TextBlock("t3", 60.0, 260.0, 160.0, "丙丙", 16.0, "#2e8b57"),
        ],
        images=[NoteImage("i1", 300.0, 60.0, 120.0, 80.0, "missing.png")],
    )])


@pytest.fixture
def view(qapp, tmp_path):
    pdf = _make_pdf(tmp_path / "多选.pdf")
    v = PdfView(Settings())
    v.resize(820, 620)
    v.show()
    qapp.processEvents()
    v.open(pdf, document_override=_objects_document())
    qapp.processEvents()
    v.set_tool("select")
    qapp.processEvents()
    _ALIVE.append(v)
    yield v
    v.close_document()
    v.close()


# ---------- 鼠标辅助 ----------

def _view_pos(view: PdfView, scene_point: QPointF) -> QPoint:
    mapped = view.mapFromScene(scene_point)
    return QPoint(round(mapped.x()), round(mapped.y()))


def _object_center(view: PdfView, page: int, kind: str, obj_id: str) -> QPointF:
    obj = view._object_for_key((page, kind, obj_id))
    return view._object_scene_rect(page, kind, obj).center()


def _click(view: PdfView, scene_point: QPointF, modifier=Qt.NoModifier) -> None:
    QTest.mouseClick(view.viewport(), Qt.LeftButton, modifier,
                     _view_pos(view, scene_point))


def _send_move(view: PdfView, pos: QPoint, buttons, modifiers) -> None:
    """QTest.mouseMove 不带按键位，拖动必须自己造 MouseMove。"""
    local = QPointF(pos)
    global_pos = QPointF(view.viewport().mapToGlobal(pos))
    ev = QMouseEvent(QEvent.MouseMove, local, global_pos, Qt.NoButton,
                     buttons, modifiers)
    QApplication.sendEvent(view.viewport(), ev)


def _drag(view: PdfView, start: QPoint, end: QPoint,
          modifier=Qt.NoModifier) -> None:
    QTest.mousePress(view.viewport(), Qt.LeftButton, modifier, start)
    mid = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
    _send_move(view, mid, Qt.LeftButton, modifier)
    _send_move(view, end, Qt.LeftButton, modifier)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, modifier, end)


def _drag_scene(view: PdfView, start: QPointF, end: QPointF,
                modifier=Qt.NoModifier) -> None:
    _drag(view, _view_pos(view, start), _view_pos(view, end), modifier)


def _visible_handles(view: PdfView) -> list[str]:
    return [mode for handle, mode in zip(
        view._sel_handles, ("nw", "n", "ne", "e", "se", "s", "sw", "w"))
        if handle.isVisible()]


def _send_context_menu(view: PdfView, scene_point: QPointF) -> None:
    pos = _view_pos(view, scene_point)
    ev = QContextMenuEvent(QContextMenuEvent.Mouse, pos,
                           view.viewport().mapToGlobal(pos))
    QApplication.sendEvent(view.viewport(), ev)


# ---------- 选择集合 ----------

def test_ctrl_shift_click_and_escape(view, qapp):
    _click(view, _object_center(view, 0, "text", "t1"))
    assert view._selection == [(0, "text", "t1")]
    assert _visible_handles(view) == ["e", "w"]

    _click(view, _object_center(view, 0, "text", "t2"), Qt.ControlModifier)
    assert view._selection == [(0, "text", "t1"), (0, "text", "t2")]
    assert _visible_handles(view) == []          # 多选不显示缩放手柄
    assert view._sel_box.isVisible() is True

    _click(view, _object_center(view, 0, "text", "t3"), Qt.ShiftModifier)
    assert [key[2] for key in view._selection] == ["t1", "t2", "t3"]

    # Ctrl 再点已选中的对象 = 从集合里去掉
    _click(view, _object_center(view, 0, "text", "t3"), Qt.ControlModifier)
    assert [key[2] for key in view._selection] == ["t1", "t2"]

    view.setFocus()
    QTest.keyClick(view, Qt.Key_Escape)
    qapp.processEvents()
    assert view._selection == []
    assert view._sel_box.isVisible() is False


def test_click_blank_clears_selection(view):
    _click(view, _object_center(view, 0, "text", "t1"))
    _click(view, _object_center(view, 0, "text", "t2"), Qt.ControlModifier)
    assert len(view._selection) == 2

    _click(view, QPointF(500.0, 400.0))          # 页内空白
    assert view._selection == []
    assert view._sel_box.isVisible() is False


# ---------- 框选 / 平移 ----------

def test_ctrl_drag_marquee_selects_intersecting_objects(view):
    _drag_scene(view, QPointF(40.0, 40.0), QPointF(430.0, 150.0),
                Qt.ControlModifier)
    selected = {(key[1], key[2]) for key in view._selection}
    assert selected == {("text", "t1"), ("image", "i1")}
    assert _visible_handles(view) == []


def test_plain_blank_drag_still_pans(view):
    view.verticalScrollBar().setValue(0)
    start = QPoint(400, 560)
    end = QPoint(400, 300)
    _drag(view, start, end)
    assert view.verticalScrollBar().value() > 0
    assert view._selection == []


# ---------- 批量移动 / 微调 / 删除 ----------

def _select_text_and_image(view):
    _click(view, _object_center(view, 0, "text", "t1"))
    _click(view, _object_center(view, 0, "image", "i1"), Qt.ControlModifier)
    assert len(view._selection) == 2


def test_multi_drag_moves_all_with_one_undo(view, qapp):
    _select_text_and_image(view)
    text_before = view.document.pages[0].texts[0]
    image_before = view.document.pages[0].images[0]
    base_depth = view.history.depth

    start = _view_pos(view, _object_center(view, 0, "text", "t1"))
    _drag(view, start, QPoint(start.x() + 40, start.y() + 30))
    qapp.processEvents()

    text_after = view.document.pages[0].texts[0]
    image_after = view.document.pages[0].images[0]
    assert text_after.x > text_before.x and text_after.y > text_before.y
    assert image_after.x > image_before.x and image_after.y > image_before.y
    assert view.history.depth == base_depth + 1
    assert isinstance(view.history._undo[-1], ReplaceMany)
    assert view.history._undo[-1].page_index is None

    view.history.pop_undo()
    assert (view.document.pages[0].texts[0].x,
            view.document.pages[0].texts[0].y) == (text_before.x, text_before.y)
    assert (view.document.pages[0].images[0].x,
            view.document.pages[0].images[0].y) == (image_before.x, image_before.y)
    view.history.pop_redo()
    assert (view.document.pages[0].texts[0].x,
            view.document.pages[0].texts[0].y) == (text_after.x, text_after.y)
    assert (view.document.pages[0].images[0].x,
            view.document.pages[0].images[0].y) == (image_after.x, image_after.y)


def test_multi_nudge_one_command_per_key(view, qapp):
    _select_text_and_image(view)
    view.setFocus()
    base_depth = view.history.depth
    x0 = view.document.pages[0].texts[0].x
    ix0 = view.document.pages[0].images[0].x

    QTest.keyClick(view, Qt.Key_Right)
    qapp.processEvents()
    assert view.document.pages[0].texts[0].x == pytest.approx(x0 + 1.0)
    assert view.document.pages[0].images[0].x == pytest.approx(ix0 + 1.0)
    assert view.history.depth == base_depth + 1

    QTest.keyClick(view, Qt.Key_Right, Qt.ShiftModifier)
    qapp.processEvents()
    assert view.document.pages[0].texts[0].x == pytest.approx(x0 + 11.0)
    assert view.document.pages[0].images[0].x == pytest.approx(ix0 + 11.0)
    assert view.history.depth == base_depth + 2

    view.history.pop_undo()
    assert view.document.pages[0].texts[0].x == pytest.approx(x0 + 1.0)
    assert view.document.pages[0].images[0].x == pytest.approx(ix0 + 1.0)
    view.history.pop_redo()
    assert view.document.pages[0].texts[0].x == pytest.approx(x0 + 11.0)


def test_multi_delete_one_undo_and_redo(view, qapp):
    _select_text_and_image(view)
    base_depth = view.history.depth
    view.setFocus()
    QTest.keyClick(view, Qt.Key_Delete)
    qapp.processEvents()

    assert [b.id for b in view.document.pages[0].texts] == ["t2", "t3"]
    assert view.document.pages[0].images == []
    assert view.history.depth == base_depth + 1
    assert isinstance(view.history._undo[-1], ReplaceMany)

    view.history.pop_undo()
    assert [b.id for b in view.document.pages[0].texts] == ["t1", "t2", "t3"]
    assert [i.id for i in view.document.pages[0].images] == ["i1"]

    view.history.pop_redo()
    assert [b.id for b in view.document.pages[0].texts] == ["t2", "t3"]
    assert view.document.pages[0].images == []


# ---------- 单选回归 ----------

def test_single_selection_handles_and_resize(view, qapp):
    _click(view, _object_center(view, 0, "text", "t1"))
    assert _visible_handles(view) == ["e", "w"]
    text = view.document.pages[0].texts[0]
    item = view._object_item(0, "text", text.id)
    right_center = item.sceneBoundingRect().topRight()
    right_center.setY(item.sceneBoundingRect().center().y())
    start = _view_pos(view, right_center)
    base_depth = view.history.depth
    _drag(view, start, QPoint(start.x() + 40, start.y()))
    qapp.processEvents()
    assert view.document.pages[0].texts[0].w > text.w
    assert view.history.depth == base_depth + 1

    _click(view, _object_center(view, 0, "image", "i1"))
    assert _visible_handles(view) == ["nw", "n", "ne", "e", "se", "s", "sw", "w"]


def test_no_object_actions_do_not_crash(view, qapp):
    view._clear_object_selection()
    view.setFocus()
    for key in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Left, Qt.Key_Right,
                Qt.Key_Up, Qt.Key_Down, Qt.Key_Escape):
        QTest.keyClick(view, key)
        qapp.processEvents()
    _send_context_menu(view, QPointF(500.0, 400.0))
    qapp.processEvents()
    assert view._selection == []
    assert len(view.document.pages[0].texts) == 3
    assert len(view.document.pages[0].images) == 1


# ---------- 右键 / 复制 ----------

def test_multi_right_click_offers_delete_selected(view, monkeypatch):
    _select_text_and_image(view)
    seen: list[str] = []

    class _FakeSignal:
        def connect(self, *_args, **_kwargs):
            pass

    class _FakeAction:
        def __init__(self, text):
            self.text = text
            self.triggered = _FakeSignal()

        def setCheckable(self, *_args):
            pass

        def setChecked(self, *_args):
            pass

        def menu(self):
            return None

    class _FakeMenu:
        """不弹真实模态菜单：记录菜单项并立即返回（QMenu.exec 会阻塞测试）。"""

        def __init__(self, *_args, **_kwargs):
            self._actions = []

        def addAction(self, text):
            action = _FakeAction(text)
            self._actions.append((text, action))
            return action

        def addMenu(self, text):
            action = _FakeAction(text)
            self._actions.append((text, action))
            return _FakeMenu()

        def addSeparator(self):
            return None

        def actions(self):
            return [action for _text, action in self._actions]

        def exec(self, *_args, **_kwargs):
            seen.extend(text for text, _action in self._actions)
            return None

    monkeypatch.setattr("sketchpad.ui.pdfview.QMenu", _FakeMenu)
    _send_context_menu(view, _object_center(view, 0, "text", "t1"))
    assert seen == ["粗体", "斜体", "下划线", "字号", "删除选中项"]


def test_multi_copy_hint_and_clipboard_untouched(view, qapp):
    _select_text_and_image(view)
    clipboard = QGuiApplication.clipboard()
    clipboard.setText("哨兵")
    hints: list[str] = []
    view.objectCopied.connect(hints.append)

    assert view.copy_selection() == 1           # 已处理，宿主不要回落去复制别的东西
    assert hints == ["多选暂不支持复制"]
    assert clipboard.text() == "哨兵"
    assert clipboard.image().isNull() is True
