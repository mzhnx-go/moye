"""PDF 文档视图离屏测试：页面排布、翻页、缩放锚定、适应页宽、搜索高亮、落笔坐标、橡皮/清屏撤销、滚轮与悬停。

样例 PDF 全在 tmp_path 现造；坐标一律 PDF 点（场景单位 = 点）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QGraphicsPathItem, QGraphicsPixmapItem,
                               QGraphicsRectItem)

from sketchpad.ui.toolbox import PALETTE
from sketchpad.core import pdfstore
from sketchpad.core.notes import Note
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.ui.pdfview import (PAGE_GAP, SUPERSAMPLE, SUPERSAMPLE_MAX_ZOOM,
                                  PdfView, _stroke_path)
from sketchpad.core.settings import WIDTHS, Settings
from sketchpad.core.strokes import Stroke

# 三页不同尺寸：窄竖版、横版、宽竖版；最宽 300 决定场景宽度
SIZES = ((200.0, 300.0), (300.0, 200.0), (250.0, 400.0))
SCENE_W = 300.0
SCENE_H = 300.0 + PAGE_GAP + 200.0 + PAGE_GAP + 400.0

# 显示出来的视图必须留 Python 引用，否则包装对象被回收
_ALIVE: list[PdfView] = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path, sizes=SIZES):
    doc = pymupdf.open()
    for width, height in sizes:
        doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def pdf(tmp_path):
    return _make_pdf(tmp_path / "sample.pdf")


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


def _page_items(view: PdfView) -> dict[int, QGraphicsPixmapItem]:
    """场景里的页面底图项，键是页号（存在 data(0)）。

    笔迹位图缓存也是 QGraphicsPixmapItem 但 data(0) 为 None，按此排除。
    """
    return {item.data(0): item for item in view.scene().items()
            if isinstance(item, QGraphicsPixmapItem) and item.data(0) is not None}


def _screen_width(view: PdfView, item) -> float:
    """页面项在视口里的像素宽度：把场景包围盒左右边映射到视口再相减。"""
    rect = item.sceneBoundingRect()
    transform = view.viewportTransform()
    left = transform.map(rect.topLeft())
    right = transform.map(QPointF(rect.right(), rect.top()))
    return right.x() - left.x()


def _hit_items(view: PdfView) -> list:
    """场景里的高亮矩形项，按左边界排序（顺序 = 传入的 rects 顺序）。"""
    items = [i for i in view.scene().items() if isinstance(i, QGraphicsRectItem)]
    return sorted(items, key=lambda i: (i.rect().left(), i.rect().top()))


def _send_move(view: PdfView, x: float, y: float, buttons=Qt.NoButton) -> None:
    """造一条鼠标移动：buttons 决定它算悬停还是按住拖动。"""
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(int(x), int(y))))
    ev = QMouseEvent(QEvent.MouseMove, local, global_pos, Qt.NoButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def _drag(view: PdfView, points: list[QPoint]) -> None:
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=points[0])
    for pos in points[1:-1]:
        _send_move(view, pos.x(), pos.y(), Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=points[-1])


def _wheel(view: PdfView, x: int, y: int, delta: int = 120,
           modifiers=Qt.NoModifier) -> None:
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(x, y)))
    ev = QWheelEvent(local, global_pos, QPoint(), QPoint(0, delta), Qt.NoButton,
                     modifiers, Qt.NoScrollPhase, False)
    QApplication.sendEvent(view.viewport(), ev)


def test_open_three_page_layout(view, pdf):
    """三页按 8 pt 间隙纵向堆叠，各自的宽度居中。"""
    view.open(pdf)
    assert view.pdf_path == pdf
    assert view.source.page_count == 3
    assert [len(page.strokes) for page in view.document.pages] == [0, 0, 0]
    rect = view.scene().sceneRect()
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == pytest.approx(
        (0.0, 0.0, SCENE_W, SCENE_H))

    items = _page_items(view)
    assert sorted(items) == [0, 1, 2]
    y = 0.0
    for index, (width, height) in enumerate(SIZES):
        item = items[index]
        assert item.pos().x() == pytest.approx((SCENE_W - width) / 2.0)
        assert item.pos().y() == pytest.approx(y)
        page = view.document.pages[index]
        assert (page.width, page.height) == pytest.approx((width, height))
        y += height + PAGE_GAP
    # 首屏那页已渲染：位图尺寸 = 页面点 × 当前缩放，场景包围盒就是页面矩形
    # 去抖渲染是 40ms 定时器；滚动条出现还会再触发一次 fit_width 改缩放。
    # 轮询到「缩放稳定 + 位图与当前缩放一致」再断言，避免并行跑测试时踩时序。
    first = items[0]

    def _matches_current_zoom() -> bool:
        pixmap = first.pixmap()
        scale = view._page_render_scale()
        return (pixmap.width() > 0
                and abs(pixmap.width() - 200.0 * scale) <= 1.0
                and abs(pixmap.height() - 300.0 * scale) <= 1.0)

    for _ in range(80):
        if _matches_current_zoom():
            zoom = view.zoom
            QTest.qWait(60)                  # 给布局/滚动条的最后一次自适应留时间
            if view.zoom == zoom and _matches_current_zoom():
                break
        QTest.qWait(50)
    assert (first.pixmap().width(), first.pixmap().height()) == pytest.approx(
        (200.0 * view._page_render_scale(), 300.0 * view._page_render_scale()), abs=1.0)
    rect = first.sceneBoundingRect()
    assert (rect.width(), rect.height()) == pytest.approx((200.0, 300.0))


def test_open_fits_current_page_to_viewport_width(view, pdf):
    """打开即适应页宽：缩放 = 视口宽 / 当前页宽，页面铺满视口。"""
    view.open(pdf)
    QTest.qWait(50)
    assert view.auto_fit is True
    viewport_width = view.viewport().width()
    assert view.zoom == pytest.approx(viewport_width / SIZES[0][0], abs=1e-3)
    item = _page_items(view)[0]
    assert _screen_width(view, item) == pytest.approx(viewport_width, abs=1.0)


def test_open_scrolls_to_page_top(view, pdf):
    """打开必须停在页首：fit_width 保住旧视口中心，首屏那页够高就会停在页中间。"""
    view.open(pdf)
    QTest.qWait(50)
    assert view.verticalScrollBar().value() == 0
    assert view.horizontalScrollBar().value() == 0
    page_top = _page_items(view)[0].sceneBoundingRect().top()
    assert view.mapToScene(QPoint(0, 0)).y() == pytest.approx(page_top, abs=24.0)


def test_resize_keeps_fit_until_manual_zoom(view, pdf):
    """自适应期间跟着窗口变；一旦手动缩放就停，fit_width 重新接管。"""
    view.open(pdf)
    QTest.qWait(50)

    view.resize(360, 240)
    QTest.qWait(50)
    assert view.auto_fit is True
    viewport_width = view.viewport().width()
    assert view.zoom == pytest.approx(viewport_width / SIZES[0][0], abs=1e-3)
    assert _screen_width(view, _page_items(view)[0]) == pytest.approx(
        viewport_width, abs=1.0)

    view.set_zoom(2.0)
    assert view.auto_fit is False
    view.resize(300, 240)
    QTest.qWait(50)
    assert view.zoom == pytest.approx(2.0)
    assert view.auto_fit is False

    view.fit_width()
    assert view.auto_fit is True
    assert view.zoom == pytest.approx(
        view.viewport().width() / SIZES[0][0], abs=1e-3)


def test_show_hits_draws_page_local_rects_with_stronger_current(view, pdf):
    """高亮只落在指定页、位置是页面局部点；当前命中更浓；clear_hits 清空。"""
    view.open(pdf)
    view.show_hits(1, [(10, 10, 50, 20), (60, 30, 90, 40)], current=1)

    hits = _hit_items(view)
    assert len(hits) == 2
    page_origin = _page_items(view)[1].pos()
    z_page = _page_items(view)[1].zValue()
    for item, expected in zip(hits, [(10.0, 10.0, 40.0, 10.0),
                                     (60.0, 30.0, 30.0, 10.0)]):
        assert item.pos() == page_origin
        assert (item.rect().x(), item.rect().y(), item.rect().width(),
                item.rect().height()) == pytest.approx(expected)
        assert item.acceptedMouseButtons() == Qt.NoButton
        assert item.zValue() > z_page
    assert (hits[1].brush().color().alpha()
            > hits[0].brush().color().alpha())

    view.clear_hits()
    assert _hit_items(view) == []


def test_drawing_still_works_with_highlights_present(view, pdf):
    """高亮不吞鼠标事件：压着高亮落笔，笔迹照常进模型。"""
    view.open(pdf)
    view.show_hits(1, [(0, 0, 300, 200)], current=0)
    view.goto_page(1)
    QTest.qWait(80)

    _drag(view, [QPoint(60, 60), QPoint(220, 120)])
    assert [len(page.strokes) for page in view.document.pages] == [0, 1, 0]
    assert len(_hit_items(view)) == 1


def test_goto_page_two_centers_and_emits(view, pdf):
    view.open(pdf)
    seen: list[int] = []
    view.pageChanged.connect(seen.append)
    view.goto_page(2)
    assert view.current_page == 2
    assert seen == [2]
    QTest.qWait(120)          # 等一次去抖渲染
    item = _page_items(view)[2]
    assert item.pixmap().width() == pytest.approx(
        250.0 * view._page_render_scale(), abs=1.0)
    rect = item.sceneBoundingRect()
    assert (rect.width(), rect.height()) == pytest.approx((250.0, 400.0))


def test_zoom_transform_and_cursor_anchor(view, pdf):
    view.open(pdf)
    view.set_zoom(2.0)
    assert view.zoom == pytest.approx(2.0)
    m = view.transform()
    assert (m.m11(), m.m22()) == pytest.approx((2.0, 2.0))

    cursor = QPointF(130.0, 90.0)
    before = view.mapToScene(cursor)
    view.zoom_at(cursor, 1.25)
    after = view.mapToScene(cursor)
    assert view.zoom == pytest.approx(2.5)
    # 光标下的场景点在缩放前后必须停在同一处
    assert after.x() == pytest.approx(before.x(), abs=1e-6)
    assert after.y() == pytest.approx(before.y(), abs=1e-6)


def test_draw_on_page_one_at_zoom_two_stores_page_local(view, pdf):
    view.open(pdf)
    view.set_zoom(2.0)
    view.goto_page(1)
    QTest.qWait(120)
    origin = _page_items(view)[1].pos()

    press, move, release = QPoint(60, 60), QPoint(140, 90), QPoint(220, 120)
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=press)
    _send_move(view, move.x(), move.y(), Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=release)

    assert [len(page.strokes) for page in view.document.pages] == [0, 1, 0]
    stroke = view.document.pages[1].strokes[0]
    expected = [view.mapToScene(QPointF(p)) - origin for p in (press, move, release)]
    assert len(stroke.points) == 3
    for (x, y), want in zip(stroke.points, expected):
        assert x == pytest.approx(want.x(), abs=1e-6)
        assert y == pytest.approx(want.y(), abs=1e-6)
    # 页面局部坐标：落在 0..页宽 / 0..页高 内
    width, height = view.document.pages[1].width, view.document.pages[1].height
    assert all(0.0 <= x <= width and 0.0 <= y <= height for x, y in stroke.points)

    # 换缩放触发重绘：同一条笔迹仍是页面 1 上唯一的路径项
    view.set_zoom(1.5)
    QTest.qWait(120)
    paths = [i for i in view.scene().items()
             if isinstance(i, QGraphicsPathItem) and i.data(0) is stroke]
    assert len(paths) == 1
    assert paths[0].pos() == origin
    box = paths[0].path().boundingRect()
    xs = [p[0] for p in stroke.points]
    ys = [p[1] for p in stroke.points]
    assert box.left() == pytest.approx(min(xs), abs=1e-6)
    assert box.top() == pytest.approx(min(ys), abs=1e-6)


def test_eraser_one_command_per_drag_and_undo_restores_order(view, pdf):
    view.open(pdf)
    view.set_zoom(2.0)
    view.goto_page(1)
    QTest.qWait(120)

    _drag(view, [QPoint(60, 60), QPoint(220, 120)])       # 第 1 条：斜线
    _drag(view, [QPoint(60, 140), QPoint(220, 140)])      # 第 2 条：横线
    strokes = view.document.pages[1].strokes
    assert len(strokes) == 2
    first, second = strokes

    # 橡皮从第 1 条拖到第 2 条：一次拖动 = 一个撤销单位（真实拖动必有中间移动）
    view.set_tool("eraser")
    _drag(view, [QPoint(60, 60), QPoint(60, 100), QPoint(60, 140)])
    assert view.document.pages[1].strokes == []
    assert [i for i in view.scene().items() if isinstance(i, QGraphicsPathItem)] == []

    cmd = view.history.pop_undo()
    assert cmd.page_index == 1
    restored = view.document.pages[1].strokes
    assert restored == [first, second]
    assert restored[0] is first and restored[1] is second
    paths = [i for i in view.scene().items() if isinstance(i, QGraphicsPathItem)]
    assert len(paths) == 2

    # 清空本页再撤销：整页笔迹按原顺序放回
    view.clear_page()
    assert view.document.pages[1].strokes == []
    view.history.pop_undo()
    assert view.document.pages[1].strokes == [first, second]


def test_wheel_scrolls_without_zoom(view, pdf):
    view.open(pdf)
    QTest.qWait(50)
    fitted = view.zoom
    vbar = view.verticalScrollBar()
    hbar = view.horizontalScrollBar()
    assert vbar.maximum() > 0 and hbar.maximum() > 0
    vbar.setValue(0)
    hbar.setValue(0)

    _wheel(view, 130, 90, -120)
    assert view.zoom == pytest.approx(fitted)
    assert vbar.value() > 0

    hbar.setValue(0)
    _wheel(view, 130, 90, -120, Qt.ShiftModifier)
    assert view.zoom == pytest.approx(fitted)
    assert hbar.value() > 0


def test_hover_adds_no_strokes(view, pdf):
    view.open(pdf)
    assert view.viewport().hasMouseTracking() is True
    for y in (30, 60, 90, 120):
        _send_move(view, 130, y, Qt.NoButton)
    QTest.qWait(60)
    assert [len(page.strokes) for page in view.document.pages] == [0, 0, 0]
    assert [i for i in view.scene().items() if isinstance(i, QGraphicsPathItem)] == []


def test_close_document_clears_and_reopen_works(view, pdf):
    view.open(pdf)
    view.close_document()
    assert view.document is None
    assert view.pdf_path is None and view.source is None
    assert view.scene().items() == []
    view.open(pdf)
    assert view.source.page_count == 3


def test_open_loads_sidecar_strokes(view, pdf):
    doc = PdfDocument([Page(200.0, 300.0), Page(300.0, 200.0), Page(250.0, 400.0)])
    doc.pages[0].strokes.append(Stroke([(10.0, 20.0), (30.0, 40.0)], "#ff0000", 3.0))
    pdfstore.save_sidecar(pdf, doc)

    view.open(pdf)
    assert len(view.document.pages[0].strokes) == 1
    paths = [i for i in view.scene().items() if isinstance(i, QGraphicsPathItem)]
    assert len(paths) == 1
    assert paths[0].data(0).color == "#ff0000"


def test_keys_switch_tool_width_and_reset_zoom_in_document_mode(view, pdf):
    """文档模式焦点在 PdfView：B/Q 切工具、1–9 换颜色、0 复位 100%。"""
    view.open(pdf)
    view.setFocus()
    assert view.zoom != pytest.approx(1.0)      # 打开即适应页宽，不是 100%

    QTest.keyClick(view, Qt.Key_Q)
    assert view.tool == "eraser"
    QTest.keyClick(view, Qt.Key_B)
    assert view.tool == "pen"

    for index, key in enumerate((Qt.Key_1, Qt.Key_2, Qt.Key_3)):
        QTest.keyClick(view, key)
        assert view.color == PALETTE[index]      # 1–3 = 红 / 蓝 / 黄

    QTest.keyClick(view, Qt.Key_0)
    assert view.zoom == pytest.approx(1.0)


def test_pen_width_is_stored_in_points_at_fixed_reference(view, pdf):
    """笔宽存 PDF 点：固定按 900px 页宽折算，之后缩放/改窗口不改数据。"""
    from sketchpad.ui.pdfview import PEN_WIDTH_REFERENCE_PX, PEN_WIDTH_SCALE
    view.open(pdf)
    view.set_width(1.5)
    view.set_zoom(2.0)
    view.goto_page(1)
    QTest.qWait(120)
    ref = PEN_WIDTH_REFERENCE_PX / SIZES[1][0]
    layer = view._layers[1]
    assert view._effective_width(layer) == pytest.approx(1.5 * PEN_WIDTH_SCALE / ref, abs=1e-6)

    _drag(view, [QPoint(60, 60), QPoint(140, 90), QPoint(220, 120)])
    strokes = [s for page in view.document.pages for s in page.strokes]
    assert len(strokes) == 1
    stroke = strokes[0]

    assert stroke.width == pytest.approx(1.5 * PEN_WIDTH_SCALE / ref, abs=1e-6)
    # 窗口宽窄不再影响换算：同一“1.5”永远存同一个点宽
    before = view._effective_width(layer)
    view.resize(420, 360)
    QTest.qWait(30)
    assert view._effective_width(layer) == pytest.approx(before, abs=1e-9)
    assert stroke.width * ref == pytest.approx(1.5 * PEN_WIDTH_SCALE, abs=1e-6)

    paths = [i for i in view.scene().items()
             if isinstance(i, QGraphicsPathItem) and i.data(0) is stroke]
    assert len(paths) == 1
    assert paths[0].pen().widthF() == pytest.approx(
        max(stroke.width, 1.0 / view.zoom), abs=1e-6)


# ---- 旋转页（v2.4-1）：模型只存显示坐标，r 落进 sidecar ----


def _make_rotated_pdf(path, rotation=90, size=(200, 300)):
    """一页旋转 PDF，文字在未旋转 (50, 60)；旋转后页框 300×200。"""
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_text((50, 60), "alpha", fontsize=12)
    page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


def test_open_rotated_page_records_rotation_and_display_size(view, tmp_path):
    """page.rotation 取 PDF 真实旋转角（不是写死 0），宽高仍是显示页框尺寸。"""
    pdf = _make_rotated_pdf(tmp_path / "rot.pdf", 90)
    view.open(pdf)
    page = view.document.pages[0]
    assert (page.width, page.height) == pytest.approx((300.0, 200.0))
    assert page.rotation == 90
    assert view.source.rotation(0) == 90


def test_rotated_page_hits_and_strokes_use_display_frame(view, tmp_path):
    """搜索高亮落在显示页框内；落笔存显示坐标（0..300 × 0..200），不是未旋转坐标。"""
    pdf = _make_rotated_pdf(tmp_path / "rot.pdf", 90)
    view.open(pdf)
    page = view.document.pages[0]

    rects = view.source.search_page(0, "alpha")
    assert len(rects) == 1
    x0, y0, x1, y1 = rects[0]
    assert 0.0 <= x0 < x1 <= page.width
    assert 0.0 <= y0 < y1 <= page.height
    # 未换算的 rect 是 x 50..79 / y 47..64：乘 rotation_matrix 后 x 必须挪到右边
    assert x0 > 100.0

    view.goto_page(0)
    QTest.qWait(120)
    view.show_hits(0, rects)
    hit = _hit_items(view)[0]
    assert (hit.rect().x(), hit.rect().y()) == pytest.approx((x0, y0))
    assert hit.pos() == _page_items(view)[0].pos()

    # 压着高亮落笔：点仍存显示坐标，且落在显示页框（300×200）内
    view.set_zoom(2.0)
    origin = _page_items(view)[0].pos()
    view.centerOn(QPointF(origin.x() + page.width / 2,
                          origin.y() + page.height / 2))
    QTest.qWait(80)
    start = QPointF(origin.x() + 60.0, origin.y() + 40.0)
    end = QPointF(origin.x() + 140.0, origin.y() + 120.0)
    _drag(view, [view.mapFromScene(start), view.mapFromScene(end)])
    strokes = page.strokes
    assert len(strokes) == 1
    for x, y in strokes[0].points:
        assert 0.0 <= x <= page.width and 0.0 <= y <= page.height
    # 首点 = 按下位置在页面局部（显示）坐标里的落点；屏幕取整误差 < 1pt
    assert strokes[0].points[0] == pytest.approx((60.0, 40.0), abs=1.0)


def test_rotation_is_written_to_sidecar_and_survives_reopen(view, tmp_path):
    """保存 sidecar 后 r=270；重新打开读回来仍是 270。"""
    pdf = _make_rotated_pdf(tmp_path / "rot.pdf", 270)
    view.open(pdf)
    assert view.document.pages[0].rotation == 270
    path = pdfstore.save_sidecar(pdf, view.document)
    text = path.read_text(encoding="utf-8")
    assert '"r":270' in text

    view.close_document()
    view.open(pdf)
    assert view.document.pages[0].rotation == 270
    assert (view.document.pages[0].width,
            view.document.pages[0].height) == pytest.approx((300.0, 200.0))


# ---- 跨页拖笔（P2-1）：笔迹只属于起笔那一页 ----


def _drag_scene(view: PdfView, points: list[tuple[float, float]]) -> None:
    """按场景坐标拖动；页面在场景里居中，屏幕坐标随缩放漂，场景点更稳。"""
    _drag(view, [view.mapFromScene(QPointF(x, y)) for x, y in points])


def _show_page_boundary(view: PdfView, center_y: float) -> None:
    """缩到 50% 并把 center_y 居中：第 1 页底部、页间空隙、第 2 页顶部同时可见。"""
    view.set_zoom(0.5)
    view.centerOn(QPointF(SCENE_W / 2.0, center_y))
    QTest.qWait(100)


def test_drag_across_page_boundary_stays_on_start_page(view, pdf):
    """从第 1 页拖过空隙/第 2 页：只提交到第 1 页，所有点都在页框内，终点钳到页底。"""
    view.open(pdf)
    page0 = view.document.pages[0]
    _show_page_boundary(view, SIZES[0][1] + 20.0)     # 视口跨第 1 页底边

    # 起笔在第 1 页内，经过页间空隙，抬笔已落在第 2 页上
    start = (100.0, 250.0)
    gap = (100.0, SIZES[0][1] + PAGE_GAP / 2.0)
    on_page_two = (100.0, SIZES[0][1] + PAGE_GAP + 40.0)
    _drag_scene(view, [start, gap, on_page_two])

    # 笔迹只进起始页；下一页一条不多
    assert [len(page.strokes) for page in view.document.pages] == [1, 0, 0]
    stroke = page0.strokes[0]
    assert all(0.0 <= x <= page0.width and 0.0 <= y <= page0.height
               for x, y in stroke.points)
    # 页外移动被丢弃，终点钳回页底（屏幕取整误差 ≤1pt/缩放）
    assert stroke.points[0] == pytest.approx((50.0, 250.0), abs=2.0)
    assert stroke.points[-1] == pytest.approx((50.0, page0.height), abs=2.0)
    # 预览/提交都不越页：唯一的路径项挂在第 1 页原点，包围盒不出页框
    paths = [i for i in view.scene().items()
             if isinstance(i, QGraphicsPathItem) and i.data(0) is stroke]
    assert len(paths) == 1
    assert paths[0].pos() == _page_items(view)[0].pos()
    box = paths[0].path().boundingRect()
    assert box.top() >= -1.0 and box.bottom() <= page0.height + 1.0


def test_eraser_across_page_boundary_only_erases_start_page(view, pdf):
    """橡皮从第 1 页拖到第 2 页：只擦起始页，下一页笔迹原封不动。"""
    page0_stroke = Stroke([(20.0, 290.0), (180.0, 290.0)], "#ff0000", 4.0)
    page1_stroke = Stroke([(20.0, 10.0), (280.0, 10.0)], "#00ff00", 4.0)
    pdfstore.save_sidecar(pdf, PdfDocument([
        Page(*SIZES[0], 0, [page0_stroke]),
        Page(*SIZES[1], 0, [page1_stroke]),
        Page(*SIZES[2], 0, []),
    ]))
    view.open(pdf)
    _show_page_boundary(view, SIZES[0][1] + 40.0)

    view.set_tool("eraser")
    page1_top = SIZES[0][1] + PAGE_GAP + 10.0          # 第 2 页那条横线所在高度
    _drag_scene(view, [(100.0, 290.0),                          # 第 1 页：压住红色横线
                       (100.0, SIZES[0][1] + PAGE_GAP / 2.0),   # 页间空隙
                       (100.0, page1_top)])                     # 第 2 页：压住绿色横线

    assert view.document.pages[0].strokes == []        # 起始页被擦
    assert view.document.pages[1].strokes == [page1_stroke]   # 下一页不受影响
    cmd = view.history.pop_undo()
    assert cmd.page_index == 0
    assert view.document.pages[0].strokes == [page0_stroke]


# ---- 视口外页面位图回收（v2.7）：30 页文档常驻约 192MB ----

MANY_SIZES = ((200.0, 300.0),) * 20          # 等尺寸 20 页，总高 ≈ 6152pt


def test_offscreen_page_pixmaps_are_evicted_and_rerendered(view, tmp_path):
    """滚走的页释放底图、滚回来按当前缩放重渲；当前页 ±1 始终保留。"""
    pdf = _make_pdf(tmp_path / "many.pdf", MANY_SIZES)
    view.open(pdf)
    QTest.qWait(150)                           # 覆盖 40ms 去抖 + 渲染
    items = _page_items(view)
    assert len(items) == 20
    assert items[0].pixmap().isNull() is False      # 首屏那页已渲染
    assert items[10].pixmap().isNull() is True      # 远处的页从来没渲过
    assert items[0].sceneBoundingRect().height() == pytest.approx(300.0)

    # 翻到相邻页：current ±1 保留，第 0 页的位图不动
    view.goto_page(1)
    QTest.qWait(150)
    assert view.current_page == 1
    assert items[0].pixmap().isNull() is False
    assert items[1].pixmap().isNull() is False

    # 翻到第 10 页：第 0 页离开 keep 集合，底图与缩放记号一起清空
    view.goto_page(10)
    QTest.qWait(150)
    assert view.current_page == 10
    assert items[0].pixmap().isNull() is True
    assert view._layers[0].rendered_zoom is None
    assert items[10].pixmap().isNull() is False
    assert items[10].pixmap().width() == pytest.approx(
        200.0 * view._page_render_scale(), abs=1.0)

    # 滚回第 0 页：重新渲染，尺寸仍 = 页面点 × 当前渲染倍率
    view.goto_page(0)
    QTest.qWait(150)
    assert items[0].pixmap().isNull() is False
    assert items[0].pixmap().width() == pytest.approx(
        200.0 * view._page_render_scale(), abs=1.0)
    assert view._layers[0].rendered_zoom == pytest.approx(view._page_render_scale())
    assert items[0].sceneBoundingRect().width() == pytest.approx(200.0)
    assert items[0].sceneBoundingRect().height() == pytest.approx(300.0)
    # 反过来第 10 页被回收了
    assert items[10].pixmap().isNull() is True


def test_zoom_rerenders_synchronously_without_blurry_frame(view, pdf):
    """显式缩放后不等去抖计时器：位图立刻换成新倍率，避免旧图被拉伸的发糊帧。"""
    view.open(pdf)
    view.render_visible()
    view.set_zoom(2.0)
    page = view.document.pages[0]
    pixmap = _page_items(view)[0].pixmap()
    assert pixmap.width() == pytest.approx(
        page.width * view._page_render_scale(), abs=2)
    assert view._layers[0].rendered_zoom == pytest.approx(
        view._page_render_scale())
    # 同步重渲覆盖全部超采样区间（≤350%），缩放后不会留下旧倍率的糊图
    view._device_ratio = lambda: 3.0
    view.set_zoom(4.0)
    assert view._layers[0].rendered_zoom == pytest.approx(
        view._page_render_scale())


def test_low_zoom_supersamples_page_for_crisper_text(view, pdf):
    """350% 以内都按 2× 渲染再平滑缩回；超过 350% 才回到 1:1 原生栅格。"""
    view.open(pdf)
    view.set_zoom(1.0)
    layer = view._layers[0]
    pixmap = layer.pixmap_item.pixmap()
    assert view._page_render_scale() == pytest.approx(2.0)
    assert pixmap.width() == pytest.approx(layer.width * 2.0, abs=2)
    assert pixmap.devicePixelRatio() == pytest.approx(2.0)
    assert layer.pixmap_item.sceneBoundingRect().width() == pytest.approx(layer.width)

    view.set_zoom(3.0)                            # 300%：仍在超采样区间
    assert view._page_render_scale() == pytest.approx(6.0)
    assert view._layers[0].pixmap_item.pixmap().devicePixelRatio() == pytest.approx(2.0)
    assert view._layers[0].pixmap_item.sceneBoundingRect().width() == pytest.approx(layer.width)

    view.set_zoom(4.0)                            # 400%：像素已够密，不再超采样
    assert view._page_render_scale() == pytest.approx(4.0)
    assert view._layers[0].pixmap_item.pixmap().devicePixelRatio() == pytest.approx(1.0)
    assert view._layers[0].pixmap_item.sceneBoundingRect().width() == pytest.approx(layer.width)


def test_page_bitmap_uses_device_pixel_ratio(view, pdf):
    """副屏 150%：位图按 页宽×渲染倍率 出图，位图 DPR 让场景包围盒仍是页面矩形。"""
    view._device_ratio = lambda: 1.5
    view.open(pdf)
    view.set_zoom(2.0)
    view.render_visible()
    first = _page_items(view)[0]
    page = view.document.pages[0]
    pixmap = first.pixmap()
    assert pixmap.width() == pytest.approx(
        page.width * view._page_render_scale(), abs=2)
    assert pixmap.height() == pytest.approx(
        page.height * view._page_render_scale(), abs=2)
    assert pixmap.devicePixelRatio() == pytest.approx(view._page_render_ratio())
    assert first.sceneBoundingRect().width() == pytest.approx(page.width)
    assert pixmap.width() == pytest.approx(
        first.sceneBoundingRect().width() * view.zoom * 1.5
        * (SUPERSAMPLE if view.zoom <= SUPERSAMPLE_MAX_ZOOM else 1.0), abs=2)


# ---- 文字选择（v2.10）----

def _make_text_pdf(path, text="alpha beta gamma", size=(300.0, 200.0)):
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_text((40, 80), text, fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def _char_points(view: PdfView, page_index: int = 0) -> list[QPoint]:
    """每个字符框中心映射到视口坐标（按阅读顺序）。"""
    layer = view._layers[page_index]
    points = []
    for _text, x0, y0, x1, y1, _block, _line in view._units(page_index):
        points.append(view.mapFromScene(QPointF(
            layer.x + (x0 + x1) / 2.0, layer.y + (y0 + y1) / 2.0)))
    return points


def _select_from_to(view: PdfView, start: QPoint, end: QPoint) -> None:
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    _send_move(view, end.x(), end.y(), Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)


def test_select_tool_drag_highlights_chars_and_copies_text(view, tmp_path):
    from PySide6.QtGui import QGuiApplication
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    view.open(pdf)
    view.set_tool("select")
    assert view.tool == "select"
    # 鼠标工具的基准光标是可平移的手形，悬停到文字上才换成 I 形
    assert view.viewport().cursor().shape() == Qt.OpenHandCursor

    points = _char_points(view)
    assert len(points) == len("alphabetagamma")   # 空格不建字符框
    _send_move(view, points[2].x(), points[2].y())
    assert view.viewport().cursor().shape() == Qt.IBeamCursor
    _select_from_to(view, points[0], points[-1])

    assert len(view._selection_items) == 1        # 同一行相邻字符并成一段高亮
    assert view.selected_text() == "alpha beta gamma"

    got = []
    view.selectionCopied.connect(got.append)
    count = view.copy_selection()
    assert count == len("alpha beta gamma")
    assert got == [count]
    assert QGuiApplication.clipboard().text() == "alpha beta gamma"


def test_select_backwards_and_single_char(view, tmp_path):
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    view.open(pdf)
    view.set_tool("select")
    points = _char_points(view)

    _select_from_to(view, points[-1], points[0])
    assert view.selected_text() == "alpha beta gamma"   # 反拖也是阅读顺序

    _select_from_to(view, points[0], points[0])
    assert view.selected_text() == "a"                  # 只选一个字符
    assert len(view._selection_items) == 1

    # 选中 "beta" 的 4 个字符（b..a）
    beta = [i for i, unit in enumerate(view._units(0)) if unit[0] == "b"]
    start = points[beta[0]]
    end = points[beta[0] + 3]
    _select_from_to(view, start, end)
    assert view.selected_text() == "beta"


def test_chinese_selection_is_per_character(view, tmp_path):
    """MuPDF 的 words 会把一整行中文当一个词；选择单位必须是单字。"""
    pdf = tmp_path / "cn.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300.0, height=200.0)
    page.insert_text((40, 80), "行测理论", fontsize=16, fontname="china-s")
    doc.save(str(pdf))
    doc.close()

    view.open(pdf)
    view.set_tool("select")
    points = _char_points(view)
    assert len(points) == 4                       # 每个汉字一个可选中单位

    _select_from_to(view, points[1], points[1])
    assert view.selected_text() == "测"           # 只选中间一个字

    _select_from_to(view, points[1], points[3])
    assert view.selected_text() == "测理论"       # 相邻字连续选，不整行
    assert len(view._selection_items) == 1        # 同一行合并成一段


def test_click_empty_area_and_switch_tool_clear_selection(view, tmp_path):
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    view.open(pdf)
    view.set_tool("select")
    points = _char_points(view)
    _select_from_to(view, points[0], points[0])
    assert view.selected_text() == "a"

    # 点页面左上角空白：选区清空
    blank = view.mapFromScene(QPointF(5.0, 5.0))
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=blank)
    assert view.selected_text() == ""
    assert view._selection_items == []

    _select_from_to(view, points[0], points[0])
    view.set_tool("pen")                       # 换回画笔：选区与 I 形光标一起收掉
    assert view.selected_text() == ""
    assert view.viewport().cursor().shape() == Qt.BlankCursor


def test_pen_and_eraser_hide_system_cursor_and_show_dot(view, pdf):
    """PDF 里画笔原来还是箭头；现在与草稿画布一致：隐藏光标，只画笔尖圆点。"""
    view.open(pdf)
    view.set_tool("pen")
    center = view.viewport().rect().center()
    _send_move(view, center.x(), center.y())          # 无按键 = 悬停
    assert view.viewport().cursor().shape() == Qt.BlankCursor
    assert view.dot_radius_px() >= 2.0
    assert view._cursor_pos is not None
    QTest.qWait(30)
    image = view.viewport().grab().toImage()
    color = image.pixelColor(center.x(), center.y())
    assert min(color.red(), color.green(), color.blue()) < 250   # 圆点盖在白页上

    view.set_tool("eraser")
    assert view.viewport().cursor().shape() == Qt.BlankCursor
    assert view.dot_radius_px() == 12.0

    view.set_tool("select")
    assert view.viewport().cursor().shape() == Qt.OpenHandCursor


def test_copy_without_selection_is_noop(view, tmp_path):
    from PySide6.QtGui import QGuiApplication
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    view.open(pdf)
    QGuiApplication.clipboard().setText("keep")
    assert view.copy_selection() == 0
    assert QGuiApplication.clipboard().text() == "keep"


def test_join_words_keeps_chinese_without_spaces_and_breaks_lines():
    words = [
        ("中", 0.0, 0.0, 14.0, 16.0, 0, 0),
        ("文", 14.1, 0.0, 28.0, 16.0, 0, 0),
        ("abc", 40.0, 0.0, 60.0, 16.0, 0, 0),      # 有空隙 → 空格
        ("def", 60.0, 20.0, 80.0, 36.0, 0, 1),     # 换行 → \n
    ]
    from sketchpad.ui.pdfview import _join_words
    assert _join_words(words) == "中文 abc\ndef"


def test_stroke_path_follows_every_sampled_point():
    """折线必须逐个穿过采样点；中点+quadTo 会把转折抹圆、整体内收。"""
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (20.0, 10.0)]
    path = _stroke_path(points)
    assert path.elementCount() == len(points)
    got = [(path.elementAt(i).x, path.elementAt(i).y)
           for i in range(path.elementCount())]
    assert got == points


def test_dense_handwriting_samples_are_not_thinned_away(view, pdf):
    """1 屏幕像素间隔的采样点必须保留，否则小字会被抽稀成折线。"""
    view.open(pdf)
    view.set_tool("pen")
    start = QPoint(80, 80)
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    moves = [QPoint(80 + i, 80 + i) for i in range(1, 9)]
    for pos in moves:
        _send_move(view, pos.x(), pos.y(), Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=moves[-1])
    stroke = view.document.pages[0].strokes[-1]
    assert len(stroke.points) >= len(moves)       # 几乎不丢点

def test_eraser_ring_follows_pointer_while_button_held(view, pdf):
    """按住橡皮拖动时圆环必须跟着走，不能冻在按下点。"""
    view.open(pdf)
    view.set_tool("eraser")
    _send_move(view, 50.0, 60.0)
    before = view._cursor_pos
    assert before is not None
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(50, 60))
    _send_move(view, 140.0, 110.0, Qt.LeftButton)
    assert view._cursor_pos is not None
    assert (view._cursor_pos.x(), view._cursor_pos.y()) != (before.x(), before.y())
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(140, 110))


# ---- 鼠标工具（V）：拖空白平移 · 拖文字选字 · 光标 ----


def _press_drag_release(view: PdfView, start: QPoint, end: QPoint,
                        steps: int = 3) -> None:
    """按下 → steps 个按住左键的中间移动 → 终点也走一步再松手：真拖动必有落在终点上的 move。"""
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    for step in range(1, steps + 2):
        ratio = step / (steps + 1)
        _send_move(view, start.x() + (end.x() - start.x()) * ratio,
                   start.y() + (end.y() - start.y()) * ratio, Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)


def _center_view_point(view: PdfView, local: tuple[float, float]) -> QPoint:
    """把某个「页面局部点」摆到视口中心并返回它的视口坐标：保证该点一定看得见。"""
    layer = view._layers[0]
    scene_point = QPointF(layer.x + local[0], layer.y + local[1])
    view.centerOn(scene_point)
    QTest.qWait(40)
    pos = view.mapFromScene(scene_point)
    return QPoint(int(pos.x()), int(pos.y()))


def test_mouse_tool_drag_blank_pans_and_keeps_zoom(view, tmp_path):
    """鼠标工具拖空白 = 平移：只改两条滚动条，缩放与自适应页宽一律不碰。"""
    pdf = _make_text_pdf(tmp_path / "text.pdf")        # 一页 300×200，文字在 y≈80
    view.open(pdf)
    view.set_zoom(2.0)                                 # 放大后横竖都有得滚
    view.set_tool("select")
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    assert hbar.maximum() > 0 and vbar.maximum() > 0
    zoom, auto_fit = view.zoom, view.auto_fit
    start = _center_view_point(view, (150.0, 160.0))    # 文字行以下：无字处
    h0, v0 = hbar.value(), vbar.value()

    _press_drag_release(view, start, QPoint(start.x() - 40, start.y() + 60))

    # 跟手：鼠标往左下走 → 内容跟着往左下 → 横向值变大、纵向值变小
    assert hbar.value() == pytest.approx(h0 + 40, abs=6)
    assert vbar.value() == pytest.approx(v0 - 60, abs=6)
    assert hbar.value() > h0 and vbar.value() < v0
    assert view.zoom == pytest.approx(zoom)            # 平移绝不改缩放
    assert view.auto_fit is auto_fit                   # 也不动「适应页宽」开关
    assert view.selected_text() == ""                  # 空白处不是选字
    assert view._panning is False                      # 松手手势收干净


def test_mouse_tool_drag_on_text_selects_without_panning(view, tmp_path):
    """同一个工具拖过文字仍是选字：选中文字照旧，滚动条一动不动。"""
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    view.open(pdf)
    view.set_zoom(1.5)                                 # 放大到两条滚动条都能动，整行字仍在视口内
    assert view.horizontalScrollBar().maximum() > 0 and view.verticalScrollBar().maximum() > 0
    view.set_tool("select")
    _center_view_point(view, (90.0, 76.0))            # 把文字行摆进视口
    points = _char_points(view)
    assert points[0].x() >= 0 and points[-1].x() < view.viewport().width()
    h0, v0 = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())

    _select_from_to(view, points[0], points[-1])

    assert view.selected_text() == "alpha beta gamma"
    assert view.horizontalScrollBar().value() == h0
    assert view.verticalScrollBar().value() == v0
    assert view._panning is False


def test_mouse_tool_cursors_track_what_is_under_pointer(view, tmp_path):
    """光标跟着指针下的东西走：文字 I 形、空白张开的手、平移中握紧、松手回手形。"""
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    view.open(pdf)
    view.set_tool("select")
    blank = _center_view_point(view, (150.0, 160.0))
    char = _char_points(view)[0]

    _send_move(view, char.x(), char.y())
    assert view.viewport().cursor().shape() == Qt.IBeamCursor
    _send_move(view, blank.x(), blank.y())
    assert view.viewport().cursor().shape() == Qt.OpenHandCursor

    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=blank)
    assert view.viewport().cursor().shape() == Qt.ClosedHandCursor
    _send_move(view, blank.x() + 10, blank.y() + 10, Qt.LeftButton)
    assert view.viewport().cursor().shape() == Qt.ClosedHandCursor
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(blank.x() + 10,
                                                                  blank.y() + 10))
    assert view.viewport().cursor().shape() == Qt.OpenHandCursor


def test_pen_tool_on_blank_draws_and_never_pans(view, tmp_path):
    """非鼠标工具保持现状：画笔按空白仍落笔，绝不变成平移。"""
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    view.open(pdf)
    view.set_zoom(2.0)
    view.set_tool("pen")
    start = _center_view_point(view, (150.0, 160.0))
    h0, v0 = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())

    _press_drag_release(view, start, QPoint(start.x() - 30, start.y() + 40))

    assert len(view.document.pages[0].strokes) == 1
    assert view.horizontalScrollBar().value() == h0
    assert view.verticalScrollBar().value() == v0
    assert view._panning is False


def test_note_tool_drag_blank_still_places_note_without_panning(view, tmp_path):
    """note 工具下点空白仍是放笔记（不是平移），拖出位移也不滚页面。"""
    pdf = _make_text_pdf(tmp_path / "text.pdf")
    view.open(pdf)
    view.set_zoom(2.0)
    view.set_tool("note")
    start = _center_view_point(view, (150.0, 160.0))
    h0, v0 = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
    got: list[tuple] = []
    view.notePlacementRequested.connect(lambda *a: got.append(a))

    _press_drag_release(view, start, QPoint(start.x() - 30, start.y() + 40))

    assert len(got) == 1 and got[0][0] == 0
    assert view.horizontalScrollBar().value() == h0
    assert view.verticalScrollBar().value() == v0
    assert view._panning is False



# ---- 右键图钉（noteContextRequested）：只发上下文信号，不开卡/不落笔 ----

def _right_click(view: PdfView, pos: QPoint) -> None:
    """在视口上发一次右键菜单事件；QContextMenuEvent 会由视口冒泡到视图。"""
    global_pos = view.viewport().mapToGlobal(pos)
    ev = QContextMenuEvent(QContextMenuEvent.Mouse, pos, global_pos)
    QApplication.sendEvent(view.viewport(), ev)


def test_context_menu_on_pin_emits_page_id_and_global_pos(view, pdf):
    view.open(pdf)
    note = Note("n-1", 50.0, 60.0, name="我的笔记")
    view.add_note(0, note)
    anchor = view.note_anchor_view_pos(0, "n-1")
    got: list[tuple[int, str, QPoint]] = []
    opened: list[tuple[int, str]] = []
    placed: list[tuple] = []
    view.noteContextRequested.connect(lambda p, i, g: got.append((p, i, g)))
    view.noteRequested.connect(lambda p, i: opened.append((p, i)))
    view.notePlacementRequested.connect(lambda *a: placed.append(a))

    _right_click(view, anchor)

    assert len(got) == 1
    page, note_id, global_pos = got[0]
    assert (page, note_id) == (0, "n-1")
    assert global_pos == view.viewport().mapToGlobal(anchor)
    # 右键只发上下文请求：不开卡、不放新笔记、不落笔、锚点不动
    assert opened == [] and placed == []
    assert view.document.pages[0].strokes == []
    assert (note.x, note.y) == (50.0, 60.0)


def test_context_menu_on_pin_under_note_tool_still_only_emits_context(view, pdf):
    view.open(pdf)
    view.add_note(0, Note("n-1", 50.0, 60.0))
    view.set_tool("note")
    anchor = view.note_anchor_view_pos(0, "n-1")
    got: list[tuple] = []
    placed: list[tuple] = []
    view.noteContextRequested.connect(lambda *a: got.append(a))
    view.notePlacementRequested.connect(lambda *a: placed.append(a))

    _right_click(view, anchor)

    assert len(got) == 1 and got[0][:2] == (0, "n-1")
    assert placed == []
    assert len(view.document.pages[0].notes) == 1


def test_context_menu_on_blank_page_emits_no_note_context(view, pdf):
    view.open(pdf)
    view.add_note(0, Note("n-1", 50.0, 60.0))
    got: list[tuple] = []
    view.noteContextRequested.connect(lambda *a: got.append(a))
    blank = view.mapFromScene(QPointF(5.0, 5.0))     # 页面左上角空白：远离图钉

    _right_click(view, blank)

    assert got == []
