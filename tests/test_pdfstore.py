"""PDF 批注 sidecar：往返一致、字段守卫、原子写。全部只在 tmp_path 操作。"""
import os

import pytest

from sketchpad.core.pdfstore import (
    SIDECAR_SUFFIX,
    Page,
    PdfDocument,
    PdfFormatError,
    PdfPageCountMismatch,
    PdfVersionTooNew,
    from_json,
    load_sidecar,
    save_sidecar,
    sidecar_path,
    to_json,
)
from sketchpad.core.notes import Note
from sketchpad.core.strokes import Stroke


def _doc() -> PdfDocument:
    """2 页 × 3 笔，坐标都是 2 位小数（编码只保留 2 位，往返才严格相等）。"""
    return PdfDocument(
        [
            Page(
                595.28,
                841.89,
                0,
                [
                    Stroke([(10.0, 20.5), (30.25, 40.0)], "#1a1a1a", 3.0),
                    Stroke([(0.0, 0.0), (595.28, 841.89)], "#c0392b", 12.0),
                    Stroke([(-1.5, 700.12), (200.0, 300.0)], "#2e86de", 1.0, "eraser"),
                ],
            ),
            Page(
                841.89,
                595.28,
                90,
                [
                    Stroke([(5.5, 6.25), (7.0, 8.5)], "#e8eaed", 48.0),
                    Stroke([(1.0, 2.0), (3.0, 4.0)], "#f1c40f", 3.0, "pen"),
                    Stroke([(0.0, 0.0), (0.25, 0.5)], "#000000", 6.0),
                ],
            ),
        ]
    )


def _minimal_text() -> str:
    return to_json(
        PdfDocument(
            [Page(595.28, 841.89, 0, [Stroke([(10.0, 20.5), (30.25, 40.0)], "#1a1a1a", 3.0)])]
        )
    )


def test_round_trip_keeps_every_page_and_stroke():
    assert from_json(to_json(_doc())) == _doc()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_page_rotation_round_trips(rotation):
    """v2.4-1：旋转角写进 `r` 并原样读回（视图打开旋转页时填它）。"""
    page = Page(300.0, 200.0, rotation, [Stroke([(1.0, 2.0), (3.0, 4.0)], "#000000", 3.0)])
    text = to_json(PdfDocument([page]))
    assert f'"r":{rotation}' in text
    loaded = from_json(text)
    assert loaded.pages[0].rotation == rotation
    assert loaded == PdfDocument([page])


def test_json_literal_shape_is_compact_and_stable():
    assert _minimal_text() == (
        '{"version":1,"pages":[{"w":595.28,"h":841.89,"r":0,"strokes":['
        '{"p":[[10.0,20.5],[30.25,40.0]],"c":"#1a1a1a","w":3.0,"t":"pen"}]}]}'
    )


def test_points_are_rounded_to_two_decimals():
    text = to_json(PdfDocument([Page(100.0, 200.0, 0, [Stroke([(1.0, 2.333333)], "#000000", 3.0)])]))
    assert '"p":[[1.0,2.33]]' in text


def test_sidecar_path_strips_pdf_and_appends_full_suffix(tmp_path):
    assert SIDECAR_SUFFIX == ".sketch.json"
    assert sidecar_path(tmp_path / "a.pdf") == tmp_path / "a.sketch.json"
    # 只去掉末尾 .pdf，文件名里的其他点保留
    assert sidecar_path(tmp_path / "lecture.v2.pdf") == tmp_path / "lecture.v2.sketch.json"
    assert sidecar_path(tmp_path / "notes") == tmp_path / "notes.sketch.json"


def test_version_two_is_accepted():
    assert from_json('{"version":2,"pages":[]}').pages == []


def test_version_four_is_accepted():
    assert from_json('{"version":4,"pages":[]}').pages == []


