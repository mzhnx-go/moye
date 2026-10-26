"""主窗口离屏测试：最小尺寸、工具栏横向滚动、置顶开关、状态栏不含保存。

草稿不落盘（设计文档 §8）——这里不测任何文件，只测窗口行为。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pymupdf
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

import main
from sketchpad.ui.toolbox import PALETTE
from sketchpad.core.settings import WIDTHS
from sketchpad.ui import frameless, icons
from sketchpad.services.hotkey import GlobalHotkey
from sketchpad.ui.toolbox import Toolbox

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path)
    ctrl.settings.close_prompt = False      # 关窗用例测收托盘逻辑，不走确认框
    w = main.MainWindow(ctrl)
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w._quitting = True
    w.close()


def _register_ok(self, hwnd, sequence):
    """模拟注册成功：真实现会置 _registered，closeEvent 靠它判断有没有退路。"""
    self._registered = True
    return True


def test_minimum_size_is_small_and_enforced(win):
    assert (win.minimumWidth(), win.minimumHeight()) == (main.MIN_W, main.MIN_H)
    win.resize(100, 100)
    QTest.qWait(20)
    assert (win.width(), win.height()) == (main.MIN_W, main.MIN_H)


def test_toolbar_scrolls_when_window_is_narrow(win):
    assert isinstance(win.toolbar_scroll.widget(), Toolbox)
    win.resize(main.MIN_W, 300)
    QTest.qWait(30)
    assert win.toolbar_scroll.horizontalScrollBar().maximum() > 0
    # 高度必须比工具栏本身高，否则滚动条被裁掉、窄窗时按钮点不到
    assert win.toolbar_scroll.height() > win.toolbox.sizeHint().height()


def test_toolbar_collapse_leaves_only_the_left_arrow(win):
    """收起后工具栏只剩最左边的箭头窄条，再点恢复原高度。"""
    QTest.qWait(20)
    expanded = win.toolbar_scroll.height()
    win.toolbox.collapse_button.click()
    QTest.qWait(30)
    assert win.toolbox.collapsed is True
    assert win.toolbar_scroll.height() < expanded
    assert win.toolbar_scroll.height() <= 26
    assert win.toolbox.collapse_button.isVisible() is True
    assert win.toolbox.collapse_button.x() < 40       # 仍在最左边

    win.toolbox.collapse_button.click()
    QTest.qWait(30)
    assert win.toolbox.collapsed is False
    assert win.toolbar_scroll.height() >= expanded - 1


# ---------- v2.9：工具栏右排（右侧竖栏） ----------

def test_toolbar_position_right_moves_scroll_to_body_right_edge(win):
    win._apply_toolbar_position("right")
    QTest.qWait(40)

    assert win.toolbox.vertical is True
    assert win.column.indexOf(win.toolbar_scroll) == -1       # 不在顶栏列里了
    assert win.top_chrome.has_row(win.toolbar_scroll) is False
    assert win.body.indexOf(win.toolbar_scroll) == win.body.count() - 1
    assert win.body.indexOf(win.stack) < win.body.indexOf(win.toolbar_scroll)
    assert win.toolbar_scroll.width() < 90                    # 竖排窄栏
    assert win.toolbar_scroll.height() > win.toolbar_scroll.width()
    # 工具栏自身也要换成竖排尺寸（滚动区不会替它改尺寸，必须由 app 套一次）
    assert win.toolbox.width() < 90
    assert win.toolbox.height() > win.toolbox.width()


def test_vertical_toolbar_collapse_and_expand_shrinks_width(win):
    win._apply_toolbar_position("right")
    QTest.qWait(40)
    expanded = win.toolbar_scroll.width()

    win.toolbox.collapse_button.click()
    QTest.qWait(50)
    assert win.toolbox.collapsed is True
    assert win.toolbar_scroll.width() <= 26
    assert win.toolbox.width() <= 26                          # 工具栏本身也收窄
    assert win.toolbox.collapse_button.isVisible() is True
    assert win.controller.settings.toolbar_collapsed is True    # 收起状态进配置

    win.toolbox.collapse_button.click()
    QTest.qWait(50)
    assert win.toolbox.collapsed is False
    assert win.toolbar_scroll.width() >= expanded - 1
    assert win.controller.settings.toolbar_collapsed is False


def test_switch_back_to_top_restores_column_placement(win):
    win.resize(1160, 760)          # 显式钉宽，避免依赖默认宽度
    QTest.qWait(60)
    win._apply_toolbar_position("right")
    QTest.qWait(40)
    win._apply_toolbar_position("top")
    QTest.qWait(50)

    assert win.toolbox.vertical is False
    assert win.top_chrome.rows.indexOf(win.toolbar_scroll) == 2
    assert win.body.indexOf(win.toolbar_scroll) == -1
    # 横排：高度压回内容高 + 可能出现的横向滚动条，宽度铺满窗口
    assert win.toolbar_scroll.height() > win.toolbox.sizeHint().height()
    assert win.toolbar_scroll.height() <= 40
    assert win.toolbar_scroll.width() > 200


def test_pdf_default_width_has_no_toolbar_horizontal_scroll(win, tmp_path):
    """默认窗口宽 1160：PDF 模式的整条工具栏（1124px）必须放得下。

   1160 宽时视口 1132px（8px 余量）；1100 宽时视口只有 1072px，
    右侧会冒横向滚动条（像一条分割线），最后一个按钮还被裁掉。
    """
    assert win.width() == 1160                     # 默认宽度本身也是验收项
    win.resize(1160, 760)
    QTest.qWait(60)
    pdf = _make_pdf(tmp_path / "toolbar-fit.pdf")
    assert win._enter_document(pdf) is True
    QTest.qWait(100)

    scroll = win.toolbar_scroll
    bar = scroll.horizontalScrollBar()
    assert bar.maximum() == 0
    assert bar.isVisible() is False
    assert scroll.height() <= 40                   # 没有滚动条多出来的那条高
    viewport = scroll.viewport()
    last = win.toolbox.scratch_button              # 设置已移到菜单栏
    assert last.isVisible() is True
    corner = last.mapTo(viewport, QPoint(0, 0))
    assert corner.x() >= 0
    assert corner.x() + last.width() <= viewport.width()


def test_key_routing_survives_the_right_toolbar(win):
    """工具栏换到右侧后，B/Q 仍然落到画布（应用级按键兜底不受布局影响）。"""
    win.activateWindow()
    win._apply_toolbar_position("right")
    QTest.qWait(40)
    win.view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.view, Qt.Key_Q)
    assert win.view.tool == "eraser"
    QTest.keyClick(win.view, Qt.Key_B)
    assert win.view.tool == "pen"


def test_saved_right_position_and_collapse_restore_on_start(qapp, tmp_path):
    from sketchpad.core import settings as store

    store.save(tmp_path / "settings.json",
               store.Settings(toolbar_position="right", toolbar_collapsed=True))
    ctrl = main.AppController(tmp_path)
    w = main.MainWindow(ctrl)
    w.show()
    QTest.qWaitForWindowExposed(w)
    QTest.qWait(50)
    _ALIVE.append(w)
    try:
        assert w.toolbox.vertical is True
        assert w.toolbox.collapsed is True
        assert w.body.indexOf(w.toolbar_scroll) >= 0
        assert w.column.indexOf(w.toolbar_scroll) == -1
        assert w.toolbar_scroll.width() <= 26
    finally:
        w.scratch.hide()
        w._quitting = True
        w.close()


def test_window_is_frameless_with_custom_titlebar(win):
    assert bool(win.windowFlags() & Qt.FramelessWindowHint) is True
    assert win.titlebar.isVisible() is True
    assert win.titlebar.title_label.text() == "墨页"


def test_main_window_resizes_only_through_native_hit_test(win):
    """主窗不装应用级缩放下手器：它按 Qt 逻辑坐标判边，高 DPI 下曾把右/下
    大片区域误判成窗口边缘；边缘缩放统一交给 WM_NCHITTEST。"""
    assert getattr(win, "_edge_resize_filter", None) is None

    plain = QWidget()
    plain.show()
    QTest.qWaitForWindowExposed(plain)
    _ALIVE.append(plain)
    installed = frameless.EdgeResizeFilter.install(plain)
    assert isinstance(installed, frameless.EdgeResizeFilter)
    assert plain._edge_resize_filter is installed
    assert installed._window is plain


def test_native_hit_test_compares_physical_coords(win, monkeypatch):
    """WM_NCHITTEST 的 lParam 是物理屏幕坐标，必须与 GetWindowRect 比；
    以前拿它去 mapFromGlobal（逻辑坐标），右/下大片内部被当成边缘。"""
    def fake_get_window_rect(hwnd, out):
        target = out._obj
        target.left, target.top, target.right, target.bottom = 100, 100, 1100, 700
        return 1

    monkeypatch.setattr(frameless.ctypes.windll.user32, "GetWindowRect",
                        fake_get_window_rect)

    def hit(x: int, y: int):
        msg = frameless.wintypes.MSG()
        msg.message = 0x0084                     # WM_NCHITTEST
        msg.lParam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        return frameless.native_hit_test(
            win, b"windows_generic_MSG", frameless.ctypes.addressof(msg))

    assert hit(600, 400) is None                 # 中间（半宽/半高之后）不是边缘
    assert hit(1098, 400) == 11                  # HTRIGHT
    assert hit(600, 698) == 15                   # HTBOTTOM
    assert hit(102, 400) == 10                   # HTLEFT
    assert hit(600, 102) == 12                   # HTTOP


def test_native_hit_test_ignores_unavailable_window_rect(win, monkeypatch):
    """GetWindowRect 失败时不能拿全 0 矩形判边：那会让整个窗口都算右边。"""
    monkeypatch.setattr(frameless.ctypes.windll.user32, "GetWindowRect",
                        lambda hwnd, out: 0)
    msg = frameless.wintypes.MSG()
    msg.message = 0x0084
    msg.lParam = (400 << 16) | 600
    assert frameless.native_hit_test(
        win, b"windows_generic_MSG", frameless.ctypes.addressof(msg)) is None


def test_titlebar_order_is_pin_minimize_maximize_close(win):
    layout = win.titlebar.layout()
    order = [layout.indexOf(b) for b in (win.titlebar.pin_button, win.titlebar.min_button,
                                         win.titlebar.max_button, win.titlebar.close_button)]
    assert order == sorted(order)          # 置顶在最小化左边


def test_always_on_top_uses_native_call_and_stays_visible(win, monkeypatch):
    calls = []
    monkeypatch.setattr("sketchpad.app.set_native_topmost",
                        lambda _window, on: calls.append(on) or True)
    assert win._always_on_top is False
    win.titlebar.pin_button.click()
    assert calls == [True]
    assert win._always_on_top is True
    assert win.isVisible() is True          # 原生路径不再 hide/show
    win.titlebar.pin_button.click()
    assert calls == [True, False]
    assert win._always_on_top is False
    assert win.isVisible() is True


def test_always_on_top_falls_back_to_qt_flag(win, monkeypatch):
    """非 Windows/无句柄时退回 Qt 标志：会闪一下，但功能不能失效。"""
    monkeypatch.setattr("sketchpad.app.set_native_topmost",
                        lambda _window, _on: False)
    win.titlebar.pin_button.click()
    assert bool(win.windowFlags() & Qt.WindowStaysOnTopHint) is True
    assert win.isVisible() is True


def test_pin_does_not_reset_pdf_zoom(win, tmp_path, monkeypatch):
    """置顶只改 Z 序：适应页宽和手动缩放都不能被重新布局冲掉。"""
    pdf = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    doc.save(str(pdf))
    doc.close()
    assert win._enter_document(pdf) is True
    fit_zoom = win.pdf_view.zoom
    monkeypatch.setattr("sketchpad.app.set_native_topmost",
                        lambda _window, _on: True)

    win.titlebar.pin_button.click()
    assert win.pdf_view.zoom == pytest.approx(fit_zoom)

    win.pdf_view.set_zoom(1.7)
    win.titlebar.pin_button.click()
    assert win.pdf_view.zoom == pytest.approx(1.7)
    assert win.isVisible() is True


def test_set_native_topmost_returns_false_instead_of_raising(win, monkeypatch):
    """Win32 不可用时返回 False，交给 set_always_on_top 回退，不能把异常抛给 UI。"""
    class BrokenWindll:
        @property
        def user32(self):
            raise OSError("win32 unavailable")

    monkeypatch.setattr(frameless.ctypes, "windll", BrokenWindll(), raising=False)
    assert frameless.set_native_topmost(win, True) is False


def test_titlebar_maximize_toggles_and_swaps_icon(win):
    win.showNormal()
    win.titlebar.max_button.click()
    QTest.qWait(30)
    assert win.isMaximized() is True
    assert win.titlebar.max_button.toolTip() == "还原"
    win.titlebar.max_button.click()
    QTest.qWait(30)
    assert win.isMaximized() is False
    assert win.titlebar.max_button.toolTip() == "最大化"


def test_titlebar_minimize_and_close(win, monkeypatch):
    win.titlebar.min_button.click()
    QTest.qWait(20)
    assert win.isMinimized() is True
    win.showNormal()
    # 收进托盘的前提是还有退路（热键在）：否则关窗就是退出，另有专门用例
    monkeypatch.setattr(GlobalHotkey, "register", _register_ok)
    assert win.start_hotkey() is True
    assert win.hotkey.is_registered is True
    win.titlebar.close_button.click()
    assert win.isVisible() is False        # 关闭 = 收进托盘（进程留着）


def test_main_hotkey_restores_main_window(win, monkeypatch):
    monkeypatch.setattr(GlobalHotkey, "register", _register_ok)
    assert win.start_main_hotkey() is True
    assert win.main_hotkey.is_registered is True

    win.hide()
    win.main_hotkey.activated.emit()
    assert win.isVisible() is True
    assert win.isMinimized() is False


def test_screenshot_hotkey_starts_region_capture(win, monkeypatch):
    """全局截图热键按下即走区域截图，不必先呼出主窗。"""
    monkeypatch.setattr(GlobalHotkey, "register", _register_ok)
    modes = []
    monkeypatch.setattr(win, "_start_screenshot",
                        lambda mode="region": modes.append(mode) or True)
    assert win.start_screenshot_hotkey() is True
    assert win.screenshot_hotkey.is_registered is True

    win.hide()
    win.screenshot_hotkey.activated.emit()
    assert modes == ["region"]


def test_main_hotkey_keeps_maximized_window_maximized(win, monkeypatch):
    """最大化后收进托盘，再按主窗热键不能被 showNormal 打回普通尺寸。"""
    monkeypatch.setattr(GlobalHotkey, "register", _register_ok)
    assert win.start_main_hotkey() is True
    win.showMaximized()
    QTest.qWait(30)
    assert win.isMaximized() is True

    win.hide()
    win.main_hotkey.activated.emit()
    assert win.isVisible() is True
    assert win.isMaximized() is True


def test_close_hides_when_only_main_hotkey_is_available(win, monkeypatch):
    """草稿纸热键没注册上，但主窗热键能把它叫回来，就不能直接退出。"""
    monkeypatch.setattr(GlobalHotkey, "register", _register_ok)
    assert win.start_main_hotkey() is True
    assert win.hotkey.is_registered is False

    win.close()
    assert win.isVisible() is False
    assert win._quitting is False


def test_status_bar_has_no_save_field(win):
    win.refresh_status()
    text = win.status_label.text()
    assert "保存" not in text
    assert "笔迹 0" in text


def test_status_message_keeps_only_the_latest_note_and_clears(win):
    """状态栏不再「；」累加：只显示最新一条，并由定时器在 4 秒后清空。"""
    win._note("第一条提示")
    win._note("第二条提示")
    assert win.message_label.text() == "第二条提示"     # 旧的被覆盖，不拼接
    assert win._note_timer.isSingleShot() is True
    assert win._note_timer.interval() == 4000
    assert win._note_timer.isActive() is True           # 每条提示都重启计时

    win._note("出错了", error=True)
    assert win.message_label.text() == "出错了"
    assert "color" in win.message_label.styleSheet()    # 错误色保留

    win._clear_note()                                   # 计时到点的清空槽
    assert win.message_label.text() == ""
    assert win.message_label.styleSheet() == ""


def test_toolbar_background_click_keeps_canvas_focus(win):
    """点工具栏右侧空白会把焦点交给滚动区，之后 B/Q/1-4 全哑。"""
    win.activateWindow()
    win.view.setFocus()
    QTest.qWait(20)
    assert win.view.hasFocus() is True
    # 注：Qt6 里 QWidget.setFocus() 无视 NoFocus 策略（用户点不到这条路），
    # 所以这里钉用户真会走的那条：点空白处。
    assert win.toolbar_scroll.focusPolicy() == Qt.NoFocus
    assert win.toolbar_scroll.viewport().focusPolicy() == Qt.NoFocus

    vp = win.toolbar_scroll.viewport()
    assert win.toolbox.width() < vp.width()          # 右侧确有空白可点
    QTest.mouseClick(vp, Qt.LeftButton, pos=QPoint(vp.width() - 10, vp.height() // 2))
    assert win.view.hasFocus() is True
    assert win.focusWidget() is win.view

    # 焦点留在画布：B/Q/1-4 立刻有效
    QTest.keyClick(win.view, Qt.Key_Q)
    assert win.view.tool == "eraser"


def test_keyboard_tool_and_width_reflect_in_toolbar_and_status(win):
    """键盘改工具/粗细必须回写工具栏高亮与状态栏，而不是等下一条笔迹。"""
    win.activateWindow()
    win.view.setFocus()
    QTest.qWait(20)

    QTest.keyClick(win.view, Qt.Key_Q)
    assert win.toolbox.eraser_button.isChecked() is True
    assert win.toolbox.pen_button.isChecked() is False

    QTest.keyClick(win.view, Qt.Key_3)
    assert win.view.color == PALETTE[2]              # 3 = 黄
    assert [b.isChecked() for b in win.toolbox.color_buttons] == [False, False, True] + [False] * 6
    assert "#f1c40f" in win.status_label.text()

    QTest.keyClick(win.view, Qt.Key_B)
    assert win.toolbox.pen_button.isChecked() is True
    QTest.keyClick(win.view, Qt.Key_1)
    assert win.view.color == PALETTE[0]              # 1 = 红
    assert win.toolbox.color_buttons[0].isChecked() is True


def test_hotkeys_reach_draft_view_when_focus_is_on_a_scrollbar(win):
    """草稿模式同样兜底：焦点在工具栏滚动条上，Q/B/1-4/0 仍落到草稿画布。"""
    win.activateWindow()
    win.resize(main.MIN_W, 520)                    # 窄窗：工具栏出现横向滚动条
    QTest.qWait(50)
    bar = win.toolbar_scroll.horizontalScrollBar()
    assert bar.isVisible() is True
    bar.setFocus()
    QTest.qWait(20)
    assert QApplication.focusWidget() is bar
    assert win.view.hasFocus() is False

    QTest.keyClick(bar, Qt.Key_Q)
    assert win.view.tool == "eraser"
    QTest.keyClick(bar, Qt.Key_B)
    assert win.view.tool == "pen"

    for index, key in enumerate((Qt.Key_1, Qt.Key_2, Qt.Key_3)):
        QTest.keyClick(bar, key)
        assert win.view.color == PALETTE[index]

    win.view.zoom_at_screen(100.0, 100.0, 2.0)
    assert win.view.viewport_scale() == pytest.approx(2.0)
    QTest.keyClick(bar, Qt.Key_0)
    assert win.view.viewport_scale() == pytest.approx(1.0)


def test_trash_button_clears_draft_canvas_immediately(win):
    """垃圾桶图标直接清草稿画布（不进捕获态），且清屏是一步可撤销动作。"""
    QTest.mousePress(win.view.viewport(), Qt.LeftButton, pos=QPoint(100, 100))
    QTest.mouseRelease(win.view.viewport(), Qt.LeftButton, pos=QPoint(200, 200))
    assert len(win.controller.document.strokes) == 1
    assert win.view.history.depth == 1

    win.toolbox.clear_button.click()
    assert win.controller.document.strokes == []
    assert win.status_text().endswith("笔迹 0")

    win.view.history.pop_undo()
    assert len(win.controller.document.strokes) == 1


def test_close_quits_when_neither_tray_nor_hotkey_works(qapp, tmp_path, monkeypatch):
    """没有托盘、热键也没注册上：关窗必须真退出，否则窗口再也叫不回来。"""
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    monkeypatch.setattr(GlobalHotkey, "register", lambda self, hwnd, sequence: False)
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))

    ctrl = main.AppController(tmp_path)
    ctrl.settings.close_prompt = False       # 本用例测无退路时的直接退出
    win = main.MainWindow(ctrl)
    win.show()
    QTest.qWaitForWindowExposed(win)
    _ALIVE.append(win)

    assert win.start_hotkey() is False
    assert win.start_tray() is False
    assert win.tray is None
    assert win.hotkey.is_registered is False
    assert "托盘" in win.message_label.text()
    assert "退出" in win.message_label.text()        # 不再承诺热键可用

    assert win.close() is True
    assert win.isVisible() is False
    assert quits == [1]


def test_main_window_uses_saved_custom_width(qapp, tmp_path):
    from sketchpad.core import settings as store

    store.save(tmp_path / "settings.json", store.Settings(width=9.5))
    ctrl = main.AppController(tmp_path)
    w = main.MainWindow(ctrl)
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    try:
        assert w.view.width == 9.5
        assert w.toolbox.size_slider.value() == 95
        assert w.toolbox.size_label.text() == "9.5"
    finally:
        w._quitting = True
        w.close()


# ---------- 应用图标 ----------

def test_app_icon_uses_asset_png(qapp):
    """应用图标优先用素材图（sketchpad/assets/app.png）。"""
    assert icons._asset_path("app.png") is not None
    icon = icons.app_icon()
    assert icon.isNull() is False
    assert icon.availableSizes()            # 素材是 1024 单张，系统自行缩放


def test_app_icon_falls_back_to_drawn_when_asset_missing(qapp, monkeypatch):
    """素材缺失时回退到现画的多尺寸图标（打包漏收资源也不至于没图标）。"""
    icons.app_icon.cache_clear()
    monkeypatch.setattr(icons, "_asset_path", lambda name: None)
    try:
        icon = icons.app_icon()
        sizes = sorted(size.width() for size in icon.availableSizes())
        assert sizes == sorted(icons.APP_ICON_SIZES)
    finally:
        icons.app_icon.cache_clear()


def test_main_window_carries_the_app_icon(win):
    assert win.windowIcon().isNull() is False


# ---------- 首次收进托盘的提示 ----------

class _TrayStub:
    """只记录 showMessage；closeEvent 只关心托盘存不存在。"""

    def __init__(self) -> None:
        self.messages: list[tuple] = []

    def showMessage(self, *args) -> None:
        self.messages.append(args)

    def hide(self) -> None:
        pass


def test_first_close_to_tray_shows_hint_only_once(win):
    stub = _TrayStub()
    win.tray = stub
    win.close()                              # closeEvent 忽略事件（只隐藏），返回 False
    assert win.isVisible() is False
    assert len(stub.messages) == 1
    title, body, level, msecs = stub.messages[0]
    assert title == "墨页"
    assert "Alt+Q" in body
    assert level == QSystemTrayIcon.Information
    assert msecs == 4000

    win.show()
    QTest.qWaitForWindowExposed(win)
    win.close()
    assert len(stub.messages) == 1               # 只提示第一次
    win.tray = None


def test_no_tray_hint_without_a_tray(win):
    assert win.tray is None
    assert win._show_tray_hint_once() is None    # 没有托盘也不能炸
    assert win._tray_hint_shown is False


# ---------- 拖放打开 PDF ----------

def _make_pdf(path):
    doc = pymupdf.open()
    doc.new_page(width=595.0, height=842.0)
    doc.save(str(path))
    doc.close()
    return path


def test_drag_and_drop_accepts_only_local_pdfs(win, tmp_path):
    pdf = _make_pdf(tmp_path / "drop.pdf")
    note = tmp_path / "note.txt"
    note.write_text("not a pdf", encoding="utf-8")

    other = QMimeData()
    other.setUrls([QUrl.fromLocalFile(str(note))])
    assert win._pdf_paths_from_mime(other) == []
    enter_other = QDragEnterEvent(QPointF(4.0, 4.0), Qt.CopyAction, other,
                                  Qt.LeftButton, Qt.NoModifier)
    win.dragEnterEvent(enter_other)
    assert enter_other.isAccepted() is False

    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(note)), QUrl.fromLocalFile(str(pdf))])
    assert win._pdf_paths_from_mime(data) == [pdf]
    enter = QDragEnterEvent(QPointF(4.0, 4.0), Qt.CopyAction, data,
                            Qt.LeftButton, Qt.NoModifier)
    win.dragEnterEvent(enter)
    assert enter.isAccepted() is True

    drop = QDropEvent(QPointF(4.0, 4.0), Qt.CopyAction, data,
                      Qt.LeftButton, Qt.NoModifier)
    win.dropEvent(drop)
    assert win.mode == "document"
    assert win.pdf_view.pdf_path == pdf


def test_drop_without_pdf_is_ignored(win, tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("not a pdf", encoding="utf-8")
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(note))])
    drop = QDropEvent(QPointF(4.0, 4.0), Qt.CopyAction, data,
                      Qt.LeftButton, Qt.NoModifier)
    win.dropEvent(drop)
    assert drop.isAccepted() is False
    assert win.mode == "draft"
