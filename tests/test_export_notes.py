"""笔记页导出测试：页序、锚点标记、纸面、笔迹与源文件保护。

样例 PDF 全部在 tmp_path 现造，不碰用户 data/；纯 pymupdf + 标准库，无需 Qt 窗口。
"""
import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest

from sketchpad.core.notes import Note
from sketchpad.core.strokes import TEXT_HIGHLIGHT, Stroke
from sketchpad.pdf.document import (NOTE_MARKER_COLOR, NOTE_PAPER_BG,
                                    NOTE_PAPER_LINE, PdfError, PdfSource,
                                    export_with_notes, export_with_strokes)

PAGE_W, PAGE_H = 300.0, 400.0
NOTE_SCALE = PAGE_W / 560.0


def _rgb(color):
    text = color.lstrip("#")
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _make_pdf(path, texts, size=(PAGE_W, PAGE_H), rotation=0):
    """每页插一行不同的文字，便于断言原页顺序与内容未被改动。"""
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page(width=size[0], height=size[1])
        if text:
            page.insert_text((40, 60), text, fontsize=12)
        if rotation:
            page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _drawings(path, index):
    doc = pymupdf.open(str(path))
    try:
        return doc[index].get_drawings(), doc[index].get_text()
    finally:
        doc.close()


def _has_fill(drawings, color):
    return any("f" in d["type"] and d.get("fill") == pytest.approx(_rgb(color))
               for d in drawings)


def _has_line(drawings, color):
    return any("s" in d["type"] and d.get("color") == pytest.approx(_rgb(color))
               for d in drawings)


def test_note_page_follows_anchor_and_source_order_is_kept(tmp_path):
    src = _make_pdf(tmp_path / "in.pdf", ["PAGE-ONE", "PAGE-TWO"])
    note = Note("n-1", 40.0, 50.0, 560.0, 720.0, "light",
                [Stroke([(10.0, 10.0), (100.0, 100.0)], "#2c2c2e", 2.0)])
    out = tmp_path / "out.pdf"

    with PdfSource(src) as source:
        assert export_with_notes(source, [[], []], [[], [note]], out) == out

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 3
        # 原页顺序与文字不变；第 3 页（索引 2）是第 2 页的笔记页
        assert "PAGE-ONE" in doc[0].get_text()
        assert "PAGE-TWO" in doc[1].get_text()
        note_page = doc[2]
        assert note_page.rect.width == pytest.approx(PAGE_W)
        assert note_page.rect.height == pytest.approx(PAGE_W * 720.0 / 560.0)
        assert "PAGE-ONE" not in note_page.get_text()
        assert "PAGE-TWO" not in note_page.get_text()
        pix = note_page.get_pixmap()
        assert pix.width > 0 and pix.height > 0 and len(pix.samples) > 0
    finally:
        doc.close()


def test_multiple_notes_inserted_in_order_after_their_page(tmp_path):
    src = _make_pdf(tmp_path / "two.pdf", ["FIRST", "SECOND"])
    notes = [Note("n-1", 30.0, 40.0), Note("n-2", 80.0, 90.0)]
    out = tmp_path / "two-out.pdf"

    export_with_notes(PdfSource(src), [[], []], [[], notes], out)

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 4
        assert "FIRST" in doc[0].get_text()
        assert "SECOND" in doc[1].get_text()
        # 同一锚点页的两张笔记按顺序插在其后；标记序号从 1 起
        assert "1" in doc[1].get_text()
        assert "2" in doc[1].get_text()
        for index in (2, 3):
            assert doc[index].rect.width == pytest.approx(PAGE_W)
            assert doc[index].rect.height == pytest.approx(PAGE_W * 720.0 / 560.0)
            assert "FIRST" not in doc[index].get_text()
            assert "SECOND" not in doc[index].get_text()
    finally:
        doc.close()


