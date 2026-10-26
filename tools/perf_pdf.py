"""PDF 批注模式性能基准（v2 设计 §7 验收线的测量工具）。

造一页 N 条 × M 点的批注文档，在真实窗口里测单帧耗时：viewport().grab()
走的是与真实帧同一条光栅化路径（离屏平台的数字偏悲观，不作数，见 v1 教训）。
只写系统临时目录，绝不碰 data/；本工具恒退 0（诊断用途，失败会打出来）。

用法：
    .venv\\Scripts\\python.exe tools/perf_pdf.py [--strokes 500] [--points 100]
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:      # 直接 python tools/perf_pdf.py 时也能 import app
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QElapsedTimer
from PySide6.QtWidgets import QApplication, QGraphicsPathItem

from sketchpad.core import pdfstore
from sketchpad.pdf.document import PdfSource
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.ui.pdfview import PdfView
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Stroke

PALETTE = ("#e8eaed", "#ff453a", "#ff9f0a", "#ffd60a",
           "#32d74b", "#0a84ff", "#bf5af2", "#ff375f")
PAGE_W, PAGE_H = 595.0, 842.0        # A4 竖版（PDF 点）
LINE_MS = 20.0                       # v2 设计 §7 验收线：单帧 ≤ 20ms


def _make_pdf(path: Path) -> None:
    import pymupdf
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    doc.save(str(path))
    doc.close()


def _make_scribble(strokes: int, points: int, seed: int = 7) -> list[Stroke]:
    """一页密集涂写：随机游走，步长 1–3 点，颜色/粗细随机。"""
    rng = random.Random(seed)
    out: list[Stroke] = []
    for _ in range(strokes):
        x = rng.uniform(20.0, PAGE_W - 20.0)
        y = rng.uniform(20.0, PAGE_H - 20.0)
        pts: list[tuple[float, float]] = []
        for _ in range(points):
            pts.append((x, y))
            x = min(max(x + rng.uniform(-3.0, 3.0), 5.0), PAGE_W - 5.0)
            y = min(max(y + rng.uniform(-3.0, 3.0), 5.0), PAGE_H - 5.0)
        out.append(Stroke(pts, rng.choice(PALETTE), rng.choice((1.5, 3.0, 6.0, 12.0))))
    return out


def _timed_grabs(view: PdfView, rounds: int) -> list[float]:
    """连续抓帧计时（毫秒）。"""
    out: list[float] = []
    for _ in range(rounds):
        timer = QElapsedTimer()
        timer.start()
        view.viewport().grab()
        out.append(timer.nsecsElapsed() / 1_000_000.0)
    return out


def _pump(app, ms: int) -> None:
    """处理事件直到过了 ms 毫秒（等去抖渲染/自适应落地）。"""
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()


def _report(name: str, samples: list[float]) -> bool:
    if not samples:
        print(f"{name:<22} 无样本")
        return False
    mean = statistics.mean(samples)
    p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
    worst = max(samples)
    ok = mean <= LINE_MS
    mark = "达标" if ok else "未达标"
    print(f"{name:<22} mean {mean:6.1f} ms | p95 {p95:6.1f} | max {worst:6.1f}  [{mark}]")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF 批注模式性能基准")
    parser.add_argument("--strokes", type=int, default=500)
    parser.add_argument("--points", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=30)
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="sketchpad-perf-"))
    pdf = work / "bench.pdf"
    _make_pdf(pdf)
    src = PdfSource(pdf)
    doc = PdfDocument([Page(PAGE_W, PAGE_H, 0, _make_scribble(args.strokes, args.points))])
    pdfstore.save_sidecar(pdf, doc)
    src.close()

    app = QApplication.instance() or QApplication([])
    view = PdfView(Settings())
    view.resize(1200, 800)                 # 真实窗口尺寸量级
    view.show()
    view.open(pdf)
    _pump(app, 400)                        # 等自适应页宽 + 去抖渲染落地

    page = view.document.pages[0]
    path_items = [i for i in view.scene().items()
                  if isinstance(i, QGraphicsPathItem)]
    print(f"文档：1 页 {PAGE_W:.0f}x{PAGE_H:.0f} pt，{len(page.strokes)} 条 x "
          f"{args.points} 点；场景路径项 {len(path_items)} 个")
    print(f"视口 {view.viewport().width()}x{view.viewport().height()} px，"
          f"当前缩放 {view.zoom:.2f}")

    results: list[bool] = []
    # 1) 100% 缩放静态帧（验收线场景）
    view.set_zoom(1.0)
    _pump(app, 200)
    results.append(_report("100% 静态帧", _timed_grabs(view, args.rounds)))

    # 2) 平移帧：连续滚动 + 抓帧
    bar = view.verticalScrollBar()
    scroll: list[float] = []
    for value in range(0, min(bar.maximum(), 4000) + 1, max(1, bar.maximum() // 10)):
        bar.setValue(value)
        _pump(app, 60)
        scroll.extend(_timed_grabs(view, 5))
    results.append(_report("100% 平移帧", scroll))

    # 3) 缩放帧：切到 200% 后连续抓帧
    view.set_zoom(2.0)
    _pump(app, 200)
    results.append(_report("200% 静态帧", _timed_grabs(view, args.rounds)))

    view.close_document()
    view.close()
    print(f"目录 {work}（可删）")
    print("结论：" + ("全部达标" if all(results) else "存在未达标场景"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
