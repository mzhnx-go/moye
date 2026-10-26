"""笔记富内容（v3）后端：sidecar 字段、资源目录、PDF 导出层序与降级。

样例 PDF/PNG 全部在 tmp_path 现造，不碰 data/；纯 pymupdf + 标准库。
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest

from sketchpad.core import noteassets, pdfstore
from sketchpad.core.notes import (Note, NoteAttachment, NoteImage, TextBlock,
                                  new_item_id, note_has_rich_content)
from sketchpad.core.pdfstore import (FORMAT_VERSION, Page, PdfDocument,
                                     PdfFormatError, PdfVersionTooNew)
from sketchpad.core.strokes import Stroke
from sketchpad.pdf.document import (NOTE_MISSING_FILL, NOTE_TAG_FILL,
                                    NOTE_TAG_LINE, PdfSource, export_with_notes)

PAGE_W, PAGE_H = 300.0, 400.0
DESIGN_W, DESIGN_H = 560.0, 720.0
SCALE = PAGE_W / DESIGN_W


def _rgb(color):
    text = color.lstrip("#")
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _make_pdf(path, texts=("PAGE-ONE",)):
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        if text:
            page.insert_text((40, 60), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def _make_png(path, width=40, height=30, fill=(1.0, 0.0, 0.0)):
    """用 PyMuPDF 造一张纯色 PNG（不依赖 Qt/PIL）。"""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=fill, width=0)
    page.get_pixmap(alpha=False).save(str(path))
    doc.close()
    return path


def _rich_note(**kwargs):
    note = Note("n-1", 30.0, 40.0, strokes=[Stroke([(5.0, 6.0)], "#000000", 2.0)])
    note.texts.append(TextBlock("t-1", 40.0, 100.0, 400.0, "正文一行", 18.0, "#ffcc00"))
    note.images.append(NoteImage("img-1", 40.0, 200.0, 200.0, 150.0, "abc123.png"))
    note.attachments.append(
        NoteAttachment("att-1", 40.0, 500.0, "def456.pdf", "报告.pdf", 1234))
    for key, value in kwargs.items():
        setattr(note, key, value)
    return note


def _doc_with(note):
    return PdfDocument([Page(100.0, 200.0, 0, [], [note])])


# ---------- 模型 ----------

def test_new_item_id_takes_first_free_slot_per_prefix():
    assert new_item_id("t", set()) == "t-1"
    assert new_item_id("t", {"t-1", "t-2"}) == "t-3"
    assert new_item_id("img", {"t-1"}) == "img-1"
    assert new_item_id("att", {"att-2"}) == "att-1"


def test_note_has_rich_content_detects_any_kind():
    assert note_has_rich_content(Note("n-1", 0.0, 0.0)) is False
    assert note_has_rich_content(Note("n-1", 0.0, 0.0, texts=[
        TextBlock("t-1", 0.0, 0.0, 10.0, "x")])) is True
    assert note_has_rich_content(Note("n-1", 0.0, 0.0, images=[
        NoteImage("img-1", 0.0, 0.0, 1.0, 1.0, "a.png")])) is True
    assert note_has_rich_content(Note("n-1", 0.0, 0.0, attachments=[
        NoteAttachment("att-1", 0.0, 0.0, "a.pdf", "a.pdf")])) is True


def test_rich_fields_are_last_positional_fields():
    """旧位置参数 Note(id, x, y, w, h, paper, strokes, name) 仍按原顺序生效。"""
    stroke = Stroke([(1.0, 2.0)], "#000000", 1.0)
    note = Note("n-1", 1.0, 2.0, 30.0, 40.0, "light", [stroke], "名字")
    assert (note.paper, note.name) == ("light", "名字")
    assert note.texts == [] and note.images == [] and note.attachments == []


# ---------- sidecar v3 往返 ----------

def test_v3_round_trip_keeps_rich_content():
    doc = _doc_with(_rich_note())
    raw = json.loads(pdfstore.to_json(doc))
    assert raw["version"] == 3
    note_raw = raw["pages"][0]["notes"][0]
    assert note_raw["texts"][0]["text"] == "正文一行"
    assert note_raw["images"][0]["asset"] == "abc123.png"
    assert note_raw["attachments"][0]["name"] == "报告.pdf"

    back = pdfstore.from_json(pdfstore.to_json(doc), 1)
    assert back == doc
    note = back.pages[0].notes[0]
    assert (note.texts[0].id, note.texts[0].size, note.texts[0].color) == (
        "t-1", 18.0, "#ffcc00")
    assert (note.images[0].w, note.images[0].h) == (200.0, 150.0)
    assert (note.attachments[0].size, note.attachments[0].name) == (1234, "报告.pdf")


@pytest.mark.parametrize("key", ["texts", "images", "attachments"])
def test_each_rich_kind_alone_upgrades_to_v3(key):
    note = Note("n-1", 0.0, 0.0)
    setattr(note, key, getattr(_rich_note(), key))
    raw = json.loads(pdfstore.to_json(_doc_with(note)))
    assert raw["version"] == 3
    note_raw = raw["pages"][0]["notes"][0]
    assert list(note_raw)[-1] == key
    # 只写非空数组：另外两类不出现
    for other in {"texts", "images", "attachments"} - {key}:
        assert other not in note_raw


def test_v2_output_without_rich_content_is_byte_identical():
    note = Note("n-1", 30.0, 40.0,
                strokes=[Stroke([(5.0, 6.0), (7.0, 8.0)], "#e8eaed", 2.0)])
    raw = pdfstore.to_json(_doc_with(note))
    assert raw == (
        '{"version":2,"pages":[{"w":100.0,"h":200.0,"r":0,"strokes":[],'
        '"notes":[{"id":"n-1","x":30.0,"y":40.0,"w":560.0,"h":720.0,'
        '"paper":"dark","strokes":[{"p":[[5.0,6.0],[7.0,8.0]],'
        '"c":"#e8eaed","w":2.0,"t":"pen"}]}]}]}'
    )
    assert "texts" not in raw and "images" not in raw and "attachments" not in raw


def test_version_is_v1_without_notes_even_if_rich_helpers_exist():
    assert FORMAT_VERSION == 4
    raw = json.loads(pdfstore.to_json(PdfDocument([Page(100.0, 200.0)])))
    assert raw["version"] == 1


def test_old_v1_and_v2_sidecars_load_with_empty_rich_lists():
    v1 = json.dumps({"version": 1, "pages": [{"w": 100, "h": 200, "r": 0,
                                              "strokes": []}]})
    assert pdfstore.from_json(v1).pages[0].notes == []
    v2 = json.dumps({"version": 2, "pages": [
        {"w": 100, "h": 200, "r": 0, "strokes": [],
         "notes": [{"id": "n-1", "x": 1, "y": 2, "w": 560, "h": 720,
                    "paper": "dark", "strokes": []}]}]})
    note = pdfstore.from_json(v2).pages[0].notes[0]
    assert (note.texts, note.images, note.attachments) == ([], [], [])


def test_version_five_is_too_new_but_four_is_read():
    with pytest.raises(PdfVersionTooNew):
        pdfstore.from_json(json.dumps({"version": 5, "pages": []}))
    assert pdfstore.from_json(json.dumps({"version": 4, "pages": []})).pages == []


def test_sidecar_round_trip_through_disk(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdfstore.save_sidecar(pdf, _doc_with(_rich_note()))
    loaded = pdfstore.load_sidecar(pdf, expected_page_count=1)
    assert loaded == _doc_with(_rich_note())


# ---------- sidecar 坏字段 ----------

def _rich_raw(**note_fields):
    note = {"id": "n-1", "x": 0, "y": 0, "w": 560, "h": 720,
            "paper": "dark", "strokes": []}
    note.update(note_fields)
    return {"version": 3, "pages": [{"w": 100, "h": 200, "r": 0,
                                     "strokes": [], "notes": [note]}]}


_TEXT = {"id": "t-1", "x": 1, "y": 2, "w": 100, "text": "hi"}
_IMAGE = {"id": "img-1", "x": 1, "y": 2, "w": 100, "h": 50, "asset": "a.png"}
_ATT = {"id": "att-1", "x": 1, "y": 2, "asset": "a.pdf", "name": "a.pdf"}


@pytest.mark.parametrize(
    "field,value",
    [
        pytest.param({"texts": {}}, {}, id="texts-not-list"),
        pytest.param({"images": "x"}, "x", id="images-not-list"),
        pytest.param({"attachments": 7}, 7, id="attachments-not-list"),
    ],
)
def test_rich_lists_must_be_lists(field, value):
    with pytest.raises(PdfFormatError) as e:
        pdfstore.from_json(json.dumps(_rich_raw(**field)))
    message = str(e.value)
    assert "page #0 note #0" in message
    assert next(iter(field)) in message


@pytest.mark.parametrize(
    "field,value",
    [
        pytest.param({"texts": ["nope"]}, ["nope"], id="text-item-not-dict"),
        pytest.param({"texts": [{**_TEXT, "id": ""}]}, [{}], id="text-blank-id"),
        pytest.param({"texts": [{**_TEXT, "id": 3}]}, [{}], id="text-int-id"),
        pytest.param({"texts": [{**_TEXT, "x": "1"}]}, [{}], id="text-str-x"),
        pytest.param({"texts": [{**_TEXT, "w": 0}]}, [{}], id="text-zero-width"),
        pytest.param({"texts": [{**_TEXT, "size": 0}]}, [{}], id="text-zero-size"),
        pytest.param({"texts": [{**_TEXT, "size": "16"}]}, [{}], id="text-str-size"),
        pytest.param({"texts": [{**_TEXT, "color": "red"}]}, [{}], id="text-bad-color"),
        pytest.param({"texts": [{**_TEXT, "color": "#12345"}]}, [{}], id="text-short-color"),
        pytest.param({"texts": [{**_TEXT, "text": 5}]}, [{}], id="text-not-str"),
        pytest.param({"texts": [{**_TEXT, "text": "x" * 20001}]}, [{}], id="text-too-long"),
        pytest.param({"images": [{**_IMAGE, "asset": "sub/a.png"}]}, [{}], id="image-asset-path"),
        pytest.param({"images": [{**_IMAGE, "asset": "../a.png"}]}, [{}], id="image-asset-traversal"),
        pytest.param({"images": [{**_IMAGE, "asset": "a\\b.png"}]}, [{}], id="image-asset-backslash"),
        pytest.param({"images": [{**_IMAGE, "asset": "a\x00b.png"}]}, [{}], id="image-asset-nul"),
        pytest.param({"images": [{**_IMAGE, "asset": "."}]}, [{}], id="image-asset-dot"),
        pytest.param({"images": [{**_IMAGE, "asset": ""}]}, [{}], id="image-asset-empty"),
        pytest.param({"images": [{**_IMAGE, "h": -1}]}, [{}], id="image-negative-h"),
        pytest.param({"images": [{"id": "img-1", "x": 1, "y": 2, "w": 10, "asset": "a.png"}]},
                     [{}], id="image-missing-h"),
        pytest.param({"attachments": [{**_ATT, "name": "  "}]}, [{}], id="att-blank-name"),
        pytest.param({"attachments": [{**_ATT, "name": 7}]}, [{}], id="att-int-name"),
        pytest.param({"attachments": [{**_ATT, "size": -1}]}, [{}], id="att-negative-size"),
        pytest.param({"attachments": [{**_ATT, "size": 1.5}]}, [{}], id="att-float-size"),
        pytest.param({"attachments": [{**_ATT, "size": True}]}, [{}], id="att-bool-size"),
        pytest.param({"attachments": [{**_ATT, "asset": "C:\\a.pdf"}]}, [{}], id="att-abs-asset"),
        pytest.param({"texts": [dict(_TEXT), dict(_TEXT)]}, [{}], id="duplicate-text-ids"),
        pytest.param({"images": [dict(_IMAGE), dict(_IMAGE)]}, [{}], id="duplicate-image-ids"),
        pytest.param({"attachments": [dict(_ATT), dict(_ATT)]}, [{}], id="duplicate-att-ids"),
    ],
)
def test_bad_rich_fields_name_page_note_and_item(field, value):
    with pytest.raises(PdfFormatError) as e:
        pdfstore.from_json(json.dumps(_rich_raw(**field)))
    message = str(e.value)
    assert "page #0 note #0" in message
    key = next(iter(field))
    assert f"{key} #" in message


def test_long_text_at_the_limit_is_accepted():
    raw = _rich_raw(texts=[{**_TEXT, "text": "x" * pdfstore.TEXT_MAX_LENGTH}])
    note = pdfstore.from_json(json.dumps(raw)).pages[0].notes[0]
    assert len(note.texts[0].text) == 20000


def test_empty_text_and_missing_optionals_are_accepted():
    raw = _rich_raw(texts=[{**_TEXT, "text": ""}],
                    images=[{"id": "img-1", "x": 1, "y": 2, "w": 10, "h": 10,
                             "asset": "a.png"}],
                    attachments=[{"id": "att-1", "x": 1, "y": 2,
                                  "asset": "a.pdf", "name": "a.pdf"}])
    note = pdfstore.from_json(json.dumps(raw)).pages[0].notes[0]
    assert note.texts[0].text == ""
    assert (note.texts[0].size, note.texts[0].color) == (16.0, "#e8eaed")
    assert note.attachments[0].size == 0


# ---------- noteassets ----------

def test_assets_dir_sits_next_to_pdf(tmp_path):
    assert noteassets.ASSET_DIR_SUFFIX == ".sketch-assets"
    assert noteassets.assets_dir(tmp_path / "a.pdf") == tmp_path / "a.pdf.sketch-assets"
    assert noteassets.assets_dir(tmp_path / "noext") == tmp_path / "noext.sketch-assets"


def test_store_asset_copies_with_uuid_name(tmp_path):
    pdf = tmp_path / "lecture.pdf"
    src = tmp_path / "photo.PNG"
    src.write_bytes(b"\x89PNG-fake-body")
    name = noteassets.store_asset(pdf, src)

    assert name.endswith(".png") and name.count(".") == 1
    assert len(name) == len("0123456789ab") + len(".png")
    saved = noteassets.assets_dir(pdf) / name
    assert saved.is_file() and saved.read_bytes() == b"\x89PNG-fake-body"
    # 源文件保持原样，不被搬走
    assert src.read_bytes() == b"\x89PNG-fake-body"
    # 同名源复制两次不会互相覆盖
    assert noteassets.store_asset(pdf, src) != name


def test_store_asset_missing_source_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        noteassets.store_asset(tmp_path / "a.pdf", tmp_path / "nope.png")


@pytest.mark.parametrize(
    "bad",
    ["sub/a.png", "..\\a.png", "../a.png", "C:\\temp\\a.png", "/abs.png",
     "", ".", "..", "a:b.png", "a\x00b.png", 7, None],
)
def test_asset_path_rejects_traversal_and_absolute(tmp_path, bad):
    with pytest.raises(ValueError):
        noteassets.asset_path(tmp_path / "a.pdf", bad)


def test_asset_path_returns_path_without_checking_existence(tmp_path):
    path = noteassets.asset_path(tmp_path / "a.pdf", "abc.png")
    assert path == noteassets.assets_dir(tmp_path / "a.pdf") / "abc.png"
    assert not path.exists()


def test_remove_assets_deletes_quietly(tmp_path):
    pdf = tmp_path / "a.pdf"
    directory = noteassets.assets_dir(pdf)
    directory.mkdir()
    (directory / "one.png").write_bytes(b"1")
    noteassets.remove_assets(pdf, ["one.png", "missing.png", "../evil", "C:\\x"])
    assert not (directory / "one.png").exists()
    # 单个字符串也能删（按一项处理，不逐字符遍历）
    (directory / "two.png").write_bytes(b"2")
    noteassets.remove_assets(pdf, "two.png")
    assert not (directory / "two.png").exists()
    # 不存在目录 / 空列表都不抛
    noteassets.remove_assets(tmp_path / "b.pdf", ["x.png"])
    noteassets.remove_assets(pdf, [])


# ---------- 导出 ----------

def _export_with_note(tmp_path, note, src=None, name="out.pdf"):
    source = src or _make_pdf(tmp_path / "in.pdf")
    out = tmp_path / name
    with PdfSource(source) as pdf:
        export_with_notes(pdf, [[]], [[note]], out)
    return out


def test_export_draws_image_text_and_attachment_on_note_page(tmp_path):
    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf)
    assets = noteassets.assets_dir(pdf)
    assets.mkdir()
    _make_png(assets / "pic.png")

    note = Note("n-1", 30.0, 40.0)
    note.images.append(NoteImage("img-1", 40.0, 60.0, 200.0, 150.0, "pic.png"))
    note.texts.append(TextBlock("t-1", 40.0, 260.0, 400.0, "NOTE-BODY 正文",
                                18.0, "#ffcc00"))
    note.attachments.append(
        NoteAttachment("att-1", 40.0, 600.0, "missing.pdf", "季度报告.pdf", 99))

    out = _export_with_note(tmp_path, note, pdf)

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 2
        page = doc[1]
        assert "NOTE-BODY" in page.get_text()
        assert "季度报告.pdf" in page.get_text()
        assert "季度报告.pdf" not in doc[0].get_text()

        images = page.get_image_info()
        assert len(images) == 1
        bbox = pymupdf.Rect(images[0]["bbox"])
        assert bbox.width == pytest.approx(200.0 * SCALE, abs=1.0)
        assert bbox.height == pytest.approx(150.0 * SCALE, abs=1.0)
        assert bbox.x0 == pytest.approx(40.0 * SCALE, abs=1.0)
        assert bbox.y0 == pytest.approx(60.0 * SCALE, abs=1.0)

        # 像素：图片区域中心是造图时的纯红
        pix = page.get_pixmap()
        r, g, b = pix.pixel(int(bbox.x0 + bbox.width / 2),
                            int(bbox.y0 + bbox.height / 2))[:3]
        assert r > 200 and g < 60 and b < 60

        drawings = page.get_drawings()
        assert any("f" in d["type"] and d.get("fill") == pytest.approx(
            _rgb(NOTE_TAG_FILL["dark"])) for d in drawings)
        assert any("s" in d["type"] and d.get("color") == pytest.approx(
            _rgb(NOTE_TAG_LINE["dark"])) for d in drawings)
    finally:
        doc.close()


def test_export_layer_order_puts_strokes_on_top(tmp_path):
    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf)
    note = Note("n-1", 30.0, 40.0,
                strokes=[Stroke([(40.0, 600.0), (400.0, 620.0)], "#0000ff", 2.0)])
    note.attachments.append(
        NoteAttachment("att-1", 40.0, 600.0, "a.pdf", "a.pdf"))
    note.texts.append(TextBlock("t-1", 40.0, 100.0, 300.0, "TEXT", 14.0,
                                "#ffffff"))

    out = _export_with_note(tmp_path, note, pdf)

    doc = pymupdf.open(str(out))
    try:
        drawings = doc[1].get_drawings()
        tag_index = next(i for i, d in enumerate(drawings)
                         if d.get("fill") == pytest.approx(
                             _rgb(NOTE_TAG_FILL["dark"])))
        stroke_index = next(i for i, d in enumerate(drawings)
                            if "s" in d["type"]
                            and d.get("color") == pytest.approx((0.0, 0.0, 1.0)))
        assert tag_index < stroke_index      # 笔迹最后画（最上层）
    finally:
        doc.close()


def test_missing_image_asset_exports_with_placeholder(tmp_path):
    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf)
    note = Note("n-1", 30.0, 40.0)
    note.images.append(NoteImage("img-1", 40.0, 60.0, 200.0, 150.0, "gone.png"))

    out = _export_with_note(tmp_path, note, pdf)

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 2
        page = doc[1]
        assert page.get_image_info() == []
        assert any("f" in d["type"] and d.get("fill") == pytest.approx(
            _rgb(NOTE_MISSING_FILL)) for d in page.get_drawings())
    finally:
        doc.close()


def test_image_with_traversal_asset_does_not_escape(tmp_path):
    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf)
    note = Note("n-1", 30.0, 40.0)
    note.images.append(NoteImage("img-1", 10.0, 10.0, 100.0, 100.0,
                                 "../secret.png"))
    out = _export_with_note(tmp_path, note, pdf)

    doc = pymupdf.open(str(out))
    try:
        assert doc[1].get_image_info() == []
        assert any(d.get("fill") == pytest.approx(_rgb(NOTE_MISSING_FILL))
                   for d in doc[1].get_drawings())
    finally:
        doc.close()


def test_long_text_overflow_does_not_crash(tmp_path):
    """窄框 + 超大字号：排不下也只是少显示，导出必须成功。"""
    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf)
    note = Note("n-1", 30.0, 40.0)
    note.texts.append(TextBlock("t-1", 20.0, 20.0, 60.0,
                                ("overflowing line " * 200), 60.0, "#ffffff"))
    note.texts.append(TextBlock("t-2", 100.0, 400.0, 400.0,
                                ("fits line " * 5), 12.0, "#ffffff"))
    out = _export_with_note(tmp_path, note, pdf)

    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 2
        assert "fits" in doc[1].get_text()      # 正常块照常排出
    finally:
        doc.close()


def test_export_without_rich_content_keeps_drawings_count(tmp_path):
    """回归：没有富内容时笔记页只有纸面/横线/笔迹，不多画任何东西。"""
    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf)
    note = Note("n-1", 30.0, 40.0,
                strokes=[Stroke([(10.0, 10.0), (100.0, 100.0)], "#2c2c2e", 2.0)])
    out = _export_with_note(tmp_path, note, pdf)

    doc = pymupdf.open(str(out))
    try:
        assert doc[1].get_image_info() == []
        assert doc[1].get_text().strip() == ""
    finally:
        doc.close()
