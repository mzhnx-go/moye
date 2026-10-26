"""异步全文搜索：QThread worker + 取消标志；不碰 UI 线程的 PdfSource。

长文档全文搜索（真实 996 页讲义实测 1.26s）不能跑在 UI 线程上，否则界面
冻结。worker 按路径自己开一份只读 `PdfSource`——PyMuPDF 文档不能跨线程
共享，视图那边还在 UI 线程用它渲染页——逐页调用 `search_page`，命中通过
Qt 信号排队回主线程；取消走 `threading.Event`，页与页之间检查，新查询
一到旧 worker 立刻停手。

命中语义与 `PdfSource.search_document` 一致：最多 `MAX_HITS` 条；用多扫
一条的办法判断是否封顶，超限截断并把 truncated 置真。取消后不发 finished
（只发 cancelled）；调用方按 generation 忽略迟到结果。
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from sketchpad.pdf.document import PdfSource

MAX_HITS = 500          # 与 app 层原有的 500 条封顶一致（P2-5 的 "+" 提示）


class PdfSearchWorker(QThread):
    """后台全文搜索线程。

    信号：
    - `progress(done, total)`：已扫页数/总页数。打开后先发 0/total，每扫完
      一页 +1；到 500 封顶提前停扫时进度停在断点。
    - `finished(generation, hits, truncated)`：扫完交结果。hits 是
      `[(0 起页码, (x0, y0, x1, y1))]`，最多 MAX_HITS 条；打不开/中途出错
      收成空结果（与同步版 `_find_search` 的容错口径一致）。
    - `cancelled(generation)`：被取消，不发 finished。

    不能复用 UI 线程的 PdfSource：PyMuPDF 文档对象不是线程安全的，跨线程
    用会和视图渲染互相踩。这里只按路径拿一份自己的只读句柄。
    """

    progress = Signal(int, int)              # done, total
    finished = Signal(int, object, bool)     # generation, hits, truncated
    cancelled = Signal(int)                  # generation

    def __init__(self, generation: int, path: Path, text: str,
                 max_hits: int = MAX_HITS, parent=None) -> None:
        super().__init__(parent)
        self.generation = generation
        self._path = Path(path)
        self._text = text
        self._max_hits = max_hits
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """请求取消；run 在页与页之间检查，最多多扫完当前一页就停。"""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def run(self) -> None:
        """Qt 线程入口；一切异常都在这里收口，绝不逃出线程。"""
        hits: list[tuple[int, tuple[float, float, float, float]]] = []
        truncated = False
        source: PdfSource | None = None
        try:
            if not self._cancelled.is_set():
                source = PdfSource(self._path)
                total = source.page_count
                self.progress.emit(0, total)
                for index in range(total):
                    if self._cancelled.is_set():
                        break
                    for rect in source.search_page(index, self._text):
                        hits.append((index, rect))
                        if len(hits) > self._max_hits:    # 多取一条判断是否封顶
                            truncated = True
                            break
                    if truncated:
                        break
                    self.progress.emit(index + 1, total)
        except Exception:
            # 文件被删/损坏等：按无命中收尾，调用方只看到 0/0
            hits, truncated = [], False
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass
        if self._cancelled.is_set():
            self.cancelled.emit(self.generation)
            return
        if truncated:
            hits = hits[: self._max_hits]
        # 显式 emit：子类把 QThread 自带的 finished() 换成了这个三参版本，
        # 这是完成任务后唯一一次收尾信号（所有正常路径都会走到这里）
        self.finished.emit(self.generation, hits, truncated)