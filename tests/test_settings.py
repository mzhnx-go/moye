# tests/test_settings.py
"""配置读写测试：坏字段各自回退默认，不整份丢弃（设计文档 §6.3）。"""
import json
import os

import pytest

from sketchpad.core.settings import (DEFAULT_CLEAR_KEY, DEFAULT_COLOR,
                          HIGHLIGHTER_COLOR, RESERVED_KEYS, WIDTHS, Settings,
                          load, save)


def test_defaults_when_file_absent(tmp_path):
    s = load(tmp_path / "settings.json")
    assert (s.clear_key, s.color, s.width_index) == ("E", "#e8eaed", 1)


def test_round_trip(tmp_path):
    p = tmp_path / "settings.json"
    save(p, Settings(clear_key="Delete", color="#2980b9", width_index=3))
    assert load(p) == Settings("Delete", "#2980b9", 3)


def test_illegal_clear_key_falls_back_silently_but_flags(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"clear_key": "", "color": "#27ae60"}', encoding="utf-8")
    s = load(p)
    assert s.clear_key == DEFAULT_CLEAR_KEY
    assert s.color == "#27ae60"          # 合法字段照常生效，不因一处坏全丢


def test_illegal_width_index_clamped(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"width_index": 99}', encoding="utf-8")
    assert load(p).width_index == 3       # 4 档：0..3


def test_reserved_keys_are_the_rejected_bindings():
    assert set(RESERVED_KEYS) == {"Ctrl+C", "Ctrl+V", "Ctrl+Z", "Ctrl+Y", "Ctrl+A",
                                 "Space", "0", "B", "H", "Q", "V", "N",
                                 "Ctrl+O", "Ctrl+S", "Ctrl+Shift+E",
                                 "PgUp", "PgDown", "Ctrl+G", "Ctrl+F", "Ctrl+,",
                                 "Ctrl+=", "Ctrl+-", "Ctrl+0", "Ctrl+9",
                                 "Ctrl+Shift+Z", "F3", "Shift+F3", "F11",
                                 "Home", "End"}


def test_constants_match_spec(tmp_path):
    assert WIDTHS == (1.5, 2.0, 4.0, 8.0)
    assert DEFAULT_CLEAR_KEY == "E"
    assert DEFAULT_COLOR == "#e8eaed"
    assert Settings() == Settings("E", "#e8eaed", 1)


def test_negative_width_index_clamped_to_zero(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"width_index": -5}', encoding="utf-8")
    assert load(p).width_index == 0


