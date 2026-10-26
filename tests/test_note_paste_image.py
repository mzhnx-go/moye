"""PDF 笔记内 Ctrl+V 粘图：写入 <pdf>.sketch-assets 并挂到 note.images。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.core import noteassets
from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.ui.note_canvas import NoteView

_ALIVE: list[NoteView] = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _cleanup(qapp):
    yield
    while _ALIVE:
        view = _ALIVE.pop()
        try:
            view.close()
            view.deleteLater()
        except RuntimeError:
            pass
    qapp.processEvents()


def make_view(tmp_path) -> tuple[NoteView, Path, Note]:
    pdf_path = tmp_path / "note.pdf"
    pdf_path.write_bytes(b"")             # assets 目录只由路径推导，不要求真 PDF
    note = Note("n-1", 40.0, 50.0)
    view = NoteView(note, Settings(), pdf_path=pdf_path)
    view.resize(640, 480)
    view.show()
    QApplication.processEvents()
    _ALIVE.append(view)
    return view, pdf_path, note


def _image(w=100, h=60) -> QImage:
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(0xFF3366CC)
    return image


def test_笔记内CtrlV粘贴剪贴板图片(tmp_path):
    view, pdf_path, note = make_view(tmp_path)
    QGuiApplication.clipboard().setImage(_image())
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()

    assert len(note.images) == 1
    asset = note.images[0].asset
    path = noteassets.asset_path(pdf_path, asset)
    assert path.exists()
    assert path.suffix == ".png"
    assert path.stat().st_size > 0


def test_笔记内CtrlV粘贴本地图片文件(tmp_path):
    view, pdf_path, note = make_view(tmp_path)
    source = tmp_path / "shot.png"
    _image(80, 40).save(str(source))
    from PySide6.QtCore import QMimeData, QUrl
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    QGuiApplication.clipboard().setMimeData(mime)
    assert view.paste_image_from_clipboard() is not None
    assert len(note.images) == 1
    assert noteassets.asset_path(pdf_path, note.images[0].asset).exists()


def test_没有PDF时笔记不粘图(tmp_path, qapp):
    note = Note("n-2", 10.0, 20.0)
    view = NoteView(note, Settings(), pdf_path=None)
    _ALIVE.append(view)
    QGuiApplication.clipboard().setImage(_image(30, 20))
    assert view.paste_image_from_clipboard() is None
    assert note.images == []


def test_粘贴后模型变更会发出changed(tmp_path):
    view, _pdf, note = make_view(tmp_path)
    QGuiApplication.clipboard().setImage(_image(50, 30))
    from PySide6.QtTest import QSignalSpy
    spy = QSignalSpy(view.changed)
    assert view.paste_image_from_clipboard() is not None
    assert spy.count() >= 1
    assert len(note.images) == 1
