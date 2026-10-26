"""草稿纸弹窗与托盘（离屏）。设计文档 §15。

数据一律落 tmp_path；全局热键的真实激活需要真桌面，不在离屏用例里断言，
只验「注册失败 → 状态栏 + 回退快捷键」与「activated → 切换」两条接线。
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialog, QMessageBox,
                               QSystemTrayIcon, QWidget)

from sketchpad.ui import frameless
from sketchpad.services.hotkey import GlobalHotkey
from sketchpad.core.commands import AddStroke
from sketchpad.ui.scratch import (SCRATCH_HEIGHT, SCRATCH_WIDTH,
                                  ScratchController, TrayIcon, clamp_to_area)
from sketchpad.core.settings import Settings, load as load_settings
from sketchpad.ui.settings_dialog import SettingsDialog
from sketchpad.core.strokes import Document, Stroke
from sketchpad.ui.toolbox import PALETTE
from sketchpad.ui.titlebar import TITLE_BAR_HEIGHT
from main import AppController, MainWindow

_ALIVE: list = []          # 窗口/控制器必须留 Python 引用，否则被 GC（PROGRESS §5）


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def make_window(qapp, tmp_path: Path) -> tuple:
    ctrl = AppController(tmp_path)
    win = MainWindow(ctrl)
    win.show()
    QTest.qWaitForWindowExposed(win)
    _ALIVE.append(win)
    return ctrl, win


# --- 草稿窗口本体 ---

def test_toggle_keeps_strokes_and_uses_own_document(qapp):
    main_document = Document()
    scratch = ScratchController(settings=Settings(), main_document=main_document)
    _ALIVE.append(scratch)
    assert scratch.is_visible() is False

    scratch.toggle()
    assert scratch.is_visible() is True
    assert scratch.window.windowTitle() == "草稿纸"
    assert bool(scratch.window.windowFlags() & Qt.WindowStaysOnTopHint) is True
    assert (scratch.window.width(), scratch.window.height()) == (
        SCRATCH_WIDTH, SCRATCH_HEIGHT)
    assert scratch.view.document is not main_document
    assert scratch.window.document is scratch.view.document

    scratch.view.document.strokes.append(Stroke([(1.0, 1.0)], "#1a1a1a", 3.0))
    scratch.toggle()
    assert scratch.is_visible() is False
    scratch.toggle()
    assert scratch.is_visible() is True
    assert len(scratch.view.scene.document.strokes) == 1       # 收起不清空


class _FakeMinimizedWindow:
    """只实现 ScratchController.show/is_visible 用到的最小窗口面。"""
    def __init__(self) -> None:
        self.visible = True
        self.minimized = True
        self.normal_calls = 0

    def isVisible(self) -> bool:        # noqa: N802 - 模拟 Qt 接口
        return self.visible

    def isMinimized(self) -> bool:      # noqa: N802
        return self.minimized

    def show(self) -> None:
        self.visible = True

    def showNormal(self) -> None:       # noqa: N802
        self.normal_calls += 1
        self.minimized = False
        self.visible = True

    def raise_(self) -> None:
        pass

    def activateWindow(self) -> None:   # noqa: N802
        pass

    def hide(self) -> None:
        self.visible = False


def test_minimized_scratch_restores_on_toggle(qapp):
    """点 − 最小化后热键应还原窗口，而不是把它 hide 掉/永远停在最小化。

    离屏平台在特定测试顺序下 showMinimized 真实顶层窗口会卡住，这里用假窗口
    直接钉住 controller 的判断逻辑（is_visible 排除最小化 + showNormal 还原）。
    """
    scratch = ScratchController(settings=Settings())
    fake = _FakeMinimizedWindow()
    scratch._window = fake
    scratch._placed = True

    assert scratch.is_visible() is False    # 最小化对用户来说就是看不见
    scratch.toggle()
    assert fake.normal_calls == 1
    assert fake.minimized is False
    assert scratch.is_visible() is True

    scratch.toggle()
    assert scratch.is_visible() is False


def test_scratch_writes_no_file(qapp, tmp_path):
    ctrl, win = make_window(qapp, tmp_path)
    win.scratch.toggle()
    view = win.scratch.view
    view.document.strokes.append(Stroke([(0.0, 0.0), (5.0, 5.0)], "#1a1a1a", 3.0))
    view.clear_all()                                # 清屏也不落盘
    QTest.qWait(300)

    assert win.scratch.view.document is not ctrl.document
    assert list(tmp_path.iterdir()) == []          # 草稿不落盘，也没有"保存"按钮


def test_escape_hides_scratch(qapp):
    scratch = ScratchController(settings=Settings())
    _ALIVE.append(scratch)
    scratch.toggle()
    scratch.window.activateWindow()
    scratch.view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(scratch.view, Qt.Key_Escape)
    assert scratch.is_visible() is False


def test_close_of_scratch_window_only_hides(qapp):
    scratch = ScratchController(settings=Settings())
    _ALIVE.append(scratch)
    scratch.toggle()
    scratch.window.close()
    assert scratch.is_visible() is False
    scratch.toggle()
    assert scratch.is_visible() is True


# --- 草稿窗的无边框外壳（与主窗同一套）---

def test_scratch_is_frameless_with_titlebar_gear_and_no_pin(qapp):
    scratch = ScratchController(settings=Settings())
    _ALIVE.append(scratch)
    scratch.toggle()
    QTest.qWait(30)
    win = scratch.window

    assert bool(win.windowFlags() & Qt.FramelessWindowHint) is True
    assert bool(win.windowFlags() & Qt.WindowStaysOnTopHint) is True
    assert win.titlebar.isVisible() is True
    assert win.titlebar.title_label.text() == "草稿纸"
    assert win.titlebar.height() == TITLE_BAR_HEIGHT
    assert win.toolbox.height() >= 28        # 28px 热区 + 上下留白，不钉死具体像素
    # 设置齿轮已从标题栏移到工具栏（2026-10-23）
    assert win.titlebar.settings_button.isVisible() is False
    assert win.toolbox.settings_button.isVisible() is True
    assert win.titlebar.pin_button.isVisible() is False      # 草稿纸常驻置顶，不给关
    assert isinstance(win._edge_resize_filter, frameless.EdgeResizeFilter)

    got = []
    scratch.settingsRequested.connect(lambda: got.append(1))
    win.toolbox.settings_button.click()
    assert got == [1]


def test_gear_in_scratch_applies_settings_to_both_views(qapp, tmp_path, monkeypatch):
    ctrl, win = make_window(qapp, tmp_path)
    win.scratch.toggle()
    scratch = win.scratch.window
    prepared = Settings(clear_key="F9", color="#2980b9", width_index=2, width=9.5)
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(SettingsDialog, "values", lambda self: prepared)

    scratch.toolbox.settings_button.click()

    # 草稿视图与主视图都吃同一份新配置
    assert win.scratch.view.color == "#2980b9"
    assert win.scratch.view.width == 9.5
    assert win.scratch.view.clear_key == "F9"
    assert win.view.color == "#2980b9"
    assert win.view.width == 9.5
    assert win.view.clear_key == "F9"
    # 两条工具栏的高亮一起跟上
    expected_colors = [color == "#2980b9" for color in PALETTE]
    assert [b.isChecked() for b in scratch.toolbox.color_buttons] == expected_colors
    assert [b.isChecked() for b in win.toolbox.color_buttons] == expected_colors
    assert scratch.toolbox.size_slider.value() == 95
    assert win.toolbox.size_slider.value() == 95
    # 配置对象就地更新并落盘
    assert win.controller.settings == prepared
    assert win.settings_dialog.parent() is scratch            # 对话框挂在请求者上
    saved = json.loads(ctrl.settings_path.read_text(encoding="utf-8"))
    assert saved == {"clear_key": "F9", "color": "#2980b9", "width_index": 2,
                     "scratch_hotkey": "Alt+Q", "width": 9.5,
                     "pdf_autosave": True, "recent_files": [],
                     "toolbar_position": "top", "toolbar_collapsed": False,
                     "main_hotkey": "Alt+W", "page_mode": "normal", "ink_smoothing": "low",
                     "highlighter_color": "#d4a017",
                     "pressure_enabled": True, "note_paper": "dark",
                     "close_to_tray": True, "close_prompt": True,
                     "open_export_folder": False}
    # 新建草稿沿用新配置，不被旧值冲回去
    win.scratch.new_draft()
    assert win.scratch.view.color == "#2980b9"
    assert win.scratch.view.width == 9.5
    assert win.scratch.view.clear_key == "F9"


def test_scratch_titlebar_close_and_escape_hide_without_quitting(qapp, monkeypatch):
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    scratch = ScratchController(settings=Settings())
    _ALIVE.append(scratch)
    scratch.toggle()

    scratch.window.titlebar.close_button.click()
    assert scratch.is_visible() is False
    assert quits == []

    scratch.toggle()
    assert scratch.is_visible() is True
    scratch.window.activateWindow()
    scratch.view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(scratch.view, Qt.Key_Escape)
    assert scratch.is_visible() is False
    assert quits == []


# --- 草稿窗工具栏 → 草稿视图（自己接自己的线）---

def _drag(view, points: list[tuple[int, int]]) -> None:
    """合成一次按住拖动：QTest.mouseMove 不带按键位，必须自己发 QMouseEvent。"""
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=QPoint(*points[0]))
    for x, y in points[1:-1]:
        local = QPointF(x, y)
        global_pos = QPointF(view.viewport().mapToGlobal(QPoint(x, y)))
        ev = QMouseEvent(QEvent.MouseMove, local, global_pos,
                         Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(view.viewport(), ev)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=QPoint(*points[-1]))


def test_scratch_undo_redo_buttons_drive_scratch_history(qapp, tmp_path):
    """撤销/重做按钮改的是草稿文档的栈：主稿笔数一条不变。"""
    ctrl, win = make_window(qapp, tmp_path)
    ctrl.document.strokes.append(Stroke([(0.0, 0.0), (5.0, 5.0)], "#1a1a1a", 3.0))
    win.scratch.toggle()
    view = win.scratch.view
    _drag(view, [(40, 40), (80, 60), (120, 90)])

    assert len(view.document.strokes) == 1
    assert view.history.depth == 1

    QTest.mouseClick(win.scratch.window.toolbox.undo_button, Qt.LeftButton)
    assert len(view.document.strokes) == 0
    assert view.history.depth == 0

    QTest.mouseClick(win.scratch.window.toolbox.redo_button, Qt.LeftButton)
    assert len(view.document.strokes) == 1
    assert view.history.depth == 1
    assert len(ctrl.document.strokes) == 1


def test_scratch_clear_key_follows_main_setting(qapp, tmp_path):
    """主窗改清屏键后，草稿窗下次显示时换上新键；草稿窗自己不发回配置。"""
    ctrl = AppController(tmp_path)
    ctrl.settings.clear_key = "Delete"
    win = MainWindow(ctrl)
    win.show()
    QTest.qWaitForWindowExposed(win)
    _ALIVE.append(win)

    win.scratch.toggle()
    view = win.scratch.view
    assert view.clear_key == "Delete"
    view.document.strokes.append(Stroke([(1.0, 1.0)], "#1a1a1a", 3.0))
    view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(view, Qt.Key_Delete)
    assert view.document.strokes == []

    ctrl.settings.clear_key = "F9"
    win.scratch.toggle()                       # 收起
    win.scratch.toggle()                       # 再弹：这时才同步
    assert view.clear_key == "F9"
    # 收起草稿窗时离屏平台会把活动窗口清空：先让主窗回到活动态，再激活草稿窗
    win.activateWindow()
    QTest.qWait(20)
    win.scratch.window.activateWindow()
    QTest.qWait(20)
    view.setFocus()
    QTest.qWait(20)
    view.document.strokes.append(Stroke([(2.0, 2.0)], "#1a1a1a", 3.0))
    QTest.keyClick(view, Qt.Key_F9)
    assert view.document.strokes == []


# --- 按键：撤销/重做、反射、捕获冲突 ---

def _push_stroke(view) -> None:
    """走真实命令入栈：直接 append 文档的话撤销栈里没有东西可撤。"""
    stroke = Stroke([(0.0, 0.0), (5.0, 5.0)], "#1a1a1a", 3.0)
    cmd = AddStroke(view.document, stroke)
    cmd.apply()
    view.history.push(cmd)
    view.scene.add_stroke(stroke)


def _make_pdf(path: Path) -> Path:
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "PageFlow dock")
    doc.save(path)
    doc.close()
    return path


def test_scratch_docks_into_document_right_pane_and_returns(qapp, tmp_path):
    """分屏发生在主窗：文档左、草稿右；浮窗收起，不新建标签。"""
    from sketchpad.workspace.note_split import NoteSplitPane
    ctrl, win = make_window(qapp, tmp_path)
    pdf = _make_pdf(tmp_path / "讲义.pdf")
    assert win._enter_document(pdf) is True
    entries_before = len(win.tabs.entries)
    win.scratch.toggle()
    qapp.processEvents()
    window = win.scratch.window
    view = win.scratch.view

    win._dock_scratch()
    qapp.processEvents()

    session = win._active_session
    dock = win._scratch_dock
    try:
        assert dock is not None
        assert window.isVisible() is False          # 浮窗收起，内容搬进右栏
        assert window.is_docked() is True
        assert dock.view is view
        assert dock.view_host.isAncestorOf(view) is True
        assert len(win.tabs.entries) == entries_before
        assert isinstance(session.pane, NoteSplitPane)
        assert session.pane.is_note_open is True
        assert session.pane.note_layout.indexOf(dock) >= 0
        assert session.pane.splitter.widget(0) is session.pane.pdf_frame
        assert session.pane.splitter.widget(1) is session.pane.note_frame
        sizes = session.pane.splitter.sizes()
        assert sizes[0] > 0 and sizes[1] > 0

        old_view = view
        win._new_scratch_draft()                    # 停靠时「新建草稿」只换右栏画布
        qapp.processEvents()
        assert win.scratch.view is not old_view
        assert win._scratch_dock is dock
        assert dock.view is win.scratch.view

        assert win._undock_scratch(show_window=True) is True
        qapp.processEvents()
        assert win._scratch_dock is None
        assert window.isVisible() is True
        assert window.is_docked() is False
        assert win.scratch.view.parent() is window
        assert session.pane.is_note_open is False
    finally:
        win._undock_scratch(show_window=False)


def test_alt_q_while_docked_brings_main_forward_not_empty_float(qapp, tmp_path):
    ctrl, win = make_window(qapp, tmp_path)
    assert win._enter_document(_make_pdf(tmp_path / "讲义.pdf")) is True
    win.scratch.toggle()
    qapp.processEvents()
    window = win.scratch.window
    win._dock_scratch()
    qapp.processEvents()
    try:
        win.hide()
        qapp.processEvents()
        assert window.isVisible() is False
        win._toggle_scratch_window()                # 等价于按 Alt+Q
        qapp.processEvents()
        assert win.isVisible() is True
        assert window.isVisible() is False           # 不弹空的浮窗
        assert win._scratch_dock is not None
    finally:
        win._undock_scratch(show_window=False)


def test_docked_scratch_follows_its_document_tab(qapp, tmp_path):
    """停靠属于某个文档标签：切走时随面板隐藏，Alt+Q 切回该标签。"""
    ctrl, win = make_window(qapp, tmp_path)
    first = _make_pdf(tmp_path / "一.pdf")
    second = _make_pdf(tmp_path / "二.pdf")
    assert win._enter_document(first) is True
    assert win._enter_document(second) is True
    entry_first = next(e for e in win.tabs.entries
                       if e.sessions and not win._is_note_session(e.sessions[0])
                       and e.sessions[0].path.name == "一.pdf")
    entry_second = next(e for e in win.tabs.entries
                        if e.sessions and not win._is_note_session(e.sessions[0])
                        and e.sessions[0].path.name == "二.pdf")
    win.tabs.set_current_entry(entry_first)
    win.scratch.toggle()
    qapp.processEvents()
    win._dock_scratch()
    qapp.processEvents()
    try:
        dock_session = win._scratch_dock_session
        win.tabs.set_current_entry(entry_second)
        qapp.processEvents()
        assert win._scratch_dock is not None
        assert win._active_session is not dock_session
        assert win._scratch_dock.isVisible() is False

        win._toggle_scratch_window()
        qapp.processEvents()
        assert win.tabs.current_entry is entry_first
        assert win._scratch_dock.isVisible() is True
    finally:
        win._undock_scratch(show_window=False)


def test_dock_on_note_tab_explains_without_docking(qapp, tmp_path, monkeypatch):
    ctrl, win = make_window(qapp, tmp_path)
    win._activate_draft()
    calls = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *args, **kwargs: calls.append(args[2]) or QMessageBox.Ok))
    win.scratch.toggle()
    qapp.processEvents()
    win._dock_scratch()
    assert win._scratch_dock is None
    assert win.scratch.window.isVisible() is True
    assert calls and "文档" in calls[0]


def test_dock_works_on_text_reader(qapp, tmp_path):
    """文本/导图查看器没有 auto_fit 属性，停靠也必须能开合。"""
    from sketchpad.reader import install as install_readers
    from sketchpad.workspace.viewers import clear_viewers
    install_readers()
    try:
        ctrl, win = make_window(qapp, tmp_path)
        txt = tmp_path / "大纲.txt"
        txt.write_text("第一行\n第二行\n", encoding="utf-8")
        assert win._enter_document(txt) is True
        win.scratch.toggle()
        qapp.processEvents()
        win._dock_scratch()
        qapp.processEvents()
        assert win._scratch_dock is not None
        assert win._scratch_dock_pane.is_note_open is True
        assert win._undock_scratch(show_window=False) is True
        assert win._scratch_dock is None
    finally:
        clear_viewers()


def test_docked_scratch_keys_stay_with_scratch(qapp, tmp_path):
    """停靠后键盘仍改草稿（B/Q/数字/Ctrl+Z），不能落到左侧文档上。"""
    ctrl, win = make_window(qapp, tmp_path)
    assert win._enter_document(_make_pdf(tmp_path / "讲义.pdf")) is True
    pdf_tool = win.pdf_view.tool
    win.scratch.toggle()
    qapp.processEvents()
    win._dock_scratch()
    qapp.processEvents()
    try:
        view = win.scratch.view
        win.activateWindow()
        view.setFocus()
        QTest.qWait(20)
        QTest.keyClick(view, Qt.Key_Q)
        assert view.tool == "eraser"
        QTest.keyClick(view, Qt.Key_2)
        assert view.color == PALETTE[1]
        assert win.pdf_view.tool == pdf_tool

        _push_stroke(view)
        assert len(view.document.strokes) == 1
        QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
        assert len(view.document.strokes) == 0
    finally:
        win._undock_scratch(show_window=False)


def test_closing_docked_document_returns_scratch_to_window(qapp, tmp_path):
    ctrl, win = make_window(qapp, tmp_path)
    assert win._enter_document(_make_pdf(tmp_path / "讲义.pdf")) is True
    win.scratch.toggle()
    qapp.processEvents()
    window = win.scratch.window
    view = window.view
    win._dock_scratch()
    qapp.processEvents()
    entry = win._entry_for_session(win._scratch_dock_session)
    win._close_entry(entry)
    qapp.processEvents()
    assert win._scratch_dock is None
    assert window.is_docked() is False
    assert window.view is view
    assert view.parent() is window
    assert window.isVisible() is False            # 关文档时浮窗不自动弹出


def test_scratch_ctrl_z_and_y_drive_history_and_survive_new_draft(qapp):
    scratch = ScratchController(settings=Settings())
    _ALIVE.append(scratch)
    scratch.toggle()
    view = scratch.view
    _push_stroke(view)
    assert len(view.document.strokes) == 1

    scratch.window.activateWindow()
    view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert len(view.document.strokes) == 0
    QTest.keyClick(view, Qt.Key_Y, Qt.ControlModifier)
    assert len(view.document.strokes) == 1

    scratch.new_draft()                        # 换文档后快捷键还要活着
    view = scratch.view
    assert len(view.document.strokes) == 0
    _push_stroke(view)
    scratch.window.activateWindow()
    view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert len(view.document.strokes) == 0


def test_scratch_keyboard_tool_and_width_reflect_in_toolbox(qapp):
    """草稿窗只有工具栏没有状态栏：键盘改的工具/粗细要回写高亮。"""
    scratch = ScratchController(settings=Settings())
    _ALIVE.append(scratch)
    scratch.toggle()
    scratch.window.activateWindow()
    scratch.view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(scratch.view, Qt.Key_Q)
    assert scratch.window.toolbox.eraser_button.isChecked() is True
    QTest.keyClick(scratch.view, Qt.Key_2)
    assert scratch.view.color == PALETTE[1]          # 2 = 蓝
    assert scratch.window.toolbox.color_buttons[1].isChecked() is True


def test_scratch_trash_button_clears_scratch_document_only(qapp, tmp_path):
    """草稿窗的垃圾桶只清草稿文档，主稿笔迹一条不动。"""
    ctrl, win = make_window(qapp, tmp_path)
    ctrl.document.strokes.append(Stroke([(3.0, 4.0)], "#27ae60", 6.0))
    win.scratch.toggle()
    view = win.scratch.view
    _push_stroke(view)
    assert len(view.document.strokes) == 1

    win.scratch.window.toolbox.clear_button.click()
    assert view.document.strokes == []
    assert len(ctrl.document.strokes) == 1
    assert win.scratch.is_visible() is True

    view.history.pop_undo()
    assert len(view.document.strokes) == 1


# --- 位置 ---

def test_clamp_to_area_concrete_values():
    area = QRect(0, 0, 1920, 1040)
    assert clamp_to_area(area, 900, 600, 960 - 450, 520 - 300) == (510, 220)
    assert clamp_to_area(area, 900, 600, -50, -70) == (0, 0)
    assert clamp_to_area(area, 900, 600, 5000, 5000) == (1020, 440)
    small = QRect(0, 0, 800, 800)
    assert clamp_to_area(small, 900, 600, -50, 100) == (0, 100)


def test_show_near_cursor_puts_window_where_clamp_says(qapp):
    scratch = ScratchController(settings=Settings())
    _ALIVE.append(scratch)
    scratch.show_near_cursor()
    assert scratch.is_visible() is True

    area = QGuiApplication.primaryScreen().availableGeometry()
    w, h = scratch.window.width(), scratch.window.height()
    expected = clamp_to_area(area, w, h,
                             area.center().x() - w // 2, area.center().y() - h // 2)
    assert (scratch.window.x(), scratch.window.y()) == expected


# --- 主窗关闭 = 隐藏 ---

def test_main_close_hides_instead_of_quitting(qapp, tmp_path, monkeypatch):
    ctrl, win = make_window(qapp, tmp_path)
    ctrl.settings.close_prompt = False       # 这条测收托盘，不走确认框
    ctrl.document.strokes.append(Stroke([(3.0, 4.0)], "#27ae60", 6.0))
    # 关窗只隐藏的前提是有退路：热键在（没托盘 + 没热键的用例见 test_window_offscreen）
    def register_ok(self, hwnd, sequence):
        self._registered = True          # 真实现也会置这个位，closeEvent 靠它判断
        return True

    monkeypatch.setattr(GlobalHotkey, "register", register_ok)
    assert win.start_hotkey() is True
    assert win.hotkey.is_registered is True

    assert win.close() is False                     # closeEvent ignore → close() 返回 False
    assert win.isVisible() is False
    assert (tmp_path / "settings.json").exists() is True   # 关窗只存配置，不存草稿
    assert (tmp_path / "draft.json").exists() is False


# --- 托盘 ---

def test_tray_menu_has_three_actions_and_quit_text(qapp):
    tray = TrayIcon()
    _ALIVE.append(tray)
    assert [action.text() for action in tray.menu.actions()] == [
        "显示主窗", "新建草稿", "退出全部实例"]
    assert tray.quit_action.text() == "退出全部实例"
    if not QSystemTrayIcon.isSystemTrayAvailable():
        # 离屏平台没有托盘：构造出来也不能显示，下面另有降级用例
        assert tray.isVisible() is False


def test_tray_unavailable_degrades_without_crash(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    ctrl, win = make_window(qapp, tmp_path)
    assert win.start_tray() is False
    assert win.tray is None
    assert "托盘" in win.message_label.text()
    assert win.isVisible() is True                  # 进程不退出


def test_tray_available_builds_and_shows_menu(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    ctrl, win = make_window(qapp, tmp_path)
    assert win.start_tray() is True
    assert [action.text() for action in win.tray.menu.actions()] == [
        "显示主窗", "新建草稿", "退出全部实例"]

    win.hide()
    win.tray.show_main_action.trigger()
    assert win.isVisible() is True


def test_new_draft_swaps_fresh_document_and_keeps_main_untouched(qapp, tmp_path):
    ctrl, win = make_window(qapp, tmp_path)
    win.scratch.toggle()
    old_view = win.scratch.view
    old_view.document.strokes.append(Stroke([(9.0, 9.0)], "#2980b9", 6.0))
    ctrl.document.strokes.append(Stroke([(1.0, 2.0)], "#c0392b", 3.0))

    win.scratch.new_draft()

    assert win.scratch.view is not old_view
    assert win.scratch.view.document.strokes == []
    assert win.scratch.view.scene.document is win.scratch.view.document
    assert win.scratch.window.document is win.scratch.view.document
    assert ctrl.document.strokes == [Stroke([(1.0, 2.0)], "#c0392b", 3.0)]
    assert win.scratch.is_visible() is True


# --- 全局热键接线 ---

def test_hotkey_failure_reports_and_installs_fallback(qapp, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(GlobalHotkey, "register",
                        lambda self, hwnd, sequence: calls.append(sequence) or False)
    ctrl, win = make_window(qapp, tmp_path)
    assert win.start_hotkey() is False

    assert calls == ["Alt+Q"]
    assert "热键" in win.message_label.text()
    shortcut = win.fallback_shortcut
    assert shortcut is not None
    assert shortcut.key().toString() == "Alt+Q"
    assert shortcut.context() == Qt.WidgetWithChildrenShortcut

    win.activateWindow()
    win.view.setFocus()
    QTest.qWait(20)
    QTest.keyClick(win.view, Qt.Key_Q, Qt.AltModifier)
    assert win.scratch.is_visible() is True


def test_hotkey_activation_toggles_scratch(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(GlobalHotkey, "register", lambda self, hwnd, sequence: True)
    ctrl, win = make_window(qapp, tmp_path)
    assert win.start_hotkey() is True
    assert win.fallback_shortcut is None

    win.hotkey.activated.emit()
    assert win.scratch.is_visible() is True
    win.hotkey.activated.emit()
    assert win.scratch.is_visible() is False


def test_quit_flushes_settings_unregisters_and_quits(qapp, tmp_path, monkeypatch):
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    ctrl, win = make_window(qapp, tmp_path)
    unregistered = []
    monkeypatch.setattr(win.hotkey, "unregister", lambda: unregistered.append(1))
    ctrl.document.strokes.append(Stroke([(3.0, 4.0)], "#27ae60", 6.0))

    win.quit_app()

    assert (tmp_path / "settings.json").exists() is True
    assert load_settings(ctrl.settings_path).clear_key == "E"
    assert (tmp_path / "draft.json").exists() is False
    assert unregistered == [1]
    assert quits == [1]
