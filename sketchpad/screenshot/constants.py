"""截图模块常量：工具、颜色、粗细、历史保留策略。

刻意与 `sketchpad.annotate.constants` 分开：批注层是「在屏幕上写」，
截图编辑器是「在图上标」，两者共用的只有颜色/粗细这类展示值。
"""
from __future__ import annotations

APP_TITLE = "截图"

# 框选小于这个尺寸当成误触
MIN_SELECTION = 4

# 选区手柄命中半径（编辑器/框选都按逻辑像素算）
HANDLE_RADIUS = 5

# 编辑器工具标识
TOOL_SELECT = "select"
TOOL_RECT = "rect"
TOOL_ELLIPSE = "ellipse"
TOOL_ARROW = "arrow"
TOOL_PEN = "pen"
TOOL_MARKER = "marker"
TOOL_TEXT = "text"
TOOL_NUMBER = "number"
TOOL_MOSAIC = "mosaic"
TOOL_BLUR = "blur"
TOOL_CROP = "crop"

SHAPE_TOOLS = (TOOL_RECT, TOOL_ELLIPSE, TOOL_ARROW)
FREEHAND_TOOLS = (TOOL_PEN, TOOL_MARKER)
REGION_EFFECT_TOOLS = (TOOL_MOSAIC, TOOL_BLUR)
DRAW_TOOLS = SHAPE_TOOLS + FREEHAND_TOOLS + REGION_EFFECT_TOOLS + (TOOL_CROP,)

ANNOTATION_COLORS = (
    ("红", "#ff3b30"),
    ("黄", "#ffcc00"),
    ("绿", "#34c759"),
    ("蓝", "#0a84ff"),
    ("黑", "#1c1c1e"),
    ("白", "#ffffff"),
)
DEFAULT_COLOR = "#ff3b30"

WIDTHS = (3, 6, 10)
DEFAULT_WIDTH = 3

TEXT_SIZE = 22                     # 像素；截图按物理像素导出，22 在 100% 屏上接近 16pt

NUMBER_RADIUS = 14
MARKER_ALPHA = 0.42

MOSAIC_BLOCK = 12
BLUR_FACTOR = 10                   # 先缩小到 1/10 再平滑放大，等价于便宜的高斯模糊

HISTORY_LIMIT = 100
HISTORY_DAYS = 30

CAPTURE_MODES = (
    ("region", "区域截图"),
    ("window", "窗口截图"),
    ("fullscreen", "全屏截图"),
    ("last", "上一区域"),
    ("color", "屏幕取色"),
)
