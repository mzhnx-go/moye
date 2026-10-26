"""Phase B 富文本（粗体/斜体/下划线）：模型、sidecar 与 PDF 导出。

覆盖：
- `TextBlock` 末尾新增字段的默认值 / 位置参数兼容 / JSON 往返；
- 无格式 sidecar 输出与其改动前逐字节一致（硬编码基线）+ 旧 v1–v4 可读；
- 坏格式字段（非布尔）不炸，按 False 收下；
- 无格式导出仍可 `get_text()` 搜到；有格式导出后页面多一张图片且可渲染；
- QTextDocument → PNG 的尺寸随字号变化。

样例 PDF 全部在 tmp_path 现造；Qt 走 offscreen（与其它 UI 测试一致）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pymupdf
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from sketchpad.core import pdfstore
from sketchpad.core.notes import Note, TextBlock, text_block_has_format
from sketchpad.core.pdfstore import Page, PdfDocument, from_json, to_json
from sketchpad.core.strokes import Stroke
from sketchpad.pdf import richtext
from sketchpad.pdf.document import PdfSource, export_with_notes

PAGE_W, PAGE_H = 300.0, 400.0


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


# ---------- 模型 ----------


def test_text_block_format_fields_default_false():
    block = TextBlock("t-1", 10.0, 20.0, 200.0, "文字")
    assert (block.bold, block.italic, block.underline) == (False, False, False)
    assert text_block_has_format(block) is False
    # 格式字段在最后：旧的 5/6/7 位置参数调用完全不受影响
    assert TextBlock("t-1", 1.0, 2.0, 3.0, "x", 18.0, "#ffffff").size == 18.0
    assert TextBlock("t-1", 1.0, 2.0, 3.0, "x", 18.0, "#ffffff").color == "#ffffff"


def test_text_block_positional_format_args_keep_order():
    block = TextBlock("t-1", 1.0, 2.0, 3.0, "x", 16.0, "#ffffff", True, True, True)
    assert (block.bold, block.italic, block.underline) == (True, True, True)
    assert text_block_has_format(block) is True
    only_italic = TextBlock("t-2", 1.0, 2.0, 3.0, "x", 16.0, "#ffffff", False, True)
    assert (only_italic.bold, only_italic.italic, only_italic.underline) == (
        False, True, False)


# ---------- sidecar ----------


def _rich_doc(*, bold=False, italic=False, underline=False) -> PdfDocument:
    note_text = TextBlock("t-1", 5.0, 6.0, 120.0, "note text", 16.0, "#e8eaed",
                          bold, italic, underline)
    page_text = TextBlock("t-2", 10.0, 20.0, 200.0, "page text", 16.0, "#e8453c",
                          bold, italic, underline)
    return PdfDocument([Page(
        300.0, 400.0, 0,
        [Stroke([(1.0, 2.0)], "#111111", 3.0)],
        [Note("n-1", 10.0, 20.0,
              strokes=[Stroke([(1.0, 2.0), (3.0, 4.0)], "#000000", 2.0)],
              texts=[note_text])],
        [page_text],
    )])


# 改动前 `to_json` 对该文档的**逐字节**输出（无格式时不新增任何字段）
_UNFORMATTED_BASELINE = (
    '{"version":4,"pages":[{"w":300.0,"h":400.0,"r":0,"strokes":'
    '[{"p":[[1.0,2.0]],"c":"#111111","w":3.0,"t":"pen"}],"notes":'
    '[{"id":"n-1","x":10.0,"y":20.0,"w":560.0,"h":720.0,"paper":"dark",'
    '"strokes":[{"p":[[1.0,2.0],[3.0,4.0]],"c":"#000000","w":2.0,"t":"pen"}],'
    '"texts":[{"id":"t-1","x":5.0,"y":6.0,"w":120.0,"text":"note text",'
    '"size":16.0,"color":"#e8eaed"}]}],"texts":'
    '[{"id":"t-2","x":10.0,"y":20.0,"w":200.0,"text":"page text",'
    '"size":16.0,"color":"#e8453c"}]}]}'
)


def test_unformatted_json_is_byte_identical_to_pre_phase_b_output():
    assert to_json(_rich_doc()) == _UNFORMATTED_BASELINE
    # 显式 False 也不写键（与缺省同一份字节）
    assert to_json(_rich_doc(bold=False, italic=False, underline=False)) == (
        _UNFORMATTED_BASELINE)


def test_minimal_v1_json_unchanged():
    """无笔记/无富内容的最低配输出仍然是改动前那一串。"""
    text = to_json(PdfDocument([Page(100.0, 200.0, 0, [
        Stroke([(1.0, 2.0)], "#000000", 3.0)])]))
    assert text == (
        '{"version":1,"pages":[{"w":100.0,"h":200.0,"r":0,"strokes":'
        '[{"p":[[1.0,2.0]],"c":"#000000","w":3.0,"t":"pen"}]}]}')


def test_format_flags_round_trip_and_only_true_keys_written():
    source = _rich_doc(bold=True, italic=False, underline=True)
    text = to_json(source)
    assert '"b":true' in text and '"u":true' in text
    assert '"i"' not in text          # False 不写键
    assert from_json(text) == source
    loaded = from_json(text).pages[0]
    assert (loaded.texts[0].bold, loaded.texts[0].italic,
            loaded.texts[0].underline) == (True, False, True)
    note_block = loaded.notes[0].texts[0]
    assert (note_block.bold, note_block.underline) == (True, True)


def test_note_only_format_bumps_version_to_four():
    """笔记卡里的文字带格式时按 v4 落盘；不带格式仍是 v3。"""
    def doc_with(block: TextBlock) -> PdfDocument:
        note = Note("n-1", 10.0, 20.0, texts=[block])
        return PdfDocument([Page(100.0, 200.0, 0, [], [note])])

    plain = to_json(doc_with(TextBlock("t-1", 5.0, 6.0, 100.0, "x")))
    assert '"version":3' in plain
    formatted = to_json(doc_with(TextBlock("t-1", 5.0, 6.0, 100.0, "x", 16.0,
                                           "#e8eaed", False, True)))
    assert '"version":4' in formatted
    assert '"i":true' in formatted
    assert from_json(formatted).pages[0].notes[0].texts[0].italic is True


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_legacy_sidecar_without_format_fields_reads_false(version):
    text = json.dumps({
        "version": version,
        "pages": [{
            "w": 300.0, "h": 400.0, "r": 0, "strokes": [],
            "texts": [{"id": "t-1", "x": 10.0, "y": 20.0, "w": 200.0,
                       "text": "旧文件", "size": 16.0, "color": "#e8eaed"}],
        }],
    }, ensure_ascii=False)
    block = from_json(text).pages[0].texts[0]
    assert (block.bold, block.italic, block.underline) == (False, False, False)
    assert block.text == "旧文件"


@pytest.mark.parametrize("bad", [
    pytest.param("yes", id="string"),
    pytest.param(1, id="int"),
    pytest.param(None, id="null"),
    pytest.param(["x"], id="list"),
    pytest.param({}, id="object"),
])
def test_non_bool_format_flag_is_ignored_with_warning(bad):
    """手改 sidecar 的坏格式字段：按 False 收下并提示，不丢整份文件。"""
    text = json.dumps({
        "version": 4,
        "pages": [{
            "w": 300.0, "h": 400.0, "r": 0, "strokes": [],
            "texts": [{"id": "t-1", "x": 10.0, "y": 20.0, "w": 200.0,
                       "text": "x", "b": bad, "i": bad, "u": bad}],
        }],
    })
    with pytest.warns(UserWarning, match="must be a bool"):
        block = from_json(text).pages[0].texts[0]
    assert (block.bold, block.italic, block.underline) == (False, False, False)
    assert block.text == "x"


# ---------- 导出 ----------


def _make_pdf(path) -> "pymupdf.Document":
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    doc.save(str(path))
    doc.close()
    return path


def _export(tmp_path, texts, notes=None):
    src = _make_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    with PdfSource(src) as source:
        export_with_notes(source, [[]], [notes or []], out, pages_texts=[texts])
    return out


def test_unformatted_text_export_stays_searchable(tmp_path):
    out = _export(tmp_path, [TextBlock("t-1", 20.0, 40.0, 240.0,
                                       "SEARCHABLE-TEXT", 16.0, "#202020")])
    doc = pymupdf.open(str(out))
    try:
        assert "SEARCHABLE-TEXT" in doc[0].get_text()
        assert doc[0].get_images(full=True) == []
    finally:
        doc.close()


def _non_white_pixels(image: QImage) -> int:
    """统计页面渲染里明显不是白底的像素（隔行采样，避免慢）。"""
    count = 0
    for y in range(0, image.height(), 4):
        for x in range(0, image.width(), 4):
            color = image.pixelColor(x, y)
            if min(color.red(), color.green(), color.blue()) < 200:
                count += 1
    return count


@pytest.mark.parametrize("flag", ["bold", "italic", "underline"])
def test_formatted_text_export_inserts_visible_image(tmp_path, qapp, flag):
    block = TextBlock("t-1", 20.0, 40.0, 240.0, "FORMAT 中文", 18.0, "#c0392b",
                      **{flag: True})
    out = _export(tmp_path, [block])
    doc = pymupdf.open(str(out))
    try:
        images = doc[0].get_images(full=True)
        assert len(images) == 1                       # 换成了位图，不再是文本层
        assert "FORMAT" not in doc[0].get_text()
        info = doc.extract_image(images[0][0])
        assert info["ext"] == "png" and info["width"] > 0 and info["height"] > 0
        page_image = QImage.fromData(doc[0].get_pixmap(dpi=72).tobytes("png"),
                                     "PNG")
        assert page_image.isNull() is False
        assert _non_white_pixels(page_image) > 0       # 页面上确实能看到东西
    finally:
        doc.close()


def test_formatted_note_text_exports_as_image_on_note_page(tmp_path, qapp):
    note = Note("n-1", 20.0, 30.0,
                texts=[TextBlock("t-1", 40.0, 80.0, 300.0, "NOTE-TEXT", 18.0,
                                 "#ffcc00", True, True, True)])
    out = _export(tmp_path, [], [note])
    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 2
        assert len(doc[1].get_images(full=True)) == 1
        assert "NOTE-TEXT" not in doc[1].get_text()
    finally:
        doc.close()


# ---------- QTextDocument → PNG helper ----------


def test_render_block_png_is_transparent_png(qapp):
    rendered = richtext.render_block_png(
        "中文 Bold", width=200.0, size=16.0, color="#e8453c", bold=True)
    assert rendered is not None
    png, height = rendered
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert height > 0.0
    image = QImage.fromData(png, "PNG")
    assert image.isNull() is False
    assert image.hasAlphaChannel() is True
    assert image.height() == pytest.approx(round(height * richtext.RICHTEXT_DPI / 72),
                                           abs=2)


def test_render_block_png_scales_with_font_size(qapp):
    """同一段文字、同一宽度：字号越大，位图越高（约高一个行高）。"""
    def render(size):
        return richtext.render_block_png("Size", width=240.0, size=size,
                                         color="#000000")

    small = render(12.0)
    large = render(32.0)
    assert small is not None and large is not None
    small_image = QImage.fromData(small[0], "PNG")
    large_image = QImage.fromData(large[0], "PNG")
    assert large[1] > small[1]
    assert large_image.height() > small_image.height()
    assert large_image.width() == small_image.width()


def test_render_block_png_rejects_empty_or_bad_geometry(qapp):
    assert richtext.render_block_png("", width=100.0, size=16.0,
                                     color="#000000") is None
    assert richtext.render_block_png("x", width=0.0, size=16.0,
                                     color="#000000") is None
    assert richtext.render_block_png("x", width=100.0, size=0.0,
                                     color="#000000") is None


def test_block_font_mirrors_model_flags(qapp):
    block = TextBlock("t-1", 1.0, 2.0, 3.0, "x", 20.0, "#ffffff",
                      True, True, True)
    font = richtext.block_font(block)
    assert font.pixelSize() == 20
    assert font.bold() and font.italic() and font.underline()
