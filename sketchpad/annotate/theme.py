"""工具栏主题：深灰外壳 + 纯图标按钮，只按 objectName 生效。

为什么不做全局 QSS：批注层是逐像素透明的窗口，一旦被全局样式刷上不透明底色，
视频画面就被盖住了。这里只提供一段样式字符串，由 Toolbar setStyleSheet 到自己的
根节点，作用域仅限那棵子树；外壳圆角由 Toolbar.paintEvent 自绘（顶层半透明窗
的根背景在部分合成路径下会丢，自绘才确定可见）。

选中态统一用亮绿（ACCENT）：穿透 / 钢笔 / 橡皮 三选一，粗细档位也共用同一个
颜色，眼睛只要认"哪块是绿的"。绿底上的图标必须换深色版（icons.ink_color）。
色块走自己的白边选中，不受这条规则影响。
"""
from __future__ import annotations

from PySide6.QtGui import QColor

from sketchpad.annotate.constants import ACCENT, SHELL, SHELL_BORDER

# 与默认画笔同色的浅色文字；深灰底上对比度足够
TEXT = "#e8eaed"
# 选中实心底（亮绿）上的文字 / 图标必须是深色才读得清
TEXT_ON_ACCENT = "#0d1a16"
_ACCENT = QColor(ACCENT)
ACCENT_TINT = (f"rgba({_ACCENT.red()}, {_ACCENT.green()}, "
               f"{_ACCENT.blue()}, 18%)")
ACCENT_TINT_PRESSED = (f"rgba({_ACCENT.red()}, {_ACCENT.green()}, "
                       f"{_ACCENT.blue()}, 30%)")
ACCENT_HOVER = "#5fe0b9"         # 已选中按钮再悬停时略提亮，仍有反馈
ACCENT_PRESSED = _ACCENT.darker(112).name()
# 未选中图标按钮的悬停 / 按下：极淡的白，深灰壳上只做“可点”提示
ICON_HOVER = "rgba(255, 255, 255, 10%)"
ICON_PRESSED = "rgba(255, 255, 255, 18%)"
SEPARATOR = "#5a5a5e"             # 分组竖线：比外壳亮两档，粗细组 / 色盘才分得开

TOOLBAR_RADIUS = 9
BUTTON_RADIUS = 6

# tooltip 规则写成深底浅字：系统默认白底在透明批注层上会“闪光”
_TOOLTIP_QSS = f"""
QToolTip {{
    background: {SHELL};
    color: {TEXT};
    border: 1px solid {SHELL_BORDER};
    border-radius: 5px;
    padding: 4px 7px;
    font-size: 12px;
}}
"""


def qcolor(value: str, alpha: float | None = None) -> QColor:
    """字符串颜色 -> QColor；alpha 用 0…1，方便需要半透明绘制的调用方。"""
    color = QColor(value)
    if alpha is not None:
        color.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return color


TOOLBAR_QSS = f"""
/* 外壳圆角与底色由 Toolbar.paintEvent 自绘，这里不再写 background/border */
/* 图标按钮：平时透明底，悬停只加淡白，不跟蓝色选中态抢眼 */
QWidget#ToolbarRoot QToolButton {{
    background: transparent;
    border: none;
    border-radius: {BUTTON_RADIUS}px;
    padding: 0;
    margin: 0;
    outline: none;
}}
QWidget#ToolbarRoot QToolButton:hover {{
    background: {ICON_HOVER};
}}
QWidget#ToolbarRoot QToolButton:pressed {{
    background: {ICON_PRESSED};
}}
/* 选中：亮绿实心块——穿透 / 钢笔 / 橡皮 三选一共用同一个标识色 */
QWidget#ToolbarRoot QToolButton:checked {{
    background: {ACCENT};
    border-radius: {BUTTON_RADIUS}px;
}}
QWidget#ToolbarRoot QToolButton:checked:hover {{
    background: {ACCENT_HOVER};
}}
QWidget#ToolbarRoot QToolButton:checked:pressed {{
    background: {ACCENT_PRESSED};
}}
/* 不可用（撤销无历史）：留位置、去掉底，图标由 Qt 自动画成灰态 */
QWidget#ToolbarRoot QToolButton:disabled {{
    background: transparent;
}}
/* 分组竖线：1px 宽的小方块，颜色比外壳亮一档 */
QFrame#Sep {{
    background: {SEPARATOR};
    border: none;
    border-radius: 0px;
}}
/* 自定义粗细滑杆：沿用工具栏的绿强调色，横/竖排各一套 */
QWidget#ToolbarRoot QSlider#widthSlider {{
    background: transparent;
    outline: none;
}}
QWidget#ToolbarRoot QSlider#widthSlider::groove:horizontal {{
    height: 4px;
    background: {SHELL_BORDER};
    border-radius: 2px;
}}
QWidget#ToolbarRoot QSlider#widthSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QWidget#ToolbarRoot QSlider#widthSlider::handle:horizontal {{
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    background: #ffffff;
}}
QWidget#ToolbarRoot QSlider#widthSlider::groove:vertical {{
    width: 4px;
    background: {SHELL_BORDER};
    border-radius: 2px;
}}
QWidget#ToolbarRoot QSlider#widthSlider::sub-page:vertical {{
    background: {ACCENT};
    border-radius: 2px;
}}
QWidget#ToolbarRoot QSlider#widthSlider::handle:vertical {{
    width: 12px;
    height: 12px;
    margin: 0 -4px;
    border-radius: 6px;
    background: #ffffff;
}}
{_TOOLTIP_QSS}"""
