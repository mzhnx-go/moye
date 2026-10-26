"""全局常量：颜色、笔宽、热键默认值。UI 与画布共用，避免两处各写一套。"""
from __future__ import annotations

APP_NAME = "墨页批注"

DEFAULT_COLOR = "#e8eaed"          # 深色画面上可见的浅色笔
# 粗细四档（屏幕像素），对应工具栏上四个由小到大的圆点；画布与工具栏共用
PEN_WIDTHS = (2.0, 4.0, 7.0, 11.0)
PEN_WIDTH = 4.0                    # 默认线宽，也是 PEN_WIDTHS 里的第二档
PEN_WIDTH_MIN = 1.0                # 自定义粗细下限（工具栏数字输入/滑杆）
PEN_WIDTH_MAX = 30.0               # 自定义粗细上限
PALETTE = ("#ff453a", "#2980b9", "#f1c40f", "#e8eaed", "#d35400",
           "#d4a017", "#27ae60", "#8e44ad", "#7f8c8d")
PALETTE_NAMES = ("红", "蓝", "黄", "白", "橙", "深黄", "绿", "紫", "灰")

DEFAULT_TOGGLE_HOTKEY = "Ctrl+Alt+D"   # 批注 <-> 穿透
DEFAULT_HIDE_HOTKEY = "Ctrl+Alt+H"     # 显示 <-> 隐藏整层
DEFAULT_PASSTHROUGH_HOTKEY = "Alt+1"   # 穿透（点击落到底层软件）
DEFAULT_PEN_HOTKEY = "Alt+2"           # 钢笔
DEFAULT_ERASER_HOTKEY = "Alt+3"        # 橡皮
DEFAULT_CLEAR_HOTKEY = "Alt+4"         # 全屏清空
DEFAULT_CAPTURE_HOTKEY = "Alt+A+S"      # 全局两步截图：Alt+A 后 1.5s 内按 S

TOOLBAR_HEIGHT = 40            # 工具栏厚度（横排是高、竖排是宽）：够放下 30 的按钮
TOOLBAR_MARGIN_TOP = 12

SHELL = "#1c1c1e"
SHELL_BORDER = "#3a3a3c"
MUTED = "#8e8e93"
ACCENT = "#4dd4ac"
TOOL_ACCENT = "#1a73e8"            # 工具栏选中态：参考截图里的蓝色方块
