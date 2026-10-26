"""橡皮即时清除回归：缓存位图里的笔迹必须在按住碰到时就消失，而不是抬笔才消失。

PDF 视图把尾部 16 条之外的笔迹烘进一张透明位图，缓存笔迹没有独立 item 可隐藏；
`_erase_at` 必须把命中的缓存笔迹从位图里即时剔除并重渲。这里直接采样缓存位图
的 alpha，不依赖"抬笔后模型变化"这种间接证据。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsPathItem

from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Stroke
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.pdfview import STROKE_TAIL, PdfView

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _CachedStrokeCase:
    """一页 20 条笔迹：前 4 条烘进缓存（STROKE_TAIL=16），其余留在矢量尾部。"""

    def __init__(self, view: PdfView, pdf):
        view.open(pdf)
        QTest.qWait(80)
        self.page = view.document.pages[0]
        for index in range(20):
            y = 10.0 + index * 12.0
            self.page.strokes.append(
                Stroke([(20.0, y), (180.0, y)], "#e74c3c", 4.0))
        view._rebind_page(0)
        self.layer = view._layers[0]
        view._bake_layer(self.layer)

    def alpha_at(self, view: PdfView, x: float, y: float) -> int:
        """缓存位图在页面点 (x, y) 处的 alpha；0 表示这条笔迹已从位图消失。"""
        zoom = view._cache_zoom()
        image = self.layer.cache_item.pixmap().toImage()
        return image.pixelColor(round(x * zoom), round(y * zoom)).alpha()


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "erase.pdf"
    doc = pymupdf.open()
    doc.new_page(width=220.0, height=320.0)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def view(qapp):
    v = PdfView(Settings())
    v.resize(260, 200)
    v.show()
    QTest.qWaitForWindowExposed(v)
    _ALIVE.append(v)
    yield v
    v.close_document()
    v.close()


def test_cached_stroke_disappears_on_contact_before_release(view, pdf):
    case = _CachedStrokeCase(view, pdf)
    assert len(case.layer.cache_strokes) == 4     # 前 4 条确实在缓存里
    target = case.layer.cache_strokes[0]           # 第一条：y=10 的红色横线
    assert case.alpha_at(view, 100.0, 10.0) > 0

    view.set_tool("eraser")
    view._begin_erase(0, (100.0, 10.0))            # 只按下/碰到，不抬笔

    assert view._erasing is True
    assert target in case.page.strokes             # 模型还没改：抬笔才落命令
    assert case.alpha_at(view, 100.0, 10.0) == 0   # 但位图里已经消失
    # 相邻的缓存笔迹不受影响
    assert case.alpha_at(view, 100.0, 22.0) > 0

    view._finish_erase()
    assert target not in case.page.strokes
    assert case.alpha_at(view, 100.0, 10.0) == 0


def test_reset_gesture_restores_cached_stroke(view, pdf):
    case = _CachedStrokeCase(view, pdf)
    target = case.layer.cache_strokes[0]
    view.set_tool("eraser")
    view._begin_erase(0, (100.0, 10.0))
    assert case.alpha_at(view, 100.0, 10.0) == 0

    view._reset_gesture()                          # 丢抬笔 / 切工具 / 撤销打断

    assert target in case.page.strokes
    assert case.alpha_at(view, 100.0, 10.0) > 0
    assert view._erased_cache_ids == set()


def test_tail_vector_stroke_hides_immediately_and_reset_restores(view, pdf):
    case = _CachedStrokeCase(view, pdf)
    tail = [item for item in case.layer.stroke_items
            if item.data(0) is case.page.strokes[STROKE_TAIL]]
    assert len(tail) == 1
    item = tail[0]
    assert item.isVisible()

    view.set_tool("eraser")
    view._begin_erase(0, (100.0, 10.0 + STROKE_TAIL * 12.0))
    assert item.isVisible() is False                # 矢量项：碰到即隐藏

    view._reset_gesture()
    assert item.isVisible() is True                 # 中断后不能留下"隐形笔迹"


def test_baking_is_paused_during_erase(view, pdf):
    case = _CachedStrokeCase(view, pdf)
    before = len(case.layer.cache_strokes)
    view.set_tool("eraser")
    view._begin_erase(0, (100.0, 10.0))
    view._bake_layer(case.layer)                    # 拖动期间被重渲触发
    assert len(case.layer.cache_strokes) == before  # 隐藏的尾部项不会被烘回缓存
    view._reset_gesture()


def test_canvas_eraser_press_requests_repaint(qapp, monkeypatch):
    """草稿画布：橡皮按下的第一下命中也必须请求重绘，不能等第一个 move。"""
    scene = SketchScene(_draft_document())
    view = SketchView(scene)
    view.resize(240, 200)
    view.show()
    QTest.qWaitForWindowExposed(view)
    _ALIVE.append(view)
    view.set_tool("eraser")
    calls = []
    monkeypatch.setattr(view.viewport(), "update",
                        lambda *a, **k: calls.append(1), raising=False)
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(20, 20))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(20, 20))
    assert calls, "橡皮按下没有请求重绘"
    view.close()
    _ALIVE.remove(view)


def _draft_document():
    from sketchpad.core.strokes import Document
    document = Document()
    document.strokes.append(Stroke([(0.0, 0.0), (200.0, 200.0)], "#e8eaed", 4.0))
    return document