def test_pdf_strokes_and_note_strokes_are_drawn(tmp_path):
    src = _make_pdf(tmp_path / "ink.pdf", ["PAGE-ONE", "PAGE-TWO"])
    pen = Stroke([(20.0, 30.0), (120.0, 80.0), (200.0, 40.0)], "#0000ff", 4.0)
    highlighter = Stroke([(10.0, 120.0), (150.0, 120.0)], "#ffcc00", 16.0,
                         "highlighter")
    selected = Stroke([(10.0, 150.0), (90.0, 170.0),
                       (100.0, 150.0), (180.0, 170.0)], "#00ff00", 1.0,
                      TEXT_HIGHLIGHT)
    note_pen = Stroke([(10.0, 10.0), (100.0, 100.0)], "#2c2c2e", 2.0)
    note_selected = Stroke([(20.0, 200.0), (120.0, 230.0)], "#00ff00", 1.0,
                           TEXT_HIGHLIGHT)
    note = Note("n-1", 40.0, 50.0, strokes=[note_pen, note_selected])
    out = tmp_path / "ink-out.pdf"

    export_with_notes(PdfSource(src),
                      [[pen, highlighter, selected], []],
                      [[], [note]], out)

    doc = pymupdf.open(str(out))
    try:
        # 源页：钢笔折线 + 荧光折线 + 选字矩形的两个 fill 矩形，共 4 个绘制对象
        page0 = doc[0].get_drawings()
        assert len(page0) == 4
        fills = [d for d in page0 if "f" in d["type"]]
        assert len(fills) == 2
        for fill in fills:
            assert fill["fill"] == pytest.approx(_rgb("#00ff00"))
        strokes = [d for d in page0 if "s" in d["type"]]
        assert len(strokes) == 2
        assert any(d["color"] == pytest.approx((0.0, 0.0, 1.0))
                   and d["width"] == pytest.approx(4.0) for d in strokes)
        assert any(d["color"] == pytest.approx(_rgb("#ffcc00"))
                   and d.get("stroke_opacity") == pytest.approx(0.35)
                   for d in strokes)

        # 锚点页只有一枚蓝色标记，没有源笔迹
        page1 = doc[1].get_drawings()
        assert len(page1) == 1
        assert page1[0]["fill"] == pytest.approx(_rgb(NOTE_MARKER_COLOR))

        # 笔记页：纸面底色 + 横线 + 页边线 + 两条笔记笔迹
        note_drawings = doc[2].get_drawings()
        assert _has_fill(note_drawings, NOTE_PAPER_BG["dark"])
        assert _has_line(note_drawings, NOTE_PAPER_LINE["dark"])
        assert _has_fill(note_drawings, "#00ff00")
        pen_notes = [d for d in note_drawings
                     if "s" in d["type"]
                     and d.get("color") == pytest.approx(_rgb("#2c2c2e"))]
        assert len(pen_notes) == 1
        # 点与线宽按 scale 缩放：90pt 的设计线段变成 90*scale
        rect = pen_notes[0]["rect"]
        assert rect.width == pytest.approx(90.0 * NOTE_SCALE, abs=1e-3)
        assert rect.height == pytest.approx(90.0 * NOTE_SCALE, abs=1e-3)
        assert pen_notes[0]["width"] == pytest.approx(2.0 * NOTE_SCALE, abs=1e-3)
        scaled_fill = [d for d in note_drawings
                       if "f" in d["type"]
                       and d.get("fill") == pytest.approx(_rgb("#00ff00"))]
        assert scaled_fill
        assert scaled_fill[0]["rect"].width == pytest.approx(
            100.0 * NOTE_SCALE, abs=1e-3)
    finally:
        doc.close()


