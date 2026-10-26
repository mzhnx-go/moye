# tests/test_toolbox_offscreen.py
"""工具栏离屏测试：8 色、4 档粗细、工具切换、清屏按钮发信号（设计文档 §2 R1/R2、§6.3）。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFrame, QHBoxLayout,
                               QSizePolicy, QVBoxLayout)

from sketchpad.ui import icons, theme
from sketchpad.core.settings import WIDTHS, Settings
from sketchpad.ui.toolbox import (COLLAPSED_BAND, COLUMN_GROUP_GAP, COLUMN_SPACING,
                                  CURRENT_COLOR_SIZE, GROUP_GAP, HIT_TARGET, ICON_SIZE,
                                  PALETTE, ROW_SPACING, SLIDER_LENGTH,
                                  VERTICAL_SLIDER_WIDTH, Toolbox)

SPEC_PALETTE = ["#ff453a", "#2980b9", "#f1c40f", "#e8eaed", "#d35400",
                "#d4a017", "#27ae60", "#8e44ad", "#7f8c8d"]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def box(qapp):
    """每个用例一个真实显示的工具栏；引用留在 fixture 里，别让 Python 回收。"""
    tb = Toolbox()
    tb.show()
    QTest.qWaitForWindowExposed(tb)
    yield tb
    tb.releaseKeyboard()
    tb.close()


def test_palette_has_nine_colors_from_spec(qapp):
    assert PALETTE == SPEC_PALETTE


def test_color_buttons_emit_their_color_and_stay_checked(box):
    got = []
    box.colorPicked.connect(got.append)
    for button in box.color_buttons:
        button.click()
    assert got == SPEC_PALETTE
    assert [b.isChecked() for b in box.color_buttons] == [False] * 8 + [True]


def test_custom_color_dialog_emits_lowercase_hex(box, monkeypatch):
    class FakeDialog:
        @staticmethod
        def getColor(*args, **kwargs):
            return QColor("#12AbCd")

    monkeypatch.setattr("sketchpad.ui.toolbox.QColorDialog", FakeDialog)
    got = []
    box.colorPicked.connect(got.append)
    box.custom_color_button.click()
    assert got == ["#12abcd"]


def test_custom_color_dialog_cancel_emits_nothing(box, monkeypatch):
    class FakeDialog:
        @staticmethod
        def getColor(*args, **kwargs):
            return QColor()

    monkeypatch.setattr("sketchpad.ui.toolbox.QColorDialog", FakeDialog)
    got = []
    box.colorPicked.connect(got.append)
    box.custom_color_button.click()
    assert got == []


def test_width_buttons_emit_the_four_spec_widths(box):
    got = []
    box.widthChosen.connect(got.append)
    for button in box.width_buttons:
        button.click()
    assert len(box.width_buttons) == 4
    assert got == [1.5, 2.0, 4.0, 8.0]
    assert got == list(WIDTHS)


def test_same_width_button_twice_emits_twice(box):
    got = []
    box.widthChosen.connect(got.append)
    box.width_buttons[2].click()
    box.width_buttons[2].click()
    assert got == [4.0, 4.0]


def test_tool_buttons_emit_pen_and_eraser(box):
    got = []
    box.toolChosen.connect(got.append)
    box.eraser_button.click()
    box.pen_button.click()
    assert got == ["eraser", "pen"]


def test_clear_button_emits_clear_requested_once(box):
    """垃圾桶图标就是清屏动作本身：点一次只发一次，不进入任何捕获态。"""
    got = []
    box.clearRequested.connect(lambda: got.append(1))
    box.clear_button.click()
    assert got == [1]


def test_clear_button_keeps_icon_and_clear_tooltip(qapp):
    tb = Toolbox()
    try:
        assert tb.clear_button.toolTip() == "清屏 / 清空本页 (E)"
        assert tb.clear_button.icon().isNull() is False
        assert tb.clear_button.toolButtonStyle() == Qt.ToolButtonIconOnly
    finally:
        tb.close()


def test_next_step_icons_are_painted_in_the_same_style(qapp):
    """目录/查找/草稿纸三枚新图标：非空、与旧图标同尺寸（24×24 逻辑、DPR 2）、真画了东西。"""
    reference = icons.clear_icon().availableSizes()
    for name in ("toc_icon", "search_icon", "scratch_icon"):
        icon = getattr(icons, name)()
        assert icon.isNull() is False, name
        assert icon.availableSizes() == reference, name
        image = icon.pixmap(QSize(20, 20)).toImage()
        painted = sum(image.pixelColor(x, y).alpha() > 0
                      for x in range(image.width()) for y in range(image.height()))
        assert painted > 0, name


def test_scratch_button_follows_clear_and_emits_once(box):
    """草稿纸按钮紧挨清屏右侧；点一次只发一次，不附带别的信号。"""
    got = []
    box.scratchRequested.connect(lambda: got.append(1))
    layout = box.layout()
    assert layout.indexOf(box.scratch_button) == layout.indexOf(box.clear_button) + 1
    assert box.scratch_button.toolTip() == "草稿纸 (Alt+Q)"
    assert box.scratch_button.icon().isNull() is False
    assert box.scratch_button.focusPolicy() == Qt.NoFocus
    box.scratch_button.click()
    assert got == [1]


def test_toolbox_has_no_capture_machinery(qapp):
    """捕获整套已删除：键位只在设置面板里改（§6.3）。"""
    tb = Toolbox()
    try:
        for name in ("clearKeyCaptured", "captureStarted", "captureFinished",
                     "is_capturing", "set_clear_key_label", "_start_capture",
                     "_capture_timer", "_clear_key", "clear_key_button", "hint_label"):
            assert hasattr(tb, name) is False, name
        assert hasattr(tb, "clearRequested") is True
    finally:
        tb.close()


def test_settings_seed_width_and_color(qapp):
    tb = Toolbox(Settings(color="#2980b9", width_index=3, width=8.0))
    tb.show()
    QTest.qWaitForWindowExposed(tb)
    try:
        assert [b.isChecked() for b in tb.width_buttons] == [False, False, False, True]
        assert [b.isChecked() for b in tb.color_buttons] == [False, True] + [False] * 7
        assert tb.pen_button.isChecked() is True
    finally:
        tb.close()


def test_toolbar_buttons_never_steal_focus(qapp):
    """按钮抢焦点会让画布的 B/Q/1-4/方向键静默失效——这里钉住 NoFocus。"""
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QAbstractButton

    tb = Toolbox()
    buttons = tb.findChildren(QAbstractButton)
    assert len(buttons) >= 15
    assert {b.focusPolicy() for b in buttons} == {_Qt.NoFocus}


def test_tool_buttons_are_icon_only_and_have_icons(qapp):
    """参考 Lorien：工具栏只放图标。文字留给工具提示/读屏，不在按钮上显示。"""
    from PySide6.QtCore import Qt as _Qt
    tb = Toolbox()
    buttons = [tb.undo_button, tb.redo_button, tb.pen_button, tb.eraser_button,
               *tb.width_buttons, tb.clear_button, tb.scratch_button,
               tb.custom_color_button]
    for button in buttons:
        assert button.icon().isNull() is False, button.toolTip()
        assert button.toolButtonStyle() == _Qt.ToolButtonIconOnly


def test_undo_redo_buttons_emit(qapp):
    tb = Toolbox()
    got = []
    tb.undoRequested.connect(lambda: got.append("undo"))
    tb.redoRequested.connect(lambda: got.append("redo"))
    tb.undo_button.click()
    tb.redo_button.click()
    assert got == ["undo", "redo"]


def test_reflect_helpers_sync_checked_state_without_emitting(box):
    """反射只改选中态：再发 clicked 会回灌视图，形成回环。"""
    got = []
    box.toolChosen.connect(got.append)
    box.widthChosen.connect(got.append)

    box.reflect_tool("eraser")
    assert box.eraser_button.isChecked() is True
    assert box.pen_button.isChecked() is False
    box.reflect_width(8.0)
    assert [b.isChecked() for b in box.width_buttons] == [False, False, False, True]
    box.reflect_width(1.5)
    assert [b.isChecked() for b in box.width_buttons] == [True, False, False, False]
    box.reflect_tool("pen")
    assert box.pen_button.isChecked() is True
    assert got == []


def test_preset_click_syncs_slider(qapp):
    tb = Toolbox()
    tb.width_buttons[2].click()                 # 4.0
    assert tb.size_slider.value() == 40         # 0.1 一档 -> 4.0 * 10
    assert tb.size_label.text() == "4"


def test_slider_emits_custom_width_and_unchecks_presets(qapp):
    tb = Toolbox()
    got = []
    tb.widthChosen.connect(got.append)
    tb.size_slider.setValue(95)                 # 9.5
    assert got[-1] == 9.5
    assert not any(b.isChecked() for b in tb.width_buttons)
    assert tb.size_label.text() == "9.5"


def test_slider_value_matching_a_preset_checks_it(qapp):
    tb = Toolbox()
    tb.size_slider.setValue(80)                 # 8.0 == 第四档
    assert [b.isChecked() for b in tb.width_buttons] == [False, False, False, True]


# ---- v2.10：查找按钮 + 点粗细数字直接输入数值 ----

def test_find_button_hidden_until_enabled_and_emits(box):
    assert box.find_button.isHidden() is True       # 草稿模式默认不显示
    box.set_find_enabled(True)
    assert box.find_button.isHidden() is False
    got = []
    box.findRequested.connect(lambda: got.append(1))
    box.find_button.click()
    assert got == [1]
    box.set_find_enabled(False)
    assert box.find_button.isHidden() is True


def test_size_label_click_prompts_numeric_width(box, monkeypatch):
    class FakeDialog:
        @staticmethod
        def getDouble(*args, **kwargs):
            return 7.5, True

    monkeypatch.setattr("sketchpad.ui.toolbox.QInputDialog", FakeDialog)
    got = []
    box.widthChosen.connect(got.append)
    QTest.mouseClick(box.size_label, Qt.LeftButton)
    assert got == [7.5]
    assert box.size_label.text() == "7.5"
    assert box.size_slider.value() == 75            # 0.1 一档


def test_size_label_prompt_cancel_keeps_width(box, monkeypatch):
    class FakeDialog:
        @staticmethod
        def getDouble(*args, **kwargs):
            return 9.0, False

    monkeypatch.setattr("sketchpad.ui.toolbox.QInputDialog", FakeDialog)
    before = box.size_label.text()
    got = []
    box.widthChosen.connect(got.append)
    QTest.mouseClick(box.size_label, Qt.LeftButton)
    assert got == []
    assert box.size_label.text() == before


# ---- 视觉打磨（v2.7）：图标 20px、点击区 32、色块 24、当前色指示器 ----


def test_tool_icons_are_18px_with_28px_hit_target(qapp):
    """只放图标，但图标 18px、点击区 28×28（18px 的图标点起来太局促）。"""
    tb = Toolbox()
    try:
        buttons = [tb.undo_button, tb.redo_button, tb.pen_button, tb.eraser_button,
                   *tb.width_buttons, tb.clear_button, tb.scratch_button,
                   tb.custom_color_button]
        assert ICON_SIZE == 18
        assert HIT_TARGET == 28
        for button in buttons:
            assert button.iconSize() == QSize(18, 18), button.toolTip()
            assert button.minimumSize() == QSize(28, 28), button.toolTip()
            assert button.toolButtonStyle() == Qt.ToolButtonIconOnly
    finally:
        tb.close()


def test_color_swatches_are_20px_and_round(qapp):
    """色块 20px，圆角 = 半径 10；选中仍是白描边。"""
    tb = Toolbox()
    try:
        assert theme.SWATCH == 20
        for button in tb.color_buttons:
            assert button.size() == QSize(20, 20)
            assert button.minimumSize() == QSize(20, 20)
            assert "border-radius: 10px" in button.styleSheet()
            assert f"border: 2px solid {theme.ACCENT}" in button.styleSheet()
    finally:
        tb.close()


def test_row_spacing_and_group_gaps(qapp):
    """行距 2 + 7px spacer = 有效 9px 组间留白（布局 spacing 只加一次）；主工具栏不再有 Sep 竖线。"""
    tb = Toolbox()
    try:
        assert ROW_SPACING == 2
        assert GROUP_GAP == 7
        assert ROW_SPACING + GROUP_GAP == 9
        assert tb.layout().spacing() == ROW_SPACING
        assert [widget for widget in tb.findChildren(QFrame)
                if widget.objectName() == "Sep"] == []
        assert len(tb._group_gaps) == 6
        assert all(gap.sizeHint().width() == GROUP_GAP for gap in tb._group_gaps)
        assert "QFrame#Sep" not in theme.QSS
    finally:
        tb.close()


def test_current_color_indicator_sits_beside_custom_button(qapp):
    """当前色指示器 22px、紧挨取色器、纯显示：不是按钮，也不收鼠标。"""
    tb = Toolbox()
    try:
        indicator = tb.current_color_swatch
        layout = tb.layout()
        assert indicator.size() == QSize(CURRENT_COLOR_SIZE, CURRENT_COLOR_SIZE) == QSize(22, 22)
        assert layout.indexOf(indicator) == layout.indexOf(tb.custom_color_button) + 1
        assert not isinstance(indicator, QAbstractButton)
        assert indicator.testAttribute(Qt.WA_TransparentForMouseEvents) is True
    finally:
        tb.close()


def test_current_color_indicator_reflects_color_changes_without_emitting(box):
    """取色/换色后指示器换色 + 提示写色值；反射本身不发任何信号。"""
    got = []
    box.colorPicked.connect(got.append)
    box.widthChosen.connect(got.append)
    indicator = box.current_color_swatch

    box.color_buttons[2].click()                  # #f1c40f（黄）
    assert got == ["#f1c40f"]
    assert "#f1c40f" in indicator.styleSheet()
    assert indicator.toolTip() == "当前颜色 #f1c40f"

    box._reflect_color("#123456")                 # 键盘/外部换色走这里
    assert got == ["#f1c40f"]                     # 反射不回声
    assert "#123456" in indicator.styleSheet()
    assert indicator.toolTip() == "当前颜色 #123456"
    assert "border-radius: 11px" in indicator.styleSheet()   # 22/2


def test_collapse_button_at_far_left_hides_and_restores(qapp):
    """最左边的箭头：收起后只留 22px 窄条和箭头，再点恢复全部控件。"""
    tb = Toolbox()
    try:
        assert tb.layout().itemAt(0).widget() is tb.collapse_button
        assert tb.collapsed is False
        seen: list[bool] = []
        tb.collapsedChanged.connect(seen.append)

        tb.collapse_button.click()
        assert tb.collapsed is True
        assert seen == [True]
        assert tb.collapse_button.toolTip() == "显示工具栏"
        assert tb.height() == 22
        assert tb.collapse_button.isHidden() is False
        assert all(widget.isHidden() for widget in tb._collapsible_widgets)

        tb.collapse_button.click()
        assert tb.collapsed is False
        assert seen == [True, False]
        assert tb.collapse_button.toolTip() == "隐藏工具栏"
        assert all(not widget.isHidden() for widget in tb._collapsible_widgets)
    finally:
        tb.close()


# ---- v2.9：顶部横排 / 右侧竖排两套布局，折叠与信号完全共用 ----


def test_set_vertical_rebuilds_layout_as_one_narrow_column(qapp):
    """竖排：QVBoxLayout、竖滑杆、1px 横线，宽高比反过来（窄列），控件全部还在。"""
    tb = Toolbox()
    tb.show()
    QTest.qWaitForWindowExposed(tb)
    try:
        assert isinstance(tb.layout(), QHBoxLayout)
        assert tb.vertical is False

        tb.set_vertical(True)
        assert tb.vertical is True
        assert isinstance(tb.layout(), QVBoxLayout)
        assert tb.layout().itemAt(0).widget() is tb.collapse_button   # 竖排最上方
        assert tb.layout().spacing() == COLUMN_SPACING
        assert tb.layout().contentsMargins().left() == 1
        assert tb.layout().contentsMargins().top() == 6

        assert tb.size_slider.orientation() == Qt.Vertical
        assert tb.size_slider.minimumWidth() == tb.size_slider.maximumWidth() == VERTICAL_SLIDER_WIDTH
        assert tb.size_slider.minimumHeight() == tb.size_slider.maximumHeight() == SLIDER_LENGTH
        assert tb.size_label.alignment() & Qt.AlignHCenter

        for gap in tb._group_gaps:
            assert gap.sizeHint().height() == COLUMN_GROUP_GAP
            assert gap.sizeHint().width() == 1
            assert gap.sizePolicy().verticalPolicy() == QSizePolicy.Fixed
        assert COLUMN_SPACING + COLUMN_GROUP_GAP == 10       # 列距只加一次，有效 10px

        assert tb.sizeHint().width() < 70                    # 窄列，不是一整条 700+ 宽
        assert tb.sizeHint().width() < tb.sizeHint().height()
        assert tb.collapse_button.isHidden() is False
        assert all(not widget.isHidden() for widget in tb._collapsible_widgets)
        # 顺序仍是同一套：折叠在最上，设置齿轮最后（stretch 之后没有控件）
        assert tb.layout().indexOf(tb.settings_button) == tb.layout().count() - 2
        assert (tb.layout().indexOf(tb.scratch_button)
                < tb.layout().indexOf(tb.split_button)
                < tb.layout().indexOf(tb.settings_button))

        tb.set_vertical(False)                               # 换回横排：滑杆/组间留白复位
        assert isinstance(tb.layout(), QHBoxLayout)
        assert tb.size_slider.orientation() == Qt.Horizontal
        assert tb.size_slider.minimumWidth() == tb.size_slider.maximumWidth() == SLIDER_LENGTH
        assert tb.size_slider.maximumHeight() == 16777215
        assert tb.layout().spacing() == ROW_SPACING
        for gap in tb._group_gaps:
            assert gap.sizeHint().width() == GROUP_GAP
    finally:
        tb.close()


def _assert_gap_axis(tb, expected: int) -> None:
    """组间留白只在当前轴上有尺寸：横排看宽，竖排看高。"""
    for gap in tb._group_gaps:
        hint = gap.sizeHint()
        if tb.vertical:
            assert hint.height() == expected
            assert hint.width() == 1
        else:
            assert hint.width() == expected
            assert hint.height() == 1


def test_group_gaps_zero_when_groups_hide_and_come_back(box):
    """收起或关闭批注组时组间留白归零，恢复后回到 GROUP_GAP / COLUMN_GROUP_GAP。"""
    _assert_gap_axis(box, GROUP_GAP)

    box.set_collapsed(True)
    _assert_gap_axis(box, 0)
    box.set_collapsed(False)
    _assert_gap_axis(box, GROUP_GAP)

    box.set_annotations_enabled(False)
    _assert_gap_axis(box, 0)
    box.set_annotations_enabled(True)
    _assert_gap_axis(box, GROUP_GAP)

    box.set_vertical(True)
    _assert_gap_axis(box, COLUMN_GROUP_GAP)
    box.set_collapsed(True)
    _assert_gap_axis(box, 0)
    box.set_collapsed(False)
    _assert_gap_axis(box, COLUMN_GROUP_GAP)
    box.set_vertical(False)
    _assert_gap_axis(box, GROUP_GAP)


def test_vertical_collapse_squeezes_width_with_left_arrow(qapp):
    """竖排收起：只留 22px 宽的一列和朝左箭头（朝右隐藏、朝左再展开）。"""
    tb = Toolbox()
    tb.show()
    QTest.qWaitForWindowExposed(tb)
    try:
        tb.set_vertical(True)
        seen: list[bool] = []
        tb.collapsedChanged.connect(seen.append)

        tb.collapse_button.click()
        assert tb.collapsed is True
        assert seen == [True]
        assert tb.minimumWidth() == tb.maximumWidth() == COLLAPSED_BAND == 22
        assert tb.maximumHeight() == 16777215                # 横排的 22px 高度限制没带过来
        assert tb.collapse_button.toolTip() == "显示工具栏"
        assert tb.collapse_button.icon().isNull() is False
        assert tb.collapse_button.icon().cacheKey() == icons.chevron_icon("left").cacheKey()
        assert tb.collapse_button.isHidden() is False
        assert all(widget.isHidden() for widget in tb._collapsible_widgets)

        tb.collapse_button.click()
        assert tb.collapsed is False
        assert seen == [True, False]
        assert tb.collapse_button.toolTip() == "隐藏工具栏"
        assert tb.collapse_button.icon().cacheKey() == icons.chevron_icon("right").cacheKey()
        assert all(not widget.isHidden() for widget in tb._collapsible_widgets)
    finally:
        tb.close()


def test_switching_orientation_while_collapsed_keeps_one_band(qapp):
    """收起态换方向：固定尺寸跟着方向走，且只压一条（22px 高 / 22px 宽）。"""
    tb = Toolbox()
    try:
        tb.set_collapsed(True)                               # 横排：压高度
        assert tb.minimumHeight() == tb.maximumHeight() == 22
        assert tb.collapse_button.toolTip() == "显示工具栏"

        tb.set_vertical(True)                                # 换竖排：改压宽度
        assert tb.minimumWidth() == tb.maximumWidth() == 22
        assert tb.maximumHeight() == 16777215
        assert tb.collapse_button.icon().cacheKey() == icons.chevron_icon("left").cacheKey()
        assert all(widget.isHidden() for widget in tb._collapsible_widgets)

        tb.set_vertical(False)                               # 换回横排：又压高度
        assert tb.minimumHeight() == tb.maximumHeight() == 22
        assert tb.maximumWidth() == 16777215
        assert tb.collapse_button.icon().cacheKey() == icons.chevron_icon("down").cacheKey()

        tb.set_collapsed(False)
        assert tb.collapse_button.icon().cacheKey() == icons.chevron_icon("up").cacheKey()
        assert tb.collapse_button.toolTip() == "隐藏工具栏"
        assert all(not widget.isHidden() for widget in tb._collapsible_widgets)
    finally:
        tb.close()


def test_chevron_icon_supports_four_directions(qapp):
    """折叠箭头四个方向：尺寸/画风与旧图标一致，且四个方向互不相同。"""
    reference = icons.clear_icon().availableSizes()
    images = {}
    for direction in ("up", "down", "left", "right"):
        icon = icons.chevron_icon(direction)
        assert icon.isNull() is False, direction
        assert icon.availableSizes() == reference, direction
        image = icon.pixmap(QSize(24, 24)).toImage()
        painted = sum(image.pixelColor(x, y).alpha() > 0
                      for x in range(image.width()) for y in range(image.height()))
        assert painted > 0, direction
        images[direction] = image
    assert images["up"] != images["down"]
    assert images["left"] != images["right"]
    assert images["up"] != images["left"]
    with pytest.raises(ValueError):
        icons.chevron_icon("diagonal")


def test_select_button_emits_select_and_reflects(qapp):
    """鼠标工具（原「选择文字」）是文档模式工具：点一下发 "select"，外部切工具时按钮跟着勾选。"""
    tb = Toolbox()
    try:
        assert tb.select_button.toolTip().startswith("鼠标 (V")
        assert "平移" in tb.select_button.toolTip()
        assert "× 删除" in tb.select_button.toolTip()
        assert tb.select_button.icon().isNull() is False
        got = []
        tb.toolChosen.connect(got.append)
        tb.select_button.click()
        assert got == ["select"]
        assert tb.select_button.isChecked() is True
        tb.reflect_tool("pen")
        assert tb.pen_button.isChecked() is True
        assert tb.select_button.isChecked() is False
        tb.reflect_tool("select")
        assert tb.select_button.isChecked() is True
    finally:
        tb.close()


def test_note_button_emits_reflects_and_follows_enabled_collapse(qapp):
    """插入笔记与选择文字同档：草稿模式/收起态隐藏，展开后按启用状态恢复。"""
    tb = Toolbox()
    try:
        assert tb.note_button.toolTip() == "插入笔记 (N)"
        assert tb.note_button.icon().isNull() is False
        got = []
        tb.toolChosen.connect(got.append)
        tb.note_button.click()
        assert got == ["note"]
        assert tb.note_button.isChecked() is True
        tb.reflect_tool("pen")
        assert tb.pen_button.isChecked() is True
        tb.reflect_tool("note")
        assert tb.note_button.isChecked() is True

        tb.set_note_enabled(False)
        tb.show()
        QApplication.processEvents()
        assert tb.note_button.isVisible() is False
        tb.set_note_enabled(True)
        assert tb.note_button.isVisible() is True
        tb.set_collapsed(True)
        assert tb.note_button.isVisible() is False
        tb.set_collapsed(False)
        assert tb.note_button.isVisible() is True
    finally:
        tb.close()


def test_select_button_hidden_when_disabled_and_on_collapse(qapp):
    """草稿模式/收起态都不该露出选择按钮；展开后按启用状态恢复。"""
    tb = Toolbox()
    try:
        assert tb.select_button.isVisible() is False   # 未 show 的父窗口：isVisible 为假
        tb.set_select_enabled(False)
        tb.show()
        QApplication.processEvents()
        assert tb.select_button.isVisible() is False
        tb.set_select_enabled(True)
        assert tb.select_button.isVisible() is True
        tb.set_collapsed(True)
        assert tb.select_button.isVisible() is False
        tb.set_collapsed(False)
        assert tb.select_button.isVisible() is True
    finally:
        tb.close()


def test_pointer_icon_is_line_art_and_not_the_text_caret(qapp):
    """鼠标工具的新图标：QPainter 现画的箭头轮廓，与 18px 线性图标同一尺寸档。"""
    icon = icons.pointer_icon()
    assert icon.isNull() is False
    assert icon.availableSizes() == icons.clear_icon().availableSizes()
    image = icon.pixmap(QSize(24, 24)).toImage()
    painted = sum(image.pixelColor(x, y).alpha() > 0
                  for x in range(image.width()) for y in range(image.height()))
    assert painted > 0
    # 旧的 I 形选字图标保留给其它调用方，两者不是同一枚图
    assert image != icons.text_select_icon().pixmap(QSize(24, 24)).toImage()
    assert icons.pointer_icon() is icon            # lru_cache：反复取同一份
