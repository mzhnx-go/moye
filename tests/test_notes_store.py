"""Note 卡片模型与 sidecar v2：往返、版本守卫、卡片字段校验。全部不碰 Qt。"""
import json

import pytest

from sketchpad.core import pdfstore
from sketchpad.core.notes import (NOTE_H, NOTE_W, Note, clamp_note_anchor,
                                  new_note_id, note_display_name)
from sketchpad.core.pdfstore import Page, PdfDocument, PdfFormatError, PdfVersionTooNew
from sketchpad.core.strokes import Stroke


def _note():
    return Note("n-1", 30.0, 40.0, strokes=[Stroke([(5.0, 6.0), (7.0, 8.0)], "#e8eaed", 2.0)])


def _v2_page(notes):
    return {"version": 2, "pages": [{"w": 100, "h": 200, "r": 0, "strokes": [], "notes": notes}]}


def _raw_note(note_id, x=0, y=0, w=10, h=20):
    return {"id": note_id, "x": x, "y": y, "w": w, "h": h, "paper": "dark", "strokes": []}


def test_v1_sidecar_has_no_notes():
    raw = json.dumps({"version": 1, "pages": [{"w": 100, "h": 200, "r": 0,
                                               "strokes": []}]})
    doc = pdfstore.from_json(raw, 1)
    assert doc.pages[0].notes == []


def test_v2_round_trip_note():
    doc = PdfDocument([Page(100.0, 200.0, 0, [], [_note()])])
    raw = pdfstore.to_json(doc)
    assert json.loads(raw)["version"] == 2
    back = pdfstore.from_json(raw, 1)
    note = back.pages[0].notes[0]
    assert (note.id, note.x, note.y, note.w, note.h, note.paper) == (
        "n-1", 30.0, 40.0, NOTE_W, NOTE_H, "dark")
    assert note.strokes[0].points == [(5.0, 6.0), (7.0, 8.0)]
    assert back == doc


def test_no_notes_still_writes_v1():
    doc = PdfDocument([Page(100.0, 200.0)])
    assert json.loads(pdfstore.to_json(doc))["version"] == 1


def test_v5_and_bad_note_fields_are_rejected():
    with pytest.raises(PdfVersionTooNew):
        pdfstore.from_json(json.dumps({"version": 5, "pages": []}))
    bad = {"version": 2, "pages": [{"w": 100, "h": 200, "r": 0, "strokes": [],
            "notes": [{"id": "", "x": 1, "y": 2, "w": 560, "h": 720,
                       "paper": "dark", "strokes": []}]}]}
    with pytest.raises(PdfFormatError):
        pdfstore.from_json(json.dumps(bad))


def test_new_id_and_anchor_clamp():
    assert new_note_id({"n-1"}) != "n-1"
    assert clamp_note_anchor(-5.0, 500.0, 100.0, 200.0) == (0.0, 200.0)


def test_new_note_id_takes_first_free_slot():
    assert new_note_id(set()) == "n-1"
    assert new_note_id({"n-1", "n-2"}) == "n-3"
    assert new_note_id({"n-2"}) == "n-1"


def test_page_positional_construction_keeps_old_order():
    """旧代码 Page(w, h, r, strokes) 仍按位置生效，notes 默认空。"""
    page = Page(10.0, 20.0, 90, [Stroke([(1.0, 2.0)], "#000000", 1.0)])
    assert page.notes == []
    assert page.strokes[0].points == [(1.0, 2.0)]


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"id": "n-1", "x": 0, "y": 0, "w": 0}, id="zero-width"),
        pytest.param({"id": "n-1", "x": 0, "y": 0, "h": -1}, id="negative-height"),
        pytest.param({"id": "n-1", "x": 0, "y": 0, "paper": "sepia"}, id="bad-paper"),
        pytest.param({"id": "  ", "x": 0, "y": 0}, id="blank-id"),
        pytest.param({"id": 7, "x": 0, "y": 0}, id="non-string-id"),
        pytest.param({"id": "n-1", "x": "0", "y": 0}, id="non-numeric-x"),
        pytest.param({"id": "n-1", "x": 0, "y": 0, "strokes": {}}, id="strokes-not-list"),
    ],
)
def test_bad_note_fields_name_the_note(kwargs):
    raw = _v2_page([{"w": 560, "h": 720, "paper": "dark", "strokes": [], **kwargs}])
    with pytest.raises(PdfFormatError) as e:
        pdfstore.from_json(json.dumps(raw))
    assert "page #0 note #0" in str(e.value)


