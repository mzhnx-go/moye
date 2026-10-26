"""草稿插入截图：剪贴板/文件、图片在下笔迹在上、清屏保留图片。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    w = main.MainWindow(ctrl)
    w._confirm_unsaved = lambda action: QMessageBox.Discard
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    w._quitting = True
    w.close()


def _image(w=400, h=300, color=0xFFB03030):
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(color)
    return image


def test_insert_image_and_draw_over_it(win):
    assert win.mode == "draft"
    assert win.toolbox.image_button.isHidden() is False
    assert win._insert_image_to_draft(_image(), "题干.png") is True
    assert win.view.image_count == 1

    start = win.view.viewport().rect().center()
    end = QPoint(start.x() + 40, start.y() + 30)
    QTest.mousePress(win.view.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(win.view.viewport(), end, delay=10)
    QTest.mouseRelease(win.view.viewport(), Qt.LeftButton, pos=end)
    assert len(win.controller.document.strokes) == 1
    assert win.view.image_count == 1


def test_clear_keeps_background_image(win):
    win._insert_image_to_draft(_image(), "题目.png")
    from sketchpad.core.strokes import Stroke
    win.controller.document.strokes.append(
        Stroke(points=[(10.0, 10.0), (20.0, 20.0)], color="#fff", width=2.0))
    win.view.scene.rebind()
    win.view.clear_all()
    assert win.controller.document.strokes == []
    assert win.view.image_count == 1
    win.view.clear_images()
    assert win.view.image_count == 0


def test_paste_from_clipboard(win):
    QGuiApplication.clipboard().setImage(_image(120, 80))
    assert win._paste_image_to_draft() is True
    assert win.view.image_count == 1


def test_没有子焦点时CtrlV仍能粘图(win, monkeypatch):
    """截图遮罩关闭回主窗时焦点可能在窗口本身，Ctrl+V 不能失效。"""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    QGuiApplication.clipboard().setImage(_image(90, 60))
    monkeypatch.setattr(win, "_focused_in_window", lambda: None)
    monkeypatch.setattr(win, "isActiveWindow", lambda: True)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V,
                      Qt.KeyboardModifier.ControlModifier)
    assert win.eventFilter(win, event) is True
    assert win.view.image_count == 1


def test_image_drop_paths_in_draft_and_pdf(win, tmp_path):
    image_path = tmp_path / "shot.png"
    _image(80, 60).save(str(image_path))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(image_path))])
    assert win._image_paths_from_mime(mime) == [image_path]

    pdf = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "x")
    doc.save(str(pdf))
    doc.close()
    assert win._enter_document(pdf) is True
    assert win.toolbox.image_button.isHidden() is False   # PDF 页也支持插图
    assert win._image_paths_from_mime(mime) == [image_path]


def test_image_button_available_in_pdf_and_draft(win, tmp_path):
    pdf = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()
    win._enter_document(pdf)
    assert win.toolbox.image_button.isHidden() is False
    win._enter_draft()
    assert win.toolbox.image_button.isHidden() is False
