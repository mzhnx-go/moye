"""v2.3 真机回归：真实讲义 PDF 端到端（spec §9 / §11 验收清单 1–8 步）。

场景（对照 v2 设计文档 §11 验收清单）：
1. 打开 26 页真实讲义，跳到第 10 页，页码显示 10/26
2. 第 10 页画 5 笔，Ctrl+Z 撤 1 笔，其余 4 笔原位原色
3. 缩放 200% 再画 1 笔：落点仍在笔尖下；缩回 100% 数据不变
4. 翻到第 3 页再翻回来，5 笔还在
5. Ctrl+S 存 sidecar → **新进程重开**（真重启语义），批注恢复
6. 导出副本：新文件存在、原文件 SHA-256 不变、笔迹是矢量（get_drawings 增量）
7. 文档模式草稿窗：热键注册 + 画几笔收起再开，内容还在
8. 连续滚动全篇：当前页渲出位图、远处页位图回收、回滚能重渲（性能线由 tools/perf_pdf.py 另行实测）

纪律：
- 讲义先复制到临时目录再操作——sidecar/导出只落副本，用户原文件只读；
- 离屏平台（视觉验收已由 tools/probe_acceptance.py 覆盖，这里查数据正确性）；
- 恒退 0；结论看汇总表。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymupdf
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main
from sketchpad.core import pdfstore
from sketchpad.pdf.document import export_with_strokes

LECTURE = Path("E:/四海/1.四海 hua生十三飞扬 （系统班、套卷班）"
               "/1.2027国省考季hua生十三飞扬系统班/zz理论/冯泽老师/课件"
               "/【四海】zz理论-m原-第1节.pdf")
TARGET_PAGE = 9                      # 第 10 页（0 起）
SHOTS = Path(tempfile.gettempdir()) / "sketchpad-regression"
RESULTS: list[tuple[str, bool, str]] = []
_ALIVE: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def pump(app, ms: int) -> None:
    from PySide6.QtCore import QElapsedTimer
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def drag_at(view, scene_x: float, scene_y: float, dx: float = 60.0) -> None:
    """在视图场景坐标处按下-抬起，得一条两点笔迹。"""
    start = view.mapFromScene(QPointF(scene_x, scene_y))
    end = view.mapFromScene(QPointF(scene_x + dx, scene_y + 40.0))
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)


def main_scenario() -> int:
    if not LECTURE.exists():
        print(f"找不到讲义：{LECTURE}")
        return 0
    work = Path(tempfile.mkdtemp(prefix="sketchpad-v23-"))
    pdf = work / LECTURE.name
    shutil.copy(LECTURE, pdf)                     # 只读原件，操作副件
    before_hash = sha256(pdf)
    original_dir = LECTURE.parent

    app = QApplication.instance() or QApplication([])
    controller = main.AppController(work / "cfg")
    win = main.MainWindow(controller)
    win.resize(1160, 760)
    win.show()
    _ALIVE.append(win)

    # ---------- 1. 打开 26 页真实讲义，跳第 10 页 ----------
    check("打开真实讲义", win._enter_document(pdf) is True)
    pump(app, 600)                                # 适应页宽 + 去抖渲染
    pages = len(win.pdf_view.document.pages)
    check("页数 ≥ 20", pages >= 20, f"{pages} 页")
    win.pdf_view.goto_page(TARGET_PAGE)
    pump(app, 300)
    check("跳第 10 页：状态栏页码", f"第 10/{pages} 页" in win.status_label.text(),
          win.status_label.text())

    # ---------- 2. 画 5 笔，Ctrl+Z 撤 1 笔 ----------
    layer = win.pdf_view._layers[TARGET_PAGE]
    colors = ("#ff453a", "#0a84ff", "#32d74b", "#ffd60a", "#bf5af2")
    for i in range(5):
        win.pdf_view.set_color(colors[i])
        drag_at(win.pdf_view, layer.x + 80.0 + i * 30.0, layer.y + 200.0 + i * 40.0)
    pump(app, 200)
    strokes = win.pdf_view.document.pages[TARGET_PAGE].strokes
    check("第 10 页落下 5 笔", len(strokes) == 5, f"{len(strokes)} 笔")

    win.pdf_view.setFocus()
    QTest.keySequence(win.pdf_view, "Ctrl+Z")     # 走真快捷键路径
    pump(app, 300)
    strokes = win.pdf_view.document.pages[TARGET_PAGE].strokes
    check("Ctrl+Z 撤掉最后 1 笔", len(strokes) == 4, f"剩 {len(strokes)} 笔")
    check("其余 4 笔原位原色（身份保持）",
          [s.color for s in strokes] == list(colors[:4]),
          f"{[s.color for s in strokes]}")

    # ---------- 3. 200% 再画 1 笔：落点在笔尖下；缩回 100% 数据不变 ----------
    win.pdf_view.set_zoom(2.0)
    pump(app, 400)
    win.pdf_view.goto_page(TARGET_PAGE)           # 先把目标页居中，保证落点在视口内
    pump(app, 300)
    layer = win.pdf_view._layers[TARGET_PAGE]
    press = win.pdf_view.mapFromScene(QPointF(layer.x + 250.0, layer.y + 150.0))
    QTest.mousePress(win.pdf_view.viewport(), Qt.LeftButton, pos=press)
    QTest.mouseRelease(win.pdf_view.viewport(), Qt.LeftButton, pos=press)
    pump(app, 200)
    strokes = win.pdf_view.document.pages[TARGET_PAGE].strokes
    expected = win.pdf_view.mapToScene(press)
    expected_local = (expected.x() - layer.x, expected.y() - layer.y)
    check("200% 落笔：笔迹数 5", len(strokes) == 5, f"{len(strokes)} 笔")
    got = strokes[-1].points[0]
    check("200% 落笔：首点=笔尖下（页面局部坐标）",
          abs(got[0] - expected_local[0]) < 1e-6 and abs(got[1] - expected_local[1]) < 1e-6,
          f"落点 {got} vs 期望 {expected_local}")
    snapshot = [list(s.points) for s in strokes]
    win.pdf_view.set_zoom(1.0)
    pump(app, 400)
    after = [list(s.points) for s in win.pdf_view.document.pages[TARGET_PAGE].strokes]
    check("缩回 100%：笔迹数据不变（缩放与数据解耦）", after == snapshot)

    # ---------- 4. 翻走再翻回 ----------
    win.pdf_view.goto_page(2)
    pump(app, 300)
    win.pdf_view.goto_page(TARGET_PAGE)
    pump(app, 300)
    strokes = win.pdf_view.document.pages[TARGET_PAGE].strokes
    check("翻到第 3 页再回来：5 笔还在且同值",
          len(strokes) == 5 and [list(s.points) for s in strokes] == snapshot)

    # ---------- 5. Ctrl+S → 新进程重开（真重启语义） ----------
    check("Ctrl+S 保存 sidecar", win._save_annotations() is True)
    sidecar = pdfstore.sidecar_path(pdf)
    check("sidecar 落副本同目录", sidecar.exists() and sidecar.parent == work,
          str(sidecar.name))
    check("用户原目录没被写 sidecar",
          not any(original_dir.glob(f"{LECTURE.name}*.sketch.json")))
    child = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()),
         "--check-sidecar", str(pdf), "5"],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    ok = child.returncode == 0 and "RESTORED 5" in child.stdout
    check("新进程重开：批注从 sidecar 恢复", ok,
          (child.stdout.strip().splitlines() or ["无输出"])[-1] if child.stdout else child.stderr[-200:])

    # ---------- 6. 导出副本 ----------
    before_drawings = len(pymupdf.open(str(pdf))[TARGET_PAGE].get_drawings())
    out = work / f"{pdf.stem}-批注.pdf"
    from sketchpad.pdf.document import PdfError
    try:
        export_with_strokes(win.pdf_view.source,
                            [p.strokes for p in win.pdf_view.document.pages], out)
        exported = True
    except PdfError as exc:
        exported = False
        print(f"导出失败：{exc}")
    check("导出副本文件存在", exported and out.exists())
    check("原文件 SHA-256 不变", sha256(pdf) == before_hash)
    if exported:
        doc = pymupdf.open(str(out))
        delta = len(doc[TARGET_PAGE].get_drawings()) - before_drawings
        check("导出笔迹是矢量（get_drawings 增量 ≥ 5）", delta >= 5,
              f"增量 {delta}（导出前 {before_drawings}）")
        check("导出页数一致", doc.page_count == pages, f"{doc.page_count} 页")
        doc.close()

    # ---------- 7. 文档模式草稿窗 ----------
    # 走真实启动路径：注册失败（如热键被别的程序占用，错误码 1409）应回退到
    # 程序内快捷键，而不是静默失效——这正是要验的行为
    win.start_hotkey()
    pump(app, 200)
    check("全局热键：注册成功或已回退程序内快捷键",
          win.hotkey.is_registered or win.fallback_shortcut is not None,
          "Ctrl+Alt+D 已注册" if win.hotkey.is_registered
          else f"注册失败（{win.hotkey.error_message}）→ 已回退")
    win.scratch.toggle()
    pump(app, 300)
    scratch_view = win.scratch.window.view
    QTest.mousePress(scratch_view.viewport(), Qt.LeftButton, pos=QPoint(80, 80))
    QTest.mouseRelease(scratch_view.viewport(), Qt.LeftButton, pos=QPoint(180, 140))
    pump(app, 200)
    # 草稿窗是独立内存文档（设计 V7），不断主文档
    check("文档模式呼出草稿窗并画 1 笔",
          win.scratch.is_visible()
          and len(win.scratch.window.document.strokes) == 1)
    win.scratch.toggle()
    pump(app, 200)
    win.scratch.toggle()
    pump(app, 200)
    check("收起再打开：草稿内容还在（独立内存文档）",
          win.scratch.is_visible()
          and len(win.scratch.window.document.strokes) == 1)
    if win.hotkey.is_registered:
        win.hotkey.unregister()

    # ---------- 8. 连续滚动全篇（v2.7：可见页渲染 + 远处页位图回收） ----------
    bar = win.pdf_view.verticalScrollBar()
    step = max(1, bar.maximum() // 40)
    for value in range(0, bar.maximum() + 1, step):
        bar.setValue(value)
        pump(app, 60)
    pump(app, 800)
    current = win.pdf_view.current_page
    current_layer = win.pdf_view._layers[current]
    check(f"连续滚动到第 {current + 1} 页：当前页已渲出位图",
          not current_layer.pixmap_item.pixmap().isNull())
    far_null = all(layer.pixmap_item.pixmap().isNull()
                   for layer in win.pdf_view._layers
                   if abs(layer.index - current) > 2)
    check("滚远后的页位图已回收（不是全篇常驻）", far_null)
    win.pdf_view.goto_page(0)
    pump(app, 700)
    check("回到第 1 页：位图重新渲染",
          not win.pdf_view._layers[0].pixmap_item.pixmap().isNull())
    check("滚动后笔迹数不变", len(win.pdf_view.document.pages[TARGET_PAGE].strokes) == 5)

    # ---------- 留证截图 ----------
    try:
        SHOTS.mkdir(parents=True, exist_ok=True)
        win.pdf_view.goto_page(TARGET_PAGE)
        win.pdf_view.set_zoom(1.0)
        pump(app, 400)
        win.grab().save(str(SHOTS / "reg_page10_annotated.png"))
        win.pdf_view.set_zoom(2.0)
        pump(app, 400)
        win.grab().save(str(SHOTS / "reg_page10_zoom200.png"))
        win.scratch.toggle()
        pump(app, 300)
        win.scratch.window.grab().save(str(SHOTS / "reg_scratch.png"))
    except Exception as exc:
        print(f"（截图失败：{exc}）")

    win._quitting = True
    win.scratch.hide()
    win.close()
    app.processEvents()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n截图目录：{SHOTS}")
    print(f"汇总：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过"
          + (f"；失败：{failed}" if failed else "；无失败"))
    return 0


def check_sidecar(pdf_path: str, expected: int) -> int:
    """新进程模式：只开 PDF，数第 10 页笔迹，打印 RESTORED <n>。"""
    app = QApplication.instance() or QApplication([])
    from sketchpad.ui.pdfview import PdfView
    from sketchpad.core.settings import Settings
    v = PdfView(Settings())
    v.resize(900, 700)
    v.show()
    v.open(Path(pdf_path))
    pump(app, 500)
    strokes = len(v.document.pages[TARGET_PAGE].strokes)
    print(f"RESTORED {strokes}")
    v.close_document()
    v.close()
    return 0 if strokes == int(expected) else 1


if __name__ == "__main__":
    if "--check-sidecar" in sys.argv:
        raise SystemExit(check_sidecar(sys.argv[sys.argv.index("--check-sidecar") + 1],
                                       sys.argv[sys.argv.index("--check-sidecar") + 2]))
    raise SystemExit(main_scenario())