def test_note_stroke_error_names_page_note_and_stroke():
    raw = _v2_page([{"id": "n-1", "x": 0, "y": 0, "w": 560, "h": 720,
                     "strokes": [{"p": [["a", 0]], "c": "#000000", "w": 1}]}])
    with pytest.raises(PdfFormatError) as e:
        pdfstore.from_json(json.dumps(raw))
    assert "page #0 note #0 stroke #0" in str(e.value)


def test_missing_paper_defaults_to_dark_and_notes_array_optional():
    raw = _v2_page([{"id": "n-2", "x": 1, "y": 2, "w": 10, "h": 20}])
    note = pdfstore.from_json(json.dumps(raw)).pages[0].notes[0]
    assert note.paper == "dark" and note.strokes == []
    # v2 页缺 notes 键（手写/旧工具）：当作空列表，不报错
    no_notes = {"version": 2, "pages": [{"w": 100, "h": 200, "r": 0, "strokes": []}]}
    assert pdfstore.from_json(json.dumps(no_notes)).pages[0].notes == []


def test_duplicate_note_ids_are_stripped_and_first_kept():
    raw = _v2_page([_raw_note(" n-1 ", x=1), _raw_note("n-1", x=2), _raw_note("n-2", x=3)])
    with pytest.warns(UserWarning, match="duplicate note id 'n-1' on page #0; keeping the first"):
        notes = pdfstore.from_json(json.dumps(raw)).pages[0].notes
    assert [n.id for n in notes] == ["n-1", "n-2"]
    assert notes[0].x == 1  # 保留首个


def test_out_of_page_anchor_is_clamped_on_load():
    raw = _v2_page([_raw_note("n-1", x=-30, y=500), _raw_note("n-2", x=250, y=-7)])
    notes = pdfstore.from_json(json.dumps(raw)).pages[0].notes
    assert (notes[0].x, notes[0].y) == (0.0, 200.0)
    assert (notes[1].x, notes[1].y) == (100.0, 0.0)


def test_notes_key_written_for_every_page():
    doc = PdfDocument([Page(100.0, 200.0, 0, [], [_note()]), Page(100.0, 200.0)])
    raw = json.loads(pdfstore.to_json(doc))
    assert raw["version"] == 2
    assert len(raw["pages"][0]["notes"]) == 1
    assert raw["pages"][1]["notes"] == []
    assert pdfstore.from_json(json.dumps(raw)).pages[1].notes == []


def test_notes_not_a_list_is_rejected():
    raw = _v2_page({"id": "n-1"})
    with pytest.raises(PdfFormatError) as e:
        pdfstore.from_json(json.dumps(raw))
    assert "page #0 notes" in str(e.value)


# ---------- 自定义名称（name） ----------

def test_note_name_round_trip():
    note = Note("n-1", 30.0, 40.0, name="周会纪要")
    doc = PdfDocument([Page(100.0, 200.0, 0, [], [note])])
    raw = json.loads(pdfstore.to_json(doc))
    assert raw["pages"][0]["notes"][0]["name"] == "周会纪要"
    assert raw["version"] == 2                    # 加 name 不改变版本判定
    back = pdfstore.from_json(json.dumps(raw), 1)
    assert back.pages[0].notes[0].name == "周会纪要"
    assert back == doc


