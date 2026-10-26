"""笔记纸面与画布离屏测试：纸色/横线、整页适配、共享笔迹列表、滚轮与键盘语义。

场景渲染测试按 1:1 把 560x720 的设计坐标画到同尺寸 QImage 上（render 会等比
缩放并居中），再在预期线位置附近取样，避免依赖像素对齐的精确行号。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.ui.toolbox import PALETTE
from sketchpad.core.commands import AddStroke
from sketchpad.core.notes import NOTE_H, NOTE_W, Note
from sketchpad.core.settings import WIDTHS, Settings
from sketchpad.core.strokes import Document, Stroke
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.note_canvas import (LINE_GAP, MARGIN_X, PAPER_BG, PAPER_LINE,
                                      NoteScene, NoteView)

# 显示出来的窗口必须留 Python 引用，否则包装对象被回收、窗口"活着但没了"
_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def note_view(qapp):
    view = NoteView(Note("n-1", 10, 20), Settings())
    view.resize(int(NOTE_W), int(NOTE_H))
    view.show()
    QTest.qWait(30)
    _ALIVE.append(view)
    yield view
    _ALIVE.remove(view)
    view.close()


def _send_mouse(view: NoteView, kind, x: float, y: float,
                button, buttons) -> None:
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(int(x), int(y))))
    ev = QMouseEvent(kind, local, global_pos, button, buttons, Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def _band_has(img: QImage, x0: int, x1: int, y0: int, y1: int, name: str) -> bool:
    """矩形取样带里有没有该颜色的像素（线宽/贴合方向因平台而异）。"""
    return any(img.pixelColor(x, y).name() == name
               for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def _render(scene: NoteScene) -> QImage:
    scene.setSceneRect(0, 0, NOTE_W, NOTE_H)
    img = QImage(int(NOTE_W), int(NOTE_H), QImage.Format_RGB32)
    img.fill(0)
    painter = QPainter(img)
    scene.render(painter, img.rect())
    painter.end()
    return img


@pytest.mark.parametrize("paper", ("light", "dark"))
def test_scene_paints_paper_and_ruled_lines(qapp, paper):
    scene = NoteScene(Document(), paper)
    img = _render(scene)
    bg, line = PAPER_BG[paper], PAPER_LINE[paper]

    assert img.pixelColor(2, 2).name() == bg
    assert img.pixelColor(100, 200).name() == bg
    # 两条横线之间是干净纸面，说明线是稀疏的、背景不是整片线色
    assert not _band_has(img, 90, 110, 172, 188, line)

    # 第 2 条横线：线心附近必须出现线色
    y = int(LINE_GAP * 2)
    assert _band_has(img, 90, 110, y - 2, y + 2, line)

    # 竖向页边线：取两条横线之间（y=172..188）的整段竖列，列上每一行都必须是线色。
    # 旧写法取横线穿过的方框，即使竖线没画也会被横线像素假阳性通过。
    assert all(img.pixelColor(int(MARGIN_X), y).name() == line
               for y in range(172, 189))


def test_note_view_shares_note_strokes_and_undo(note_view):
    view = note_view
    note = view.note
    stroke = Stroke([(10.0, 10.0), (30.0, 30.0)], "#e8eaed", 2.0)
    cmd = AddStroke(view.document, stroke)
    cmd.apply()
    view.history.push(cmd)
    view.scene.rebind()

    assert view.document.strokes is note.strokes
    assert note.strokes == [stroke]
    assert view.scene.item_for(stroke) is not None

    view.history.pop_undo()
    assert note.strokes == []
    assert view.scene.item_for(stroke) is None
    assert view.document.viewport.scale > 0


def test_fit_page_keeps_page_geometry(note_view):
    view = note_view
    view.fit_page()
    vw, vh = view.viewport().width(), view.viewport().height()
    assert view.document.viewport.scale == pytest.approx(min(vw / NOTE_W, vh / NOTE_H))

    rect = view.sceneRect()
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == pytest.approx(
        (0.0, 0.0, NOTE_W, NOTE_H))

    # 整页可见：页面四角都落在视口内
    top_left = view.mapFromScene(QPointF(0.0, 0.0))
    bottom_right = view.mapFromScene(QPointF(NOTE_W, NOTE_H))
    for point in (top_left, bottom_right):
        assert 0.0 <= point.x() <= vw
        assert 0.0 <= point.y() <= vh


def test_fit_page_scales_down_with_viewport(note_view):
    view = note_view
    view.resize(280, 360)
    QTest.qWait(30)
    assert view.document.viewport.scale == pytest.approx(0.5, rel=0.05)
    assert view.sceneRect().width() == pytest.approx(NOTE_W)
    assert view.sceneRect().height() == pytest.approx(NOTE_H)


def test_mouse_drawing_lands_in_note_design_coordinates(note_view):
    """整页缩到 0.5 后落笔：屏幕点必须换算回设计坐标存进 note.strokes。"""
    view = note_view
    view.resize(280, 360)
    QTest.qWait(30)
    scale = view.document.viewport.scale
    origin = tuple(view.document.viewport.origin)

    _send_mouse(view, QEvent.MouseButtonPress, 100, 120, Qt.LeftButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseMove, 140, 160, Qt.NoButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseButtonRelease, 160, 180, Qt.LeftButton, Qt.NoButton)

    assert len(view.note.strokes) == 1
    stroke = view.note.strokes[0]
    assert stroke.points[0] == pytest.approx(
        ((100.0 - origin[0]) / scale, (120.0 - origin[1]) / scale), abs=0.01)
    assert stroke.points[-1] == pytest.approx(
        ((160.0 - origin[0]) / scale, (180.0 - origin[1]) / scale), abs=0.01)
    assert view.scene.item_for(stroke) is not None


def test_middle_drag_cannot_pan_or_shift_ink(note_view):
    """中键拖动必须被挡住：基类会改写 origin 但 AlignCenter 不动画面，之后落笔错位。"""
    view = note_view
    view.resize(280, 360)
    QTest.qWait(30)
    before = (view.document.viewport.scale, tuple(view.document.viewport.origin))

    _send_mouse(view, QEvent.MouseButtonPress, 40, 50, Qt.MiddleButton, Qt.MiddleButton)
    assert view._panning is False
    _send_mouse(view, QEvent.MouseMove, 140, 150, Qt.NoButton, Qt.MiddleButton)
    _send_mouse(view, QEvent.MouseButtonRelease, 140, 150, Qt.MiddleButton, Qt.NoButton)
    assert (view.document.viewport.scale,
            tuple(view.document.viewport.origin)) == before

    point = QPointF(100.0, 120.0)
    _send_mouse(view, QEvent.MouseButtonPress, point.x(), point.y(),
                Qt.LeftButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseButtonRelease, point.x(), point.y(),
                Qt.LeftButton, Qt.NoButton)

    inverted, ok = view.viewportTransform().inverted()
    assert ok
    mapped = inverted.map(point)
    assert len(view.note.strokes) == 1
    assert view.note.strokes[0].points[0] == pytest.approx(
        (mapped.x(), mapped.y()), abs=0.01)


def test_reset_zoom_refits_page_instead_of_100_percent(note_view):
    view = note_view
    view.resize(280, 360)
    QTest.qWait(30)
    view.document.viewport.scale = 3.0
    view.document.viewport.origin = (12.0, 34.0)

    view.reset_zoom()

    vw, vh = view.viewport().width(), view.viewport().height()
    scale = view.document.viewport.scale
    assert scale == pytest.approx(min(vw / NOTE_W, vh / NOTE_H))
    assert tuple(view.document.viewport.origin) == pytest.approx(
        ((vw - NOTE_W * scale) / 2.0, (vh - NOTE_H * scale) / 2.0))


def test_ink_is_clamped_to_design_page(qapp):
    """页外 Slack（左右留白）里落笔：存进笔记的点必须钳到 [0,w]x[0,h]。"""
    note = Note("n-3", 0, 0)
    view = NoteView(note, Settings())
    view.resize(800, 720)
    view.show()
    QTest.qWait(30)
    _ALIVE.append(view)
    try:
        vw = view.viewport().width()
        scale = view.document.viewport.scale
        assert scale == pytest.approx(1.0)
        left = (vw - NOTE_W * scale) / 2.0
        assert left > 0.0

        _send_mouse(view, QEvent.MouseButtonPress, 5, 10, Qt.LeftButton, Qt.LeftButton)
        _send_mouse(view, QEvent.MouseMove, 795, 700, Qt.NoButton, Qt.LeftButton)
        _send_mouse(view, QEvent.MouseButtonRelease, 795, 700, Qt.LeftButton, Qt.NoButton)

        assert len(note.strokes) == 1
        stroke = note.strokes[0]
        assert stroke.points[0] == pytest.approx((0.0, 10.0), abs=0.01)
        assert stroke.points[-1] == pytest.approx((NOTE_W, 700.0), abs=0.01)
        assert all(0.0 <= x <= NOTE_W and 0.0 <= y <= NOTE_H
                   for x, y in stroke.points)
        # 笔尖圆点仍按真实指针位置换算，不被 _world 的钳制影响
        inverted, _ = view.viewportTransform().inverted()
        assert inverted.map(QPointF(5.0, 10.0)).x() < 0.0
    finally:
        _ALIVE.remove(view)
        view.close()


def test_eraser_and_clear_are_inherited(note_view):
    view = note_view
    stroke = Stroke([(120.0, 140.0), (200.0, 220.0)], "#e8eaed", 2.0)
    cmd = AddStroke(view.document, stroke)
    cmd.apply()
    view.history.push(cmd)
    view.scene.rebind()

    view.set_tool("eraser")
    _send_mouse(view, QEvent.MouseButtonPress, 160, 180, Qt.LeftButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseButtonRelease, 160, 180, Qt.LeftButton, Qt.NoButton)
    assert view.note.strokes == []
    assert view.scene.item_for(stroke) is None

    view.set_tool("pen")
    _send_mouse(view, QEvent.MouseButtonPress, 100, 120, Qt.LeftButton, Qt.LeftButton)
    _send_mouse(view, QEvent.MouseButtonRelease, 100, 120, Qt.LeftButton, Qt.NoButton)
    assert len(view.note.strokes) == 1

    view.clear_all()
    assert view.note.strokes == []
    view.history.pop_undo()
    assert len(view.note.strokes) == 1


def test_fit_page_centers_page_in_non_square_viewport(note_view):
    """卡片里视口不是 560x720（头部/工具栏占位）：整页缩放后水平居中。"""
    view = note_view
    view.resize(int(NOTE_W), 500)
    QTest.qWait(30)
    vw, vh = view.viewport().width(), view.viewport().height()
    scale = view.document.viewport.scale
    assert scale == pytest.approx(vh / NOTE_H)

    left = (vw - NOTE_W * scale) / 2.0
    top_left = view.mapFromScene(QPointF(0.0, 0.0))
    bottom_right = view.mapFromScene(QPointF(NOTE_W, NOTE_H))
    assert top_left.x() == pytest.approx(left, abs=1.0)
    assert top_left.y() == pytest.approx(0.0, abs=1.0)
    assert bottom_right.x() == pytest.approx(vw - left, abs=1.0)
    assert bottom_right.y() == pytest.approx(vh, abs=1.0)


def _wheel(view, modifiers=Qt.NoModifier) -> None:
    local = QPointF(100.0, 100.0)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(100, 100)))
    ev = QWheelEvent(local, global_pos, QPoint(), QPoint(0, 120), Qt.NoButton,
                     modifiers, Qt.NoScrollPhase, False)
    QApplication.sendEvent(view.viewport(), ev)


def test_wheel_does_not_zoom_or_pan(qapp, note_view):
    view = note_view
    before = (view.document.viewport.scale, tuple(view.document.viewport.origin))
    _wheel(view, Qt.NoModifier)
    _wheel(view, Qt.ControlModifier)
    assert (view.document.viewport.scale,
            tuple(view.document.viewport.origin)) == before

    # 对照组：同一个事件送给普通画布会缩放，说明事件确实送达 wheelEvent，
    # 笔记页不改视口是覆写生效、不是事件没到。
    plain = SketchView(SketchScene(Document()))
    plain.resize(400, 300)
    plain.show()
    QTest.qWait(30)
    _ALIVE.append(plain)
    try:
        scale_before = plain.document.viewport.scale
        _wheel(plain, Qt.ControlModifier)
        assert plain.document.viewport.scale != scale_before
    finally:
        _ALIVE.remove(plain)
        plain.close()


def test_keyboard_keeps_tools_widths_and_undo(note_view):
    view = note_view
    QTest.keyClick(view, Qt.Key_Q)
    assert view.tool == "eraser"
    QTest.keyClick(view, Qt.Key_B)
    assert view.tool == "pen"
    QTest.keyClick(view, Qt.Key_1)
    assert view.color == PALETTE[0]        # 1 = 红
    QTest.keyClick(view, Qt.Key_3)
    assert view.color == PALETTE[2]        # 3 = 黄

    stroke = Stroke([(5.0, 5.0), (9.0, 9.0)], "#e8eaed", 2.0)
    cmd = AddStroke(view.document, stroke)
    cmd.apply()
    view.history.push(cmd)
    assert view.note.strokes == [stroke]

    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert view.note.strokes == []
    QTest.keyClick(view, Qt.Key_Y, Qt.ControlModifier)
    assert view.note.strokes == [stroke]


def test_zero_space_and_arrows_do_not_pan_or_zoom(note_view):
    view = note_view
    before = (view.document.viewport.scale, tuple(view.document.viewport.origin))
    QTest.keyClick(view, Qt.Key_0)
    QTest.keyClick(view, Qt.Key_Right)
    QTest.keyClick(view, Qt.Key_Down)
    QTest.keyClick(view, Qt.Key_Space)
    assert (view.document.viewport.scale,
            tuple(view.document.viewport.origin)) == before


def test_view_honours_note_paper_and_switches(qapp):
    note = Note("n-2", 0, 0, paper="light")
    view = NoteView(note, Settings())
    _ALIVE.append(view)
    try:
        assert view.scene.paper == "light"
        assert view.scene.backgroundBrush().color().name() == PAPER_BG["light"]

        view.set_paper("dark")
        assert note.paper == "dark"
        assert view.scene.paper == "dark"
        assert view.scene.backgroundBrush().color().name() == PAPER_BG["dark"]
    finally:
        _ALIVE.remove(view)
        view.close()
