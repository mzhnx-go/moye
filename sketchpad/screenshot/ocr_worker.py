"""OCR 后台线程：识别放到 QThread，避免 RapidOCR 初始化/推理冻住截图遮罩。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

# 遮罩关闭后 worker 仍可能跑完；用模块级集合保证 QThread 不会被 Python 回收
_LIVE_WORKERS: set["OcrWorker"] = set()


class OcrWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, image: QImage, func) -> None:
        super().__init__()
        self._image = image.copy()
        self._func = func

    def run(self) -> None:
        try:
            text = self._func(self._image)
        except Exception as exc:              # 引擎/依赖错误统一回到 UI 线程提示
            self.failed.emit(str(exc))
            return
        self.done.emit(text or "")


def start_ocr(image: QImage, func, on_done, on_failed) -> OcrWorker:
    """启动一个 OCR worker；调用方必须立即返回，不要在这里等。"""
    worker = OcrWorker(image, func)
    _LIVE_WORKERS.add(worker)
    worker.done.connect(on_done)
    worker.failed.connect(on_failed)

    def _cleanup() -> None:
        _LIVE_WORKERS.discard(worker)
        worker.deleteLater()

    worker.finished.connect(_cleanup)
    worker.start()
    return worker
