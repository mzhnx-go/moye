"""纯文本 / Markdown 阅读视图离屏测试：编码、Markdown 渲染与大纲、搜索高亮、
缩放、阅读位置、无 sidecar、关闭。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from sketchpad.core.settings import Settings
from sketchpad.reader.text_view import TextViewer

_ALIVE: list[TextViewer] = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def viewer(qapp):
    v = TextViewer(Settings())
    _ALIVE.append(v)
    yield v
    v.close()


def _write(path, text, encoding="utf-8"):
    path.write_text(text, encoding=encoding)
    return path


def test_open_utf8_text(viewer, tmp_path):
    content = "第一行：中文内容。\nSecond line, ASCII.\n第三行结束。"
    path = _write(tmp_path / "sample.txt", content)
    viewer.open(path)

    assert viewer.kind == "text"
    assert viewer.document is None
    assert viewer.source is None
    assert viewer.pdf_path == path
    assert viewer.path == path
    assert viewer.encoding == "utf-8"
    assert viewer.text() == content
    assert viewer._editor.toPlainText() == content
    assert viewer.tool == "select"
    assert viewer.width == float(Settings().width)


def test_open_gb18030_text(viewer, tmp_path):
    content = "中文测试：编码检测。\n第二行。"
    path = tmp_path / "gbk.txt"
    path.write_bytes(content.encode("gb18030"))
    viewer.open(path)

    assert viewer.encoding == "gb18030"
    assert viewer.text() == content


def test_markdown_rendering_and_outline(viewer, tmp_path):
    content = (
        "# 标题一\n\n正文段落。\n\n"
        "## 小节二\n\n- 项目甲\n- 项目乙\n\n"
        "```python\nprint('hello')\n```\n\n"
        "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |\n"
    )
    path = _write(tmp_path / "doc.md", content)
    viewer.open(path)

    html = viewer._editor.toHtml()
    assert "标题一" in html
    assert "项目甲" in html
    assert "<pre" in html
    assert "<ul" in html.lower()

    outline = viewer.outline()
    assert [level for level, _, _ in outline] == [1, 2]
    assert [title for _, title, _ in outline] == ["标题一", "小节二"]
    anchors = [anchor for _, _, anchor in outline]
    assert anchors == sorted(anchors)
    assert anchors[1] > anchors[0]

    viewer.goto_page(anchors[1])
    assert viewer._editor.textCursor().position() == anchors[1]


def test_search_and_hit_highlighting(viewer, tmp_path):
    content = "Alpha beta 中文 alpha ALPHA test alpha."
    path = _write(tmp_path / "search.txt", content)
    viewer.open(path)

    expected = []
    start = 0
    while True:
        found = content.lower().find("alpha", start)
        if found < 0:
            break
        expected.append(found)
        start = found + 1
    assert len(expected) >= 3

    cursor = QTextCursor(viewer._editor.document())
    cursor.setPosition(3)
    viewer._editor.setTextCursor(cursor)
    hits = viewer.search("alpha")
    assert [offset for _, offset in hits] == expected
    assert viewer._editor.textCursor().position() == 3
    assert viewer.search("") == []

    viewer.show_hits(0, [offset for _, offset in hits], current=1)
    selections = viewer._editor.extraSelections()
    assert len(selections) == len(expected)
    current_color = selections[1].format.background().color()
    assert current_color.alpha() > 0

    viewer.clear_hits()
    assert viewer._editor.extraSelections() == []


def test_zoom_scales_font_and_clamps(viewer, tmp_path):
    path = _write(tmp_path / "zoom.txt", "缩放测试\n" * 40)
    viewer.open(path)
    base = viewer._editor.font().pointSizeF()
    assert base > 0

    seen: list[float] = []
    viewer.zoomChanged.connect(seen.append)
    viewer.set_zoom(1.5)
    assert viewer.zoom == pytest.approx(1.5)
    assert viewer._editor.font().pointSizeF() == pytest.approx(base * 1.5)
    assert seen == [pytest.approx(1.5)]

    viewer.set_zoom(10.0)
    assert viewer.zoom == pytest.approx(4.0)
    assert viewer._editor.font().pointSizeF() == pytest.approx(base * 4.0)
    viewer.set_zoom(4.0)
    assert len(seen) == 2

    viewer.fit_width()
    assert viewer.zoom == pytest.approx(1.0)


def test_restore_scroll_position(viewer, tmp_path):
    content = "\n".join(f"第 {index} 行：滚动位置的测试内容。" for index in range(500))
    path = _write(tmp_path / "scroll.txt", content)
    viewer.resize(600, 400)
    viewer.show()
    viewer.open(path)
    QApplication.processEvents()

    bar = viewer.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(int(0.6 * bar.maximum()))
    position = viewer.current_position()
    assert position["page"] == 0
    assert position["offset"] > 0.5
    assert position["zoom"] == pytest.approx(1.0)
    assert position["auto_fit"] is False

    viewer.scroll_to_top()
    assert viewer.current_position()["offset"] == pytest.approx(0.0)
    viewer.restore_position(0, 0.6, 1.0, False)
    assert viewer.current_position()["offset"] == pytest.approx(0.6, abs=0.05)


def test_markdown_creates_no_sidecar(viewer, tmp_path):
    path = _write(tmp_path / "notes.md", "# 无 sidecar\n\n正文。\n")
    viewer.open(path)

    assert not list(tmp_path.glob("*.sketch.json"))
    assert not list(tmp_path.glob("notes.sketch.json"))
    assert {item.name for item in tmp_path.iterdir()} == {"notes.md"}


def test_close_document_resets(viewer, tmp_path):
    path = _write(tmp_path / "close.txt", "关闭前的文本。")
    viewer.open(path)
    pages: list[int] = []
    viewer.pageChanged.connect(pages.append)
    viewer.close_document()

    assert viewer.pdf_path is None
    assert viewer.path is None
    assert viewer.text() == ""
    assert pages == [0]
