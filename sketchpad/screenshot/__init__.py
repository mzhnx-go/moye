"""截图工作流（PixPin 式）：冻结屏幕 → 选区内直接编辑 → 复制/保存/贴图/OCR/历史。

- `capture.py`：区域/窗口/全屏/上一区域抓屏与 DPI 换算
- `selection.py`：选区 8 向缩放/移动/钳制
- `overlay.py`：全屏 in-place 编辑器（生产路径）
- `session.py`：场景/工具/撤销/裁剪（无窗口）
- `items.py` / `scene.py` / `toolbar.py`：标注图元、按选区导出、工具栏
- `pin.py`：置顶贴图窗口
- `history.py` / `history_ui.py`：本地历史与列表窗口
- `ocr.py`：Standard 的 RapidOCR 适配（Lite 不可用）
- `controller.py`：应用接线、模式分派、主窗/批注层暂隐恢复
- `editor.py`：旧版独立编辑器（仅兼容/测试保留）
"""
from __future__ import annotations

from sketchpad.screenshot.controller import ScreenshotController, install

__all__ = ["ScreenshotController", "install"]
