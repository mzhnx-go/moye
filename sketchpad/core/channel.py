"""应用标识：单一产品「墨页」；源码与打包版使用同一个名字、配置目录和批注文件。

配置固定写 `data/`，批注仍写 `.sketch.json`；多开时只有第一个实例注册托盘/
全局热键/全屏批注，见 `services/single_instance.py`。
"""
from __future__ import annotations

APP_NAME = "墨页"
APP_ID = "Moye"
DATA_DIR = "data"
