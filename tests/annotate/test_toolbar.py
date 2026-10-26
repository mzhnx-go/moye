"""工具栏（离屏）：纯图标按钮 + 颜色盘发信号、状态倒映、排布与拖动。

颜色是否"选中"用两种稳定判据之一判断：checkable 的 checked 状态，或按钮
样式里出现 TOOL_ACCENT；当前色圆点则读 objectName 为 currentColorSwatch 的
控件（动态属性 color 或样式串里的色值）。不打开 QColorDialog，避免阻塞。
"""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QAbstractButton, QDialog, QInputDialog, QSlider, QWidget

from sketchpad.annotate.constants import (PALETTE, PEN_WIDTH, PEN_WIDTHS, TOOL_ACCENT,
                                  TOOLBAR_HEIGHT)
from sketchpad.annotate.toolbar import Toolbar

CORE_BUTTONS = ("undoButton", "penButton", "eraserButton", "clearButton",
                "passthroughButton", "hideButton")
COLOR_BUTTONS = tuple(f"colorButton{i}" for i in range(9))
WIDTH_BUTTONS = tuple(f"widthButton{i}" for i in range(len(PEN_WIDTHS)))


@pytest.fixture
def toolbar(qapp):
    widget = Toolbar()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def button(widget, object_name: str) -> QAbstractButton:
    found = widget.findChild(QAbstractButton, object_name)
    assert found is not None, f"找不到按钮 {object_name}"
    return found


def widget(widget_root, object_name: str) -> QWidget:
    found = widget_root.findChild(QWidget, object_name)
    assert found is not None, f"找不到控件 {object_name}"
    return found


def _color_marks(target: QWidget) -> str:
    """收集控件上能表示颜色的文本：动态属性 color + 自身样式串。"""
    parts: list[str] = []
    prop = target.property("color")
    if isinstance(prop, QColor):
        parts.append(prop.name())
    elif prop is not None:
        parts.append(str(prop))
    parts.append(target.styleSheet())
    return " ".join(parts).lower().replace("#", "")


def _mentions(target: QWidget, color: str) -> bool:
    return color.lower().lstrip("#") in _color_marks(target)


def _is_selected(swatch: QAbstractButton) -> bool:
    if swatch.isChecked():
        return True
    return TOOL_ACCENT.lower().lstrip("#") in _color_marks(swatch)


# ---------- 结构 ----------

def test_按钮objectName齐全(toolbar):
    for name in CORE_BUTTONS + COLOR_BUTTONS + ("customColorButton",):
        assert button(toolbar, name) is not None, name
    assert widget(toolbar, "currentColorSwatch") is not None


def test_九个色块数量正确(toolbar):
    assert len(PALETTE) == 9
    found = {
        w.objectName()
        for w in toolbar.findChildren(QWidget)
        if w.objectName().startswith("colorButton")
    }
    assert found == set(COLOR_BUTTONS)
    assert toolbar.findChild(QWidget, "colorButton8") is not None


def test_色盘顺序与主软件一致且红蓝在前(toolbar):
    from sketchpad.annotate.constants import PALETTE_NAMES
    from sketchpad.ui.toolbox import PALETTE as MAIN_PALETTE
    assert list(PALETTE) == list(MAIN_PALETTE)
    assert PALETTE[0].lower() == "#ff453a"      # 红
    assert PALETTE[1].lower() == "#2980b9"      # 蓝
    assert PALETTE_NAMES[0] == "红" and PALETTE_NAMES[1] == "蓝"
    for index in range(3):
        tip = button(toolbar, f"colorButton{index}").toolTip()
        assert tip.startswith(f"{index + 1} · ")


def test_核心按钮与色块都是纯图标(toolbar):
    for name in CORE_BUTTONS:
        target = button(toolbar, name)
        assert target.text() == "", f"{name} 不应带文字"
        assert target.icon().isNull() is False, f"{name} 缺少图标"
    for name in COLOR_BUTTONS:
        assert button(toolbar, name).text() == "", f"{name} 不应带文字"


def test_已删除功能的按钮不存在(toolbar):
    removed = (
        "highlighterButton", "highlighterColorButton",
        "textButton", "selectButton", "boxButton",
        "redoButton", "saveButton", "thicknessButton",
    )
    for name in removed:
        assert toolbar.findChild(QWidget, name) is None, name


