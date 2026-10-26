"""画布视图离屏测试：Qt 变换与相机算术一致、滚轮语义、落笔、清屏、橡皮。

设备实测（设计文档 §6.2）：笔只产生鼠标事件，悬空也发无按键的 mouseMove，
所以悬停不落笔这条必须在这里钉住。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QShortcut, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sketchpad.ui.toolbox import PALETTE
from sketchpad.core import camera
from sketchpad.ui.canvas import CANVAS_BG, SketchScene, SketchView, _stroke_path
from sketchpad.core.commands import AddStroke, ClearAll, EraseStrokes
from sketchpad.core.strokes import MAX_POINTS_PER_STROKE, Document, Stroke

# 显示出来的窗口必须留 Python 引用，否则包装对象被回收、窗口"活着但没了"
_ALIVE: list[SketchView] = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp):
    scene = SketchScene(Document())
    v = SketchView(scene)
    v.resize(400, 300)
    v.show()
    QTest.qWaitForWindowExposed(v)
    _ALIVE.append(v)
    yield v
    v.close()


def _send_move(view: SketchView, x: float, y: float, buttons=Qt.NoButton) -> None:
    """造一条鼠标移动：buttons 决定它算悬停还是按住拖动。"""
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(int(x), int(y))))
    ev = QMouseEvent(QEvent.MouseMove, local, global_pos, Qt.NoButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def _drag(view: SketchView, points: list[tuple[int, int]]) -> None:
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(*points[0]))
    for x, y in points[1:-1]:
        _send_move(view, x, y, Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(*points[-1]))


def _click(view: SketchView, x: int, y: int) -> None:
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(x, y))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(x, y))


def _wheel(view: SketchView, x: int, y: int, delta: int = 120,
           modifiers=Qt.NoModifier) -> None:
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(x, y)))
    ev = QWheelEvent(local, global_pos, QPoint(), QPoint(0, delta), Qt.NoButton,
                     modifiers, Qt.NoScrollPhase, False)
    QApplication.sendEvent(view.viewport(), ev)


def _stroke_item_count(view: SketchView) -> int:
    return len([i for i in view.scene.items() if i.data(0) is not None])


def _assert_mapping(view: SketchView, label: str) -> None:
    vp = view.document.viewport
    assert vp.origin != (0.0, 0.0), label
    for x, y in [(-12.5, 3.25), (123.4, -56.7), (4321.0, 876.5)]:
        got = view.mapFromScene(QPointF(x, y))
        want = camera.world_to_screen(vp, (x, y))
        assert (got.x(), got.y()) == pytest.approx(want, abs=1e-6), label
        assert got == pytest.approx(want, abs=1e-6), label


def test_map_from_scene_matches_camera_math(view):
    """Qt 变换与 camera.world_to_screen 逐值一致：平移后、缩放后、两者之后。"""
    view.pan_screen(37.5, -21.25)
    _assert_mapping(view, "pan")

    view.zoom_at_screen(210.0, 140.0, camera.WHEEL_ZOOM_STEP ** 3)
    assert view.viewport_scale() == pytest.approx(camera.WHEEL_ZOOM_STEP ** 3)
    _assert_mapping(view, "zoom")

    view.pan_screen(-13.5, 7.75)
    assert view.viewport_scale() == pytest.approx(camera.WHEEL_ZOOM_STEP ** 3)
    _assert_mapping(view, "both")


def test_plain_wheel_pans_and_keeps_scale(view):
    height = view.viewport().height()
    scale_before = view.viewport_scale()
    y_before = view.viewport_origin()[1]
    _wheel(view, 200, 150, 120)
    assert view.viewport_scale() == scale_before
    assert view.viewport_origin()[1] == pytest.approx(
        y_before + height * camera.WHEEL_PAN_RATIO)
    assert view.viewport_origin()[0] == pytest.approx(0.0)


def test_shift_wheel_pans_horizontally_only(view):
    width = view.viewport().width()
    origin_before = view.viewport_origin()
    _wheel(view, 200, 150, 120, Qt.ShiftModifier)
    assert view.viewport_scale() == 1.0
    assert view.viewport_origin() == pytest.approx(
        (origin_before[0] + width * camera.WHEEL_PAN_RATIO, origin_before[1]))


def test_ctrl_wheel_zooms_around_cursor(view):
    cursor = QPointF(200.0, 150.0)
    world_before = view.screen_to_world(cursor)
    _wheel(view, 200, 150, 120, Qt.ControlModifier)
    assert view.viewport_scale() == pytest.approx(camera.WHEEL_ZOOM_STEP)
    assert view.screen_to_world(cursor) == pytest.approx(world_before, abs=1e-6)


def test_hover_adds_no_stroke_and_drag_adds_exactly_one(view):
    assert view.scene.document.strokes == []
    # 悬空移动：无按键，一个点都不该收
    _send_move(view, 60.0, 60.0)
    _send_move(view, 90.0, 70.0)
    assert view.scene.document.strokes == []

    view.set_color("#c0392b")
    view.set_width(6.0)
    _drag(view, [(50, 50), (60, 60), (90, 70), (130, 80)])

    assert len(view.scene.document.strokes) == 1
    assert _stroke_item_count(view) == 1
    stroke = view.scene.document.strokes[0]
    assert (stroke.color, stroke.width, stroke.tool) == ("#c0392b", 6.0, "pen")
    assert len(stroke.points) == 4
    assert stroke.points[0] == pytest.approx(
        camera.screen_to_world(view.document.viewport, (50.0, 50.0)))
    assert stroke.points[-1] == pytest.approx(
        camera.screen_to_world(view.document.viewport, (130.0, 80.0)))

    # 抬笔之后再悬停：不追点、不新起一条
    _send_move(view, 200.0, 200.0)
    assert len(view.scene.document.strokes) == 1
    assert len(view.scene.document.strokes[0].points) == 4


def test_single_point_stroke_renders_a_dot(view):
    """单点笔迹 = 直径 width 的实心圆点；零点线段 Qt 不画，这里盯住渲染。"""
    view.scene.document.strokes = [Stroke([(60.0, 30.0)], "#c0392b", 12.0)]
    view.scene.rebind()
    image = view.grab().toImage()
    dpr = image.devicePixelRatio()          # 抓图是设备像素，坐标要乘 DPR（高 DPI 下否则错位）
    sx, sy = camera.world_to_screen(view.document.viewport, (60.0, 30.0))
    assert image.pixelColor(int(sx * dpr), int(sy * dpr)).name() == "#c0392b"

    filled = [x for x in range(image.width())
              if image.pixelColor(x, int(sy * dpr)).name() != CANVAS_BG]
    assert max(filled) - min(filled) + 1 == int(round(12 * dpr))   # 12 世界像素 = 12*DPR 设备像素


def test_drag_pushes_one_add_stroke_and_signals(view):
    changed = []
    view.changed.connect(lambda: changed.append(1))
    _drag(view, [(20, 20), (60, 40), (100, 90)])

    assert len(changed) == 1
    assert view.history.depth == 1
    cmd = view.history.pop_undo()
    assert isinstance(cmd, AddStroke)
    assert view.scene.document.strokes == []
    assert _stroke_item_count(view) == 0


def test_stroke_never_exceeds_max_points(view):
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(10, 10))
    for i in range(4500):
        _send_move(view, 12 + (i % 90) * 4, 10 + (i // 90) * 4, Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(20, 20))

    assert len(view.scene.document.strokes) == 1
    assert len(view.scene.document.strokes[0].points) == MAX_POINTS_PER_STROKE


def test_clear_all_is_one_undo_restoring_document_and_items(view):
    view.scene.document.strokes = [
        Stroke([(float(i), 0.0), (float(i), 40.0)], f"#10{i:04x}", 3.0)
        for i in range(5)
    ]
    view.scene.rebind()
    assert _stroke_item_count(view) == 5

    changed = []
    view.changed.connect(lambda: changed.append(1))
    view.clear_all()

    assert view.scene.document.strokes == []
    assert _stroke_item_count(view) == 0
    assert view.history.depth == 1
    assert len(changed) == 1

    cmd = view.history.pop_undo()
    assert isinstance(cmd, ClearAll)
    assert len(view.scene.document.strokes) == 5
    assert _stroke_item_count(view) == 5


def test_clear_key_shortcut_clears_document(view):
    view.scene.document.strokes = [Stroke([(1.0, 1.0)], "#000000", 3.0)]
    view.scene.rebind()
    view.activateWindow()
    view.setFocus()
    QTest.keyClick(view, Qt.Key_E)
    assert view.scene.document.strokes == []
    assert view.history.depth == 1


def test_set_clear_key_keeps_exactly_one_shortcut(view):
    view.set_clear_key("Delete")
    view.set_clear_key("Ctrl+Shift+X")
    shortcuts = view.findChildren(QShortcut)
    assert len(shortcuts) == 1
    assert shortcuts[0].key().toString() == "Ctrl+Shift+X"
    assert shortcuts[0].context() == Qt.WidgetWithChildrenShortcut


def test_zero_size_viewport_ignores_pan_and_zoom(view):
    before = (view.viewport_scale(), view.viewport_origin())
    view.resize(0, 0)
    assert (view.viewport().width(), view.viewport().height()) == (0, 0)
    view.pan_screen(0.0, 40.0)
    view.zoom_at_screen(10.0, 10.0, 1.08)
    assert (view.viewport_scale(), view.viewport_origin()) == before


def test_eraser_click_removes_whole_stroke_as_one_command(view):
    near = Stroke([(0.0, 0.0), (200.0, 0.0)], "#000000", 6.0)
    far = Stroke([(0.0, 100.0), (200.0, 100.0)], "#000000", 6.0)
    view.scene.document.strokes = [near, far]
    view.scene.rebind()

    view.set_width(6.0)          # 命中半径 max(6, 6)/2 = 3 世界像素
    view.set_tool("eraser")
    _click(view, 100, 2)         # 距 near 折线 2 世界像素

    assert [s is far for s in view.scene.document.strokes] == [True]
    assert _stroke_item_count(view) == 1
    assert view.history.depth == 1

    cmd = view.history.pop_undo()
    assert isinstance(cmd, EraseStrokes)
    assert len(view.scene.document.strokes) == 2
    assert _stroke_item_count(view) == 2


def test_eraser_tolerance_boundary(view):
    line = Stroke([(0.0, 0.0), (200.0, 0.0)], "#000000", 12.0)
    view.scene.document.strokes = [line]
    view.scene.rebind()
    view.set_width(6.0)          # 阈值 3
    view.set_tool("eraser")

    _click(view, 100, 4)         # 距折线 4 > 3：不该删
    assert len(view.scene.document.strokes) == 1
    assert view.history.depth == 0

    _click(view, 100, 3)         # 恰好压线：删
    assert view.scene.document.strokes == []
    assert view.history.depth == 1


def test_wheel_down_moves_content_up(view):
    """滚轮向下（负 delta）= 内容向上移，与验收清单第 6 步一致。"""
    y_before = view.viewport_origin()[1]
    _wheel(view, 200, 150, -120)
    assert view.viewport_origin()[1] < y_before


def test_cursor_is_hidden_and_dot_follows_width(view):
    # 鼠标真正进过画布后，画笔类工具才隐藏系统光标、改画自己的笔尖圆点
    # （冷启动、还没收到第一次 move 时先显示箭头，避免“鼠标消失”）。
    _send_move(view, 50.0, 50.0)
    assert view.viewport().cursor().shape() == Qt.BlankCursor
    view.set_width(6.0)
    assert view.dot_radius_px() == pytest.approx(6.0)
    view.set_width(1.5)
    assert view.dot_radius_px() == pytest.approx(6.0)      # 所有粗细统一 12px 直径
    view.set_tool("eraser")
    assert view.dot_radius_px() == pytest.approx(12.0)     # 空心圆环固定 24px 直径


def test_dot_is_painted_under_the_cursor(view):
    _send_move(view, 100.0, 80.0)                          # 悬停也更新圆点
    image = view.grab().toImage()
    dpr = image.devicePixelRatio()
    assert image.pixelColor(int(100 * dpr), int(80 * dpr)).name() != CANVAS_BG


def test_middle_button_drag_pans(view):
    before = view.viewport_origin()
    QTest.mousePress(view.viewport(), Qt.MiddleButton, pos=QPoint(100, 100))
    _send_move(view, 130.0, 120.0, Qt.MiddleButton)
    QTest.mouseRelease(view.viewport(), Qt.MiddleButton, pos=QPoint(130, 120))
    assert view.viewport_origin() == pytest.approx((before[0] + 30.0, before[1] + 20.0))
    assert view.scene.document.strokes == []               # 平移不落笔


def test_space_drag_pans(view):
    before = view.viewport_origin()
    view.activateWindow()
    view.setFocus()
    QTest.keyPress(view, Qt.Key_Space)
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(50, 50))
    _send_move(view, 70.0, 60.0, Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(70, 60))
    QTest.keyRelease(view, Qt.Key_Space)
    assert view.viewport_origin() == pytest.approx((before[0] + 20.0, before[1] + 10.0))
    assert view.scene.document.strokes == []


def test_zero_key_resets_zoom_and_centers_strokes(view):
    view.scene.document.strokes = [
        Stroke([(100.0, 100.0), (200.0, 300.0)], "#000000", 3.0)]
    view.pan_screen(111.0, 222.0)
    view.zoom_at_screen(50.0, 50.0, 2.0)
    view.activateWindow()
    view.setFocus()
    QTest.keyClick(view, Qt.Key_0)
    vw, vh = view.viewport().width(), view.viewport().height()
    assert view.viewport_scale() == 1.0
    assert view.viewport_origin() == pytest.approx((vw / 2 - 150.0, vh / 2 - 200.0))


def test_eraser_leak_when_release_is_lost(view):
    """擦除手势没收尾（抬笔丢了）：下一次按笔必须把 item 恢复，不能留下"看不到但还在文档里"的笔迹。"""
    a = Stroke([(0.0, 0.0), (200.0, 0.0)], "#000000", 6.0)
    view.scene.document.strokes = [a]
    view.scene.rebind()
    view.set_tool("eraser")
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(100, 2))   # 只按下，不抬起
    assert view.scene.item_for(a).isVisible() is False

    view.set_tool("pen")
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(50, 50))   # 新手势
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(50, 50))

    restored = view.scene.item_for(a)
    assert restored is not None and restored.isVisible() is True
    assert view.scene.document.strokes[0] is a                              # 没被悄悄删掉
    assert len(view.scene.document.strokes) == 2                            # 原笔迹 + 新画的一笔
    assert view.history.depth == 1                                          # 只有新笔那一笔


def test_undo_during_erase_aborts_the_gesture(view):
    _drag(view, [(20, 20), (120, 60), (200, 100)])
    assert view.history.depth == 1
    view.set_tool("eraser")
    view.set_width(12.0)
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(120, 60))   # 开始擦
    view.history.pop_undo()                                                 # 擦到一半撤销
    assert view._erasing is False
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(120, 60))
    assert view.scene.document.strokes == []
    assert view.history.can_redo is True                                     # redo 没被空命令清掉


def test_middle_pan_mid_stroke_still_commits_the_stroke(view):
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(50, 50))
    _send_move(view, 70.0, 60.0, Qt.LeftButton)
    QTest.mousePress(view.viewport(), Qt.MiddleButton, pos=QPoint(70, 60))
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(80, 70))
    assert len(view.scene.document.strokes) == 1
    assert view._drawing is False


def test_stroke_coords_follow_pan_and_zoom(view):
    """审计盲区：所有落笔用例都在 identity 视口下，坐标错用屏幕值也能全绿。"""
    view.pan_screen(37.0, -19.0)
    view.zoom_at_screen(200.0, 150.0, 2.0)
    _drag(view, [(60, 60), (100, 90), (140, 120)])
    s = view.scene.document.strokes[0]
    assert view.viewport_scale() == pytest.approx(2.0)
    assert s.points[0] == pytest.approx(view.screen_to_world(QPointF(60.0, 60.0)))
    assert s.points[-1] == pytest.approx(view.screen_to_world(QPointF(140.0, 120.0)))


def test_redo_through_view_restores_stroke_and_item(view):
    _drag(view, [(20, 20), (100, 90)])
    view.history.pop_undo()
    assert view.scene.document.strokes == []
    assert _stroke_item_count(view) == 0
    view.history.pop_redo()
    assert len(view.scene.document.strokes) == 1
    assert _stroke_item_count(view) == 1


class _FakeTabletEvent:
    """只喂 tabletEvent 需要的四个方法；这条测守卫逻辑，不冒充 Qt 集成。"""

    def __init__(self, kind, pos, buttons):
        self._kind, self._pos, self._buttons = kind, pos, buttons
        self.ignored = False

    def type(self):
        return self._kind

    def position(self):
        return QPointF(*self._pos)

    def buttons(self):
        return self._buttons

    def ignore(self):
        self.ignored = True


def test_tablet_press_is_ignored_while_space_pan(view):
    view.activateWindow()
    view.setFocus()
    QTest.keyPress(view, Qt.Key_Space)
    ev = _FakeTabletEvent(QEvent.TabletPress, (50, 50), Qt.LeftButton)
    view.tabletEvent(ev)
    QTest.keyRelease(view, Qt.Key_Space)
    assert ev.ignored is True
    assert view.scene.document.strokes == []
    assert view._drawing is False


def test_b_q_and_digit_shortcuts_switch_tool_and_width(view):
    view.activateWindow()
    view.setFocus()
    QTest.keyClick(view, Qt.Key_Q)
    assert view.tool == "eraser"
    QTest.keyClick(view, Qt.Key_B)
    assert view.tool == "pen"
    QTest.keyClick(view, Qt.Key_2)
    assert view.color == PALETTE[1]        # 2 = 第 2 色（蓝）
    QTest.keyClick(view, Qt.Key_1)
    assert view.color == PALETTE[0]        # 1 = 第 1 色（红）


def test_state_setters_emit_changed_for_keyboard_updates(view):
    """键盘改状态必须广播 changed：工具栏高亮与状态栏全靠它跟上。"""
    changed = []
    view.changed.connect(lambda: changed.append(1))
    view.set_tool("eraser")
    view.set_color("#2980b9")
    view.set_width(12.0)
    assert changed == [1, 1, 1]
    assert (view.tool, view.color, view.width) == ("eraser", "#2980b9", 12.0)


def test_arrow_keys_pan_by_ten_percent_and_keep_scale(view):
    """方向键与滚轮同方向：Down = 内容向上，一格 = 视口 10%。"""
    view.activateWindow()
    view.setFocus()
    w = view.viewport().width() * camera.WHEEL_PAN_RATIO
    h = view.viewport().height() * camera.WHEEL_PAN_RATIO
    x0, y0 = view.viewport_origin()

    QTest.keyClick(view, Qt.Key_Down)
    assert view.viewport_origin() == pytest.approx((x0, y0 - h))
    QTest.keyClick(view, Qt.Key_Up)
    assert view.viewport_origin() == pytest.approx((x0, y0))
    QTest.keyClick(view, Qt.Key_Right)
    assert view.viewport_origin() == pytest.approx((x0 - w, y0))
    QTest.keyClick(view, Qt.Key_Left)
    assert view.viewport_origin() == pytest.approx((x0, y0))
    assert view.viewport_scale() == 1.0


def test_erase_deletes_only_hit_items_without_rebuilding_the_scene(view):
    """橡皮卡顿的回归：抬笔时不能 rebind 整个场景，未命中的 item 必须原对象保留。"""
    near = Stroke([(0.0, 0.0), (200.0, 0.0)], "#000000", 6.0)
    far = Stroke([(0.0, 100.0), (200.0, 100.0)], "#000000", 6.0)
    view.scene.document.strokes = [near, far]
    view.scene.rebind()
    item_far = view.scene.item_for(far)

    view.set_width(6.0)
    view.set_tool("eraser")
    _click(view, 100, 2)

    assert view.scene.item_for(near) is None
    assert view.scene.item_for(far) is item_far      # 同一个 item，没有重建
    assert [s is far for s in view.scene.document.strokes] == [True]


def test_eraser_ring_follows_cursor_while_button_is_held(view):
    """按住橡皮拖动时圆环必须跟着走（曾经漏了 viewport().update()，圆环冻在原地）。"""
    view.set_tool("eraser")
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(50, 50))
    _send_move(view, 200.0, 150.0, Qt.LeftButton)

    image = view.grab().toImage()
    dpr = image.devicePixelRatio()

    def ring_pixels(cx: int, cy: int) -> int:
        return sum(1 for dx in range(-13, 14)
                   if image.pixelColor(int((cx + dx) * dpr), int(cy * dpr)).name() != CANVAS_BG)

    assert ring_pixels(200, 150) > 0      # 新位置有圆环
    assert ring_pixels(50, 50) == 0       # 旧位置没有残留
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(200, 150))


def test_eraser_drag_requests_a_repaint(view, monkeypatch):
    """真红用例：按住橡皮拖动必须请求重绘，否则真实窗口里圆环不动（grab() 会强制重绘，测不出）。"""
    view.set_tool("eraser")
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(50, 50))
    calls = []
    monkeypatch.setattr(view.viewport(), "update",
                        lambda *a, **k: calls.append(1), raising=False)
    _send_move(view, 200.0, 150.0, Qt.LeftButton)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(200, 150))
    assert calls, "橡皮拖动没有请求重绘：圆环会冻在按下的位置"


def test_stroke_path_follows_every_sampled_point():
    """折线必须逐个穿过采样点；中点+quadTo 会把转折抹圆、整体内收。"""
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (20.0, 10.0)]
    path = _stroke_path(points)
    assert path.elementCount() == len(points)
    got = [(path.elementAt(i).x, path.elementAt(i).y)
           for i in range(path.elementCount())]
    assert got == points
