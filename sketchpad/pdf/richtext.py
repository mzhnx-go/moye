"""富文本（粗体/斜体/下划线）的排版与光栅化：QTextDocument → 透明 PNG。

为什么单独成模块：PDF 引擎（`document.py`）不能 import UI 模块，而屏幕投影
（`ui/pdfview.py` 的 QGraphicsTextItem）与导出必须用**同一套**字体族/字号/
换行宽度，否则"导出的中文粗斜下划线和屏幕上不一致"。这里只依赖 QtGui，
不认识 `TextBlock`（鸭子类型：size/bold/italic/underline），也不读写文件。

坐标口径：所有入参/出参都是**设计坐标**（1 单位 = 1 PDF 点，与屏幕 100%
缩放一致）。排版按 1:1 像素做，光栅化时只把画笔放大 `dpi/72` 倍，所以换行
位置与屏幕逐行一致，不会因为放大而改变折行。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QPainter,
                           QTextCharFormat, QTextCursor, QTextDocument)

# 与 `sketchpad/ui/pdfview.py` 的 TEXT_FONT_FAMILY 保持一致（PDF 引擎不 import UI，
# 这里留一份副本；换字体时两处一起改）。不引入任何外部字体文件。
RICHTEXT_FONT_FAMILY = "Microsoft YaHei UI"
# 导出光栅化密度：约 300 dpi（1 PDF 点 = 1/72 英寸）
RICHTEXT_DPI = 300.0
_MIN_PIXELS = 1
_MAX_IMAGE_EDGE = 20000        # 手改 sidecar 可能给出超大框，避免一次分配几 GB


def text_font(size: float, bold: bool = False, italic: bool = False,
              underline: bool = False) -> QFont:
    """整段文字的字体：像素字号取整（与 QGraphicsTextItem 投影完全一致）。"""
    font = QFont(RICHTEXT_FONT_FAMILY)
    font.setPixelSize(max(1, int(round(float(size)))))
    font.setBold(bool(bold))
    font.setItalic(bool(italic))
    font.setUnderline(bool(underline))
    return font


def block_font(block) -> QFont:
    """`TextBlock` → QFont；屏幕投影与导出共用，UI 接入时直接调这个。

    鸭子类型：只读 size/bold/italic/underline，缺字段按默认值，不 import
    模型层，避免 pdf ↔ core 互相牵扯。
    """
    return text_font(getattr(block, "size", 16.0),
                     getattr(block, "bold", False),
                     getattr(block, "italic", False),
                     getattr(block, "underline", False))


def build_document(text: str, *, width: float, size: float, color: str,
                   bold: bool = False, italic: bool = False,
                   underline: bool = False) -> QTextDocument:
    """按屏幕同一套规则排版：margin 0、像素字号、限宽换行、整段前景色。"""
    document = QTextDocument()
    document.setDocumentMargin(0.0)          # 与导出/屏幕的文字起点对齐
    document.setDefaultFont(text_font(size, bold, italic, underline))
    document.setPlainText(str(text))
    # QTextDocument 没有公开的 setDefaultTextColor（Qt6），用整段 char format 上色
    char_format = QTextCharFormat()
    char_format.setForeground(QColor(str(color)))
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.Document)
    cursor.mergeCharFormat(char_format)
    document.setTextWidth(max(1.0, float(width)))
    return document


def render_block_png(
    text: str,
    *,
    width: float,
    size: float,
    color: str,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    max_height: float | None = None,
    dpi: float = RICHTEXT_DPI,
) -> tuple[bytes, float] | None:
    """把一段文字排成透明 PNG；返回 `(png 字节, 设计坐标高度)`。

    - `width` / `size` / 返回的高度都是设计坐标（PDF 点）；宽度决定换行，
      高度是排版自然高度（可选被 `max_height` 截断，避免溢出页面后拉伸）。
    - 透明背景 + 文字用 `color`；PNG 由 PyMuPDF 按 alpha 铺回页面。
    - 不可渲染时返回 None（没有 QGuiApplication、空文字、字号/宽度非法、
      Qt 抛错都算）：调用方回退到可搜索的纯文本导出，绝不让导出失败。
    """
    if not text or QGuiApplication.instance() is None:
        return None
    try:
        width = float(width)
        size = float(size)
        scale = max(1.0, float(dpi) / 72.0)
        if width <= 0.0 or size <= 0.0 or not math.isfinite(width * size):
            return None
        document = build_document(text, width=width, size=size, color=color,
                                  bold=bold, italic=italic, underline=underline)
        height = float(document.size().height())
        if max_height is not None:
            height = min(height, float(max_height))
        if height <= 0.0 or not math.isfinite(height):
            return None
        pixel_w = min(_MAX_IMAGE_EDGE, max(_MIN_PIXELS, int(math.ceil(width * scale))))
        pixel_h = min(_MAX_IMAGE_EDGE, max(_MIN_PIXELS, int(math.ceil(height * scale))))
        image = QImage(pixel_w, pixel_h, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.scale(scale, scale)      # 排版仍是 1:1，只放大光栅化
            document.drawContents(painter, QRectF(0.0, 0.0, width, height))
        finally:
            painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        try:
            if not image.save(buffer, "PNG"):
                return None
            data = bytes(buffer.data())
        finally:
            buffer.close()
    except Exception:
        return None
    if not data:
        return None
    return data, height
