"""PDF 底图阅读模式：正常 / 夜间 / 护眼（只改底图位图，笔迹是独立场景项不受影响）。

夜间：反相后 `Plus` 抬底、`Multiply` 压字，底色是深灰而不是纯黑，字是浅灰而不是
纯白——降低极端对比度，避免暗房里白字刺眼（NN/g、Atmos 的深色模式建议）。

护眼：**不反相**。直接在原图上叠一层低蓝光的暖色半透明滤镜，得到羊皮纸底与暖
深灰字，同时保留彩色高亮的色相；这符合"护眼模式=降低蓝光/色温变暖，而非反色"
的通行做法（WPS PDF 的羊皮纸、手机护眼模式的滤蓝光口径）。

所有填充都在 Qt 的 C++ 光栅层完成，1–2MP 的页面耗时在毫秒级，不需要逐像素循环。
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QImage, QPainter

from sketchpad.core.settings import PAGE_MODES

PAGE_MODE_LABELS = {"normal": "正常", "night": "夜间", "eye": "护眼"}

# 夜间：反相后的白底是纯黑、黑字是纯白；Plus 把黑位抬到目标底色，Multiply 把白位压到目标字色
_NIGHT_LIFT = "#262626"     # 中性深灰底（不是纯黑，暗房里不凿眼）
_NIGHT_TINT = "#dadada"     # 浅灰字（不是纯白）
# 护眼：不反相，直接叠暖色低蓝光滤镜（白底→羊皮纸、黑字→暖深灰）
_EYE_TINT = QColor(216, 195, 160, 77)   # #d8c3a0 @ 30%


def apply_page_mode(image: QImage, mode: str) -> QImage:
    """按阅读模式处理一张页面底图；normal/未知模式原样返回。

    只处理底图：笔迹、搜索高亮、选中文字都画在页面项之上，颜色不受影响。
    """
    if mode == "night":
        out = image.convertToFormat(QImage.Format_RGB32)
        out.invertPixels(QImage.InvertRgb)
        painter = QPainter(out)
        try:
            painter.setCompositionMode(QPainter.CompositionMode_Plus)
            painter.fillRect(out.rect(), QColor(_NIGHT_LIFT))
            painter.setCompositionMode(QPainter.CompositionMode_Multiply)
            painter.fillRect(out.rect(), QColor(_NIGHT_TINT))
        finally:
            painter.end()
        return out
    if mode == "eye":
        out = image.convertToFormat(QImage.Format_RGB32)
        painter = QPainter(out)
        try:
            painter.fillRect(out.rect(), _EYE_TINT)   # SourceOver：降低对比度并滤蓝
        finally:
            painter.end()
        return out
    return image
