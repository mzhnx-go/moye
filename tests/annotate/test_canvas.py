"""画布交互（离屏）：钢笔、橡皮、清空、撤销、快捷键、笔色、透明 PNG。"""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from sketchpad.annotate.canvas import (CURSOR_IDLE_MS, CURSOR_LARGE_RADIUS,
                                       CURSOR_SMALL_RADIUS, ERASER_RING_RADIUS,
                                       MAX_PEN_WIDTH, MIN_PEN_WIDTH, Canvas)
from sketchpad.annotate.constants import DEFAULT_COLOR, PEN_WIDTH


def make_canvas() -> Canvas:
    last_error = None
    for args in ((), (None,)):
        try:
            return Canvas(*args)
        except TypeError as exc:
            last_error = exc
    raise AssertionError(f"Canvas 构造失败: {last_error}")


@pytest.fixture
def canvas(qapp):
    widget = make_canvas()
    widget.resize(400, 300)
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_光标半径统一且不随线宽变化(canvas):
    assert canvas._cursor_radius() == CURSOR_LARGE_RADIUS   # 初始/空闲=大点
    canvas.set_pen_width(MIN_PEN_WIDTH)
    assert canvas._cursor_radius() == CURSOR_LARGE_RADIUS
    canvas.set_pen_width(MAX_PEN_WIDTH)
    assert canvas._cursor_radius() == CURSOR_LARGE_RADIUS
    canvas.set_tool("eraser")
    assert canvas._cursor_radius() == ERASER_RING_RADIUS
    assert ERASER_RING_RADIUS > CURSOR_LARGE_RADIUS > CURSOR_SMALL_RADIUS


def test_画的时候小点空闲后变大(canvas):
    assert CURSOR_IDLE_MS == 3000        # 用户指定停笔 3 秒后恢复
    canvas._cursor_idle_timer.setInterval(50)   # 测试不真等 2.5s
    canvas._cursor = QPointF(20, 20)
    canvas._mark_cursor_active()
    assert canvas._cursor_radius() == CURSOR_SMALL_RADIUS
    QTest.qWait(150)
    assert canvas._cursor_large is True
    assert canvas._cursor_radius() == CURSOR_LARGE_RADIUS

    # 手势还没结束时不放大
    canvas._drawing = True
    canvas._mark_cursor_active()
    QTest.qWait(150)
    assert canvas._cursor_radius() == CURSOR_SMALL_RADIUS
    canvas._drawing = False


def test_悬停移动不会重置空闲计时(canvas):
    """数位板悬停会连续发 move；不能因此让大圆点永远不出现。"""
    canvas._cursor_idle_timer.setInterval(80)
    canvas._cursor = QPointF(10, 10)
    canvas._mark_cursor_active()
    QTest.qWait(30)
    send_mouse(canvas, QEvent.Type.MouseMove, (20, 20), Qt.MouseButton.NoButton)
    QTest.qWait(100)
    assert canvas._cursor_large is True
    assert canvas._cursor_radius() == CURSOR_LARGE_RADIUS


def test_数字1到6切换颜色(canvas):
    from sketchpad.annotate.constants import PALETTE
    for index, color in enumerate(PALETTE[:6]):
        QTest.keyClick(canvas, Qt.Key.Key_1 + index)
        assert canvas.color.lower() == color.lower()


def send_mouse(widget, event_type, pos, buttons, button=Qt.MouseButton.NoButton):
    """QTest.mouseMove 不带按键位，移动事件必须自己造。"""
    local = QPointF(float(pos[0]), float(pos[1]))
    global_pos = QPointF(widget.mapToGlobal(QPoint(int(pos[0]), int(pos[1]))))
    event = QMouseEvent(
        event_type,
        local,
        global_pos,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def drag(canvas, points):
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     QPoint(int(points[0][0]), int(points[0][1])))
    for point in points[1:]:
        send_mouse(canvas, QEvent.Type.MouseMove, point,
                   Qt.MouseButton.LeftButton)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier,
                       QPoint(int(points[-1][0]), int(points[-1][1])))


