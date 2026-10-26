"""覆盖层（离屏）：模式链、显隐、工具栏信号接线、笔色落盘。不校验 Win32 样式。"""
import json

import pytest

from sketchpad.annotate.config import Config
from sketchpad.annotate.constants import TOOLBAR_HEIGHT
from sketchpad.annotate.core.commands import AddStroke
from sketchpad.annotate.core.strokes import Stroke
from sketchpad.annotate.overlay import OverlayWindow


def make_overlay() -> OverlayWindow:
    last_error = None
    for args in ((Config(),), ()):
        try:
            return OverlayWindow(*args)
        except TypeError as exc:
            last_error = exc
    raise AssertionError(f"OverlayWindow 构造失败: {last_error}")


@pytest.fixture
def overlay(qapp):
    window = make_overlay()
    yield window
    window.hide_overlay()
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_组件已装配(overlay):
    assert overlay.canvas is not None
    assert overlay.toolbar is not None
    assert overlay.mode in ("hidden", "draw", "passthrough")
    # 穿透模式下整层点穿，工具栏必须是独立顶层窗口才点得动
    assert overlay.toolbar.isWindow() is True
    assert not hasattr(overlay, "chip"), "「回到批注」浮条已移除"


def test_mode链(qapp, overlay):
    overlay.hide_overlay()
    assert overlay.mode == "hidden"

    overlay.toggle_mode()
    qapp.processEvents()
    assert overlay.mode == "passthrough"      # 打开默认穿透

    overlay.toggle_mode()
    qapp.processEvents()
    assert overlay.mode == "draw"

    overlay.toggle_mode()
    qapp.processEvents()
    assert overlay.mode == "passthrough"

    overlay.hide_overlay()
    assert overlay.mode == "hidden"


def test_draw模式工具栏可见(qapp, overlay):
    overlay.enter_draw_mode()
    qapp.processEvents()

    assert overlay.mode == "draw"
    assert overlay.toolbar.isVisible() is True
    assert overlay.toolbar.passthrough_button.isChecked() is False


def test_passthrough模式工具栏不隐藏且穿透按钮点亮(qapp, overlay):
    overlay.enter_passthrough_mode()
    qapp.processEvents()

    assert overlay.mode == "passthrough"
    # 工具栏留在原处：它自己是独立窗口，穿透层没能把它一起吃掉
    assert overlay.toolbar.isVisible() is True
    assert overlay.toolbar.passthrough_button.isChecked() is True


def test_hidden模式工具栏也隐藏(qapp, overlay):
    overlay.enter_draw_mode()
    qapp.processEvents()
    overlay.hide_overlay()
    qapp.processEvents()

    assert overlay.mode == "hidden"
    assert overlay.toolbar.isVisible() is False


def test_穿透是互斥状态只能靠选笔退出(qapp, overlay):
    overlay.enter_draw_mode()
    qapp.processEvents()

    overlay.toolbar.passthroughToggled.emit()
    qapp.processEvents()
    assert overlay.mode == "passthrough"

    # 再点一次穿透不会退出：三选一，退出只能去点钢笔 / 橡皮
    overlay.toolbar.passthroughToggled.emit()
    qapp.processEvents()
    assert overlay.mode == "passthrough"

    overlay.toolbar.toolChosen.emit("eraser")
    qapp.processEvents()
    assert overlay.mode == "draw"
    assert overlay.canvas.tool == "eraser"
    assert overlay.toolbar.passthrough_button.isChecked() is False
    assert overlay.toolbar.eraser_button.isChecked() is True


def test_toolbar的toolChosen改变canvas工具(qapp, overlay):
    overlay.enter_draw_mode()
    qapp.processEvents()

    overlay.toolbar.toolChosen.emit("eraser")
    qapp.processEvents()
    assert overlay.canvas.tool == "eraser"

    overlay.toolbar.toolChosen.emit("pen")
    qapp.processEvents()
    assert overlay.canvas.tool == "pen"


