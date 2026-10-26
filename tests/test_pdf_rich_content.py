"""PDF 页面级文字/图片：模型、sidecar v4、视图编辑、导出与主窗接线。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.app import AppController, MainWindow
from sketchpad.core import noteassets, pdfstore
from sketchpad.core.notes import NoteImage, TextBlock
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.core.settings import Settings
from sketchpad.pdf.document import export_with_notes
from sketchpad.ui.pdfview import PdfView

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def pdf(tmp_path):
    return _make_pdf(tmp_path / "讲义.pdf")


@pytest.fixture
def view(qapp, pdf):
    v = PdfView(Settings())
    v.resize(820, 620)
    v.show()
    qapp.processEvents()
    v.open(pdf)
    qapp.processEvents()
    _ALIVE.append(v)
    yield v
    v.close_document()


def _red_image() -> QImage:
    image = QImage(80, 40, QImage.Format_RGB32)
    image.fill(QColor("#e8453c"))
    return image


# ---------- 模型 / sidecar ----------

def test_page_rich_items_roundtrip_v4(tmp_path):
    doc = PdfDocument([Page(
        595.0, 842.0, texts=[TextBlock("t1", 10, 20, 200, "页面文字", 16, "#e8453c")],
        images=[NoteImage("i1", 30, 40, 120, 60, "abc.png")])])
    text = pdfstore.to_json(doc)
    assert '"version":4' in text
    back = pdfstore.from_json(text)
    page = back.pages[0]
    assert page.texts == doc.pages[0].texts
    assert page.images == doc.pages[0].images


def test_old_sidecar_has_empty_page_rich_lists():
    doc = pdfstore.from_json('{"version":3,"pages":[{"w":100,"h":200,"r":0,"strokes":[]}]}')
    assert doc.pages[0].texts == [] and doc.pages[0].images == []


# ---------- 视图 ----------

def test_text_tool_keeps_i_beam_mouse_cursor(view, qapp):
    """T 工具悬停在 PDF 上要有 I 形光标，不能像画笔一样把系统光标藏掉。"""
    view.set_tool("text")
    view.setFocus()
    QTest.mouseMove(view.viewport(), QPoint(320, 220))
    qapp.processEvents()
    assert view.viewport().cursor().shape() == Qt.IBeamCursor


def test_pdf_text_insert_edit_undo_redo(view):
    view.set_tool("text")
    view._begin_text_edit(0, None, QPointF(90.0, 120.0))
    view._text_editing.setPlainText("第一行\n第二行")
    view.commit_text_edit()
    page = view.document.pages[0]
    assert len(page.texts) == 1
    assert page.texts[0].text == "第一行\n第二行"
    assert len(view._layers[0].text_items) == 1

    view.history.pop_undo()
    assert page.texts == []
    assert view._layers[0].text_items == []
    view.history.pop_redo()
    assert len(page.texts) == 1

    view._begin_text_edit(0, page.texts[0], QPointF(90.0, 120.0))
    view._text_editing.setPlainText("改成一句")
    view.commit_text_edit()
    assert page.texts[0].text == "改成一句"
    assert page.texts[0].id


def test_pdf_empty_text_edit_is_discarded(view):
    view.set_tool("text")
    view._begin_text_edit(0, None, QPointF(70.0, 90.0))
    view._text_editing.setPlainText("   ")
    view.commit_text_edit()
    assert view.document.pages[0].texts == []
    assert view._layers[0].text_items == []


def test_pdf_image_insert_writes_asset_and_undoes(view, pdf):
    assert view.insert_image(_red_image(), "red") is True
    page = view.document.pages[0]
    assert len(page.images) == 1
    image = page.images[0]
    assert noteassets.asset_path(pdf, image.asset).is_file()
    assert len(view._layers[0].image_items) == 1

    view.history.pop_undo()
    assert page.images == []
    assert view._layers[0].image_items == []
    view.history.pop_redo()
    assert len(page.images) == 1


# ---------- 导出 ----------

def test_export_includes_page_text_and_image(view, pdf, tmp_path):
    view.set_tool("text")
    view._begin_text_edit(0, None, QPointF(60.0, 90.0))
    view._text_editing.setPlainText("导出文字")
    view.commit_text_edit()
    assert view.insert_image(_red_image()) is True

    out = tmp_path / "导出.pdf"
    pages = view.document.pages
    export_with_notes(
        view.source,
        [page.strokes for page in pages],
        [page.notes for page in pages],
        out,
        pages_texts=[page.texts for page in pages],
        pages_images=[page.images for page in pages],
        assets_path=pdf,
    )
    doc = pymupdf.open(out)
    try:
        assert "导出文字" in doc[0].get_text()
        assert len(doc[0].get_images(full=True)) >= 1
    finally:
        doc.close()


# ---------- 主窗接线 ----------

def test_pdf_ctrlv_inserts_clipboard_image(qapp, tmp_path, pdf):
    """真实事件路径：焦点在 PDF 上按 Ctrl+V 插到当前页。"""
    ctrl = AppController(tmp_path / "cfg")
    win = MainWindow(ctrl)
    win.show()
    qapp.processEvents()
    _ALIVE.append(win)
    try:
        assert win._enter_document(pdf) is True
        qapp.processEvents()
        win.pdf_view.setFocus()
        QGuiApplication.clipboard().setImage(_red_image())
        qapp.processEvents()
        QTest.keyClick(win.pdf_view, Qt.Key_V, Qt.ControlModifier)
        qapp.processEvents()
        assert len(win.pdf_view.document.pages[0].images) == 1
    finally:
        win._quitting = True
        win.close()


def test_ctrlv_image_while_typing_commits_text_and_inserts(qapp, tmp_path, pdf):
    """文字输入中 Ctrl+V 图片：先提交文字，再把图片插到页面。"""
    ctrl = AppController(tmp_path / "cfg")
    win = MainWindow(ctrl)
    win.show()
    qapp.processEvents()
    _ALIVE.append(win)
    try:
        assert win._enter_document(pdf) is True
        qapp.processEvents()
        view = win.pdf_view
        view.set_tool("text")
        view._begin_text_edit(0, None, QPointF(70.0, 90.0))
        view._text_editing.setPlainText("先写字")
        QGuiApplication.clipboard().setImage(_red_image())
        qapp.processEvents()
        QTest.keyClick(view, Qt.Key_V, Qt.ControlModifier)
        qapp.processEvents()
        assert [b.text for b in view.document.pages[0].texts] == ["先写字"]
        assert len(view.document.pages[0].images) == 1
    finally:
        win._quitting = True
        win.close()


def test_pdf_image_write_failure_reports_reason(qapp, tmp_path, pdf, monkeypatch):
    ctrl = AppController(tmp_path / "cfg")
    win = MainWindow(ctrl)
    win.show()
    qapp.processEvents()
    _ALIVE.append(win)
    try:
        assert win._enter_document(pdf) is True
        qapp.processEvents()
        QGuiApplication.clipboard().setImage(_red_image())

        def boom(*args, **kwargs):
            raise OSError("磁盘只读")

        monkeypatch.setattr("sketchpad.core.noteassets.store_png", boom)
        assert win._paste_image_to_pdf() is False
        assert "无法写入图片资源" in win.message_label.text()
    finally:
        win._quitting = True
        win.close()


def _view_pos(view, scene_point: QPointF) -> QPoint:
    mapped = view.mapFromScene(scene_point)
    return QPoint(round(mapped.x()), round(mapped.y()))


def test_image_object_select_move_resize_delete(view, qapp):
    assert view.insert_image(_red_image()) is True
    view.set_tool("select")
    qapp.processEvents()
    image = view.document.pages[0].images[0]
    assert view._sel_kind == "image" and view._sel_id == image.id
    assert sum(1 for h in view._sel_handles if h.isVisible()) == 8

    layer = view._layers[0]
    start = QPointF(layer.x + image.x + image.w, layer.y + image.y + image.h)
    view._begin_object_drag(0, "image", image, "se", start)
    view._drag_object(QPointF(_view_pos(view, start).x() + 60,
                              _view_pos(view, start).y() + 40), Qt.NoModifier)
    view._finish_object_drag()
    qapp.processEvents()
    bigger = view.document.pages[0].images[0]
    assert bigger.w > image.w and bigger.h > image.h

    start = QPointF(layer.x + bigger.x + 10, layer.y + bigger.y + 10)
    view._begin_object_drag(0, "image", bigger, "move", start)
    view._drag_object(QPointF(_view_pos(view, start).x() + 30,
                              _view_pos(view, start).y() + 20), Qt.NoModifier)
    view._finish_object_drag()
    qapp.processEvents()
    moved = view.document.pages[0].images[0]
    assert (moved.x, moved.y) != (bigger.x, bigger.y)

    assert view.copy_selection() == 1
    assert QGuiApplication.clipboard().image().isNull() is False
    view.setFocus()
    QTest.keyClick(view, Qt.Key_Delete)
    qapp.processEvents()
    assert view.document.pages[0].images == []
    view.history.pop_undo()
    assert len(view.document.pages[0].images) == 1


def test_text_object_select_width_resize_and_double_click_edit(view, qapp):
    view.set_tool("text")
    view._begin_text_edit(0, None, QPointF(70.0, 90.0))
    view._text_editing.setPlainText("可以点选的一段文字")
    view.commit_text_edit()
    block = view.document.pages[0].texts[0]
    view.set_tool("select")
    qapp.processEvents()
    assert view._sel_kind == "text"
    visible = [mode for handle, mode in zip(view._sel_handles, ("nw", "n", "ne", "e", "se", "s", "sw", "w"))
               if handle.isVisible()]
    assert visible == ["e", "w"]

    item = view._object_item(0, "text", block.id)
    rect = item.sceneBoundingRect()
    start = QPointF(rect.right(), rect.center().y())
    view._begin_object_drag(0, "text", block, "e", start)
    view._drag_object(QPointF(_view_pos(view, start).x() + 40,
                              _view_pos(view, start).y()), Qt.NoModifier)
    view._finish_object_drag()
    qapp.processEvents()
    wider = view.document.pages[0].texts[0]
    assert wider.w > block.w

    view.setFocus()
    x_before = wider.x
    QTest.keyClick(view, Qt.Key_Right)
    assert view.document.pages[0].texts[0].x > x_before

    item = view._object_item(0, "text", wider.id)
    center = item.sceneBoundingRect().center()
    QTest.mouseDClick(view.viewport(), Qt.LeftButton,
                      pos=_view_pos(view, center), delay=10)
    qapp.processEvents()
    assert view.is_text_editing() is True
    view._cancel_text_edit()


def test_pdf_mode_enables_text_and_image_buttons(qapp, tmp_path, pdf):
    ctrl = AppController(tmp_path / "cfg")
    win = MainWindow(ctrl)
    win.show()
    qapp.processEvents()
    _ALIVE.append(win)
    try:
        assert win._enter_document(pdf) is True
        qapp.processEvents()
        assert win.toolbox.text_button.isHidden() is False
        assert win.toolbox.image_button.isHidden() is False

        win.pdf_view.setFocus()
        QTest.keyClick(win.pdf_view, Qt.Key_T)
        assert win.pdf_view.tool == "text"

        QGuiApplication.clipboard().setImage(_red_image())
        qapp.processEvents()
        assert win._paste_image_to_pdf() is True
        assert len(win.pdf_view.document.pages[0].images) == 1
    finally:
        win._quitting = True
        win.close()