LINE = [(10.0 + 10.0 * i, 50.0) for i in range(9)]        # (10,50) -> (90,50)
SECOND = [(10.0 + 10.0 * i, 200.0) for i in range(9)]     # (10,200) -> (90,200)


def draw_line(canvas, points=LINE):
    canvas.set_tool("pen")
    drag(canvas, points)


def test_拖动画一条钢笔线(canvas):
    drag(canvas, LINE)

    assert len(canvas.document.strokes) == 1
    stroke = canvas.document.strokes[0]
    assert stroke.tool == "pen"
    assert stroke.color == DEFAULT_COLOR
    assert stroke.width == pytest.approx(float(PEN_WIDTH))
    assert stroke.points[0][0] == pytest.approx(10.0)
    assert stroke.points[0][1] == pytest.approx(50.0)
    assert stroke.points[-1][0] == pytest.approx(90.0)
    assert stroke.points[-1][1] == pytest.approx(50.0)
    assert all(abs(p[1] - 50.0) < 1.0 for p in stroke.points)
    assert len(stroke.points) >= 2
    assert canvas.has_ink() is True
    assert canvas.history.can_undo is True


def test_落笔会发changed信号(canvas):
    spy = QSignalSpy(canvas.changed)
    draw_line(canvas)
    assert spy.count() >= 1


def test_默认笔色是DEFAULT_COLOR(canvas):
    assert canvas.color == DEFAULT_COLOR


def test_set_color后落笔用新颜色(canvas):
    canvas.set_color("#ff453a")
    assert canvas.color == "#ff453a"

    draw_line(canvas)
    stroke = canvas.document.strokes[0]
    assert stroke.color == "#ff453a"
    assert stroke.width == pytest.approx(float(PEN_WIDTH))

    # 换色后新笔迹用新色，旧笔迹保持原色
    canvas.set_color("#2980b9")
    draw_line(canvas, SECOND)
    assert canvas.document.strokes[0].color == "#ff453a"
    assert canvas.document.strokes[1].color == "#2980b9"


def test_abort_gesture把画到一半的笔收掉(canvas):
    """数位板下 release 可能迟到/丢失，外部兜底要把这一笔正常收尾。"""
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(20, 40))
    send_mouse(canvas, QEvent.Type.MouseMove, (60, 40), Qt.MouseButton.LeftButton)
    send_mouse(canvas, QEvent.Type.MouseMove, (100, 40), Qt.MouseButton.LeftButton)
    assert canvas._drawing is True

    canvas.abort_gesture()

    assert canvas._drawing is False
    assert len(canvas.document.strokes) == 1
    assert canvas.history.can_undo is True


def test_abort_gesture没有手势时是空操作(canvas):
    canvas.abort_gesture()
    assert canvas.document.strokes == []
    assert canvas._drawing is False
    assert canvas._erasing is False


def test_abort_gesture能把橡皮收尾(canvas):
    canvas.set_tool("pen")
    draw_line(canvas)
    canvas.set_tool("eraser")
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(30, 50))
    assert canvas._erasing is True

    canvas.abort_gesture()

    assert canvas._erasing is False
    assert canvas.document.strokes == []


def test_松开左键后不再握着鼠标(canvas):
    draw_line(canvas)
    assert QWidget.mouseGrabber() is None


def test_set_color发changed(canvas):
    spy = QSignalSpy(canvas.changed)
    canvas.set_color("#27ae60")
    assert spy.count() >= 1


# ---------- 线宽 ----------

def test_默认线宽是PEN_WIDTH(canvas):
    assert canvas.pen_width == pytest.approx(float(PEN_WIDTH))


def test_set_pen_width后落笔用新线宽(canvas):
    canvas.set_pen_width(11.0)
    assert canvas.pen_width == pytest.approx(11.0)

    draw_line(canvas)
    assert canvas.document.strokes[0].width == pytest.approx(11.0)

    canvas.set_pen_width(2.0)
    draw_line(canvas, SECOND)
    # 旧笔迹保留自己的粗线，新笔迹用新的
    assert canvas.document.strokes[0].width == pytest.approx(11.0)
    assert canvas.document.strokes[1].width == pytest.approx(2.0)


