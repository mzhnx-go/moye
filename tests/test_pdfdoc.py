"""PDF 引擎测试：页面尺寸、位图渲染、矢量导出。

样例 PDF 全部在 tmp_path 现造；坏文件、加密文件也造在这里，不碰用户 data/。
"""
import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtGui import QColor, QImage

from sketchpad.pdf.document import PdfError, PdfSource, export_with_strokes
from sketchpad.core.strokes import Stroke

# 三页样例：竖版 A4、横版小页、旋转 90° 的竖版 A4
MIXED = ((612, 792, 0), (400, 300, 0), (612, 792, 90))


def _make_pdf(path, pages=MIXED):
    """按 (宽, 高, 旋转) 造一份样例 PDF。"""
    doc = pymupdf.open()
    for width, height, rotation in pages:
        page = doc.new_page(width=width, height=height)
        if rotation:
            page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_page_count_of_three_page_pdf(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    with PdfSource(pdf) as src:
        assert src.page_count == 3


def test_page_size_equals_rotated_page_rect(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    doc = pymupdf.open(str(pdf))
    expected = [(page.rect.width, page.rect.height) for page in doc]
    doc.close()
    with PdfSource(pdf) as src:
        assert [src.page_size(i) for i in range(3)] == expected
    # 旋转页宽高互换：612×792 转 90° 后是 792×612
    assert expected[2] == (792.0, 612.0)


def test_render_format_and_exact_pixel_size(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    with PdfSource(pdf) as src:
        for index in range(3):
            width, height = src.page_size(index)
            for zoom in (2.0, 0.333):
                img = src.render(index, zoom)
                assert img.format() == QImage.Format_RGB32
                assert (img.width(), img.height()) == (
                    round(width * zoom),
                    round(height * zoom),
                )


def test_render_blank_page_is_white(tmp_path):
    pdf = _make_pdf(tmp_path / "blank.pdf", pages=((200, 100, 0),))
    with PdfSource(pdf) as src:
        img = src.render(0, 2.0)
        assert QColor(img.pixel(5, 5)).name() == "#ffffff"
        assert QColor(img.pixel(img.width() - 5, img.height() - 5)).name() == "#ffffff"


def test_render_shows_page_content(tmp_path):
    pdf = tmp_path / "red.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=100)
    page.draw_rect(pymupdf.Rect(0, 0, 100, 100), color=(1.0, 0.0, 0.0), fill=(1.0, 0.0, 0.0))
    doc.save(str(pdf))
    doc.close()
    with PdfSource(pdf) as src:
        img = src.render(0, 1.0)
        assert QColor(img.pixel(10, 10)).name() == "#ff0000"
        assert QColor(img.pixel(150, 10)).name() == "#ffffff"


def test_render_rejects_nonpositive_zoom(tmp_path):
    pdf = _make_pdf(tmp_path / "z.pdf")
    with PdfSource(pdf) as src:
        for zoom in (0, -1.0):
            with pytest.raises(PdfError):
                src.render(0, zoom)


def test_page_size_out_of_range_raises(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    with PdfSource(pdf) as src:
        for index in (3, -1):
            with pytest.raises(PdfError):
                src.page_size(index)


def test_render_out_of_range_raises(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    with PdfSource(pdf) as src:
        for index in (3, -1):
            with pytest.raises(PdfError):
                src.render(index, 1.0)


def test_corrupt_pdf_raises_with_path(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(PdfError) as excinfo:
        PdfSource(bad)
    assert str(bad) in str(excinfo.value)


def test_missing_file_raises_with_path(tmp_path):
    missing = tmp_path / "missing.pdf"
    with pytest.raises(PdfError) as excinfo:
        PdfSource(missing)
    assert str(missing) in str(excinfo.value)


def test_encrypted_pdf_raises_with_path(tmp_path):
    path = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page(width=100, height=100)
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    doc.close()
    with pytest.raises(PdfError) as excinfo:
        PdfSource(path)
    assert str(path) in str(excinfo.value)


def test_export_draws_stroke_and_leaves_source_untouched(tmp_path):
    pdf = _make_pdf(tmp_path / "src.pdf")
    stroke = Stroke([(20.0, 30.0), (120.0, 80.0), (200.0, 40.0)], "#0000ff", 4.0)
    before = _sha(pdf)
    out = tmp_path / "out.pdf"
    with PdfSource(pdf) as src:
        result = export_with_strokes(src, [[], [stroke], []], out)
    assert result == out
    assert out.exists()
    assert _sha(pdf) == before
    doc = pymupdf.open(str(out))
    assert doc.page_count == 3
    drawings = doc[1].get_drawings()
    assert len(drawings) == 1
    assert drawings[0]["color"] == (0.0, 0.0, 1.0)
    assert drawings[0]["width"] == 4.0
    assert len(doc[0].get_drawings()) == 0
    assert len(doc[2].get_drawings()) == 0
    doc.close()


def test_export_single_point_is_filled_circle_and_empty_draws_nothing(tmp_path):
    pdf = _make_pdf(tmp_path / "one.pdf", pages=((200, 200, 0),))
    strokes = [
        Stroke([(50.0, 60.0)], "#00ff00", 6.0),
        Stroke([], "#ff0000", 2.0),
    ]
    out = tmp_path / "circles.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [strokes], out)
    doc = pymupdf.open(str(out))
    drawings = doc[0].get_drawings()
    doc.close()
    assert len(drawings) == 1
    assert "f" in drawings[0]["type"]
    assert drawings[0]["rect"].width == 6.0
    assert drawings[0]["rect"].height == 6.0


def test_export_refuses_to_overwrite_source(tmp_path):
    pdf = _make_pdf(tmp_path / "src.pdf")
    before = _sha(pdf)
    with PdfSource(pdf) as src:
        with pytest.raises(PdfError):
            export_with_strokes(src, [[], [], []], pdf)
        # 带 "./" 的等价路径同样要拦住
        with pytest.raises(PdfError):
            export_with_strokes(src, [[], [], []], tmp_path / "." / "src.pdf")
    assert _sha(pdf) == before


def test_export_rejects_wrong_page_count(tmp_path):
    pdf = _make_pdf(tmp_path / "src.pdf")
    out = tmp_path / "never.pdf"
    with PdfSource(pdf) as src:
        with pytest.raises(PdfError):
            export_with_strokes(src, [[], []], out)
    assert not out.exists()


def test_export_opens_source_fresh_after_close(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    src = PdfSource(pdf)
    src.close()
    out = tmp_path / "after-close.pdf"
    assert export_with_strokes(src, [[], [], []], out) == out
    assert out.exists()


def test_context_manager_closes_and_keeps_path(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    with PdfSource(pdf) as src:
        assert src.path == pdf
        assert src.page_count == 3
    assert src.path == pdf
    src.close()  # 重复 close 不炸
    with pytest.raises(PdfError):
        src.page_count
    with pytest.raises(PdfError):
        src.render(0, 1.0)


# ---- 目录与全文搜索 ----


def _make_toc_pdf(path, rows):
    """4 页空文档 + 给定目录行（页码按 pymupdf 约定从 1 起）。"""
    doc = pymupdf.open()
    for _ in range(4):
        doc.new_page(width=200, height=200)
    doc.set_toc(rows)
    doc.save(str(path))
    doc.close()
    return path


def _make_text_pdf(path, texts, size=(300, 200)):
    """每页在 (50, 60) 处插一行文本，页尺寸固定。"""
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page(width=size[0], height=size[1])
        if text:
            page.insert_text((50, 60), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def test_get_toc_converts_page_numbers_to_zero_based(tmp_path):
    pdf = _make_toc_pdf(tmp_path / "toc.pdf", [[1, "第一章", 1], [2, "小节", 3]])
    with PdfSource(pdf) as src:
        assert src.get_toc() == [(1, "第一章", 0), (2, "小节", 2)]


def test_get_toc_without_toc_returns_empty(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    with PdfSource(pdf) as src:
        assert src.get_toc() == []


def test_get_toc_clamps_invalid_page_numbers(tmp_path):
    # 删掉目录指向的第 4 页后，pymupdf 给出的页码是 -1；要钳到 0
    pdf = _make_toc_pdf(tmp_path / "toc.pdf", [[1, "尾页", 4], [1, "首页", 1]])
    doc = pymupdf.open(str(pdf))
    doc.delete_page(3)
    doc.saveIncr()
    doc.close()
    with PdfSource(pdf) as src:
        assert src.page_count == 3
        assert src.get_toc() == [(1, "尾页", 0), (1, "首页", 0)]


def test_get_toc_after_close_raises(tmp_path):
    pdf = _make_toc_pdf(tmp_path / "toc.pdf", [[1, "第一章", 1]])
    src = PdfSource(pdf)
    src.close()
    with pytest.raises(PdfError):
        src.get_toc()


def test_search_page_finds_all_occurrences(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["", "", "alpha beta alpha"])
    with PdfSource(pdf) as src:
        assert len(src.search_page(2, "alpha")) == 2


def test_search_page_rects_within_page(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["", "", "alpha beta alpha"])
    with PdfSource(pdf) as src:
        width, height = src.page_size(2)
        rects = src.search_page(2, "alpha")
        assert len(rects) == 2
        for x0, y0, x1, y1 in rects:
            assert 0.0 <= x0 < x1 <= width
            assert 0.0 <= y0 < y1 <= height


def test_search_page_is_case_insensitive(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["", "", "alpha beta alpha"])
    with PdfSource(pdf) as src:
        assert src.search_page(2, "ALPHA") == src.search_page(2, "alpha")
        assert len(src.search_page(2, "ALPHA")) == 2


def test_search_page_empty_text_returns_empty(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["", "", "alpha"])
    with PdfSource(pdf) as src:
        assert src.search_page(2, "") == []
        assert src.search_page(2, "   ") == []


def test_search_page_out_of_range_raises(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["alpha"])
    with PdfSource(pdf) as src:
        for index in (1, -1):
            with pytest.raises(PdfError):
                src.search_page(index, "alpha")


def test_search_page_after_close_raises(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["alpha"])
    src = PdfSource(pdf)
    src.close()
    with pytest.raises(PdfError):
        src.search_page(0, "alpha")


def test_search_document_returns_hits_in_page_order(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["hit here", "hit", "hit hit"])
    with PdfSource(pdf) as src:
        hits = src.search_document("hit")
        assert [index for index, _ in hits] == [0, 1, 2, 2]
        # 每页矩形与单页搜索逐项一致
        for index in range(3):
            assert [rect for i, rect in hits if i == index] == src.search_page(index, "hit")


def test_search_document_respects_max_hits(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["hit here", "hit", "hit hit"])
    with PdfSource(pdf) as src:
        hits = src.search_document("hit", max_hits=1)
        assert hits == [(0, src.search_page(0, "hit")[0])]


def test_search_document_empty_text_returns_empty(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["", "", "alpha"])
    with PdfSource(pdf) as src:
        assert src.search_document("") == []
        assert src.search_document("  ") == []


def test_search_document_after_close_raises(tmp_path):
    pdf = _make_text_pdf(tmp_path / "s.pdf", ["alpha"])
    src = PdfSource(pdf)
    src.close()
    with pytest.raises(PdfError):
        src.search_document("alpha")


# ---- 旋转页（v2.4-1）：唯一坐标系 = 显示/页框坐标 ----


def _make_rotated_text_pdf(path, rotation, text="alpha", size=(200, 300),
                           insert_at=(50, 60)):
    """造一页带文字的旋转 PDF。

    返回 (path, 未旋转字框 word, 显示页宽高, 显示字框 display_word)。
    两个字框都由 MuPDF 直接算，不经过被测代码，导出用例据此独立取"屏幕位置"。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_text(insert_at, text, fontsize=12)
    word = page.search_for(text)[0]
    page.set_rotation(rotation)
    display_size = (page.rect.width, page.rect.height)
    display_word = word * page.rotation_matrix
    doc.save(str(path))
    doc.close()
    return path, word, display_size, display_word


def test_rotation_reports_page_rotation_and_validates_index(tmp_path):
    pdf = _make_pdf(tmp_path / "mixed.pdf")
    with PdfSource(pdf) as src:
        assert [src.rotation(i) for i in range(3)] == [0, 0, 90]
        for bad in (3, -1, True):
            with pytest.raises(PdfError):
                src.rotation(bad)
    src = PdfSource(pdf)
    src.close()
    with pytest.raises(PdfError):
        src.rotation(0)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_search_page_returns_display_rects_on_rotated_page(tmp_path, rotation):
    """search_for 给未旋转 rect，`search_page` 必须乘 rotation_matrix 换到显示坐标。"""
    pdf, word, display_size, _ = _make_rotated_text_pdf(
        tmp_path / f"rot{rotation}.pdf", rotation)
    assert display_size == ((300.0, 200.0) if rotation in (90, 270)
                            else (200.0, 300.0))

    # 期望值用 MuPDF 自己算，不抄实现里的矩阵
    doc = pymupdf.open(str(pdf))
    page = doc[0]
    assert page.rotation == rotation
    expected = page.search_for("alpha")[0] * page.rotation_matrix
    doc.close()

    with PdfSource(pdf) as src:
        rects = src.search_page(0, "alpha")
    assert len(rects) == 1
    x0, y0, x1, y1 = rects[0]
    assert (x0, y0, x1, y1) == pytest.approx(
        (expected.x0, expected.y0, expected.x1, expected.y1), abs=1e-6)
    # 落在显示页框内（未换算是 50..79 / 47..64，90°/180°/270° 都会越界或错位）
    assert 0.0 <= x0 < x1 <= display_size[0]
    assert 0.0 <= y0 < y1 <= display_size[1]
    # 与未旋转字框不同：真换算了才可能这样
    assert (x0, y0, x1, y1) != pytest.approx(
        (word.x0, word.y0, word.x1, word.y1), abs=1e-6)


def test_search_document_inherits_display_coords_on_rotated_page(tmp_path):
    pdf = tmp_path / "two.pdf"
    doc = pymupdf.open()
    for rotation in (0, 90):
        page = doc.new_page(width=200, height=300)
        page.insert_text((50, 60), "alpha", fontsize=12)
        page.set_rotation(rotation)
    doc.save(str(pdf))
    doc.close()
    with PdfSource(pdf) as src:
        hits = src.search_document("alpha")
        assert [index for index, _ in hits] == [0, 1]
        assert hits[1][1] == src.search_page(1, "alpha")[0]
        x0, y0, x1, y1 = hits[1][1]
        width, height = src.page_size(1)
        assert 0.0 <= x0 < x1 <= width and 0.0 <= y0 < y1 <= height


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_export_maps_display_stroke_back_over_rotated_text(tmp_path, rotation):
    """显示坐标的笔迹导出时要 derotate：墨迹落在导出页（未旋转坐标）的文字框上。"""
    pdf, word, _, display_word = _make_rotated_text_pdf(
        tmp_path / f"src{rotation}.pdf", rotation)
    # “屏幕上看到的”文字中心（MuPDF 独立算，不经过 search_page，避免两边同时错还互相抵消）
    cx = (display_word.x0 + display_word.x1) / 2.0
    cy = (display_word.y0 + display_word.y1) / 2.0
    # 显示位置必须真的不同于未旋转位置，否则这条用例会自相抵消、抓不到漏换算
    assert (display_word.x0, display_word.y0) != pytest.approx(
        (word.x0, word.y0), abs=1e-6)
    stroke = Stroke([(cx - 5.0, cy), (cx, cy), (cx + 5.0, cy)], "#0000ff", 2.0)
    out = tmp_path / f"ink{rotation}.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [[stroke]], out)

    doc = pymupdf.open(str(out))
    page = doc[0]
    # 导出副本保留原页旋转（否则读者看到的页面会转向）
    assert page.rotation == rotation
    drawings = page.get_drawings()
    doc.close()
    assert len(drawings) == 1
    rect = drawings[0]["rect"]
    # get_drawings 给未旋转坐标；墨迹必须回到源文字框里（容差 1pt）
    assert rect.x0 >= word.x0 - 1.0
    assert rect.y0 >= word.y0 - 1.0
    assert rect.x1 <= word.x1 + 1.0
    assert rect.y1 <= word.y1 + 1.0
    # 不是退化成一点：横线仍有 10pt 长
    assert max(rect.width, rect.height) == pytest.approx(10.0, abs=1e-3)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_export_single_point_on_rotated_page_keeps_radius(tmp_path, rotation):
    """单点笔迹只换圆心，半径/线宽不变。"""
    pdf, word, _, display_word = _make_rotated_text_pdf(
        tmp_path / f"dot{rotation}.pdf", rotation)
    cx = (display_word.x0 + display_word.x1) / 2.0
    cy = (display_word.y0 + display_word.y1) / 2.0
    out = tmp_path / f"dot{rotation}-out.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [[Stroke([(cx, cy)], "#00ff00", 6.0)]], out)

    doc = pymupdf.open(str(out))
    drawings = doc[0].get_drawings()
    doc.close()
    assert len(drawings) == 1
    assert "f" in drawings[0]["type"]
    rect = drawings[0]["rect"]
    assert (rect.width, rect.height) == pytest.approx((6.0, 6.0), abs=1e-3)
    # 圆心回到未旋转文字框内
    assert word.x0 <= rect.x0 + 3.0 <= word.x1
    assert word.y0 <= rect.y0 + 3.0 <= word.y1


def test_export_pressure_stroke_is_filled_polygon_with_opacity(tmp_path):
    """压感笔迹导出成 fill 多边形（不再描边）；荧光笔透明度仍是 0.35。"""
    pdf = _make_pdf(tmp_path / "pressure.pdf", pages=((300, 300, 0), (300, 300, 0)))
    pen = Stroke([(20.0, 30.0), (120.0, 30.0), (120.0, 80.0)], "#0000ff", 10.0,
                 pressures=[0.0, 0.5, 1.0])
    highlighter = Stroke([(20.0, 120.0), (200.0, 120.0)], "#ffcc00", 16.0,
                         "highlighter", pressures=[1.0, 1.0])
    out = tmp_path / "pressure-out.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [[pen, highlighter], []], out)

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 2
        drawings = doc[0].get_drawings()
        assert doc[1].get_drawings() == []
    finally:
        doc.close()
    assert drawings
    assert all("s" not in d["type"] for d in drawings)
    pen_fills = [d for d in drawings if d.get("fill") == pytest.approx((0.0, 0.0, 1.0))]
    hl_fills = [d for d in drawings
                if d.get("fill") == pytest.approx((1.0, 0.8, 0.0))]
    # 每条笔迹 = ribbon 多边形 + 首尾两个端点半宽圆
    assert len(pen_fills) == 3
    assert len(hl_fills) == 3
    assert all(d["fill_opacity"] == pytest.approx(1.0) for d in pen_fills)
    assert all(d["fill_opacity"] == pytest.approx(0.35, abs=1e-6) for d in hl_fills)
    polygon = max(pen_fills, key=lambda d: d["rect"].width * d["rect"].height)
    # 起点压力 0 → 半宽 1.75（平头，不沿切线外扩）；终点压力 1 → 半宽 5
    assert polygon["rect"].x0 == pytest.approx(20.0, abs=1e-3)
    assert polygon["rect"].x1 == pytest.approx(125.0, abs=1e-3)
    # 中间点压力 0.5 → 半宽 3.375，45° 拐角方向上的投影
    assert polygon["rect"].y0 == pytest.approx(30.0 - 3.375 / 2 ** 0.5, abs=1e-3)
    assert polygon["rect"].y1 == pytest.approx(80.0, abs=1e-3)


def test_export_single_point_pressure_stroke_is_a_circle(tmp_path):
    pdf = _make_pdf(tmp_path / "pdot.pdf", pages=((200, 200, 0),))
    out = tmp_path / "pdot-out.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(
            src, [[Stroke([(50.0, 60.0)], "#00ff00", 10.0, pressures=[1.0])]], out)

    doc = pymupdf.open(str(out))
    try:
        drawings = doc[0].get_drawings()
    finally:
        doc.close()
    assert len(drawings) == 1
    assert "f" in drawings[0]["type"]
    assert drawings[0]["fill"] == pytest.approx((0.0, 1.0, 0.0))
    assert (drawings[0]["rect"].width, drawings[0]["rect"].height) == pytest.approx(
        (10.0, 10.0), abs=1e-3)


def test_export_pressure_stroke_derotates_on_rotated_page(tmp_path):
    """压感多边形的每个顶点都要乘 derotation，墨迹仍在显示位置。"""
    pdf, word, _, display_word = _make_rotated_text_pdf(
        tmp_path / "prot.pdf", 90)
    cx = (display_word.x0 + display_word.x1) / 2.0
    cy = (display_word.y0 + display_word.y1) / 2.0
    stroke = Stroke([(cx - 5.0, cy), (cx + 5.0, cy)], "#0000ff", 4.0,
                    pressures=[1.0, 1.0])
    out = tmp_path / "prot-out.pdf"
    with PdfSource(pdf) as src:
        export_with_strokes(src, [[stroke]], out)

    doc = pymupdf.open(str(out))
    try:
        drawings = doc[0].get_drawings()
    finally:
        doc.close()
    assert drawings and all("s" not in d["type"] for d in drawings)
    rect = max(drawings, key=lambda d: d["rect"].width * d["rect"].height)["rect"]
    assert word.x0 - 2.5 <= rect.x0 and rect.x1 <= word.x1 + 2.5
    assert word.y0 - 2.5 <= rect.y0 and rect.y1 <= word.y1 + 2.5


def test_export_unrotated_page_is_unchanged_by_derotation(tmp_path):
    """0° 页 derotation 是单位阵：旧行为逐值不变。"""
    pdf, word, _, _ = _make_rotated_text_pdf(tmp_path / "plain.pdf", 0)
    stroke = Stroke([(word.x0, word.y0), (word.x1, word.y1)], "#0000ff", 3.0)
    out = tmp_path / "plain-out.pdf"
    with PdfSource(pdf) as src:
        assert src.rotation(0) == 0
        export_with_strokes(src, [[stroke]], out)
    doc = pymupdf.open(str(out))
    drawings = doc[0].get_drawings()
    doc.close()
    assert len(drawings) == 1
    rect = drawings[0]["rect"]
    # 落盘会过 PDF 的定点数（~1e-5 量级），容差 1e-3
    assert (rect.x0, rect.y0, rect.x1, rect.y1) == pytest.approx(
        (word.x0, word.y0, word.x1, word.y1), abs=1e-3)


# ---- 文字选择（v2.10）：按词取文本 + 显示坐标 ----

def test_page_words_returns_text_and_display_rects(tmp_path):
    pdf, _word, display_size, _display_word = _make_rotated_text_pdf(
        tmp_path / "words.pdf", 90, text="alpha beta")
    # 期望值用 MuPDF 自己算第一个词的显示矩形，不抄实现里的矩阵
    doc = pymupdf.open(str(pdf))
    page = doc[0]
    expected = page.search_for("alpha")[0] * page.rotation_matrix
    doc.close()
    with PdfSource(pdf) as src:
        words = src.page_words(0)
    assert [w[0] for w in words] == ["alpha", "beta"]
    # 两个词都在显示页框内；第一个词的矩形与独立算出的显示矩形一致
    for _text, x0, y0, x1, y1, block, line in words:
        assert 0.0 <= x0 < x1 <= display_size[0]
        assert 0.0 <= y0 < y1 <= display_size[1]
        assert block >= 0 and line >= 0
    assert words[0][1:5] == pytest.approx(
        (expected.x0, expected.y0, expected.x1, expected.y1), abs=1e-6)


def test_page_words_empty_page_and_after_close(tmp_path):
    pdf = _make_pdf(tmp_path / "blank.pdf", ((200, 300, 0),))
    src = PdfSource(pdf)
    assert src.page_words(0) == []
    src.close()
    with pytest.raises(PdfError):
        src.page_words(0)


def test_page_chars_splits_chinese_per_character(tmp_path):
    """中文没有空格：page_words 会把整行当一个词，page_chars 必须逐字给框。"""
    pdf = tmp_path / "cn.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((40, 80), "行测理论", fontsize=16, fontname="china-s")
    doc.save(str(pdf))
    doc.close()
    with PdfSource(pdf) as src:
        chars = src.page_chars(0)
    assert [c[0] for c in chars] == ["行", "测", "理", "论"]
    for text, x0, y0, x1, y1, block, line in chars:
        assert 0.0 <= x0 < x1 <= 300.0 and 0.0 <= y0 < y1 <= 200.0
        assert block >= 0 and line >= 0
    # 相邻字按阅读顺序、横向递增，宽度接近字号（每个汉字一个独立框）
    assert chars[0][1] < chars[1][1] < chars[2][1] < chars[3][1]
    assert 10.0 <= chars[0][3] - chars[0][1] <= 20.0


def test_page_chars_rotation_and_close(tmp_path):
    pdf, _word, display_size, _display_word = _make_rotated_text_pdf(
        tmp_path / "chars.pdf", 90, text="abc")
    with PdfSource(pdf) as src:
        chars = src.page_chars(0)
    assert [c[0] for c in chars] == ["a", "b", "c"]
    for _text, x0, y0, x1, y1, _block, _line in chars:
        assert 0.0 <= x0 < x1 <= display_size[0]
        assert 0.0 <= y0 < y1 <= display_size[1]
    src = PdfSource(pdf)
    src.close()
    with pytest.raises(PdfError):
        src.page_chars(0)