def test_unparseable_width_index_falls_back_to_default(tmp_path):
    p = tmp_path / "settings.json"
    for raw in ('{"width_index": null}', '{"width_index": []}', '{"width_index": "abc"}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).width_index == 1       # 坏值只影响这一个字段


def test_bad_color_falls_back_to_default_only(tmp_path):
    p = tmp_path / "settings.json"
    for raw in ('{"color": "red", "width_index": 2}', '{"color": "#abc", "width_index": 2}',
                '{"color": "#1234567", "width_index": 2}', '{"color": null, "width_index": 2}',
                '{"color": 1234567, "width_index": 2}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p) == Settings("E", DEFAULT_COLOR, 2, width=WIDTHS[2])


def test_non_string_clear_key_falls_back(tmp_path):
    p = tmp_path / "settings.json"
    for raw in ('{"clear_key": null}', '{"clear_key": []}', '{"clear_key": "   "}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).clear_key == DEFAULT_CLEAR_KEY


def test_reserved_clear_key_in_config_is_rejected(tmp_path):
    # 设计文档 §6.3：Ctrl+C/V/Z/Y/A 与系统键撞车，写进配置也不接受
    p = tmp_path / "settings.json"
    for raw in ('{"clear_key": "Ctrl+Z"}', '{"clear_key": "ctrl+z"}',
                '{"clear_key": "Ctrl+C", "color": "#8e44ad"}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).clear_key == DEFAULT_CLEAR_KEY


def test_legal_clear_key_of_several_tokens_survives(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"clear_key": "Ctrl+Shift+X"}', encoding="utf-8")
    assert load(p).clear_key == "Ctrl+Shift+X"


def test_garbage_files_give_defaults(tmp_path):
    p = tmp_path / "settings.json"
    for raw in ('{not json', '[]', '{"a": 1', 'null', '"just a string"', '17'):
        p.write_text(raw, encoding="utf-8")
        assert load(p) == Settings(DEFAULT_CLEAR_KEY, DEFAULT_COLOR, 1)


def test_directory_at_path_gives_defaults(tmp_path):
    (tmp_path / "settings.json").mkdir()
    assert load(tmp_path / "settings.json") == Settings(DEFAULT_CLEAR_KEY, DEFAULT_COLOR, 1)


def test_save_creates_missing_parent_dirs(tmp_path):
    p = tmp_path / "cfg" / "deep" / "settings.json"
    save(p, Settings("Delete", "#f1c40f", 0))
    assert load(p) == Settings("Delete", "#f1c40f", 0)


def test_highlighter_color_round_trip_and_fallback(tmp_path):
    """荧光笔颜色独立存取：坏值只回自己的默认，不拖累画笔颜色。"""
    p = tmp_path / "settings.json"
    p.write_text('{"color": "#2980b9", "highlighter_color": "#27ae60"}',
                 encoding="utf-8")
    s = load(p)
    assert (s.color, s.highlighter_color) == ("#2980b9", "#27ae60")

    p.write_text('{"color": "#2980b9", "highlighter_color": "深黄"}',
                 encoding="utf-8")
    s = load(p)
    assert s.color == "#2980b9"
    assert s.highlighter_color == HIGHLIGHTER_COLOR


def test_save_writes_plain_json_object(tmp_path):
    p = tmp_path / "settings.json"
    save(p, Settings("F9", "#d35400", 2))
    assert json.loads(p.read_text(encoding="utf-8")) == {
        "clear_key": "F9", "color": "#d35400", "width_index": 2,
        "scratch_hotkey": "Alt+Q", "width": 2.0,
        "pdf_autosave": True, "recent_files": [],
        "toolbar_position": "top", "toolbar_collapsed": False,
        "main_hotkey": "Alt+W", "page_mode": "normal", "ink_smoothing": "low",
        "highlighter_color": "#d4a017",
        "pressure_enabled": True, "note_paper": "dark",
        "close_to_tray": True, "close_prompt": True,
        "open_export_folder": False}


def test_scratch_hotkey_default_and_round_trip(tmp_path):
    assert load(tmp_path / "s.json").scratch_hotkey == "Alt+Q"
    p = tmp_path / "s.json"
    p.write_text('{"scratch_hotkey": "Ctrl+Alt+D"}', encoding="utf-8")
    assert load(p).scratch_hotkey == "Alt+Q"        # 旧默认一次性迁移
    p.write_text('{"scratch_hotkey": "Ctrl+Alt+F"}', encoding="utf-8")
    assert load(p).scratch_hotkey == "Ctrl+Alt+F"


def test_scratch_hotkey_invalid_falls_back_and_save_keeps_it(tmp_path):
    p = tmp_path / "s.json"
    for raw in ('{"scratch_hotkey": ""}', '{"scratch_hotkey": "   "}',
                '{"scratch_hotkey": null}', '{"scratch_hotkey": 3}',
                '{"scratch_hotkey": "Ctrl+Ctrl+S"}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).scratch_hotkey == "Alt+Q"
    p.write_text('{"scratch_hotkey": "Ctrl+", "clear_key": "Delete"}', encoding="utf-8")
    assert load(p).scratch_hotkey == "Alt+Q"   # 坏值只影响自己
    assert load(p).clear_key == "Delete"
    save(p, Settings(scratch_hotkey="Shift+F2"))
    assert load(p) == Settings(scratch_hotkey="Shift+F2")
    assert json.loads(p.read_text(encoding="utf-8"))["scratch_hotkey"] == "Shift+F2"


def test_main_hotkey_default_round_trip_and_bad_values(tmp_path):
    p = tmp_path / "s.json"
    assert load(p).main_hotkey == "Alt+W"
    p.write_text('{"main_hotkey": "Ctrl+Shift+M"}', encoding="utf-8")
    assert load(p).main_hotkey == "Ctrl+Shift+M"
    for raw in ('{"main_hotkey": ""}', '{"main_hotkey": "   "}',
                '{"main_hotkey": null}', '{"main_hotkey": 3}',
                '{"main_hotkey": "Ctrl+Ctrl+S"}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).main_hotkey == "Alt+W"      # 坏值回默认，不影响草稿纸热键
    save(p, Settings(main_hotkey="Shift+F3"))
    assert load(p) == Settings(main_hotkey="Shift+F3")


def test_width_index_infinity_falls_back(tmp_path):
    # 1e999 在 json 里是合法字面量，解析成 inf；int(inf) 会抛 OverflowError
    p = tmp_path / "settings.json"
    p.write_text('{"width_index": 1e999}', encoding="utf-8")
    assert load(p).width_index == 1


def test_width_index_non_finite_falls_back(tmp_path):
    p = tmp_path / "settings.json"
    for raw in ('{"width_index": NaN}', '{"width_index": Infinity}',
                '{"width_index": -Infinity}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).width_index == 1


def test_color_must_be_hex_literal(tmp_path):
    p = tmp_path / "settings.json"
    for raw in ('{"color": "#gggggg"}', '{"color": "#12345"}',
                '{"color": "not-a-color"}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).color == DEFAULT_COLOR


def test_clear_key_with_spaces_around_plus_is_reserved(tmp_path):
    # QKeySequence 会把 "Ctrl + Z" 规范化成 Ctrl+Z，与撤销撞车
    p = tmp_path / "settings.json"
    p.write_text('{"clear_key": "Ctrl + Z"}', encoding="utf-8")
    assert load(p).clear_key == DEFAULT_CLEAR_KEY


def test_clear_key_needs_real_key_token(tmp_path):
    p = tmp_path / "settings.json"
    for raw in ('{"clear_key": "Mouse"}', '{"clear_key": "Ctrl"}',
                '{"clear_key": "Ctrl+Alt"}', '{"clear_key": "Ctrl+"}',
                '{"clear_key": "F25"}', '{"clear_key": "Hyper+Z"}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).clear_key == DEFAULT_CLEAR_KEY


def test_known_clear_keys_survive(tmp_path):
    p = tmp_path / "settings.json"
    for key in ("Delete", "F24", "Ctrl+Shift+X", "Insert", "a"):
        save(p, Settings(clear_key=key))
        assert load(p).clear_key == key
    p.write_text('{"clear_key": "Ctrl + Shift + X"}', encoding="utf-8")
    assert load(p).clear_key == "Ctrl+Shift+X"


def test_v24_reserved_combos_are_rejected_for_clear_key(tmp_path):
    # v2/v2.4 的快捷键不许当清屏键
    p = tmp_path / "settings.json"
    for key in ("Ctrl+O", "Ctrl+S", "Ctrl+Shift+E", "PgUp", "PgDown",
                "Ctrl+G", "Ctrl+F", "Ctrl+,",
                "Ctrl+=", "Ctrl+-", "Ctrl+0", "Ctrl+9", "Ctrl+Shift+Z",
                "F3", "Shift+F3", "F11", "Home", "End"):
        p.write_text(json.dumps({"clear_key": key}), encoding="utf-8")
        assert load(p).clear_key == DEFAULT_CLEAR_KEY, key


def test_reserved_detection_ignores_modifier_order_and_case(tmp_path):
    # Shift+Ctrl+E 与 Ctrl+Shift+E 是同一个键；大小写/空格/"+"两侧空白都不影响判定
    p = tmp_path / "settings.json"
    for key in ("Shift+Ctrl+E", "ctrl+shift+e", "SHIFT+CTRL+E", "Ctrl + Shift + E",
                "Shift + Ctrl + E", "ctrl+o", "CTRL+s", "shift+ctrl+e"):
        p.write_text(json.dumps({"clear_key": key}), encoding="utf-8")
        assert load(p).clear_key == DEFAULT_CLEAR_KEY


def test_pageup_spellings_of_reserved_keys_are_rejected(tmp_path):
    # 配置里手写 PageUp/PgDn 与 Qt 规范名 PgUp/PgDown 是同一个键
    p = tmp_path / "settings.json"
    for key in ("PageUp", "pageup", "PageDown", "PgDn", "pgdn"):
        p.write_text(json.dumps({"clear_key": key}), encoding="utf-8")
        assert load(p).clear_key == DEFAULT_CLEAR_KEY


def test_plain_local_keys_still_load(tmp_path):
    # 清屏键是本地快捷键：不加修饰的普通键照旧可用（F9/Delete 不该被新规则误伤）
    p = tmp_path / "settings.json"
    for key in ("F9", "Delete", "Insert"):
        p.write_text(json.dumps({"clear_key": key}), encoding="utf-8")
        assert load(p).clear_key == key


def test_save_leaves_no_tmp_file(tmp_path):
    p = tmp_path / "settings.json"
    save(p, Settings("Delete", "#2980b9", 2))
    assert sorted(f.name for f in tmp_path.iterdir()) == ["settings.json"]
    assert json.loads(p.read_text(encoding="utf-8"))["width_index"] == 2


def test_save_keeps_old_file_when_replace_fails(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    save(p, Settings("Delete", "#2980b9", 2))
    before = p.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save(p, Settings("F9", "#d35400", 0))
    assert p.read_text(encoding="utf-8") == before
    assert load(p) == Settings("Delete", "#2980b9", 2)


def test_custom_width_bounds_and_fallback(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"width": 9.5}', encoding="utf-8")
    assert load(p).width == 9.5
    p.write_text('{"width": 10}', encoding="utf-8")
    assert load(p).width == 10.0
    p.write_text('{"width": 0.5}', encoding="utf-8")
    assert load(p).width == 0.5
    p.write_text('{"width": 0.4}', encoding="utf-8")      # <0.5 太细：回默认
    assert load(p).width == 2.0
    p.write_text('{"width": 20}', encoding="utf-8")      # >10 用不上：回默认
    assert load(p).width == 2.0
    p.write_text('{"width": 0}', encoding="utf-8")
    assert load(p).width == 2.0
    p.write_text('{"width": 1e999}', encoding="utf-8")
    assert load(p).width == 2.0
    p.write_text('{"width_index": 2}', encoding="utf-8")     # 老配置：从预设推
    assert load(p).width == 4.0

def test_pdf_autosave_default_on_and_bool_only(tmp_path):
    p = tmp_path / "s.json"
    assert load(p).pdf_autosave is True
    p.write_text('{"pdf_autosave": false}', encoding="utf-8")
    assert load(p).pdf_autosave is False
    p.write_text('{"pdf_autosave": true}', encoding="utf-8")
    assert load(p).pdf_autosave is True
    for bad in ('"true"', "1", "null"):
        p.write_text('{"pdf_autosave": ' + bad + "}", encoding="utf-8")
        assert load(p).pdf_autosave is True      # 坏值回默认开


def test_recent_files_filter_dedupe_and_cap(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"recent_files": ["C:/a.pdf", "C:/A.PDF", "C:/B.pdf", 5, "", "rel.pdf"]}', encoding="utf-8")
    assert load(p).recent_files == ["C:/a.pdf", "C:/B.pdf"]
    many = "[" + ",".join('"C:/p%d.pdf"' % i for i in range(15)) + "]"
    p.write_text('{"recent_files": ' + many + "}", encoding="utf-8")
    assert len(load(p).recent_files) == 10


def test_push_recent_moves_caps_and_does_not_mutate():
    from sketchpad.core.settings import push_recent
    assert push_recent(["C:/a.pdf", "C:/b.pdf"], "C:/b.pdf") == ["C:/b.pdf", "C:/a.pdf"]
    assert push_recent(["C:/a.pdf"], "rel.pdf") == ["C:/a.pdf"]
    full = ["C:/p%d.pdf" % i for i in range(10)]
    moved = push_recent(full, "C:/new.pdf")
    assert moved[0] == "C:/new.pdf" and len(moved) == 10
    src = ["C:/a.pdf"]
    push_recent(src, "C:/b.pdf")
    assert src == ["C:/a.pdf"]


def test_saved_json_contains_pdf_keys(tmp_path):
    p = tmp_path / "s.json"
    save(p, Settings())
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["pdf_autosave"] is True
    assert data["recent_files"] == []


# ---------- v2.9：工具栏位置 / 收起状态 ----------

def test_toolbar_fields_default_to_top_and_expanded(tmp_path):
    s = load(tmp_path / "settings.json")
    assert s.toolbar_position == "top"
    assert s.toolbar_collapsed is False
    assert Settings().toolbar_position == "top"
    assert Settings().toolbar_collapsed is False


def test_invalid_toolbar_position_falls_back_to_top(tmp_path):
    p = tmp_path / "settings.json"
    for bad in ('"left"', '"bottom"', '"RIGHT"', '" right"', '""', "null", "1", "[]"):
        p.write_text('{"toolbar_position": ' + bad + "}", encoding="utf-8")
        assert load(p).toolbar_position == "top", bad


def test_toolbar_position_and_collapsed_round_trip(tmp_path):
    p = tmp_path / "settings.json"
    save(p, Settings(toolbar_position="right", toolbar_collapsed=True))
    loaded = load(p)
    assert loaded.toolbar_position == "right"
    assert loaded.toolbar_collapsed is True
    assert loaded == Settings(toolbar_position="right", toolbar_collapsed=True)
    save(p, Settings())
    assert load(p) == Settings()                 # 回顶部/展开也能落盘再读回


def test_toolbar_collapsed_only_accepts_real_bools(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('{"toolbar_collapsed": true}', encoding="utf-8")
    assert load(p).toolbar_collapsed is True
    for bad in ('"true"', "1", "0", "null", "[]", "{}"):
        p.write_text('{"toolbar_collapsed": ' + bad + "}", encoding="utf-8")
        assert load(p).toolbar_collapsed is False, bad


def test_saved_json_contains_toolbar_keys(tmp_path):
    p = tmp_path / "s.json"
    save(p, Settings(toolbar_position="right", toolbar_collapsed=True))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["toolbar_position"] == "right"
    assert data["toolbar_collapsed"] is True


def test_pressure_enabled_round_trip_and_bad_values(tmp_path):
    p = tmp_path / "settings.json"
    save(p, Settings(pressure_enabled=False))
    assert load(p).pressure_enabled is False
    for raw in ('{"pressure_enabled": "no"}', '{"pressure_enabled": 0}',
                '{"pressure_enabled": null}'):
        p.write_text(raw, encoding="utf-8")
        assert load(p).pressure_enabled is True      # 只认真正的布尔值


def test_new_preferences_round_trip(tmp_path):
    p = tmp_path / "settings.json"
    save(p, Settings(note_paper="light", close_to_tray=False,
                     close_prompt=False, open_export_folder=True))
    s = load(p)
    assert (s.note_paper, s.close_to_tray, s.close_prompt,
            s.open_export_folder) == ("light", False, False, True)
    p.write_text('{"note_paper": "blue", "close_to_tray": "no", '
                 '"close_prompt": "yes", "open_export_folder": 1}',
                 encoding="utf-8")
    s = load(p)
    assert (s.note_paper, s.close_to_tray, s.close_prompt,
            s.open_export_folder) == ("dark", True, True, False)


def test_unknown_fields_are_preserved_without_overriding_known(tmp_path):
    """开源扩展点：手写在 settings.json 里的未知字段保存时原样写回。"""
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({
        "color": "#2980b9",
        "my_plugin": {"enabled": True, "level": 3},
        "pressure_enabled": "bad",          # 已知字段的坏值仍按程序规则回默认
    }), encoding="utf-8")
    s = load(p)
    assert s.extra == {"my_plugin": {"enabled": True, "level": 3}}
    assert s.pressure_enabled is True

    save(p, s)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["my_plugin"] == {"enabled": True, "level": 3}
    assert data["pressure_enabled"] is True
    assert data["color"] == "#2980b9"