def test_light_paper_page_has_background_and_ruled_lines(tmp_path):
    src = _make_pdf(tmp_path / "paper.pdf", ["ONLY"])
    note = Note("n-1", 50.0, 60.0, paper="light")
    out = tmp_path / "paper-out.pdf"

    export_with_notes(PdfSource(src), [[]], [[note]], out)

    doc = pymupdf.open(str(out))
    try:
        page = doc[1]
        drawings = page.get_drawings()
        assert drawings
        background = [d for d in drawings
                      if "f" in d["type"]
                      and d.get("fill") == pytest.approx(_rgb("#f6f1e6"))]
        assert background
        assert background[0]["rect"].width == pytest.approx(PAGE_W)
        assert background[0]["rect"].height == pytest.approx(PAGE_W * 720.0 / 560.0)
        lines = [d for d in drawings
                 if "s" in d["type"]
                 and d.get("color") == pytest.approx(_rgb("#d8cdb8"))]
        assert len(lines) >= 2      # 至少一条横线 + 页边线

        # 像素兜底：左上角是浅纸底色，横线所在行比空白行暗
        pix = page.get_pixmap()
        r, g, b = pix.pixel(5, 5)[:3]
        assert abs(r - 246) <= 2 and abs(g - 241) <= 2 and abs(b - 230) <= 2
        line_band = min(sum(pix.pixel(150, y)[:3]) for y in range(12, 19))
        plain_band = sum(pix.pixel(150, 22)[:3])
        assert line_band < plain_band
    finally:
        doc.close()


def test_anchor_page_marker_added_only_when_notes_exist(tmp_path):
    src = _make_pdf(tmp_path / "mark.pdf", ["ONLY"])
    plain = tmp_path / "plain.pdf"
    marked = tmp_path / "marked.pdf"
    note = Note("n-1", 40.0, 50.0)

    export_with_notes(PdfSource(src), [[]], [[]], plain)
    export_with_notes(PdfSource(src), [[]], [[note]], marked)

    plain_drawings, plain_text = _drawings(plain, 0)
    marked_drawings, marked_text = _drawings(marked, 0)
    assert len(marked_drawings) == len(plain_drawings) + 1
    assert _has_fill(marked_drawings, NOTE_MARKER_COLOR)
    assert "1" in marked_text
    assert "1" not in plain_text


def test_marker_lands_at_anchor_on_rotated_page(tmp_path):
    src = _make_pdf(tmp_path / "rot.pdf", ["ROTATED"], size=(300.0, 200.0),
                    rotation=90)
    note = Note("n-1", 50.0, 60.0)
    out = tmp_path / "rot-out.pdf"

    export_with_notes(PdfSource(src), [[]], [[note]], out)

    doc = pymupdf.open(str(out))
    try:
        page = doc[0]
        assert page.rotation == 90
        # 位图是显示方向：蓝色标记的重心应落在显示坐标锚点附近
        pix = page.get_pixmap()
        hits = []
        for y in range(pix.height):
            for x in range(pix.width):
                r, g, b = pix.pixel(x, y)[:3]
                if b > 120 and b - r > 60 and b - g > 40:
                    hits.append((x, y))
        assert hits
        cx = sum(x for x, _ in hits) / len(hits)
        cy = sum(y for _, y in hits) / len(hits)
        assert cx == pytest.approx(50.0, abs=2.5)
        assert cy == pytest.approx(60.0, abs=2.5)
    finally:
        doc.close()


def test_export_never_overwrites_source_and_source_hash_is_stable(tmp_path):
    src = _make_pdf(tmp_path / "safe.pdf", ["SAFE"])
    before = _sha(src)

    with PdfSource(src) as source:
        with pytest.raises(PdfError):
            export_with_notes(source, [[]], [[]], src)
        # 带 "./" 的等价路径同样要拦住
        with pytest.raises(PdfError):
            export_with_notes(source, [[]], [[]], tmp_path / "." / "safe.pdf")
        assert _sha(src) == before

        out = tmp_path / "nested" / "out.pdf"
        assert export_with_notes(source, [[]], [[Note("n-1", 10.0, 20.0)]], out) == out
    assert out.exists()
    assert _sha(src) == before


