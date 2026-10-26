"""OCR 适配：复用 RapidOCR，不新增外部程序。

源码/打包环境装了 rapidocr 就可用；没装时编辑器不显示 OCR 按钮。
"""
from __future__ import annotations

import importlib.util
import threading

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage


class OCRError(RuntimeError):
    pass


_engine = None
_engine_lock = threading.Lock()


def ocr_available() -> bool:
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def _get_engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OCRError("缺少 OCR 组件（rapidocr-onnxruntime）") from exc
        _engine = RapidOCR()
    return _engine


def recognize(image: QImage) -> str:
    """识别整张图；返回按行拼接的纯文本。失败抛 OCRError。"""
    if image is None or image.isNull():
        raise OCRError("没有可识别的图片")
    if not ocr_available():
        raise OCRError("OCR 只在标准版可用")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OCRError("截图编码失败")
    data = bytes(buffer.data())
    buffer.close()
    try:
        with _engine_lock:            # RapidOCR/onnxruntime 不做并发推理
            result, _elapsed = _get_engine()(data)
    except Exception as exc:
        raise OCRError(f"OCR 引擎出错：{exc}") from exc
    lines = [str(item[1]) for item in (result or []) if len(item) >= 2]
    return "\n".join(lines)
