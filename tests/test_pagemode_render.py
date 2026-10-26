"""离屏测试：PDF 底图阅读模式（正常 / 夜间 / 护眼）的滤镜、视图重渲、笔迹独立、缩略图同步。

样例 PDF 全在 tmp_path 现造。断言口径：
- 滤镜对白底/黑底的输出色（容差 ±3），夜间中性、护眼偏暖；
- PdfView 切模式后可见页底图立即按新模式重渲，笔迹项不动；
- ThumbPanel 切模式后缩略图按新模式重排队重渲；
- 未知模式一律 ValueError。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsPathItem

import sketchpad.ui.pdfview as pdfview_mod
import sketchpad.ui.thumb_panel as thumb_panel_mod
from sketchpad.core.settings import Settings
from sketchpad.core.strokes import Stroke
from sketchpad.pdf.document import PdfSource
from sketchpad.pdf.pagemode import apply_page_mode
from sketchpad.ui.pdfview import PdfView
from sketchpad.ui.thumb_panel import ThumbPanel

# 两页：竖版 + 横版；文字写在页中，四角留白给底图取色
SIZES = ((200.0, 300.0), (300.0, 200.0))

# 显示出来的控件必须留 Python 引用，否则包装对象被回收
_ALIVE: list = []

# 白底/黑底经过滤镜后的目标色（Qt 光栅层实测值，±3 容差）
NIGHT_PAGE = QColor("#212121")      # 夜间白底（中性深灰，不纯黑）
NIGHT_INK = QColor("#dadada")       # 夜间黑字（浅灰，不纯白）
EYE_PAGE = QColor("#f3ede2")        # 护眼白底（羊皮纸浅暖色）
EYE_INK = QColor("#413b30")         # 护眼黑字（暖深灰）
WHITE = QColor("#ffffff")
BLACK = QColor("#000000")


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path, sizes=SIZES):
    doc = pymupdf.open()
    for index, (width, height) in enumerate(sizes):
        page = doc.new_page(width=width, height=height)
        page.insert_text((40, 70), f"Page {index + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def pdf(tmp_path):
    return _make_pdf(tmp_path / "pagemode.pdf")


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


# ---- 工具 ----


def _solid(color: str, size: int = 8) -> QImage:
    image = QImage(size, size, QImage.Format_RGB32)
    image.fill(QColor(color))
    return image


def _assert_close(got: QColor, want: QColor, tolerance: int = 3) -> None:
    deltas = (abs(got.red() - want.red()), abs(got.green() - want.green()),
              abs(got.blue() - want.blue()))
    assert max(deltas) <= tolerance, (
        f"{got.name()} 与 {want.name()} 相差 {deltas}（容差 {tolerance}）")


def _corner_color(image: QImage) -> QColor:
    """取图像左上角靠内几像素：这里的 PDF 是空白页角，颜色就是底图色。"""
    assert not image.isNull()
    x = min(3, image.width() - 1)
    y = min(3, image.height() - 1)
    return image.pixelColor(x, y)


def _page_image(view: PdfView, index: int = 0) -> QImage:
    """页面底图项的位图（物理像素；页角即页面左上角）。"""
    pixmap = view._layers[index].pixmap_item.pixmap()
    assert not pixmap.isNull(), "页面底图还没渲染"
    return pixmap.toImage()


def _wait_page_image(view: PdfView, index: int = 0, limit: int = 40) -> QImage:
    for _ in range(limit):
        if not view._layers[index].pixmap_item.pixmap().isNull():
            return _page_image(view, index)
        QTest.qWait(25)
    raise AssertionError("页面底图渲染超时")


def _drain_panel(panel: ThumbPanel, limit: int = 100) -> None:
    for _ in range(limit):
        if panel._render_batch() == 0:
            return
    raise AssertionError(f"{limit} 次 tick 还没排空")


# ---- 1. 滤镜像素 ----


def test_normal_and_unknown_modes_return_input_unchanged():
    image = _solid("#ffffff")
    assert apply_page_mode(image, "normal") is image
    out = apply_page_mode(image, "sepia")
    assert out is image
    assert out.pixelColor(0, 0) == WHITE
    assert apply_page_mode(image, "") is image


@pytest.mark.parametrize("background, mode, expected", [
    ("#ffffff", "night", NIGHT_PAGE),
    ("#000000", "night", NIGHT_INK),
    ("#ffffff", "eye", EYE_PAGE),
    ("#000000", "eye", EYE_INK),
])
def test_mode_filter_pixels(background, mode, expected):
    out = apply_page_mode(_solid(background), mode)
    assert out.format() == QImage.Format_RGB32
    _assert_close(out.pixelColor(4, 4), expected)


def test_night_is_neutral_and_eye_is_warm_on_white_page():
    night = apply_page_mode(_solid("#ffffff"), "night").pixelColor(4, 4)
    eye = apply_page_mode(_solid("#ffffff"), "eye").pixelColor(4, 4)
    # 夜间：三通道接近，不偏色
    assert abs(night.red() - night.blue()) <= 6
    assert abs(night.red() - night.green()) <= 6
    # 护眼：偏暖，红明显高于蓝
    assert eye.red() - eye.blue() >= 12
    assert eye.red() > eye.green() > eye.blue()
    assert eye.lightness() > 180        # 护眼是浅色纸面，不反相


# ---- 2. PdfView：切模式立即重渲可见页 ----


def test_page_mode_rerenders_visible_page(view, pdf):
    view.open(pdf)
    QTest.qWait(80)
    assert view.page_mode == "normal"
    _assert_close(_corner_color(_wait_page_image(view)), WHITE)

    view.set_page_mode("night")
    assert view.page_mode == "night"
    night_image = _page_image(view)
    night = _corner_color(night_image)
    _assert_close(night, NIGHT_PAGE)
    assert abs(night.red() - night.blue()) <= 6

    before = _page_image(view)
    view.set_page_mode("eye")
    assert view.page_mode == "eye"
    after = _page_image(view)
    assert after.cacheKey() != before.cacheKey(), "切护眼后底图位图没换"
    eye = _corner_color(after)
    _assert_close(eye, EYE_PAGE)
    assert eye.red() - eye.blue() >= 12
    assert eye.lightness() > 180        # 护眼是浅色纸面，不反相

    # 页面项在场景里的包围盒不因模式变化而变：底图只换像素
    rect = view._layers[0].pixmap_item.sceneBoundingRect()
    page = view.document.pages[0]
    assert (rect.width(), rect.height()) == pytest.approx((page.width, page.height))


def test_visible_render_calls_use_current_mode(view, pdf, monkeypatch):
    calls: list[str] = []
    real = pdfview_mod.apply_page_mode

    def spy(image, mode):
        calls.append(mode)
        return real(image, mode)

    monkeypatch.setattr(pdfview_mod, "apply_page_mode", spy)
    view.open(pdf)
    QTest.qWait(80)
    calls.clear()

    view.set_page_mode("night")
    assert calls, "切模式后没有重渲任何可见页"
    assert set(calls) == {"night"}

    calls.clear()
    view.set_zoom(view.zoom * 1.2)      # 缩放走 _render_sharp_now 同步重渲
    assert calls, "缩放后没有同步重渲可见页"
    assert set(calls) == {"night"}


# ---- 3. 笔迹不受底图模式影响 ----


def test_strokes_keep_color_and_path_across_page_modes(view, pdf):
    view.open(pdf)
    QTest.qWait(60)

    stroke = Stroke([(10.0, 20.0), (40.0, 50.0), (80.0, 30.0)], "#ff0000", 3.0)
    view.document.pages[0].strokes.append(stroke)
    view._rebind_page(0)

    def _items():
        return [item for item in view.scene().items()
                if isinstance(item, QGraphicsPathItem) and item.data(0) is stroke]

    items = _items()
    assert len(items) == 1
    item = items[0]
    pen_before = item.pen().color()
    width_before = item.pen().widthF()
    path_before = item.path()
    count_before = path_before.elementCount()
    points_before = [(path_before.elementAt(i).x, path_before.elementAt(i).y)
                     for i in range(count_before)]
    assert count_before > 0

    view.set_page_mode("night")
    _assert_close(_corner_color(_page_image(view)), NIGHT_PAGE)

    items_after = _items()
    assert items_after == [item], "切模式后笔迹项被替换/移除了"
    assert item.pen().color() == pen_before
    assert item.pen().widthF() == pytest.approx(width_before)
    assert item.path().elementCount() == count_before
    assert [(item.path().elementAt(i).x, item.path().elementAt(i).y)
            for i in range(count_before)] == points_before


# ---- 4. ThumbPanel：缩略图按新模式重渲 ----


def test_thumb_panel_requeues_thumbnails_in_night_mode(qapp, pdf, monkeypatch):
    calls: list[str] = []
    real = thumb_panel_mod.apply_page_mode

    def spy(image, mode):
        calls.append(mode)
        return real(image, mode)

    monkeypatch.setattr(thumb_panel_mod, "apply_page_mode", spy)
    panel = ThumbPanel()
    _ALIVE.append(panel)
    source = PdfSource(pdf)
    try:
        panel.set_source(source)
        panel.set_page_mode("night")
        assert panel._page_mode == "night"
        _drain_panel(panel)

        icons = [panel.list.item(i).icon() for i in range(panel.list.count())]
        assert any(not icon.isNull() for icon in icons)
        assert calls, "切夜间后缩略图没有重渲"
        assert set(calls) == {"night"}

        icon = icons[0]
        assert not icon.isNull()
        size = icon.availableSizes()[0]
        image = icon.pixmap(size).toImage()
        _assert_close(_corner_color(image), NIGHT_PAGE)
    finally:
        panel.clear()
        panel.close()
        source.close()


# ---- 5. 未知模式 ----


def test_unknown_page_mode_raises_for_view_and_panel(view):
    with pytest.raises(ValueError):
        view.set_page_mode("sepia")
    assert view.page_mode == "normal"

    panel = ThumbPanel()
    _ALIVE.append(panel)
    try:
        with pytest.raises(ValueError):
            panel.set_page_mode("warm")
        assert panel._page_mode == "normal"
    finally:
        panel.close()