def test_page_count_mismatch_raises(tmp_path):
    src = _make_pdf(tmp_path / "count.pdf", ["A", "B"])
    out = tmp_path / "never.pdf"

    with PdfSource(src) as source:
        with pytest.raises(PdfError, match="页数不一致"):
            export_with_notes(source, [[]], [[], []], out)
        with pytest.raises(PdfError, match="页数不一致"):
            export_with_notes(source, [[], []], [[]], out)
    assert not out.exists()


def test_bad_note_size_raises_and_bad_paper_falls_back_to_dark(tmp_path):
    src = _make_pdf(tmp_path / "bad.pdf", ["A"])

    with PdfSource(src) as source:
        with pytest.raises(PdfError, match="尺寸"):
            export_with_notes(source, [[]],
                              [[Note("n-1", 10.0, 20.0, w=0.0)]],
                              tmp_path / "bad-out.pdf")
        assert not (tmp_path / "bad-out.pdf").exists()

        # 非法 paper 不崩，按深色纸导出
        out = tmp_path / "fallback.pdf"
        export_with_notes(source, [[]],
                          [[Note("n-2", 10.0, 20.0, paper="neon")]], out)
    drawings, _text = _drawings(out, 1)
    assert _has_fill(drawings, NOTE_PAPER_BG["dark"])


def test_no_notes_still_exports_same_page_count(tmp_path):
    src = _make_pdf(tmp_path / "plain.pdf", ["ALPHA", "BETA"])
    out = tmp_path / "plain-out.pdf"

    export_with_notes(PdfSource(src), [[], []], [[], []], out)

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 2
        assert "ALPHA" in doc[0].get_text()
        assert "BETA" in doc[1].get_text()
        # 无笔记时没有任何标记/附加页
        assert doc[0].get_text().strip() == "ALPHA"
        assert doc[1].get_text().strip() == "BETA"
        assert doc[0].get_drawings() == []
        assert doc[1].get_drawings() == []
    finally:
        doc.close()

    # 无笔记路径仍由 export_with_strokes 服务，行为保持一致
    legacy_out = tmp_path / "legacy.pdf"
    export_with_strokes(PdfSource(src), [[], []], legacy_out)
    doc = pymupdf.open(str(legacy_out))
    try:
        assert doc.page_count == 2
    finally:
        doc.close()


# ---------- 富内容渲染（追加） ----------

def test_note_page_renders_text_and_attachment_label(tmp_path):
    from sketchpad.core.notes import NoteAttachment, TextBlock

    src = _make_pdf(tmp_path / "rich.pdf", ["RICH"])
    note = Note("n-1", 40.0, 50.0, texts=[
        TextBlock("t-1", 40.0, 80.0, 400.0, "RICH-BODY", 18.0, "#ffffff")])
    note.attachments.append(
        NoteAttachment("att-1", 40.0, 600.0, "x.pdf", "报告.pdf"))

    out = tmp_path / "rich-out.pdf"
    export_with_notes(PdfSource(src), [[]], [[note]], out)

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 2
        assert "RICH-BODY" in doc[1].get_text()
        assert "报告.pdf" in doc[1].get_text()
        assert "RICH-BODY" not in doc[0].get_text()
        assert "RICH" in doc[0].get_text()      # 锚点页原内容不受影响
        assert doc[1].get_image_info() == []
    finally:
        doc.close()


def test_note_page_without_rich_content_has_no_extra_text(tmp_path):
    src = _make_pdf(tmp_path / "bare.pdf", ["BARE"])
    out = tmp_path / "bare-out.pdf"
    export_with_notes(PdfSource(src), [[]], [[Note("n-1", 10.0, 20.0)]], out)

    doc = pymupdf.open(str(out))
    try:
        assert doc[1].get_text().strip() == ""
        assert doc[1].get_drawings()
    finally:
        doc.close()
