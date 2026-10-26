"""纯图像操作：马赛克、模糊、裁剪、取色、另存。

全部接收/返回 QImage，不碰窗口状态，方便离屏测试。裁剪/效果都按物理像素坐标
处理——调用方在入口统一把设备像素比归 1，避免 150% 屏上坐标差 1.5 倍。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage

from sketchpad.screenshot.constants import BLUR_FACTOR, MOSAIC_BLOCK


def normalize(image: QImage) -> QImage:
    """拷贝一份并把 DPR 归 1：场景坐标 = 图像像素坐标。"""
    copy = image.copy()
    copy.setDevicePixelRatio(1.0)
    return copy


def crop(image: QImage, rect: QRect) -> QImage:
    rect = rect.intersected(image.rect())
    if rect.isEmpty():
        return QImage()
    return image.copy(rect)


def _region_slice(image: QImage, rect: QRect) -> tuple[QRect, QImage] | None:
    rect = rect.intersected(image.rect())
    if rect.width() < 2 or rect.height() < 2:
        return None
    return rect, image.copy(rect)


def pixelate(image: QImage, rect: QRect, block: int = MOSAIC_BLOCK) -> QImage:
    """选中区域打成马赛克；返回与 rect 等大的小图，调用方贴回原位。"""
    sliced = _region_slice(image, rect)
    if sliced is None:
        return QImage()
    rect, region = sliced
    block = max(2, int(block))
    small_w = max(1, rect.width() // block)
    small_h = max(1, rect.height() // block)
    small = region.scaled(small_w, small_h, Qt.IgnoreAspectRatio,
                          Qt.FastTransformation)
    return small.scaled(rect.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation)


def blur_region(image: QImage, rect: QRect,
                factor: int = BLUR_FACTOR) -> QImage:
    """用「缩小再平滑放大」近似高斯模糊，避免逐像素卷积卡住 UI。"""
    sliced = _region_slice(image, rect)
    if sliced is None:
        return QImage()
    rect, region = sliced
    factor = max(2, int(factor))
    small_w = max(1, rect.width() // factor)
    small_h = max(1, rect.height() // factor)
    small = region.scaled(small_w, small_h, Qt.IgnoreAspectRatio,
                          Qt.SmoothTransformation)
    return small.scaled(rect.size(), Qt.IgnoreAspectRatio,
                        Qt.SmoothTransformation)


def hex_at(image: QImage, x: int, y: int) -> str | None:
    """屏幕像素颜色；越界返回 None。"""
    if image is None or image.isNull():
        return None
    if not (0 <= x < image.width() and 0 <= y < image.height()):
        return None
    return QColor(image.pixel(x, y)).name().upper()


def rgb_at(image: QImage, x: int, y: int) -> tuple[int, int, int] | None:
    if image is None or image.isNull():
        return None
    if not (0 <= x < image.width() and 0 <= y < image.height()):
        return None
    color = QColor(image.pixel(x, y))
    return color.red(), color.green(), color.blue()


def save_image(image: QImage, path: str | Path) -> bool:
    """按扩展名选格式；JPEG 没有透明通道，先铺白底再存。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        canvas = QImage(image.size(), QImage.Format_RGB32)
        canvas.fill(Qt.white)
        from PySide6.QtGui import QPainter
        painter = QPainter(canvas)
        painter.drawImage(0, 0, image)
        painter.end()
        return canvas.save(str(path), "JPG")
    if suffix == ".webp":
        return image.save(str(path), "WEBP")
    return image.save(str(path), "PNG")
