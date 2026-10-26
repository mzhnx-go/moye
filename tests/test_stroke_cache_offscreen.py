"""笔迹位图缓存离屏测试（v2.2 性能）：烘焙不变量、增量绘制、缩放重渲、擦/撤/清页的缓存失效。

结构断言代替帧计时：真正的单帧数字由 tools/perf_pdf.py 在真实窗口量
（见 docs/verification/perf-pdf-2026-10-20.md）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsPathItem

from sketchpad.core import pdfstore
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.ui.pdfview import CACHE_MAX_ZOOM, STROKE_TAIL, PdfView
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Stroke

SIZES = ((200.0, 300.0), (300.0, 200.0), (250.0, 400.0))
TOTAL = STROKE_TAIL + 4          # 20 条：16 条留尾部、4 条烘缓存

_ALIVE: list[PdfView] = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path):
    doc = pymupdf.open()
    for width, height in SIZES:
        doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return path


def _strokes(count: int) -> list[Stroke]:
    """每条一根竖线，x 相距 8 点（大于擦除半径 3，点哪根只中哪根）。"""
    return [Stroke([(20.0 + i * 8.0, 20.0), (20.0 + i * 8.0, 280.0)],
                   "#e8eaed" if i % 2 else "#0a84ff", 3.0)
            for i in range(count)]


@pytest.fixture
def scribble_pdf(tmp_path):
    pdf = _make_pdf(tmp_path / "scribble.pdf")
    doc = PdfDocument([Page(*SIZES[0], 0, _strokes(TOTAL)),
                       Page(*SIZES[1], 0, []),
                       Page(*SIZES[2], 0, [])])
    pdfstore.save_sidecar(pdf, doc)
    return pdf


@pytest.fixture
def blank_pdf(tmp_path):
    """无批注的三页 PDF（撤销测试用：sidecar 笔迹不进历史）。"""
    return _make_pdf(tmp_path / "blank.pdf")


@pytest.fixture
def view(qapp):
    v = PdfView(Settings())
    v.resize(260, 180)
    v.show()
    QTest.qWaitForWindowExposed(v)
    _ALIVE.append(v)
    yield v
    v.close_document()
    v.close()


def _layer(view: PdfView):
    return view._layers[0]


def _settle(view: PdfView) -> None:
    QTest.qWait(150)              # 覆盖 40ms 去抖 + 渲染


def _drag(view: PdfView, start: QPoint, end: QPoint) -> None:
    """按下-抬起即一条两点笔迹（松手时视图会补终点）。"""
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)


def _draw_one(view: PdfView, index: int) -> None:
    """在第 0 页画一条斜线。坐标走场景点（页面在场景里居中，屏幕坐标会落到页外）。"""
    y = 15.0 + (index % 18) * 15.0
    start = view.mapFromScene(QPointF(60.0, y))          # 原生返回整数 QPoint
    end = view.mapFromScene(QPointF(140.0, y + 25.0))
    _drag(view, start, end)


# ---------- 烘焙与不变量 ----------

def test_open_bakes_excess_strokes_into_cache(view, scribble_pdf):
    view.open(scribble_pdf)
    _settle(view)
    layer = _layer(view)
    model = view.document.pages[0].strokes
    assert len(model) == TOTAL
    # 超出尾部的烘进缓存，尾部只留 STROKE_TAIL 条矢量项
    assert len(layer.stroke_items) == STROKE_TAIL
    assert len(layer.cache_strokes) == TOTAL - STROKE_TAIL
    # 互补不变量：缓存 + 尾部正好覆盖模型，且不重叠
    assert len(layer.stroke_items) + len(layer.cache_strokes) == len(model)
    # 缓存位图非空，且正好盖住页面矩形（缩放取整不外泄）
    assert layer.cache_item.pixmap().isNull() is False
    rect = layer.cache_item.sceneBoundingRect()
    assert (rect.x(), rect.y()) == pytest.approx((layer.x, layer.y))
    assert (rect.width(), rect.height()) == pytest.approx((layer.width, layer.height))
    # 场景里剩下的矢量路径项就是尾部那批
    paths = [i for i in view.scene().items() if isinstance(i, QGraphicsPathItem)]
    assert len(paths) == STROKE_TAIL


def test_pen_up_bakes_one_more_into_cache(view, scribble_pdf):
    view.open(scribble_pdf)
    _settle(view)
    layer = _layer(view)
    before_cache = len(layer.cache_strokes)
    _draw_one(view, 0)                     # 抬笔 → 尾部 17 条 → 烘最旧 1 条
    assert len(view.document.pages[0].strokes) == TOTAL + 1
    assert len(layer.cache_strokes) == before_cache + 1
    assert len(layer.stroke_items) == STROKE_TAIL
    assert len(layer.stroke_items) + len(layer.cache_strokes) == TOTAL + 1


# ---------- 缩放重渲 ----------

def test_zoom_change_rerenders_cache_at_new_zoom(view, scribble_pdf):
    view.open(scribble_pdf)
    _settle(view)
    layer = _layer(view)
    view.set_zoom(2.0)
    _settle(view)
    assert layer.cache_zoom == pytest.approx(2.0)
    pixmap = layer.cache_item.pixmap()
    assert (pixmap.width(), pixmap.height()) == pytest.approx(
        (layer.width * 2.0, layer.height * 2.0), abs=1.0)
    # 重渲后仍盖住页面矩形，笔迹数不变
    rect = layer.cache_item.sceneBoundingRect()
    assert (rect.width(), rect.height()) == pytest.approx((layer.width, layer.height))
    assert len(layer.cache_strokes) == TOTAL - STROKE_TAIL


def test_stroke_cache_uses_device_pixel_ratio(view, scribble_pdf):
    """副屏 150%：缓存位图同样带 DPR，避免 Qt 先缩 1/1.5 再被系统放大。"""
    view._device_ratio = lambda: 1.5
    view.open(scribble_pdf)
    _settle(view)
    view.render_visible()           # 显式收口：不依赖 40ms 去抖在负载下准时到点
    layer = _layer(view)
    pixmap = layer.cache_item.pixmap()
    assert pixmap.devicePixelRatio() == pytest.approx(1.5)
    assert layer.cache_zoom == pytest.approx(view._cache_zoom())
    assert pixmap.width() == pytest.approx(layer.width * view._cache_zoom(), abs=1)
    # 场景包围盒仍是页面尺寸：按设备无关尺寸回缩，而不是按物理像素数
    rect = layer.cache_item.sceneBoundingRect()
    assert (rect.width(), rect.height()) == pytest.approx((layer.width, layer.height))

    # 增量烘焙也走同一套坐标：DPR 与包围盒不能被改回物理像素口径
    _draw_one(view, 0)
    pixmap = layer.cache_item.pixmap()
    assert pixmap.devicePixelRatio() == pytest.approx(1.5)
    rect = layer.cache_item.sceneBoundingRect()
    assert (rect.width(), rect.height()) == pytest.approx((layer.width, layer.height))
    # 新烘进去的笔迹必须真画在“页面点 × cache_zoom”处；只查 DPR/bbox 抓不到 scale 写错
    stroke = layer.cache_strokes[-1]
    mx, my = stroke.points[len(stroke.points) // 2]
    image = pixmap.toImage()
    cx, cy = round(mx * layer.cache_zoom), round(my * layer.cache_zoom)
    painted = any(image.pixelColor(cx + dx, cy + dy).alpha() > 0
                  for dx in range(-2, 3) for dy in range(-2, 3))
    assert painted is True


def test_cache_is_capped_at_four_times_but_page_still_renders(view, scribble_pdf):
    """8× 视图只用 4× 缓存图（A4 约 32MB 而不是 128MB），放大显示、页面照常渲染。"""
    view.open(scribble_pdf)
    _settle(view)
    layer = _layer(view)
    view.set_zoom(8.0)
    _settle(view)

    assert view.zoom == pytest.approx(8.0)
    assert CACHE_MAX_ZOOM == 4.0
    assert layer.cache_zoom == pytest.approx(CACHE_MAX_ZOOM)     # 缓存倍率封顶
    pixmap = layer.cache_item.pixmap()
    assert pixmap.width() <= layer.width * CACHE_MAX_ZOOM + 1
    assert pixmap.height() <= layer.height * CACHE_MAX_ZOOM + 1
    # 4× 图像放大后仍正好盖住页面矩形（不缩水、不偏移）
    rect = layer.cache_item.sceneBoundingRect()
    assert (rect.x(), rect.y()) == pytest.approx((layer.x, layer.y))
    assert (rect.width(), rect.height()) == pytest.approx((layer.width, layer.height))
    # 底图不受缓存上限影响，仍按 8× 渲染
    assert layer.pixmap_item.pixmap().width() == pytest.approx(layer.width * 8.0, abs=1.0)
    assert layer.pixmap_item.pixmap().height() == pytest.approx(layer.height * 8.0, abs=1.0)
    # 缓存内容没丢：已烘笔迹仍在缓存里
    assert len(layer.cache_strokes) == TOTAL - STROKE_TAIL


# ---------- 擦已烘笔迹：缓存失效并按剩余重渲 ----------

def test_erase_baked_stroke_updates_cache(view, scribble_pdf):
    view.open(scribble_pdf)
    _settle(view)
    layer = _layer(view)
    target = layer.cache_strokes[0]
    px, py = target.points[0]
    screen = view.mapFromScene(QPointF(layer.x + px, layer.y + py))

    view.set_tool("eraser")
    # 按住-抬起都落在笔迹点上（抬笔会再采样一次落点，同点最稳）
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=screen)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=screen)

    model = view.document.pages[0].strokes
    assert len(model) == TOTAL - 1
    assert target not in model
    # 缓存里被擦的那条已过滤掉，并按剩余笔迹重渲（仍盖住页面）
    assert target not in layer.cache_strokes
    assert len(layer.cache_strokes) == TOTAL - STROKE_TAIL - 1
    assert layer.cache_item.pixmap().isNull() is False
    assert len(layer.stroke_items) + len(layer.cache_strokes) == len(model)


# ---------- 清页 / 撤销：缓存不得留幽灵笔迹 ----------

def test_clear_page_drops_cache_and_undo_restores(view, scribble_pdf):
    view.open(scribble_pdf)
    _settle(view)
    layer = _layer(view)
    view.clear_page()
    assert view.document.pages[0].strokes == []
    assert layer.cache_strokes == []            # 清掉的笔迹不得留在缓存里
    assert layer.stroke_items == []
    assert layer.cache_item.pixmap().isNull() is False   # 重渲成透明，不是旧图

    view.history.pop_undo()                     # 撤销清页：20 条整体回来
    assert len(view.document.pages[0].strokes) == TOTAL
    assert len(layer.cache_strokes) == TOTAL - STROKE_TAIL
    assert len(layer.stroke_items) == STROKE_TAIL


def test_undo_after_bake_keeps_disjoint_cover(view, blank_pdf):
    view.open(blank_pdf)
    _settle(view)
    layer = _layer(view)
    # 空文档画 20 条（边画边烘）：4 条进缓存、16 条留尾部，全部进历史
    for i in range(TOTAL):
        _draw_one(view, i)
    _settle(view)
    assert len(view.document.pages[0].strokes) == TOTAL
    assert len(layer.stroke_items) + len(layer.cache_strokes) == TOTAL

    for _ in range(5):                          # 撤 5 条（都在尾部，缓存不动）
        view.history.pop_undo()
    model = view.document.pages[0].strokes
    assert len(model) == TOTAL - 5
    assert len(layer.stroke_items) + len(layer.cache_strokes) == len(model)

    for _ in range(len(model)):                 # 全撤：缓存笔迹也死光，整体重渲
        view.history.pop_undo()
    assert view.document.pages[0].strokes == []
    assert layer.cache_strokes == []
    assert layer.stroke_items == []
    assert layer.cache_item.pixmap().isNull() is False


# ---------- 视口外页面：底图与笔迹缓存一起回收，回到视口再重渲 ----------

MANY_PAGES = 20
MANY_SIZES = ((200.0, 300.0),) * MANY_PAGES


@pytest.fixture
def many_page_pdf(tmp_path):
    """20 页等尺寸 PDF；第 0 页和第 10 页各 20 条笔迹（4 条烘进缓存）。"""
    doc = pymupdf.open()
    for width, height in MANY_SIZES:
        doc.new_page(width=width, height=height)
    pdf = tmp_path / "many-scribble.pdf"
    doc.save(str(pdf))
    doc.close()
    model = PdfDocument([Page(*size, 0, _strokes(TOTAL) if i in (0, 10) else [])
                         for i, size in enumerate(MANY_SIZES)])
    pdfstore.save_sidecar(pdf, model)
    return pdf


def test_offscreen_page_stroke_cache_is_evicted_and_rerendered(view, many_page_pdf):
    """滚走的页把笔迹缓存位图也放掉（模型与烘焙清单保留），回来按当前倍率重画。"""
    view.open(many_page_pdf)
    _settle(view)
    first = view._layers[0]
    model = list(view.document.pages[0].strokes)
    assert len(first.cache_strokes) == TOTAL - STROKE_TAIL
    assert first.cache_item.pixmap().isNull() is False

    view.goto_page(10)
    _settle(view)
    assert view.current_page == 10
    # 缓存位图被回收，缓存倍率记号清空；笔迹数据与烘焙清单原样保留
    assert first.cache_item.pixmap().isNull() is True
    assert first.cache_zoom is None
    assert len(first.cache_strokes) == TOTAL - STROKE_TAIL
    assert len(first.stroke_items) == STROKE_TAIL
    assert view.document.pages[0].strokes == model
    # 当前页的缓存照常在场
    tenth = view._layers[10]
    assert tenth.cache_item.pixmap().isNull() is False

    view.goto_page(0)
    _settle(view)
    # 回到视口：缓存按当前倍率重渲，仍正好盖住页面，内容与模型互补
    assert first.cache_item.pixmap().isNull() is False
    assert first.cache_zoom == pytest.approx(view._cache_zoom())
    rect = first.cache_item.sceneBoundingRect()
    assert (rect.x(), rect.y()) == pytest.approx((first.x, first.y))
    assert (rect.width(), rect.height()) == pytest.approx(
        (first.width, first.height))
    assert len(first.cache_strokes) == TOTAL - STROKE_TAIL
    assert len(first.stroke_items) + len(first.cache_strokes) == len(model)
    # 反过来第 10 页被回收
    assert tenth.cache_item.pixmap().isNull() is True
