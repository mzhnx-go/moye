"""一次性性能测量：1000 条 × 200 点文档的构建 / 平移 / 缩放 / 整屏重绘耗时。

用法：QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tools/perf_check.py
测量而非门禁：无论数值多少都以 0 退出，16ms 线由人读表判断（设计文档 §7）。

口径：
- 平移 / 缩放各 20 次只计入口耗时（相机算术 + 变换 + 缩放时的 1000 支画笔更新），
  不含光栅化；一帧的真实代价由后面两次同步整屏重绘给出。
- 平移后 repaint() 一次（100% 缩放）与 20 次缩放后 grab() 一次（466%），
  都是同步全量重绘，等价于一次完整帧。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:      # 直接 python tools/perf_check.py 时也能 import app
    sys.path.insert(0, str(ROOT))

import PySide6
from PySide6.QtWidgets import QApplication

from sketchpad.core import camera
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.core.strokes import Document, Stroke

STROKES = 1000
POINTS = 200
SEED = 1234
VIEW_W, VIEW_H = 1200, 800
ROUNDS = 20
COLORS = ("#e8eaed", "#c0392b", "#d35400", "#f1c40f")
WIDTHS = (1.5, 3.0, 6.0, 12.0)


def build_document(strokes: int, points: int, seed: int) -> Document:
    """按种子生成确定性笔迹：4 色轮转、4 档线宽轮转，坐标取 2 位小数。"""
    rng = random.Random(seed)
    out: list[Stroke] = []
    for i in range(strokes):
        pts = [(round(rng.uniform(-4000.0, 4000.0), 2),
                round(rng.uniform(-4000.0, 4000.0), 2)) for _ in range(points)]
        out.append(Stroke(pts, COLORS[i % 4], WIDTHS[i % 4]))
    return Document(out)


def stats(samples: list[float]) -> tuple[float, float, float]:
    """mean / p95（最近秩）/ max，单位 ms。"""
    s = sorted(samples)
    mean = sum(s) / len(s)
    p95 = s[max(0, math.ceil(0.95 * len(s)) - 1)]
    return mean, p95, s[-1]


def ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def main() -> int:
    app = QApplication.instance() or QApplication([])

    # 先造文档（不计时），再搭空场景：rebind 的耗时单独量
    doc = build_document(STROKES, POINTS, SEED)
    scene = SketchScene(Document())
    view = SketchView(scene)
    view.resize(VIEW_W, VIEW_H)
    view.show()
    app.processEvents()

    scene.document = doc
    view.document = doc

    t0 = time.perf_counter()
    scene.rebind()
    build_ms = ms(t0)

    vw, vh = view.viewport().width(), view.viewport().height()
    dx = vw * camera.WHEEL_PAN_RATIO
    dy = vh * camera.WHEEL_PAN_RATIO

    pans: list[float] = []
    for i in range(ROUNDS):
        sx = dx if i % 2 == 0 else -dx
        sy = dy if (i // 2) % 2 == 0 else -dy
        t0 = time.perf_counter()
        view.pan_screen(sx, sy)
        pans.append(ms(t0))

    # 100% 缩放下量一次完整帧：平移 + 同步整屏重绘
    t0 = time.perf_counter()
    view.pan_screen(dx, dy)
    view.viewport().repaint()
    pan_frame_ms = ms(t0)

    zooms: list[float] = []
    cx, cy = vw / 2.0, vh / 2.0
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        view.zoom_at_screen(cx, cy, camera.WHEEL_ZOOM_STEP)
        zooms.append(ms(t0))

    # 缩放后的完整帧：一次 grab 全量重绘（此时 scale = 1.08^20 ≈ 4.66x）
    t0 = time.perf_counter()
    view.grab()
    grab_ms = ms(t0)

    items = view.scene.items()
    stroke_items = [it for it in items if it.data(0) is not None]

    print(f"环境: Python {sys.version.split()[0]}, PySide6 {PySide6.__version__}, "
          f"平台 {os.environ.get('QT_QPA_PLATFORM')}")
    print(f"文档: {STROKES} 条 × {POINTS} 点, seed={SEED}, 视口 {vw}×{vh}, "
          f"最终缩放 {view.viewport_scale():.2f}x")
    print(f"图形项: 总数 {len(items)}, 笔迹项 {len(stroke_items)}")
    print()
    print(f"{'操作':<24}{'mean(ms)':>12}{'p95(ms)':>12}{'max(ms)':>12}")
    for label, value in (
        ("rebind 重建 ×1", (build_ms,)),
        (f"平移操作 ×{ROUNDS}", pans),
        (f"Ctrl+滚轮缩放操作 ×{ROUNDS}", zooms),
        ("平移+整屏重绘 ×1 (100%)", (pan_frame_ms,)),
        ("grab 整屏重绘 ×1 (466%)", (grab_ms,)),
    ):
        mean, p95, mx = stats(list(value))
        print(f"{label:<24}{mean:>12.2f}{p95:>12.2f}{mx:>12.2f}")
    print()
    print("平移样本(ms): " + " ".join(f"{v:.3f}" for v in pans))
    print("缩放样本(ms): " + " ".join(f"{v:.2f}" for v in zooms))
    return 0                          # 测量不是门禁：任何数值都退 0


if __name__ == "__main__":
    raise SystemExit(main())