def test_画到一半改线宽不影响这一笔(canvas):
    start = LINE[0]
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     QPoint(int(start[0]), int(start[1])))
    send_mouse(canvas, QEvent.Type.MouseMove, LINE[1],
               Qt.MouseButton.LeftButton)
    canvas.set_pen_width(11.0)          # 拖到一半拨粗细档位
    for point in LINE[2:]:
        send_mouse(canvas, QEvent.Type.MouseMove, point,
                   Qt.MouseButton.LeftButton)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier,
                       QPoint(int(LINE[-1][0]), int(LINE[-1][1])))

    assert canvas.document.strokes[0].width == pytest.approx(float(PEN_WIDTH))


def test_线宽被夹在上下限内(canvas):
    canvas.set_pen_width(0.1)
    assert canvas.pen_width == pytest.approx(MIN_PEN_WIDTH)
    canvas.set_pen_width(999.0)
    assert canvas.pen_width == pytest.approx(MAX_PEN_WIDTH)


def test_线宽非法值忽略(canvas):
    for bad in ("粗", None, float("nan")):
        canvas.set_pen_width(bad)
        assert canvas.pen_width == pytest.approx(float(PEN_WIDTH)), bad


def test_set_pen_width相同值不发changed(canvas):
    spy = QSignalSpy(canvas.changed)
    canvas.set_pen_width(canvas.pen_width)
    assert spy.count() == 0

    canvas.set_pen_width(7.0)
    assert spy.count() == 1


def test_没有高亮笔线宽与重做接口(canvas):
    for name in ("redo", "set_width", "set_smoothing", "set_highlighter",
                 "highlighter"):
        assert not hasattr(canvas, name), f"Canvas 不应再暴露 {name}"
    assert canvas.tool in ("pen", "eraser")


def test_全屏批注可同步主设置平滑档位(canvas):
    assert canvas.ink_smoothing == "low"
    canvas.set_ink_smoothing("ultra")
    assert canvas.ink_smoothing == "ultra"
    canvas.set_ink_smoothing("bogus")
    assert canvas.ink_smoothing == "low"


def test_全屏批注收笔使用设定档位(canvas, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "sketchpad.annotate.canvas.smooth_points",
        lambda points, level: seen.append(level) or points)
    canvas.set_ink_smoothing("ultra")
    canvas._begin_stroke(QPointF(10, 10))
    canvas._append(QPointF(20, 20))
    canvas._append(QPointF(30, 20))
    canvas._finish_stroke(QPointF(40, 10))
    assert seen == ["ultra"]


def test_未注册裸键透传给进入批注前的窗口(qapp):
    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.forwarded = []

        def forward_key_to_previous(self, ev):
            self.forwarded.append(ev.key())
            return True

    host = Host()
    canvas = Canvas(host)
    try:
        QTest.keyClick(canvas, Qt.Key_Space)
        QTest.keyClick(canvas, Qt.Key_H)
        assert host.forwarded == [Qt.Key_Space, Qt.Key_H]

        # 批注自己的工具键/修饰键组合不往外送
        QTest.keyClick(canvas, Qt.Key_B)
        QTest.keyClick(canvas, Qt.Key_E)
        QTest.keyClick(canvas, Qt.Key_Z, Qt.KeyboardModifier.ControlModifier)
        assert host.forwarded == [Qt.Key_Space, Qt.Key_H]
    finally:
        canvas.close()
        host.close()


def test_悬停只move不按键不落笔(canvas):
    for x in range(10, 200, 20):
        send_mouse(canvas, QEvent.Type.MouseMove, (float(x), 30.0),
                   Qt.MouseButton.NoButton)
    QTest.mouseMove(canvas, QPoint(120, 60))

    assert canvas.document.strokes == []
    assert canvas.has_ink() is False


