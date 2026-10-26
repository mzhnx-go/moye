"""批注层自己的配置：`data/annotate.json`（并入 PageFlow 后独立成文件）。

为什么不塞进 `sketchpad/core/settings.py`：那个 `Settings` dataclass 是 PageFlow 的核心
配置，协作者正在改它。批注层的配置自成一体，写自己的文件，谁都不用等谁。

只保留两个热键、笔色、线宽、工具栏排布与位置；旧文件里多出来的键会被忽略，
不报错也不迁移。文件路径必须由调用方给（PageFlow 的 data 目录跟着 exe 走），
包里不写死任何绝对路径。
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sketchpad.annotate.constants import (DEFAULT_CAPTURE_HOTKEY,
                                  DEFAULT_CLEAR_HOTKEY, DEFAULT_COLOR,
                                  DEFAULT_ERASER_HOTKEY, DEFAULT_HIDE_HOTKEY,
                                  DEFAULT_PASSTHROUGH_HOTKEY, DEFAULT_PEN_HOTKEY,
                                  DEFAULT_TOGGLE_HOTKEY, PEN_WIDTH)

ORIENTATIONS = ("horizontal", "vertical")
CONFIG_NAME = "annotate.json"          # 落在 PageFlow 的 data 目录下
STORAGE_SUBDIR = "annotations"         # 导出的 PNG 放 data/annotations/


@dataclass
class Config:
    toggle_hotkey: str = DEFAULT_TOGGLE_HOTKEY
    hide_hotkey: str = DEFAULT_HIDE_HOTKEY
    passthrough_hotkey: str = DEFAULT_PASSTHROUGH_HOTKEY
    pen_hotkey: str = DEFAULT_PEN_HOTKEY
    eraser_hotkey: str = DEFAULT_ERASER_HOTKEY
    clear_hotkey: str = DEFAULT_CLEAR_HOTKEY
    capture_hotkey: str = DEFAULT_CAPTURE_HOTKEY
    color: str = DEFAULT_COLOR
    pen_width: float = PEN_WIDTH
    # 工具栏排布与位置：orientation 决定横排/竖排，pos 为 None 表示用默认位置
    toolbar_orientation: str = "horizontal"
    toolbar_pos: list | None = None
    # 导出目录，不参与序列化、不参与相等比较；由 load() 按配置路径推导
    storage_dir: Path | None = field(default=None, compare=False, repr=False)
    # 这份配置是从哪个文件读出来的；save() 不带参数时写回同一文件
    config_path: Path | None = field(default=None, compare=False, repr=False)

    @classmethod
    def load(cls, path: Path) -> "Config":
        """读配置；文件不存在或损坏都用默认值，绝不因为配置坏掉起不来。"""
        config = cls()
        # 导出目录只跟配置文件的位置走：文件不存在也要能导出，所以在读文件之前就定
        config.storage_dir = Path(path).parent / STORAGE_SUBDIR
        config.config_path = Path(path)
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return config
        if not isinstance(raw, dict):
            return config
        if isinstance(raw.get("toggle_hotkey"), str) and raw["toggle_hotkey"].strip():
            config.toggle_hotkey = raw["toggle_hotkey"].strip()
        if isinstance(raw.get("hide_hotkey"), str) and raw["hide_hotkey"].strip():
            config.hide_hotkey = raw["hide_hotkey"].strip()
        if isinstance(raw.get("capture_hotkey"), str) and raw["capture_hotkey"].strip():
            config.capture_hotkey = raw["capture_hotkey"].strip()
        for name in ("passthrough_hotkey", "pen_hotkey", "eraser_hotkey",
                     "clear_hotkey"):
            value = raw.get(name)
            if isinstance(value, str) and value.strip():
                setattr(config, name, value.strip())
        if isinstance(raw.get("color"), str) and raw["color"].startswith("#"):
            config.color = raw["color"]
        width = raw.get("pen_width")
        # bool 也是 int，先排除；再夹到合法档位范围，坏值退回默认
        if isinstance(width, (int, float)) and not isinstance(width, bool):
            width = float(width)
            if math.isfinite(width) and width > 0:
                config.pen_width = width
        orientation = raw.get("toolbar_orientation")
        if isinstance(orientation, str) and orientation in ORIENTATIONS:
            config.toolbar_orientation = orientation
        pos = raw.get("toolbar_pos")
        # 位置必须是两个有限数；坏数据当没设过，回到默认位置
        if (isinstance(pos, (list, tuple)) and len(pos) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        for v in pos)):
            x, y = float(pos[0]), float(pos[1])
            if math.isfinite(x) and math.isfinite(y):
                config.toolbar_pos = [int(x), int(y)]
        return config

    def save(self, path: Path | None = None) -> bool:
        """立即落盘（tmp + replace）；不传 path 时写回 load() 的那份文件。"""
        path = Path(path) if path is not None else self.config_path
        if path is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            payload = asdict(self)
            payload.pop("storage_dir", None)          # 运行时字段，不回写
            payload.pop("config_path", None)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(path)
            return True
        except OSError:
            return False