def test_version_five_is_refused_and_names_the_version():
    with pytest.raises(PdfVersionTooNew) as e:
        from_json('{"version":5,"pages":[]}')
    assert "5" in str(e.value)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("{not json", id="corrupt-json"),
        pytest.param("[]", id="root-not-object"),
        pytest.param('{"pages":[]}', id="missing-version"),
        pytest.param('{"version":true,"pages":[]}', id="boolean-version"),
        pytest.param('{"version":1}', id="missing-pages"),
        pytest.param('{"version":1,"pages":{}}', id="pages-not-list"),
        pytest.param('{"version":1,"pages":[{"h":842.0,"strokes":[]}]}', id="missing-width"),
        pytest.param(
            '{"version":1,"pages":[{"w":595.28,"h":841.89,"strokes":'
            '[{"p":[[0,1e999]],"c":"#000000","w":3.0}]}]}',
            id="infinite-coordinate",
        ),
        pytest.param(
            '{"version":1,"pages":[{"w":595.28,"h":841.89,"strokes":[{"p":[[0,0]],"c":"#000000","w":1e999}]}]}',
            id="infinite-width",
        ),
    ],
)
def test_bad_sidecar_raises_format_error(text):
    with pytest.raises(PdfFormatError):
        from_json(text)


def test_page_count_mismatch_names_both_counts():
    three = PdfDocument([Page(100.0, 200.0) for _ in range(3)])
    with pytest.raises(PdfPageCountMismatch) as e:
        from_json(to_json(three), expected_page_count=2)
    msg = str(e.value)
    assert "3" in msg and "2" in msg


def test_expected_page_count_matching_is_accepted():
    assert from_json(_minimal_text(), expected_page_count=1) == from_json(_minimal_text())


def test_save_twice_replaces_and_leaves_no_tmp(tmp_path):
    pdf = tmp_path / "a.pdf"
    first = save_sidecar(pdf, _doc())
    assert first == sidecar_path(pdf)
    assert load_sidecar(pdf) == _doc()

    second = PdfDocument([Page(100.0, 200.0)])
    assert save_sidecar(pdf, second) == sidecar_path(pdf)
    assert load_sidecar(pdf) == second
    assert [p.name for p in tmp_path.iterdir()] == ["a.sketch.json"]


def test_save_creates_missing_parent_dirs(tmp_path):
    pdf = tmp_path / "sub" / "dir" / "a.pdf"
    assert save_sidecar(pdf, _doc()) == sidecar_path(pdf)
    assert load_sidecar(pdf) == _doc()