def test_右键不落笔(canvas):
    QTest.mousePress(canvas, Qt.MouseButton.RightButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(120, 80))
    send_mouse(canvas, QEvent.Type.MouseMove, (140.0, 80.0),
               Qt.MouseButton.RightButton)
    QTest.mouseRelease(canvas, Qt.MouseButton.RightButton,
                       Qt.KeyboardModifier.NoModifier, QPoint(140, 80))

    assert canvas.document.strokes == []
    assert canvas.has_ink() is False


def test_橡皮整条擦且撤销恢复(canvas):
    draw_line(canvas, LINE)
    draw_line(canvas, SECOND)
    assert len(canvas.document.strokes) == 2
    order_before = [s.points[0] for s in canvas.document.strokes]

    canvas.set_tool("eraser")
    # 竖着划过第一条（y=50），不碰第二条（y=200）
    drag(canvas, [(50.0, 20.0), (50.0, 40.0), (50.0, 50.0),
                  (50.0, 60.0), (50.0, 80.0)])

    assert len(canvas.document.strokes) == 1
    assert canvas.document.strokes[0].points[0][0] == pytest.approx(10.0)
    assert canvas.document.strokes[0].points[0][1] == pytest.approx(200.0)

    canvas.undo()
    assert len(canvas.document.strokes) == 2
    assert [s.points[0] for s in canvas.document.strokes] == order_before


def test_E清空后0条且CtrlZ可恢复(canvas):
    draw_line(canvas, LINE)
    draw_line(canvas, SECOND)
    assert len(canvas.document.strokes) == 2

    QTest.keyClick(canvas, Qt.Key.Key_E)
    assert canvas.document.strokes == []
    assert canvas.has_ink() is False

    QTest.keyClick(canvas, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(canvas.document.strokes) == 2


def test_BQ切换工具(canvas):
    QTest.keyClick(canvas, Qt.Key.Key_Q)
    assert canvas.tool == "eraser"
    QTest.keyClick(canvas, Qt.Key.Key_B)
    assert canvas.tool == "pen"


def test_CtrlS发saveRequested(canvas):
    spy = QSignalSpy(canvas.saveRequested)
    QTest.keyClick(canvas, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert spy.count() == 1


def test_Esc发escapePressed(canvas):
    spy = QSignalSpy(canvas.escapePressed)
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    assert spy.count() == 1


def test_CtrlY不再重做(canvas):
    draw_line(canvas)
    canvas.undo()
    assert canvas.document.strokes == []
    assert canvas.history.can_undo is False

    QTest.keyClick(canvas, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)

    assert canvas.document.strokes == []
    assert canvas.has_ink() is False
    assert not hasattr(canvas, "redo")


def test_H不改变工具_数字键只改颜色(canvas):
    from sketchpad.annotate.constants import PALETTE
    canvas.set_tool("pen")
    QTest.keyClick(canvas, Qt.Key.Key_H)
    assert canvas.tool == "pen"

    for key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3):
        QTest.keyClick(canvas, key)
    assert canvas.tool == "pen"
    assert canvas.pen_width == pytest.approx(float(PEN_WIDTH))
    assert canvas.color.lower() == PALETTE[2].lower()   # 3 = 黄

    drag(canvas, LINE)
    assert len(canvas.document.strokes) == 1
    stroke = canvas.document.strokes[0]
    assert stroke.tool == "pen"
    assert stroke.width == pytest.approx(float(PEN_WIDTH))
    assert stroke.color.lower() == PALETTE[2].lower()


def test_render_png透明且尺寸正确(canvas, tmp_path):
    canvas.resize(320, 240)
    draw_line(canvas)
    path = tmp_path / "ink.png"

    assert canvas.render_png(path) is True
    assert path.exists()

    image = QImage(str(path))
    assert not image.isNull()
    dpr = canvas.devicePixelRatioF()
    assert image.width() == int(round(canvas.width() * dpr))
    assert image.height() == int(round(canvas.height() * dpr))
    assert image.hasAlphaChannel() is True

    # 没有笔迹的角落必须完全透明
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(image.width() - 1, image.height() - 1).alpha() == 0

    # 笔迹附近必须有可见像素
    visible = any(
        image.pixelColor(x, y).alpha() > 0
        for x in range(5, 100)
        for y in range(40, 62)
    )
    assert visible is True
