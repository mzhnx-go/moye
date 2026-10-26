"""Standard 阅读器接线：TXT/Markdown 打开、查找、大纲、缩放、与 PDF 共存。

注册表是进程级的，模块内每个测试用 install/clear 包住，避免影响其它测试。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.core import pdfstore
from sketchpad.reader import install as install_readers
from sketchpad.workspace.viewers import clear_viewers

_ALIVE: list = []

MD_TEXT = """# 第一章

正文 **加粗** 与 `code`。

## 1.1 小节

- 列表一
- 列表二

```python
print("hi")
```
"""


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def readers():
    install_readers()
    yield
    clear_viewers()


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


@pytest.fixture
def md(tmp_path):
    path = tmp_path / "笔记.md"
    path.write_text(MD_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def txt(tmp_path):
    path = tmp_path / "讲义.txt"
    path.write_text("第一行\n第二行\n行测 数量关系\n数量关系\n", encoding="utf-8")
    return path


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text((40, 60), "PDF page")
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


def test_markdown_opens_in_document_mode(win, md):
    assert win._enter_document(md) is True
    assert win.mode == "document"
    assert win.pdf_view.kind == "text"
    assert win.pdf_view.pdf_path == md
    assert "第一章" in win.pdf_view.text()
    assert win.tabs.current_entry.title == md.name
    assert win.menubar.save_action.isEnabled() is False
    assert win.menubar.thumbs_action.isEnabled() is False
    assert win.menubar.find_action.isEnabled() is True
    assert win.toolbox.pen_button.isHidden()
    assert win.toolbox.clear_button.isHidden()
    assert win.toolbox.select_button.isHidden() is False


def test_markdown_outline_feeds_toc_panel(win, md):
    win._enter_document(md)
    tree = win.toc_panel.tree
    titles = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert titles[0] == "第一章"
    child = tree.topLevelItem(0).child(0)
    assert child.text(0) == "1.1 小节"


def test_text_find_is_synchronous_and_highlights(win, txt):
    win._enter_document(txt)
    win._queue_find("数量关系")
    assert len(win._find_hits) == 2
    assert all(page == 0 for page, _ in win._find_hits)
    assert win.find_bar.count_label.text().startswith("1/2")
    editor = win.pdf_view._editor
    assert len(editor.extraSelections()) >= 2
    win._find_step(1)
    assert win.find_bar.count_label.text().startswith("2/2")
    win._find_close()
    assert editor.extraSelections() == []


def test_zoom_and_status_for_text(win, md):
    win._enter_document(md)
    before = win.pdf_view.zoom
    win._zoom_by(1.25)
    assert win.pdf_view.zoom > before
    assert "缩放" in win.status_text()
    assert "字" in win.status_text()
    win._set_pdf_zoom(1.0)
    assert win.pdf_view.zoom == pytest.approx(1.0)


def test_markdown_creates_no_sidecar(win, md):
    win._enter_document(md)
    win._close_current_entry()
    assert not pdfstore.sidecar_path(md).exists()
    assert list(md.parent.glob("*.sketch.json")) == []


def test_text_and_pdf_tabs_can_split(win, md, pdf):
    win._enter_document(md)
    md_entry = win.tabs.current_entry
    win._enter_document(pdf)
    pdf_entry = win.tabs.current_entry
    win._merge_document_entries(md_entry, pdf_entry)
    entry = win.tabs.current_entry
    assert entry.is_split
    assert entry.title == f"{md.name} | {pdf.name}"
    assert len(entry.page.sessions) == 2


def test_drop_accepts_registered_text_suffix(win, md):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(md))])
    assert win._pdf_paths_from_mime(mime) == [md]


def test_unsupported_suffix_is_rejected(win, tmp_path):
    docx = tmp_path / "合同.docx"
    docx.write_bytes(b"PK\x03\x04")
    assert win._enter_document(docx) is False
    assert win.mode == "draft"
    assert "不支持" in win.message_label.text()
