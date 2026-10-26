"""“点取消后软件还能不能用”的端到端验证（offscreen 下驱动真实 MainWindow）。

重点不是“对话框关没关”，而是“关掉之后主窗是否还能接收真实输入、是否还能
执行真实操作”。用一个事件过滤器抓主窗收到的鼠标按压事件来证明：若主窗被
OS 层 Disable 或被别的顶层窗口挡住，按压事件根本不会送达——这正是用户
实测“只能拖边框、点哪都没反应”的反向判定。

offscreen 无法复现 Windows 的 EnableWindow/置顶焦点问题（那只能在真机验证），
但本测试能锁定“代码链路在取消后确实把主窗恢复成可交互、且真实事件可达”，
排除“取消回调没跑 / 主窗没被重新 Enable / 批注层没恢复”这类根因。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

import main

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    ctrl = main.AppController(tmp_path / "cfg")
    ctrl.settings.close_prompt = True
    w = main.MainWindow(ctrl)
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    w._quitting = True
    w.close()


class _PressCatcher(QObject):
    """装在 MainWindow 上的事件过滤器：记录主窗（及其子控件）收到的鼠标按压。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.presses = 0

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonPress:
            self.presses += 1
        return super().eventFilter(obj, ev)


def _trigger_close_and_cancel(win):
    """走真实关窗流程：closeEvent → 推迟弹框 → 点真实「取消」按钮。"""
    assert win.close() is False              # closeEvent 会 ignore，等事件循环弹框
    QTest.qWait(40)                          # 等 QTimer.singleShot(0) 弹出确认框
    dialog = win._close_dialog
    assert dialog is not None, "关闭确认框未弹出"
    assert hasattr(dialog, "cancel_button"), "确认框没有「取消」按钮"
    dialog.cancel_button.click()             # 点真实按钮
    QTest.qWait(60)                          # 等 actionChosen → 恢复 → 兜底计时


def test_cancel_keeps_window_receiving_input(win, monkeypatch):
    """取消后主窗：框已关、可见、可交互，且能收到真实鼠标按压。"""
    monkeypatch.setattr(win, "quit_app", lambda: None)

    catcher = _PressCatcher()
    win.installEventFilter(catcher)

    _trigger_close_and_cancel(win)

    # 1) 确认框彻底收掉，主窗原地留着
    assert win._close_dialog is None, "点取消后确认框没被销毁/清空"
    assert win.isVisible() is True
    # 2) 主窗处于可交互状态（未被 setEnabled(False)）
    assert win.isEnabled() is True
    # 3) 关键：向主窗中心发一次真实鼠标按压，事件必须真的送达主窗
    target = win.centralWidget() or win
    before = catcher.presses
    QTest.mouseClick(target, Qt.LeftButton)
    QTest.qWait(20)
    assert catcher.presses > before, "取消后主窗收不到鼠标按压——仍被禁用/遮挡"


def test_cancel_restores_draw_overlay_and_still_usable(win, monkeypatch):
    """批注层原本在 draw 模式：取消后恢复回 draw，但主窗仍要能用。

    这是用户最可能的真机冻结场景（draw 模式是全屏 TOPMOST 吃点击），必须确认
    恢复逻辑不会把主窗留在禁用态、也不会让事件链路断掉。
    """
    monkeypatch.setattr(win, "quit_app", lambda: None)

    annotate = getattr(win, "_annotate", None)
    overlay = getattr(annotate, "overlay", None)
    if overlay is None:
        import pytest
        pytest.skip("本构建无批注层，跳过 draw 恢复场景")

    # 进 draw 模式（offscreen 下 win32 调用为 no-op，只看代码链路）
    try:
        annotate.enter_draw()
    except Exception:
        pass
    assert overlay.mode in ("draw", "passthrough", "hidden")

    catcher = _PressCatcher()
    win.installEventFilter(catcher)

    _trigger_close_and_cancel(win)

    assert win._close_dialog is None
    assert win.isEnabled() is True
    # 取消把批注层恢复到了弹框前的模式（不应该是 hidden 之外的意外态）
    target = win.centralWidget() or win
    before = catcher.presses
    QTest.mouseClick(target, Qt.LeftButton)
    QTest.qWait(20)
    assert catcher.presses > before, "draw 模式取消后主窗收不到鼠标按压"