def test_failed_replace_keeps_previous_sidecar(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    save_sidecar(pdf, _doc())
    old_text = sidecar_path(pdf).read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_sidecar(pdf, PdfDocument([Page(1.0, 2.0)]))

    assert sidecar_path(pdf).read_text(encoding="utf-8") == old_text
    assert [p.name for p in tmp_path.iterdir()] == ["a.sketch.json"]


def test_load_missing_sidecar_returns_none(tmp_path):
    assert load_sidecar(tmp_path / "absent.pdf") is None


def test_load_invalid_sidecar_raises_instead_of_empty_doc(tmp_path):
    pdf = tmp_path / "a.pdf"
    sidecar_path(pdf).write_text("{bad", encoding="utf-8")
    with pytest.raises(PdfFormatError):
        load_sidecar(pdf)


# ---- 压感（v2.11）：可选字段 `pr` ----


def _pressure_doc() -> PdfDocument:
    return PdfDocument([
        Page(595.28, 841.89, 0, [
            Stroke([(10.0, 20.5), (30.25, 40.0), (50.0, 60.0)], "#1a1a1a", 3.0,
                   pressures=[0.0, 0.125, 1.0]),
            Stroke([(1.0, 2.0), (3.0, 4.0)], "#f1c40f", 3.0, "pen"),
        ]),
    ])


def test_pressure_round_trips_and_is_rounded_to_three_decimals():
    doc = _pressure_doc()
    text = to_json(doc)
    assert '"pr":[0.0,0.125,1.0]' in text
    assert from_json(text) == doc
    assert from_json(text).pages[0].strokes[0].pressures == [0.0, 0.125, 1.0]


def test_pressure_round_trips_inside_note_strokes():
    note = Note("n-1", 10.0, 20.0, strokes=[
        Stroke([(1.0, 2.0), (3.0, 4.0)], "#000000", 2.0, pressures=[0.25, 0.75]),
    ])
    doc = PdfDocument([Page(100.0, 200.0, 0, [], [note])])
    loaded = from_json(to_json(doc))
    assert loaded == doc
    assert loaded.pages[0].notes[0].strokes[0].pressures == [0.25, 0.75]


def test_stroke_without_pressure_writes_no_pr_field():
    stroke = Stroke([(1.0, 2.0)], "#000000", 3.0)
    assert to_json(PdfDocument([Page(100.0, 200.0, 0, [stroke])])) == (
        '{"version":1,"pages":[{"w":100.0,"h":200.0,"r":0,"strokes":'
        '[{"p":[[1.0,2.0]],"c":"#000000","w":3.0,"t":"pen"}]}]}'
    )


def test_all_zero_pressure_is_treated_as_no_pressure_and_not_written():
    stroke = Stroke([(1.0, 2.0), (3.0, 4.0)], "#000000", 3.0,
                    pressures=[0.0, 0.0])
    text = to_json(PdfDocument([Page(100.0, 200.0, 0, [stroke])]))
    assert '"pr"' not in text


def test_legacy_json_without_pr_reads_none():
    text = ('{"version":1,"pages":[{"w":100.0,"h":200.0,"r":0,"strokes":'
            '[{"p":[[1.0,2.0],[3.0,4.0]],"c":"#000000","w":3.0,"t":"pen"}]}]}')
    stroke = from_json(text).pages[0].strokes[0]
    assert stroke.pressures is None


@pytest.mark.parametrize("bad", [
    pytest.param([0.5], id="length-mismatch"),
    pytest.param([0.5, 0.5, 0.5], id="too-many"),
    pytest.param([0.5, "0.5"], id="string"),
    pytest.param([0.5, None], id="null"),
    pytest.param([1.2, 0.5], id="out-of-range"),
    pytest.param([-0.2, 0.5], id="negative"),
    pytest.param(0.5, id="not-a-list"),
    pytest.param(None, id="null"),
])
def test_bad_pressure_raises_format_error_with_position(bad):
    import json
    text = json.dumps({"version": 1, "pages": [{
        "w": 100.0, "h": 200.0, "r": 0, "strokes": [
            {"p": [[1.0, 2.0], [3.0, 4.0]], "c": "#000000", "w": 3.0,
             "t": "pen", "pr": bad}]}]})
    with pytest.raises(PdfFormatError) as excinfo:
        from_json(text)
    message = str(excinfo.value)
    assert "pressure" in message
    assert "page #0 stroke #0" in message


def test_pressure_length_mismatch_in_note_names_note_position():
    import json
    text = json.dumps({"version": 2, "pages": [{
        "w": 100.0, "h": 200.0, "r": 0, "strokes": [], "notes": [{
            "id": "n-1", "x": 10.0, "y": 20.0, "w": 560.0, "h": 720.0,
            "paper": "dark", "strokes": [
                {"p": [[1.0, 2.0], [3.0, 4.0]], "c": "#000000", "w": 3.0,
                 "t": "pen", "pr": [0.5]}]}]}]})
    with pytest.raises(PdfFormatError) as excinfo:
        from_json(text)
    assert "pressure" in str(excinfo.value)
    assert "note #0" in str(excinfo.value)


def test_sidecar_save_and_load_keeps_pressure(tmp_path):
    pdf = tmp_path / "a.pdf"
    save_sidecar(pdf, _pressure_doc())
    loaded = load_sidecar(pdf)
    assert loaded == _pressure_doc()
    assert loaded.pages[0].strokes[0].pressures == [0.0, 0.125, 1.0]