def test_四个粗细按钮存在且互斥(toolbar):
    buttons = [button(toolbar, name) for name in WIDTH_BUTTONS]
    assert len(buttons) == len(PEN_WIDTHS) == 4
    for item in buttons:
        assert item.isCheckable() is True
        assert item.toolTip().strip() != ""

    toolbar.reflect_width(PEN_WIDTHS[2])
    assert [item.isChecked() for item in buttons] == [False, False, True, False]

    buttons[0].click()                      # 互斥组：点一档就熄掉其它档
    assert [item.isChecked() for item in buttons] == [True, False, False, False]


@pytest.mark.parametrize("index", range(4))
def test_每个粗细按钮发对应线宽(toolbar, index):
    spy = QSignalSpy(toolbar.widthChosen)
    button(toolbar, f"widthButton{index}").click()
    assert spy.count() == 1
    assert float(spy.at(0)[0]) == pytest.approx(PEN_WIDTHS[index])


def test_reflect_width取最近档位(toolbar):
    toolbar.reflect_width(6.9)              # 介于 4 和 7 之间，靠 7
    assert toolbar.width_buttons[2].isChecked()

    toolbar.reflect_width(1.0)              # 比最小档还细，落回第一档
    assert toolbar.width_buttons[0].isChecked()

    toolbar.reflect_width("bad")            # 坏值不炸，点亮默认档
    assert toolbar.width_buttons[PEN_WIDTHS.index(PEN_WIDTH)].isChecked()


def test_粗细滑杆与数字同步且不回声(toolbar):
    toolbar.reflect_width(7.0)
    assert toolbar.width_slider.value() == 70
    assert toolbar.width_value.text() == "7"
    assert toolbar.width_buttons[2].isChecked()

    spy = QSignalSpy(toolbar.widthChosen)
    toolbar.width_slider.setValue(15)       # 1.5，不落在四档上
    assert spy.count() == 1
    assert float(spy.at(0)[0]) == pytest.approx(1.5)
    assert toolbar.width_value.text() == "1.5"
    assert toolbar.width_buttons[0].isChecked()

    spy = QSignalSpy(toolbar.widthChosen)
    toolbar.reflect_width(20.0)             # 外部倒映不触发 widthChosen
    assert spy.count() == 0
    assert toolbar.width_value.text() == "20"
    assert toolbar.width_slider.value() == 200


