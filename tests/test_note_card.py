"""笔记卡片与遮罩的离屏测试：卡片工具栏只改自己的 note、纸色切换、遮罩开合与放大，
外加右下角手柄的自主缩放（只改显示尺寸，绝不动 note.w/h 这块设计版面）。

卡片是独立组件：这里不建 MainWindow，宿主只是一个普通 QWidget，用来验证
「按钮直接调用 card.view」「遮罩只在卡片外关闭」「动画结束后状态稳定」。

2026-10-22 追加：标题自定义名/双击重命名、手柄开关、独立笔记窗口（窗口外自由缩放）。
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractButton, QApplication, QMessageBox, QWidget)

from sketchpad.core.commands import AddStroke
from sketchpad.core.notes import Note
from sketchpad.core.settings import WIDTHS, Settings
from sketchpad.core.strokes import Stroke
from sketchpad.ui import icons
from sketchpad.ui.note_card import (CARD_MIN_H, CARD_MIN_W, GRIP_SIZE, NoteCard,
                                    NoteWindow, clamp_display_size)
from sketchpad.ui.toolbox import PALETTE

NOTE_ASPECT = 720.0 / 560.0             # 版面纵横比：显示尺寸必须一直合这个比例

# 显示出来的控件必须留 Python 引用，否则包装对象被回收、窗口"活着但没了"
_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _send_mouse(view, kind, x: float, y: float, button, buttons) -> None:
    """直接给视口发鼠标事件（QTest.mouseMove 带按键拖动在 offscreen 上不稳定）。"""
    local = QPointF(x, y)
    global_pos = QPointF(view.viewport().mapToGlobal(QPoint(int(x), int(y))))
    ev = QMouseEvent(kind, local, global_pos, button, buttons, Qt.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def _show_card(note: Note | None = None, settings: Settings | None = None,
               display_name: str = "") -> NoteCard:
    card = NoteCard(note or Note("n-1", 10, 20), settings or Settings(),
                    display_name=display_name)
    card.resize(560, 720)
    card.show()
    QTest.qWait(30)
    _ALIVE.append(card)
    return card


def _show_window(note: Note | None = None, display_name: str = "") -> NoteWindow:
    """独立窗口必须留 Python 引用：没有 WA_DeleteOnClose，窗口不会自己消失。"""
    window = NoteWindow(note or Note("n-win", 10, 20), Settings(),
                        display_name=display_name)
    window.show()
    QTest.qWait(40)
    _ALIVE.append(window)
    return window


def _note_with_name(note_id: str = "n-name", name: str = "函数笔记") -> Note:
    """造一张带 `name` 的笔记。`Note.name` 由另一个任务在加，可能还没落地：
    有字段就用构造参数，没有就先建再挂属性（卡片走的是 `getattr` 兜底，两条路等价）。"""
    try:
        return Note(note_id, 10, 20, name=name)
    except TypeError:
        note = Note(note_id, 10, 20)
        try:
            note.name = name
        except AttributeError:                  # 真遇到不可写的模型就跳过该分支
            pytest.skip("Note 无法承载 name：跳过 name 回退分支")
        return note


def _icon_bytes(icon, size: int = 18) -> bytes:
    image = icon.pixmap(size, size).toImage().convertToFormat(QImage.Format_ARGB32)
    return bytes(image.constBits())


def _make_host(width: int, height: int) -> QWidget:
    """遮罩的宿主（替身 PDF 视图栈）：普通 QWidget，显示出来才有真实 resize 事件。"""
    host = QWidget()
    host.resize(width, height)
    host.show()
    QTest.qWait(30)
    _ALIVE.append(host)
    return host


def _drop(*widgets) -> None:
    for widget in widgets:
        if widget in _ALIVE:
            _ALIVE.remove(widget)
        widget.close()


# --- 手柄拖拽的测试替身 ---


def _send_grip(card: NoteCard, kind, global_pos: QPoint, button, buttons) -> None:
    """给右下角手柄发鼠标事件：一律用全局坐标，跟真鼠标一样（卡片在动也不飘）。"""
    grip = card.grip
    local = grip.mapFromGlobal(global_pos)
    ev = QMouseEvent(kind, QPointF(local), QPointF(global_pos),
                     button, buttons, Qt.NoModifier)
    QApplication.sendEvent(grip, ev)


def _drag_grip(card: NoteCard, start: QPoint, delta: QPoint, steps: int = 3) -> list[QSize]:
    """按→挪→松一整轮，返回拖拽过程中收到的全部 resizeRequested 尺寸。

    `start` 是卡片坐标（按在角上），`delta` 是**屏幕**上的位移：卡片被遮罩边拖边改
    尺寸，只有全局坐标才是用户眼里那条真实的轨迹。
    """
    sizes: list[QSize] = []
    card.resizeRequested.connect(sizes.append)
    origin = card.mapToGlobal(start)
    _send_grip(card, QEvent.MouseButtonPress, origin, Qt.LeftButton, Qt.LeftButton)
    for step in range(1, steps + 1):
        ratio = step / steps
        _send_grip(card, QEvent.MouseMove,
                   QPoint(origin.x() + int(round(delta.x() * ratio)),
                          origin.y() + int(round(delta.y() * ratio))),
                   Qt.NoButton, Qt.LeftButton)
    _send_grip(card, QEvent.MouseButtonRelease,
               QPoint(origin.x() + delta.x(), origin.y() + delta.y()),
               Qt.LeftButton, Qt.NoButton)
    return sizes


def _grip_corner(card: NoteCard) -> QPoint:
    """手柄热区里靠边的一粒（卡片坐标）：模拟「按住了角」。"""
    return QPoint(card.width() - 4, card.height() - 4)


def _assert_aspect_locked(size: QSize) -> None:
    assert size.height() / size.width() == pytest.approx(NOTE_ASPECT, rel=0.02)
    assert size.width() >= CARD_MIN_W and size.height() >= CARD_MIN_H


# --- 卡片工具栏 ---


def test_card_toolbar_controls_note_only(qapp):
    note = Note("n-1", 10, 20)
    card = _show_card(note)
    try:
        card.eraser_button.click()
        assert card.view.tool == "eraser"
        card.pen_button.click()
        assert card.view.tool == "pen"

        card.color_buttons[1].click()
        assert card.view.color.lower() == PALETTE[1].lower()
        assert card.color_buttons[1].isChecked()
        assert not card.color_buttons[0].isChecked()

        card.width_buttons[3].click()
        assert card.view.width == WIDTHS[3]
        assert card.width_buttons[3].isChecked()

        stroke = Stroke([(10.0, 10.0), (30.0, 30.0)], "#e8eaed", 2.0)
        cmd = AddStroke(card.view.document, stroke)
        cmd.apply()
        card.view.history.push(cmd)
        card.view.scene.rebind()
        assert note.strokes == [stroke]

        card.clear_button.click()
        assert note.strokes == []
    finally:
        _ALIVE.remove(card)
        card.close()


def test_paper_toggle_switches_colors(qapp):
    note = Note("n-1", 10, 20)
    card = _show_card(note)
    try:
        card.paper_button.click()
        assert note.paper == "light"
        assert card.view.scene.paper == "light"
        assert card.view.color == "#2c2c2e"       # 浅纸自动换深色笔

        card.paper_button.click()
        assert note.paper == "dark"
        assert card.view.scene.paper == "dark"
        assert card.view.color == "#e8eaed"       # 深纸再把笔换回浅色

        card.set_paper("paper?")                  # 坏纸名归一成深纸，不抛也不分叉
        assert note.paper == "dark"
        assert card.view.scene.paper == "dark"
    finally:
        _ALIVE.remove(card)
        card.close()


def test_card_restores_note_paper_and_readable_ink(qapp):
    note = Note("n-2", 0, 0, paper="light")
    card = _show_card(note)
    try:
        assert card.view.scene.paper == "light"
        assert card.view.color == "#2c2c2e"
        assert not card.paper_button.icon().isNull()
    finally:
        _ALIVE.remove(card)
        card.close()


def test_card_buttons_do_not_take_focus_and_forward_signals(qapp):
    card = _show_card()
    try:
        for button in card.findChildren(QAbstractButton):
            assert button.focusPolicy() == Qt.NoFocus
        hits: list[str] = []
        card.toggleExpandRequested.connect(lambda: hits.append("expand"))
        card.closeRequested.connect(lambda: hits.append("close"))
        card.expand_button.click()
        card.close_button.click()
        assert hits == ["expand", "close"]

        card.view.setFocus()
        QTest.qWait(20)
        assert card.view.hasFocus()
        QTest.mouseClick(card.eraser_button, Qt.LeftButton)
        QTest.qWait(20)
        assert card.view.hasFocus()             # 点工具栏不抢画布焦点
        assert card.view.tool == "eraser"
    finally:
        _ALIVE.remove(card)
        card.close()


def test_drawing_on_card_grows_strokes_and_undo(qapp):
    note = Note("n-draw", 10, 20)
    card = _show_card(note)
    try:
        changes: list[int] = []
        card.contentChanged.connect(lambda: changes.append(1))
        view = card.view
        cx, cy = view.viewport().width() // 2, view.viewport().height() // 2
        _send_mouse(view, QEvent.MouseButtonPress, cx, cy, Qt.LeftButton, Qt.LeftButton)
        _send_mouse(view, QEvent.MouseMove, cx + 18, cy + 12, Qt.NoButton, Qt.LeftButton)
        _send_mouse(view, QEvent.MouseButtonRelease, cx + 36, cy + 24,
                    Qt.LeftButton, Qt.NoButton)

        assert len(note.strokes) == 1
        assert changes                            # view.changed → contentChanged
        assert view.scene.item_for(note.strokes[0]) is not None
        assert view.document.strokes is note.strokes

        card.undo_button.click()
        assert note.strokes == []
        card.redo_button.click()
        assert len(note.strokes) == 1
        card.clear_button.click()
        assert note.strokes == []
    finally:
        _ALIVE.remove(card)
        card.close()


def test_delete_button_asks_before_emitting(qapp, monkeypatch):
    card = _show_card()
    try:
        got: list[int] = []
        card.deleteRequested.connect(lambda: got.append(1))
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.No))
        card.delete_button.click()
        assert got == []

        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.Yes))
        card.delete_button.click()
        assert got == [1]
    finally:
        _ALIVE.remove(card)
        card.close()


# --- 标题：自定义名 / 双击重命名 ---


def test_card_title_uses_display_name_and_updates(qapp):
    note = Note("n-title", 10, 20)
    card = _show_card(note, display_name="函数笔记")
    try:
        assert card.title_label.text() == "函数笔记"
        assert card.display_name == "函数笔记"
        assert note.id in card.title_label.toolTip()
        assert "双击重命名" in card.title_label.toolTip()
        assert card.title_label.cursor().shape() == Qt.PointingHandCursor

        card.set_display_name("新名字")             # 即时生效，不用重建卡片
        assert card.title_label.text() == "新名字"
        assert card.display_name == "新名字"
        assert "新名字" in card.title_label.toolTip()
        assert note.id in card.title_label.toolTip()

        card.set_display_name("   ")                # 只有空白的名字等同空名
        assert card.title_label.text() == "笔记"
        # 名字只落在控件上：写回 Note.name、落盘、图钉标签都归接线层
        assert getattr(note, "name", "") == ""
    finally:
        _drop(card)


def test_card_title_falls_back_to_note_name(qapp):
    note = _note_with_name(name="函数笔记")
    card = _show_card(note)
    try:
        assert card.title_label.text() == "函数笔记"    # 没显式名就用 note.name
        card.set_display_name("")
        assert card.title_label.text() == "函数笔记"    # 空名回退 note.name
        card.set_display_name("显式标题")
        assert card.title_label.text() == "显式标题"    # 显式名优先级最高
        assert note.name == "函数笔记"                  # 卡片不写模型
    finally:
        _drop(card)


def test_double_click_title_requests_rename_once(qapp):
    card = _show_card()
    try:
        hits: list[int] = []
        card.renameRequested.connect(lambda: hits.append(1))
        QTest.mouseClick(card.title_label, Qt.LeftButton)
        assert hits == []                          # 单击不触发改名
        QTest.mouseDClick(card.title_label, Qt.LeftButton)
        assert hits == [1]
        QTest.mouseDClick(card.title_label, Qt.LeftButton)
        assert hits == [1, 1]                      # 每次双击一个意图，卡片自己不弹框
    finally:
        _drop(card)


def test_set_resize_enabled_toggles_grip(qapp):
    card = _show_card()
    try:
        corner = _grip_corner(card)
        assert card.resize_enabled() is True
        assert card.grip.isVisible() is True
        assert card.sizeHitTest(corner) is True

        card.set_resize_enabled(False)              # 独立窗口：系统边框负责缩放
        QTest.qWait(20)
        assert card.resize_enabled() is False
        assert card.grip.isVisible() is False
        assert card.grip.isEnabled() is False
        assert card.sizeHitTest(corner) is False    # 热区不再吃事件
        assert card.sizeHitTest(card._grip_rect().topLeft()) is False

        card.set_resize_enabled(True)               # 遮罩里恢复：可见可命中
        QTest.qWait(20)
        assert card.resize_enabled() is True
        assert card.grip.isVisible() is True
        assert card.grip.isEnabled() is True
        assert card.sizeHitTest(corner) is True
    finally:
        _drop(card)


# --- 独立笔记窗口（窗口外自由缩放） ---


def test_note_window_is_native_standalone_with_filling_card(qapp):
    window = _show_window(Note("n-win", 10, 20), display_name="数学")
    try:
        assert window.isWindow() is True
        assert "数学" in window.windowTitle()
        assert window.card is not None
        assert window.card.parent() is window
        title_h = window.titlebar.height()
        # 自绘标题栏占顶部 28px，卡片在它下面铺满其余区域
        assert title_h == 28
        assert window.card.geometry() == QRect(0, title_h, window.width(),
                                               window.height() - title_h)
        assert window.card.size() == window.size() - QSize(0, title_h)
        # 窗口边框才是缩放入口：卡片手柄收起来，热区也不再命中
        assert window.card.grip.isVisible() is False
        assert window.card.sizeHitTest(_grip_corner(window.card)) is False
        # 2026-10-24 用户要求能缩得更小：窗口下限不再钳到卡片的最小可用尺寸
        assert window.minimumSize() == QSize(260, 300)
        assert window.card.minimumSize() == QSize(0, 0)

        before = window.card.size()
        window.resize(700, 900)
        QTest.qWait(30)
        assert window.size() == QSize(700, 900)
        assert window.card.size() == window.size() - QSize(0, title_h)
        assert window.card.size() != before
        assert (window.note.w, window.note.h) == (560.0, 720.0)   # 设计版面不受影响
    finally:
        _drop(window)


def test_note_window_forwards_signals_and_closes_once(qapp):
    window = _show_window(display_name="英语")
    try:
        changes: list[int] = []
        renames: list[int] = []
        closed: list[int] = []
        window.contentChanged.connect(lambda: changes.append(1))
        window.renameRequested.connect(lambda: renames.append(1))
        window.closed.connect(lambda: closed.append(1))

        window.card.eraser_button.click()               # view.changed → contentChanged
        assert changes == [1]

        QTest.mouseDClick(window.card.title_label, Qt.LeftButton)
        assert renames == [1]                           # 卡片双击 → 窗口改名意图

        QTest.keyClick(window, Qt.Key_Escape)           # Esc 不关窗（避免误关丢上下文）
        QTest.qWait(20)
        assert window.isVisible()
        assert closed == []

        window.close()
        assert closed == [1]
        window.close()                                  # 重复关闭不再发 closed
        assert closed == [1]
    finally:
        if window in _ALIVE:
            _ALIVE.remove(window)
        window.close()


def test_note_window_set_display_name_syncs_title_and_card(qapp):
    window = _show_window(display_name="数学")
    try:
        window.set_display_name("物理")
        assert window.windowTitle() == "笔记 · 物理"
        assert window.card.title_label.text() == "物理"

        window.set_display_name("")
        assert window.card.title_label.text() == "笔记"
        assert "笔记" in window.windowTitle()
    finally:
        _drop(window)


# --- 图标 ---


def test_new_note_icons_exist_and_are_distinct(qapp):
    assert not icons.note_icon().isNull()
    assert not icons.delete_icon().isNull()
    dark = _icon_bytes(icons.paper_icon(True))
    light = _icon_bytes(icons.paper_icon(False))
    assert dark != light
    assert _icon_bytes(icons.delete_icon()) != _icon_bytes(icons.clear_icon())
    assert _icon_bytes(icons.note_icon()) != _icon_bytes(icons.delete_icon())
