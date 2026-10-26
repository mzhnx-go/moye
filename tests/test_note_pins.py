"""PDF 页上的笔记图标离屏测试：note 工具落点、图钉点击/拖动/× 删除、删除重编号、缩放后仍可点。

坐标一律 PDF 点（场景单位 = 点）；页面局部坐标 = 场景坐标减页面左上角。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsItem, QStyleOptionGraphicsItem

from sketchpad.core.notes import Note
from sketchpad.core.settings import Settings
from sketchpad.ui.note_pin import (CLOSE_CENTER, CLOSE_RADIUS, LABEL_HEIGHT,
                                   LABEL_MAX_WIDTH, LABEL_TOP, PIN_SIZE, NotePin)
from sketchpad.ui.pdfview import PdfView

# 显示出来的视图必须留 Python 引用，否则包装对象被回收
_ALIVE: list[PdfView] = []

# 两页同尺寸：第二页用来验证图钉位置包含页面 y 偏移
SIZES = ((300.0, 400.0), (300.0, 400.0))


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "notes.pdf"
    doc = pymupdf.open()
    for width, height in SIZES:
        doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def view(qapp):
    v = PdfView(Settings())
    v.resize(400, 500)
    v.show()
    QTest.qWaitForWindowExposed(v)
    _ALIVE.append(v)
    yield v
    v.close_document()
    v.close()


def _click(view: PdfView, pos: QPoint) -> None:
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=pos)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=pos)


def _send_move(view: PdfView, x: float, y: float, buttons=Qt.NoButton) -> None:
    """造一条鼠标移动：buttons 决定它算悬停还是按住拖动。"""
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(int(x), int(y))))
    ev = QMouseEvent(QEvent.MouseMove, local, global_pos, Qt.NoButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def _drag_pin(view: PdfView, start: QPoint, end: QPoint,
              steps: int = 3) -> tuple[QPoint, QPoint]:
    """从 start 按住拖到 end（终点也补一步 move，真拖动必然有落在终点上的移动）。"""
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    for step in range(1, steps + 2):
        ratio = step / (steps + 1)
        _send_move(view, start.x() + (end.x() - start.x()) * ratio,
                   start.y() + (end.y() - start.y()) * ratio, Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)
    return start, end


def _close_pos(view: PdfView, page_index: int, note_id: str) -> QPoint:
    """图钉右上角 × 圆心的视口坐标。

    图钉 `ItemIgnoresTransformations`：1 图钉单位 = 1 视口像素，所以直接把
    图钉中心的视口坐标加上 × 在图钉坐标里的偏移即可（与缩放无关）。
    """
    anchor = view.note_anchor_view_pos(page_index, note_id)
    assert anchor is not None
    return QPoint(int(anchor.x() + CLOSE_CENTER.x()), int(anchor.y() + CLOSE_CENTER.y()))


def test_note_tool_emits_placement_without_stroke(view, pdf):
    view.open(pdf)
    view.set_tool("note")
    got: list[tuple] = []
    view.notePlacementRequested.connect(lambda *a: got.append(a))
    _click(view, QPoint(100, 100))
    assert got and got[0][0] == 0
    assert 0 <= got[0][1] <= SIZES[0][0]
    assert 0 <= got[0][2] <= SIZES[0][1]
    # note 工具只上报落点，绝不落笔
    assert view.document.pages[0].strokes == []


def test_add_note_creates_pin_and_click_emits_request(view, pdf):
    view.open(pdf)
    note = Note("n-1", 50.0, 60.0)
    view.add_note(0, note)
    assert view.document.pages[0].notes == [note]

    pin = view._note_pins[(0, "n-1")]
    assert isinstance(pin, NotePin)
    assert pin.note_id == "n-1" and pin.page_index == 0 and pin.number == 1
    assert pin.flags() & QGraphicsItem.ItemIgnoresTransformations
    layer = view._layers[0]
    assert pin.scenePos() == QPointF(layer.x + 50.0, layer.y + 60.0)

    pos = view.note_anchor_view_pos(0, "n-1")
    assert pos is not None

    got: list[tuple[int, str]] = []
    view.noteRequested.connect(lambda p, i: got.append((p, i)))
    view.set_tool("pen")
    _click(view, pos)
    assert got == [(0, "n-1")]
    # 点在图钉上只开卡，不落笔
    assert view.document.pages[0].strokes == []


def test_remove_note_removes_pin_and_renumbers(view, pdf):
    view.open(pdf)
    for note in (Note("n-1", 10.0, 10.0), Note("n-2", 20.0, 20.0),
                 Note("n-3", 30.0, 30.0)):
        view.add_note(0, note)
    assert [view._note_pins[(0, nid)].number for nid in ("n-1", "n-2", "n-3")] \
        == [1, 2, 3]

    view.remove_note(0, "n-2")
    assert (0, "n-2") not in view._note_pins
    assert [note.id for note in view.document.pages[0].notes] == ["n-1", "n-3"]
    assert view._note_pins[(0, "n-1")].number == 1
    assert view._note_pins[(0, "n-3")].number == 2
    # 旧图钉已离开场景，新图钉在场景里
    assert view._note_pins[(0, "n-3")].scene() is view.scene()
    # 点空位不再触发 noteRequested
    got: list[tuple[int, str]] = []
    view.noteRequested.connect(lambda p, i: got.append((p, i)))
    assert view.note_anchor_view_pos(0, "n-2") is None
    assert got == []


def test_pin_click_after_zoom(view, pdf):
    view.open(pdf)
    view.add_note(0, Note("n-1", 50.0, 60.0))
    before = view.note_anchor_view_pos(0, "n-1")
    view.set_zoom(2.0)
    after = view.note_anchor_view_pos(0, "n-1")
    assert after is not None
    assert after != before                     # 锚点按新缩放映射到新的视口位置

    got: list[tuple[int, str]] = []
    view.noteRequested.connect(lambda p, i: got.append((p, i)))
    _click(view, after)
    assert got == [(0, "n-1")]


def test_notes_on_page_one_include_layer_offset(view, pdf):
    view.open(pdf)
    view.add_note(1, Note("n-1", 40.0, 30.0))
    pin = view._note_pins[(1, "n-1")]
    layer = view._layers[1]
    assert layer.y > 0
    assert pin.page_index == 1
    assert pin.scenePos() == QPointF(layer.x + 40.0, layer.y + 30.0)


# ---------- × 热区几何 ----------

def test_close_hot_zone_is_the_top_right_circle(qapp):
    """× 的圆心在图钉自身坐标 (7.5, -7.5)：圆内算点删除，图钉正中心不算。"""
    pin = NotePin("n-1", 0, 1)
    box = pin.close_rect()
    assert (box.width(), box.height()) == pytest.approx((CLOSE_RADIUS * 2, CLOSE_RADIUS * 2))
    assert box.center() == QPointF(CLOSE_CENTER)
    assert CLOSE_CENTER.x() > 0 and CLOSE_CENTER.y() < 0     # 右上角
    assert pin.is_over_close(QPointF(CLOSE_CENTER)) is True  # 圆心
    assert pin.is_over_close(QPointF(7.5, -1.6)) is True     # 圆内下缘
    assert pin.is_over_close(QPointF(0.0, 0.0)) is False     # 图钉中心是"开卡"
    assert pin.is_over_close(QPointF(-7.5, -7.5)) is False   # 左上角不是删除键
    assert pin.is_over_close(QPointF(14.0, -14.0)) is False  # 包围盒角上也算不上


# ---------- 拖动图钉改锚点 ----------

def test_pin_drag_writes_anchor_back_and_never_opens_card(view, pdf):
    view.open(pdf)
    note = Note("n-1", 50.0, 60.0)
    view.add_note(0, note)
    layer = view._layers[0]
    start = view.note_anchor_view_pos(0, "n-1")
    end = QPoint(start.x() + 30, start.y() + 40)
    opened: list[tuple[int, str]] = []
    pages: list[int] = []
    view.noteRequested.connect(lambda p, i: opened.append((p, i)))
    view.notesChanged.connect(pages.append)

    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    _send_move(view, start.x() + 15, start.y() + 20, Qt.LeftButton)
    assert view._pin_dragging is True                # 位移超过 4px 阈值：升级为拖动
    _send_move(view, end.x(), end.y(), Qt.LeftButton)
    assert view.viewport().cursor().shape() == Qt.SizeAllCursor
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)

    shift = view.mapToScene(QPointF(end)) - view.mapToScene(QPointF(start))
    assert note.x == pytest.approx(50.0 + shift.x(), abs=0.5)
    assert note.y == pytest.approx(60.0 + shift.y(), abs=0.5)
    assert view.document.pages[0].notes[0] is note     # 改的就是模型里那一张
    pin = view._note_pins[(0, "n-1")]
    assert pin.scenePos() == QPointF(layer.x + note.x, layer.y + note.y)
    assert opened == []                                # 拖完不开卡
    assert pages == [0]


def test_pin_drag_threshold_is_four_viewport_px(view, pdf):
    """移动没超过 4px 仍算"点击开卡"：锚点一个字都不改。"""
    view.open(pdf)
    note = Note("n-1", 50.0, 60.0)
    view.add_note(0, note)
    start = view.note_anchor_view_pos(0, "n-1")
    opened: list[tuple[int, str]] = []
    pages: list[int] = []
    view.noteRequested.connect(lambda p, i: opened.append((p, i)))
    view.notesChanged.connect(pages.append)

    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
    _send_move(view, start.x() + 2, start.y() + 2, Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(start.x() + 2,
                                                                  start.y() + 2))

    assert opened == [(0, "n-1")]
    assert (note.x, note.y) == (50.0, 60.0)
    assert pages == []


def test_pin_drag_clamps_anchor_into_page(view, pdf):
    """拖出页框：锚点被钳在页面矩形内，图钉弹回钳后的位置（不会飘到下一页）。"""
    view.open(pdf)
    note = Note("n-1", 250.0, 350.0)
    view.add_note(0, note)
    page, layer = view.document.pages[0], view._layers[0]
    start = view.note_anchor_view_pos(0, "n-1")

    _drag_pin(view, start, QPoint(start.x() + 600, start.y() + 600))

    assert (note.x, note.y) == (page.width, page.height)
    pin = view._note_pins[(0, "n-1")]
    assert pin.scenePos() == QPointF(layer.x + page.width, layer.y + page.height)


def test_pin_drag_works_under_pen_tool_without_stroke(view, pdf):
    """图钉在任何工具下都优先吃掉按下：画笔下去拖图钉只挪图标，绝不落笔。"""
    view.open(pdf)
    view.set_tool("pen")
    note = Note("n-1", 50.0, 60.0)
    view.add_note(0, note)
    pages: list[int] = []
    view.notesChanged.connect(pages.append)
    start = view.note_anchor_view_pos(0, "n-1")
    end = QPoint(start.x() + 20, start.y() - 30)

    _drag_pin(view, start, end)

    assert view.document.pages[0].strokes == []
    assert note.x != 50.0 and note.y != 60.0
    assert pages == [0]


def test_pin_tap_opens_card_and_keeps_anchor(view, pdf):
    """原地按下松手 = 一次点击：发一次 noteRequested，锚点与图钉位置都不动。"""
    view.open(pdf)
    note = Note("n-1", 50.0, 60.0)
    view.add_note(0, note)
    before = view._note_pins[(0, "n-1")].scenePos()
    opened: list[tuple[int, str]] = []
    deleted: list[tuple[int, str]] = []
    pages: list[int] = []
    view.noteRequested.connect(lambda p, i: opened.append((p, i)))
    view.noteDeleteRequested.connect(lambda p, i: deleted.append((p, i)))
    view.notesChanged.connect(pages.append)

    _click(view, view.note_anchor_view_pos(0, "n-1"))

    assert opened == [(0, "n-1")]
    assert deleted == [] and pages == []
    assert (note.x, note.y) == (50.0, 60.0)
    assert view._note_pins[(0, "n-1")].scenePos() == before


# ---------- 点 × 请求删除 ----------

def test_pin_close_button_emits_delete_request_only(view, pdf):
    view.open(pdf)
    note = Note("n-1", 50.0, 60.0)
    view.add_note(0, note)
    opened: list[tuple[int, str]] = []
    deleted: list[tuple[int, str]] = []
    view.noteRequested.connect(lambda p, i: opened.append((p, i)))
    view.noteDeleteRequested.connect(lambda p, i: deleted.append((p, i)))

    _click(view, _close_pos(view, 0, "n-1"))

    assert deleted == [(0, "n-1")]
    assert opened == []                              # 点 × 绝不开卡
    assert (note.x, note.y) == (50.0, 60.0)          # 删除由接线层确认后才落手


def test_pin_close_release_outside_hot_zone_emits_nothing(view, pdf):
    """按下 × 后拖出热区再松手：既不误删也不开卡（按住的这一段算取消）。"""
    view.open(pdf)
    view.add_note(0, Note("n-1", 50.0, 60.0))
    opened: list[tuple[int, str]] = []
    deleted: list[tuple[int, str]] = []
    view.noteRequested.connect(lambda p, i: opened.append((p, i)))
    view.noteDeleteRequested.connect(lambda p, i: deleted.append((p, i)))

    close = _close_pos(view, 0, "n-1")
    center = view.note_anchor_view_pos(0, "n-1")
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=close)
    _send_move(view, center.x(), center.y(), Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=center)

    assert deleted == []
    assert opened == []


def test_pin_close_wins_over_other_tools(view, pdf):
    """画笔/note 工具下按 × 也只发删除请求：不落笔、不放新笔记。"""
    view.open(pdf)
    view.add_note(0, Note("n-1", 50.0, 60.0))
    for tool in ("pen", "eraser", "note"):
        view.set_tool(tool)
        opened, deleted, placed = [], [], []
        view.noteRequested.connect(lambda p, i: opened.append((p, i)))
        view.noteDeleteRequested.connect(lambda p, i: deleted.append((p, i)))
        view.notePlacementRequested.connect(lambda *a: placed.append(a))
        _click(view, _close_pos(view, 0, "n-1"))
        assert deleted == [(0, "n-1")], tool
        assert opened == [] and placed == [], tool
        assert view.document.pages[0].strokes == [], tool
        view.noteRequested.disconnect()
        view.noteDeleteRequested.disconnect()
        view.notePlacementRequested.disconnect()
        view.remove_note(0, "n-1")                   # 下一轮重新放一枚干净图钉
        view.add_note(0, Note("n-1", 50.0, 60.0))


def test_pin_eats_press_under_note_tool(view, pdf):
    """note 工具按在图钉身上（不是 ×）：开卡，而不是在旁边再放一张笔记。"""
    view.open(pdf)
    view.set_tool("note")
    view.add_note(0, Note("n-1", 50.0, 60.0))
    opened, placed = [], []
    view.noteRequested.connect(lambda p, i: opened.append((p, i)))
    view.notePlacementRequested.connect(lambda *a: placed.append(a))

    _click(view, view.note_anchor_view_pos(0, "n-1"))

    assert opened == [(0, "n-1")]
    assert placed == []
    assert len(view.document.pages[0].notes) == 1


# ---------- 光标 ----------

def test_pin_and_blank_cursors_under_mouse_tool(view, pdf):
    view.open(pdf)
    view.add_note(0, Note("n-1", 50.0, 60.0))
    view.set_tool("select")
    center = view.note_anchor_view_pos(0, "n-1")

    _send_move(view, 380.0, 470.0)                   # 页面外的深灰底：可平移
    assert view.viewport().cursor().shape() == Qt.OpenHandCursor
    _send_move(view, center.x(), center.y())
    assert view.viewport().cursor().shape() == Qt.PointingHandCursor
    close = _close_pos(view, 0, "n-1")
    _send_move(view, close.x(), close.y())           # × 上也是手形
    assert view.viewport().cursor().shape() == Qt.PointingHandCursor


# ---------- 名字标签 ----------

def _render_pin(pin: NotePin, size: int = 160) -> QImage:
    """把图钉单独画进透明图（不依赖场景/窗口），用于断言标签真的落在画面上。"""
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.translate(size / 2.0, size / 2.0)
    pin.paint(painter, QStyleOptionGraphicsItem(), None)
    painter.end()
    return image


def _label_band_extent(image: QImage) -> tuple[int, int, int]:
    """标签带（图钉下沿以下 20px）里不透明像素的 (数量, 最左 x, 最右 x)。"""
    size = image.width()
    top = int(size / 2 + PIN_SIZE / 2)
    left, right, count = size, -1, 0
    for y in range(top, min(size, top + 20)):
        for x in range(size):
            if image.pixelColor(x, y).alpha() > 0:
                count += 1
                left, right = min(left, x), max(right, x)
    return count, left, right


def test_pin_name_bounding_rect_covers_label_but_shape_does_not(qapp):
    pin = NotePin("n-1", 0, 2, name="我的笔记")
    assert pin.name == "我的笔记"
    rect = pin.boundingRect()
    assert rect.contains(pin.pin_rect())
    label_point = QPointF(0.0, LABEL_TOP + LABEL_HEIGHT / 2.0)
    assert rect.contains(label_point)                 # 标签在重绘范围内
    assert rect.width() >= LABEL_MAX_WIDTH
    assert pin.shape().contains(label_point) is False  # 但不在命中热区里
    assert pin.shape().contains(QPointF(LABEL_MAX_WIDTH / 2.0 - 1.0,
                                        LABEL_TOP + 1.0)) is False
    assert pin.shape().contains(QPointF(0.0, 0.0)) is True
    # 标签几何恒定：长名字不改变包围盒（set_name 不需要 prepareGeometryChange）
    pin.set_name("一个特别特别特别长的名字" * 5)
    assert pin.boundingRect() == rect


def test_pin_set_name_repaints_label(qapp):
    pin = NotePin("n-1", 0, 1)
    before = _render_pin(pin)
    assert _label_band_extent(before)[0] > 0          # 空名也画「笔记 1」

    pin.set_name("新名字")
    assert pin.name == "新名字"
    after = _render_pin(pin)
    assert _label_band_extent(after)[0] > 0
    assert after != before                            # 名字变了 → 画面变了

    again = _render_pin(pin)
    assert again == after                             # 同名再设：画面不变


def test_pin_empty_name_falls_back_to_numbered_label(qapp):
    pin = NotePin("n-1", 0, 7)
    assert pin.label_text() == "笔记 7"
    image = _render_pin(pin)
    assert _label_band_extent(image)[0] > 0

    pin.set_name("   ")
    assert pin.label_text() == "笔记 7"


def test_pin_long_name_is_elided_within_max_label_width(qapp):
    pin = NotePin("n-1", 0, 1, name="很长的名字" * 20)
    image = _render_pin(pin, size=300)
    count, left, right = _label_band_extent(image)
    assert count > 0
    assert right - left + 1 <= LABEL_MAX_WIDTH + 1     # 标签宽度上限 ~96px


def test_pin_name_is_passed_from_page_notes_on_rebuild(view, pdf):
    view.open(pdf)
    view.add_note(0, Note("n-1", 10.0, 10.0))
    view.add_note(0, Note("n-2", 20.0, 20.0, name="自定义名"))
    assert view._note_pins[(0, "n-1")].name == "笔记 1"
    assert view._note_pins[(0, "n-2")].name == "自定义名"

    view.document.pages[0].notes[0].name = "改过的名字"
    view.rebuild_note_pins(0)
    assert view._note_pins[(0, "n-1")].name == "改过的名字"
    assert view._note_pins[(0, "n-2")].name == "自定义名"

    # 外部即时改名：set_name 直接生效（rebuild 之外的第二条路）
    view._note_pins[(0, "n-2")].set_name("即时标签")
    assert view._note_pins[(0, "n-2")].name == "即时标签"


def test_click_on_label_area_does_not_hit_pin(view, pdf):
    """标签只是展示：点在标签上既不开卡，也不拖图钉（锚点不动）。"""
    view.open(pdf)
    note = Note("n-1", 50.0, 60.0, name="标签")
    view.add_note(0, note)
    anchor = view.note_anchor_view_pos(0, "n-1")
    label_pos = QPoint(anchor.x(), anchor.y() + int(LABEL_TOP + LABEL_HEIGHT / 2.0))
    opened: list[tuple[int, str]] = []
    pages: list[int] = []
    view.noteRequested.connect(lambda p, i: opened.append((p, i)))
    view.notesChanged.connect(pages.append)

    view.set_tool("select")
    _click(view, label_pos)

    assert opened == [] and pages == []
    assert (note.x, note.y) == (50.0, 60.0)
    assert view.document.pages[0].strokes == []
