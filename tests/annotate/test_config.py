"""批注层自己的配置（data/annotate.json）：热键 + 笔色 + 线宽 + 工具栏排布。

并入 PageFlow 后刻意不并进 `sketchpad/core/settings.py`——那边有协作者在改；
所以这里连路径都由调用方给，测试里一律显式传 tmp_path。
"""
import dataclasses
import json

from sketchpad.annotate.config import CONFIG_NAME, Config
from sketchpad.annotate.constants import (DEFAULT_CAPTURE_HOTKEY, DEFAULT_CLEAR_HOTKEY,
                                  DEFAULT_COLOR, DEFAULT_ERASER_HOTKEY,
                                  DEFAULT_HIDE_HOTKEY, DEFAULT_PASSTHROUGH_HOTKEY,
                                  DEFAULT_PEN_HOTKEY, DEFAULT_TOGGLE_HOTKEY,
                                  PEN_WIDTH)


def _path(tmp_path, name=CONFIG_NAME):
    return tmp_path / name


def test_默认键且笔色线宽取默认():
    config = Config()
    assert config.toggle_hotkey == "Ctrl+Alt+D" == DEFAULT_TOGGLE_HOTKEY
    assert config.hide_hotkey == "Ctrl+Alt+H" == DEFAULT_HIDE_HOTKEY
    assert config.capture_hotkey == "Alt+A+S" == DEFAULT_CAPTURE_HOTKEY
    assert config.passthrough_hotkey == "Alt+1" == DEFAULT_PASSTHROUGH_HOTKEY
    assert config.pen_hotkey == "Alt+2" == DEFAULT_PEN_HOTKEY
    assert config.eraser_hotkey == "Alt+3" == DEFAULT_ERASER_HOTKEY
    assert config.clear_hotkey == "Alt+4" == DEFAULT_CLEAR_HOTKEY
    assert config.color == DEFAULT_COLOR
    assert config.pen_width == PEN_WIDTH
    assert config.toolbar_orientation == "horizontal"
    assert config.toolbar_pos is None
    assert config.storage_dir is None              # 运行时字段，默认没有
    assert config.config_path is None
    assert {f.name for f in dataclasses.fields(Config)} == {
        "toggle_hotkey",
        "hide_hotkey",
        "passthrough_hotkey",
        "pen_hotkey",
        "eraser_hotkey",
        "clear_hotkey",
        "capture_hotkey",
        "color",
        "pen_width",
        "toolbar_orientation",
        "toolbar_pos",
        "storage_dir",
        "config_path",
    }


def test_round_trip七个键且storage_dir不落盘(tmp_path):
    path = _path(tmp_path)
    config = Config(toggle_hotkey="Alt+F9", hide_hotkey="Ctrl+Shift+H",
                    capture_hotkey="Alt+F10", color="#123456", pen_width=7.0,
                    toolbar_orientation="vertical", toolbar_pos=[120, 260])
    config.save(path)

    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"toggle_hotkey", "hide_hotkey",
                            "passthrough_hotkey", "pen_hotkey", "eraser_hotkey",
                            "clear_hotkey", "capture_hotkey",
                            "color", "pen_width",
                            "toolbar_orientation", "toolbar_pos"}
    assert payload["color"] == "#123456"
    assert payload["pen_width"] == 7.0
    assert payload["toolbar_orientation"] == "vertical"
    assert payload["toolbar_pos"] == [120, 260]
    assert Config.load(path) == config


def test_load按配置路径推导导出目录(tmp_path):
    path = _path(tmp_path)
    loaded = Config.load(path)
    assert loaded.storage_dir == tmp_path / "annotations"


def test_save写的临时文件不带storage_dir(tmp_path):
    path = _path(tmp_path)
    Config().save(path)
    assert "storage_dir" not in json.loads(path.read_text(encoding="utf-8"))


def test_工具栏方向与位置的坏值回退(tmp_path):
    bad_values = [
        ({"toolbar_orientation": "diagonal"}, "horizontal"),
        ({"toolbar_orientation": 3}, "horizontal"),
        ({"toolbar_pos": [1, 2, 3]}, None),
        ({"toolbar_pos": ["a", "b"]}, None),
        ({"toolbar_pos": "12,34"}, None),
        ({"toolbar_pos": [float("inf"), 0]}, None),
        ({"toolbar_pos": [True, False]}, None),
    ]
    for payload, expected in bad_values:
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = Config.load(path)
        if isinstance(expected, str):
            assert loaded.toolbar_orientation == expected, payload
        else:
            assert loaded.toolbar_pos is expected, payload


def test_工具栏位置浮点会被取整(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"toolbar_pos": [12.7, 40.2]}), encoding="utf-8")
    assert Config.load(path).toolbar_pos == [12, 40]


def test_线宽坏值回退默认(tmp_path):
    """字符串 / 布尔 / 非正数都不认，一律回退默认线宽，别把笔迹画成一条边。"""
    for bad in ("7", True, 0, -3, None, "abc"):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"pen_width": bad}), encoding="utf-8")
        assert Config.load(path).pen_width == PEN_WIDTH, bad


def test_save自动建父目录(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    config = Config(toggle_hotkey="Alt+F9")
    config.save(path)

    assert path.exists()
    assert Config.load(path).toggle_hotkey == "Alt+F9"


def test_旧文件多余键被忽略且不报错(tmp_path):
    path = tmp_path / "settings.json"
    payload = {
        "toggle_hotkey": "Alt+F9",
        "hide_hotkey": "Ctrl+Shift+H",
        "color": "#ff453a",
        "highlighter_color": "#f5c542",
        "width": 8.0,
        "smoothing": "medium",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = Config.load(path)
    assert loaded == Config(toggle_hotkey="Alt+F9",
                            hide_hotkey="Ctrl+Shift+H",
                            color="#ff453a")
    for removed in ("highlighter_color", "width", "smoothing"):
        assert not hasattr(loaded, removed)


def test_坏json回退默认(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"toggle_hotkey": "Ctrl+Alt+D", ', encoding="utf-8")
    assert Config.load(path) == Config()


def test_非对象json回退默认(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert Config.load(path) == Config()


def test_文件不存在回退默认(tmp_path):
    assert Config.load(tmp_path / "missing.json") == Config()


def test_只提供部分键时其余取默认(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"hide_hotkey": "Alt+F10"}), encoding="utf-8")

    loaded = Config.load(path)
    assert loaded.toggle_hotkey == DEFAULT_TOGGLE_HOTKEY
    assert loaded.hide_hotkey == "Alt+F10"
    assert loaded.color == DEFAULT_COLOR


def test_空串与非字符串热键回退默认(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"toggle_hotkey": "   ", "hide_hotkey": 123}),
        encoding="utf-8",
    )
    assert Config.load(path) == Config()