def test_toolbar的colorChosen改canvas颜色并写config(
        qapp, overlay, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    real_save = Config.save
    calls: list[bool] = []

    def save_to_tmp(config, _path=None):
        calls.append(True)
        return real_save(config, path)

    monkeypatch.setattr(Config, "save", save_to_tmp)

    overlay.toolbar.colorChosen.emit("#123456")
    qapp.processEvents()

    assert overlay.canvas.color == "#123456"
    assert overlay.config.color == "#123456"
    assert calls, "colorChosen 后没有调用 config.save 落盘"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["color"] == "#123456"

    # 再换一色也必须同步覆盖
    overlay.toolbar.colorChosen.emit("#27ae60")
    qapp.processEvents()
    assert overlay.canvas.color == "#27ae60"
    assert json.loads(path.read_text(encoding="utf-8"))["color"] == "#27ae60"


def test_toolbar的widthChosen改canvas线宽并写config(
        qapp, overlay, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    real_save = Config.save
    calls: list[bool] = []

    def save_to_tmp(config, _path=None):
        calls.append(True)
        return real_save(config, path)

    monkeypatch.setattr(Config, "save", save_to_tmp)

    overlay.toolbar.widthChosen.emit(11.0)
    qapp.processEvents()

    assert overlay.canvas.pen_width == pytest.approx(11.0)
    assert overlay.config.pen_width == pytest.approx(11.0)
    assert calls, "widthChosen 后没有调用 config.save 落盘"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["pen_width"] == pytest.approx(11.0)

    overlay.toolbar.widthChosen.emit(2.0)
    qapp.processEvents()
    assert overlay.canvas.pen_width == pytest.approx(2.0)
    assert json.loads(path.read_text(encoding="utf-8"))["pen_width"] == pytest.approx(2.0)


def test_启动时把config里的线宽应用到画布(qapp):
    window = OverlayWindow(Config(pen_width=11.0))
    try:
        assert window.canvas.pen_width == pytest.approx(11.0)
    finally:
        window.hide_overlay()
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_toolbar的undoRequested调用canvas撤销(qapp, overlay):
    canvas = overlay.canvas
    command = AddStroke(
        canvas.document,
        Stroke([(10.0, 10.0), (30.0, 10.0)], "#e8eaed", 4.0),
    )
    command.apply()
    canvas.history.push(command)
    assert canvas.has_ink() is True

    overlay.toolbar.undoRequested.emit()
    qapp.processEvents()

    assert canvas.has_ink() is False
    assert canvas.history.can_undo is False


def test_toolbar的clearRequested调用canvas清空(qapp, overlay):
    canvas = overlay.canvas
    command = AddStroke(
        canvas.document,
        Stroke([(10.0, 10.0), (30.0, 10.0)], "#e8eaed", 4.0),
    )
    command.apply()
    canvas.history.push(command)

    overlay.enter_draw_mode()
    qapp.processEvents()
    overlay.toolbar.clearRequested.emit()
    qapp.processEvents()

    assert canvas.has_ink() is False
    # 清空可撤销
    assert canvas.history.can_undo is True


def test_toolbar的passthroughToggled切穿透(qapp, overlay):
    overlay.enter_draw_mode()
    qapp.processEvents()

    overlay.toolbar.passthroughToggled.emit()
    qapp.processEvents()

    assert overlay.mode == "passthrough"
    assert overlay.toolbar.isVisible() is True
    assert overlay.toolbar.passthrough_button.isChecked() is True


def test_竖排后工具栏贴右边并落盘(qapp, overlay, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    real_save = Config.save
    monkeypatch.setattr(Config, "save", lambda cfg, _p=None: real_save(cfg, path))

    overlay.enter_draw_mode()
    qapp.processEvents()
    overlay.toolbar.set_orientation("vertical")
    qapp.processEvents()

    if overlay.toolbar.orientation != "vertical":
        pytest.skip("当前屏幕高度放不下竖排工具栏，overlay 会主动退回横排")

    assert overlay.config.toolbar_orientation == "vertical"
    # 换方向会清掉旧位置：竖排比横排高一截，沿用旧坐标容易顶出屏幕
    assert overlay.config.toolbar_pos is None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["toolbar_orientation"] == "vertical"

    rect = overlay.toolbar.geometry()
    area = overlay.toolbar.screen().geometry()
    assert rect.width() == TOOLBAR_HEIGHT
    assert rect.right() <= area.right()
    assert rect.x() > area.center().x()          # 贴右半屏，别挡中间内容
    assert rect.height() <= area.height()


def test_拖动位置落盘并在重新显示时沿用(qapp, overlay, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    real_save = Config.save
    monkeypatch.setattr(Config, "save", lambda cfg, _p=None: real_save(cfg, path))

    overlay.enter_draw_mode()
    qapp.processEvents()
    area = overlay.toolbar.screen().geometry()
    x, y = area.left(), area.top() + 60          # 左上角附近，必然在屏内
    overlay.toolbar.moved.emit(x, y)             # 拖动结束时工具栏发出的信号
    qapp.processEvents()

    landed = [overlay.toolbar.x(), overlay.toolbar.y()]
    assert overlay.config.toolbar_pos == landed
    assert json.loads(path.read_text(encoding="utf-8"))["toolbar_pos"] == landed

    # 隐藏再出来，还摆在拖动后的地方
    overlay.hide_overlay()
    overlay.enter_draw_mode()
    qapp.processEvents()
    assert (overlay.toolbar.x(), overlay.toolbar.y()) == tuple(landed)


def test_拖到屏幕外会被夹回来(qapp, overlay, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    real_save = Config.save
    monkeypatch.setattr(Config, "save", lambda cfg, _p=None: real_save(cfg, path))

    overlay.enter_draw_mode()
    qapp.processEvents()
    overlay.toolbar.moved.emit(99999, 99999)
    qapp.processEvents()

    area = overlay.toolbar.screen().geometry()
    rect = overlay.toolbar.geometry()
    # 夹取只保证左上角不越界；工具栏本身比屏幕宽时（离屏 800 宽放不下 928）
    # 右边只能贴到边界，不能再强求完整可见
    assert rect.x() <= area.right()
    assert rect.y() <= area.bottom()
    assert rect.x() >= area.left()
    assert rect.y() >= area.top()
    assert overlay.config.toolbar_pos == [rect.x(), rect.y()]


def test_切方向后强制回到穿透模式(qapp, overlay, tmp_path, monkeypatch):
    """用户要求：换横/竖排后强制回穿透，避免重排期间误拦鼠标。"""
    path = tmp_path / "settings.json"
    real_save = Config.save
    monkeypatch.setattr(Config, "save", lambda cfg, _p=None: real_save(cfg, path))

    overlay.enter_passthrough_mode()
    qapp.processEvents()
    assert overlay.mode == "passthrough"

    overlay.toolbar.set_orientation("vertical")
    qapp.processEvents()

    assert overlay.mode == "passthrough"
    assert overlay.toolbar.isVisible() is True
    assert overlay.toolbar.passthrough_button.isChecked() is True


def test_自愈巡检定时器已启动(overlay):
    """画多之后工具栏点不动这类失效，靠这个巡检兜底。"""
    timer = getattr(overlay, "_self_heal_timer", None)
    assert timer is not None, "缺少自愈巡检定时器"
    assert timer.isActive() is True


def test_自愈巡检在隐藏模式下直接返回(overlay):
    overlay.hide_overlay()
    overlay._self_heal()                 # 不应抛异常
    assert overlay.mode == "hidden"


def test_独占全屏只提示一次(overlay, monkeypatch):
    from sketchpad.annotate import overlay as overlay_module
    monkeypatch.setattr(overlay_module, "exclusive_fullscreen_active", lambda: True)
    overlay.mode = "passthrough"
    messages: list[str] = []
    overlay.trayMessage.connect(messages.append)
    overlay._self_heal()
    overlay._self_heal()
    assert len(messages) == 1
    assert "独占全屏" in messages[0]


def test_退出独占全屏后允许再次提示(overlay, monkeypatch):
    from sketchpad.annotate import overlay as overlay_module
    states = iter([True, False, True])
    monkeypatch.setattr(overlay_module, "exclusive_fullscreen_active",
                        lambda: next(states, False))
    overlay.mode = "passthrough"
    messages: list[str] = []
    overlay.trayMessage.connect(messages.append)
    overlay._self_heal()
    overlay._self_heal()
    overlay._self_heal()
    assert len(messages) == 2


def test_Esc不收起批注层(qapp, overlay):
    overlay.enter_draw_mode()
    qapp.processEvents()
    overlay.canvas.escapePressed.emit()
    qapp.processEvents()
    assert overlay.mode == "draw"       # 用户要求：Esc 不再误关整层
    overlay.hide_overlay()
    assert overlay.mode == "hidden"


def test_收起时释放进行中的手势(qapp, overlay):
    calls: list[bool] = []
    overlay.canvas.abort_gesture = lambda: calls.append(True)
    overlay.enter_draw_mode()
    overlay.hide_overlay()
    assert calls == [True]
    assert overlay.mode == "hidden"


def test_toolbar的hideRequested收起(qapp, overlay):
    overlay.enter_draw_mode()
    qapp.processEvents()

    overlay.toolbar.hideRequested.emit()
    qapp.processEvents()

    assert overlay.mode == "hidden"
    assert overlay.toolbar.isVisible() is False