def test_name_is_stripped_on_load():
    raw = _v2_page([{**_raw_note("n-1"), "name": "  我的笔记  "}])
    assert pdfstore.from_json(json.dumps(raw)).pages[0].notes[0].name == "我的笔记"


def test_missing_name_defaults_to_empty_string():
    raw = _v2_page([_raw_note("n-1")])
    note = pdfstore.from_json(json.dumps(raw)).pages[0].notes[0]
    assert note.name == ""


@pytest.mark.parametrize("bad", [7, 3.5, True, ["x"], {"a": 1}, None])
def test_non_string_name_is_rejected_with_page_and_note(bad):
    raw = _v2_page([{**_raw_note("n-1"), "name": bad}])
    with pytest.raises(PdfFormatError) as e:
        pdfstore.from_json(json.dumps(raw))
    assert "page #0 note #0" in str(e.value)
    assert "name" in str(e.value)


@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_name_key_is_not_written(name):
    note = Note("n-1", 1.0, 2.0, name=name)
    doc = PdfDocument([Page(100.0, 200.0, 0, [], [note])])
    raw = json.loads(pdfstore.to_json(doc))
    assert "name" not in raw["pages"][0]["notes"][0]
    assert raw["version"] == 2


def test_note_display_name_prefers_custom_name_and_falls_back():
    assert note_display_name(Note("n-1", 0.0, 0.0, name="周会纪要"), 3) == "周会纪要"
    assert note_display_name(Note("n-2", 0.0, 0.0, name="  带空格  "), 2) == "带空格"
    assert note_display_name(Note("n-3", 0.0, 0.0, name="   "), 3) == "笔记 3"
    assert note_display_name(Note("n-4", 0.0, 0.0), 1) == "笔记 1"
    assert note_display_name(Note("n-5", 0.0, 0.0, name=None), 4) == "笔记 4"


def test_name_is_last_positional_field_for_backward_compatibility():
    """旧位置参数 Note(id, x, y, w, h, paper, strokes) 仍按原顺序生效。"""
    stroke = Stroke([(1.0, 2.0)], "#000000", 1.0)
    note = Note("n-1", 1.0, 2.0, 30.0, 40.0, "light", [stroke])
    assert (note.id, note.x, note.y, note.w, note.h, note.paper) == (
        "n-1", 1.0, 2.0, 30.0, 40.0, "light")
    assert note.strokes == [stroke]
    assert note.name == ""



# ---------- 富内容 v3（追加） ----------

def test_v3_sidecar_keeps_rich_content_and_only_writes_non_empty_lists():
    from sketchpad.core.notes import NoteAttachment, NoteImage, TextBlock

    note = Note("n-1", 1.0, 2.0,
                texts=[TextBlock("t-1", 3.0, 4.0, 100.0, "你好", 18.0, "#ffffff")],
                images=[NoteImage("img-1", 5.0, 6.0, 10.0, 20.0, "a.png")],
                attachments=[NoteAttachment("att-1", 7.0, 8.0, "a.pdf", "报告.pdf", 3)])
    raw = json.loads(pdfstore.to_json(PdfDocument([Page(100.0, 200.0, 0, [], [note])])))
    assert raw["version"] == 3
    entry = raw["pages"][0]["notes"][0]
    assert (entry["texts"][0]["text"], entry["images"][0]["asset"],
            entry["attachments"][0]["name"]) == ("你好", "a.png", "报告.pdf")
    assert pdfstore.from_json(json.dumps(raw), 1).pages[0].notes[0] == note


def test_note_without_rich_content_stays_v2_and_writes_no_arrays():
    raw = json.loads(pdfstore.to_json(
        PdfDocument([Page(100.0, 200.0, 0, [], [_note()])])))
    assert raw["version"] == 2
    entry = raw["pages"][0]["notes"][0]
    assert not {"texts", "images", "attachments"} & set(entry)