def test_点粗细数字可输入数值(toolbar, monkeypatch):
    toolbar.reflect_width(4.0)
    spy = QSignalSpy(toolbar.widthChosen)
    monkeypatch.setattr(QInputDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(QInputDialog, "doubleValue", lambda self: 12.5)

    QTest.mouseClick(toolbar.width_value, Qt.LeftButton)
    assert spy.count() == 1
    assert float(spy.at(0)[0]) == pytest.approx(12.5)
    assert toolbar.width_value.text() == "12.5"
    assert toolbar.width_slider.value() == 125


def test_粗细输入取消不改值(toolbar, monkeypatch):
    toolbar.reflect_width(4.0)
    spy = QSignalSpy(toolbar.widthChosen)
    monkeypatch.setattr(QInputDialog, "exec", lambda self: QDialog.Rejected)

    QTest.mouseClick(toolbar.width_value, Qt.LeftButton)
    assert spy.count() == 0
    assert toolbar.width_value.text() == "4"


def test_竖排时粗细滑杆跟着转向(toolbar):
    toolbar.set_orientation("vertical", notify=False)
    assert toolbar.width_slider.orientation() == Qt.Vertical
    assert toolbar.width_slider.height() > toolbar.width_slider.width()

    toolbar.set_orientation("horizontal", notify=False)
    assert toolbar.width_slider.orientation() == Qt.Horizontal
    assert toolbar.width_slider.width() > toolbar.width_slider.height()


def test_自定义颜色按钮存在且有tooltip(toolbar):
    custom = button(toolbar, "customColorButton")
    assert custom.toolTip().strip() != ""


# ---------- 点击发信号 ----------

@pytest.mark.parametrize("object_name,tool", [
    ("penButton", "pen"),
    ("eraserButton", "eraser"),
])
def test_工具按钮发toolChosen(toolbar, object_name, tool):
    spy = QSignalSpy(toolbar.toolChosen)
    button(toolbar, object_name).click()
    assert spy.count() == 1
    assert spy.at(0)[0] == tool


@pytest.mark.parametrize("object_name,signal_name", [
    ("clearButton", "clearRequested"),
    ("hideButton", "hideRequested"),
])
def test_动作按钮发对应信号(toolbar, object_name, signal_name):
    spy = QSignalSpy(getattr(toolbar, signal_name))
    button(toolbar, object_name).click()
    assert spy.count() == 1


def test_撤销按钮发undoRequested(toolbar):
    toolbar.reflect_history(True)
    spy = QSignalSpy(toolbar.undoRequested)
    button(toolbar, "undoButton").click()
    assert spy.count() == 1


@pytest.mark.parametrize("index", range(9))
def test_每个色块发对应颜色(toolbar, index):
    spy = QSignalSpy(toolbar.colorChosen)
    button(toolbar, f"colorButton{index}").click()
    assert spy.count() == 1
    assert str(spy.at(0)[0]).lower() == PALETTE[index].lower()


# ---------- 状态倒映 ----------

def test_reflect_tool只选中当前工具(toolbar):
    pen = button(toolbar, "penButton")
    eraser = button(toolbar, "eraserButton")

    toolbar.reflect_tool("eraser")
    assert eraser.isChecked() is True
    assert pen.isChecked() is False

    toolbar.reflect_tool("pen")
    assert pen.isChecked() is True
    assert eraser.isChecked() is False


def test_reflect_tool不发toolChosen(toolbar):
    spy = QSignalSpy(toolbar.toolChosen)
    toolbar.reflect_tool("eraser")
    toolbar.reflect_tool("pen")
    assert spy.count() == 0


def test_reflect_color选中对应色块(toolbar):
    swatches = [button(toolbar, f"colorButton{i}") for i in range(9)]

    toolbar.reflect_color(PALETTE[1])
    assert _is_selected(swatches[1]) is True
    assert _is_selected(swatches[0]) is False
    assert _is_selected(swatches[4]) is False

    toolbar.reflect_color(PALETTE[4])
    assert _is_selected(swatches[4]) is True
    assert _is_selected(swatches[1]) is False


def test_reflect_color更新当前色圆点(toolbar):
    swatch = widget(toolbar, "currentColorSwatch")

    toolbar.reflect_color(PALETTE[1])
    assert _mentions(swatch, PALETTE[1]) is True

    toolbar.reflect_color(PALETTE[4])
    assert _mentions(swatch, PALETTE[4]) is True
    assert _mentions(swatch, PALETTE[1]) is False


def test_reflect_history控制撤销按钮可用性(toolbar):
    undo = button(toolbar, "undoButton")
    assert undo.isEnabled() is False

    spy = QSignalSpy(toolbar.undoRequested)
    undo.click()
    assert spy.count() == 0

    toolbar.reflect_history(True)
    assert undo.isEnabled() is True
    undo.click()
    assert spy.count() == 1

    toolbar.reflect_history(False)
    assert undo.isEnabled() is False


# ---------- 穿透开关（替代旧的「回到批注」浮条）----------

def test_工具栏自己是顶层窗口(qapp, toolbar):
    """必须是独立 hwnd，否则穿透模式下整层点穿，工具栏也跟着失联。"""
    assert toolbar.isWindow() is True


def test_穿透按钮点击发passthroughToggled(toolbar):
    spy = QSignalSpy(toolbar.passthroughToggled)
    button(toolbar, "passthroughButton").click()
    assert spy.count() == 1


def test_reflect_mode让穿透按钮变绿并换深色图标(toolbar):
    target = button(toolbar, "passthroughButton")

    toolbar.reflect_mode("passthrough")
    assert target.isChecked() is True

    toolbar.reflect_mode("draw")
    assert target.isChecked() is False

    toolbar.reflect_mode("hidden")
    assert target.isChecked() is False


# ---------- 排布方向 ----------

def test_默认横排且高度等于工具栏高度(toolbar):
    assert toolbar.orientation == "horizontal"
    hint = toolbar.sizeHint()
    assert hint.height() == TOOLBAR_HEIGHT
    assert hint.width() > hint.height()


def test_切竖排后变窄变高(toolbar):
    horizontal_hint = toolbar.sizeHint()
    spy = QSignalSpy(toolbar.orientationChanged)

    toolbar.set_orientation("vertical")

    vertical_hint = toolbar.sizeHint()
    assert toolbar.orientation == "vertical"
    assert spy.count() == 1
    assert str(spy.at(0)[0]) == "vertical"
    assert vertical_hint.width() == TOOLBAR_HEIGHT
    # 20 多个控件竖着叠起来，高度远大于 44 的横排厚度
    assert vertical_hint.height() > 10 * TOOLBAR_HEIGHT


def test_切回横排恢复原尺寸(toolbar):
    horizontal_hint = toolbar.sizeHint()
    toolbar.set_orientation("vertical")
    toolbar.set_orientation("horizontal")
    assert toolbar.sizeHint() == horizontal_hint


def test_方向没变化或非法值不发信号(toolbar):
    spy = QSignalSpy(toolbar.orientationChanged)
    toolbar.set_orientation("horizontal")     # 已经是横排
    toolbar.set_orientation("diagonal")       # 非法
    assert toolbar.orientation == "horizontal"
    assert spy.count() == 0


def test_方向按钮点击来回切换(toolbar):
    button(toolbar, "orientButton").click()
    assert toolbar.orientation == "vertical"
    button(toolbar, "orientButton").click()
    assert toolbar.orientation == "horizontal"


def test_切回横排后窗口尺寸仍等于sizeHint(qapp, toolbar):
    """回归：只锁"厚度"时 layout 会在事件循环里把窗口再缩一次（实测 749×48 → 499×32），
    画面被拉成畸形、鼠标命中还算在旧表面上，整条工具栏都点不动。"""
    toolbar.show()
    toolbar.set_orientation("vertical")
    qapp.processEvents()
    assert toolbar.size() == toolbar.sizeHint()

    toolbar.set_orientation("horizontal")
    qapp.processEvents()
    assert toolbar.size() == toolbar.sizeHint()
    assert toolbar.width() == toolbar.sizeHint().width()


def test_穿透按钮排在最前面(toolbar):
    """穿透是常驻的模式开关，默认状态就是穿透，所以放在拖动把手之后第一个。"""
    order = [w.objectName() for w in toolbar._items]
    assert order[0] == "DragGrip"
    assert order[1] == "passthroughButton"
    assert order.index("passthroughButton") < order.index("penButton")


def test_三态互斥任何时候只亮一个(toolbar):
    """穿透 / 钢笔 / 橡皮 是同一组的三个状态，不能并列。"""
    trio = (toolbar.passthrough_button, toolbar.pen_button, toolbar.eraser_button)

    toolbar.reflect_mode("passthrough")
    assert [b.isChecked() for b in trio] == [True, False, False]

    toolbar.reflect_tool("eraser")
    assert [b.isChecked() for b in trio] == [False, False, True]

    toolbar.reflect_tool("pen")
    assert [b.isChecked() for b in trio] == [False, True, False]

    # 批注模式下按当前工具点亮
    toolbar.reflect_mode("draw")
    assert [b.isChecked() for b in trio] == [False, True, False]
    toolbar.reflect_mode("passthrough")
    assert [b.isChecked() for b in trio] == [True, False, False]


def test_撤销排在清空后面(toolbar):
    order = [w.objectName() for w in toolbar._items]
    assert order.index("clearButton") < order.index("undoButton")
    assert order.index("undoButton") < order.index("hideButton")


# ---------- 拖动 ----------

def test_拖动把手搬动窗口并发moved(qapp, toolbar):
    toolbar.show()
    toolbar.move(300, 200)
    qapp.processEvents()
    origin = toolbar.pos()

    spy = QSignalSpy(toolbar.moved)
    toolbar.begin_drag(origin + QPoint(5, 5))
    toolbar.drag_to(origin + QPoint(85, 65))
    toolbar.end_drag()

    assert toolbar.pos() == origin + QPoint(80, 60)
    assert spy.count() == 1
    assert (int(spy.at(0)[0]), int(spy.at(0)[1])) == (toolbar.x(), toolbar.y())
    assert toolbar.dragging is False


def test_没按下就drag_to不会移动(toolbar):
    toolbar.move(100, 100)
    toolbar.drag_to(QPoint(900, 900))
    assert toolbar.pos() == QPoint(100, 100)
    assert toolbar.dragging is False


def test_拖动结束后把手光标还原(toolbar):
    toolbar.begin_drag(QPoint(0, 0))
    assert toolbar.dragging is True
    toolbar.end_drag()
    assert toolbar.dragging is False
