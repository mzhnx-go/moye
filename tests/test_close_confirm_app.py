"""关闭主窗确认接线：异步取消 / 退出 / 最小化。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
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


class _FakePrompt:
    def __init__(self):
        self.deleted = False

    def hide(self):
        pass

    def deleteLater(self):
        self.deleted = True


def _fake_prompt(win, action: str):
    prompt = _FakePrompt()
    win._close_prompt = prompt
    win._close_dialog_restore = None
    win._on_close_action_chosen(action)
    QTest.qWait(20)
    return prompt


def test_close_asks_before_acting(win, monkeypatch):
    seen = []
    monkeypatch.setattr(
        win, "_show_close_confirm",
        lambda can_recover, extra: seen.append((can_recover, extra)))

    assert win.close() is False
    assert seen == []                       # 确认框推迟到 closeEvent 之后
    QTest.qWait(30)
    assert len(seen) == 1 and isinstance(seen[0][0], bool)
    assert win.isVisible() is True


def test_dialog_cancel_keeps_window_and_does_not_restore_overlay(win):
    """取消只隐藏主窗内遮罩，不恢复覆盖层，主窗保持可交互。"""
    class FakeAnnotate:
        def __init__(self):
            self.overlay = type("O", (), {"mode": "draw"})()
            self.draws = 0

        def hide(self):
            pass

        def enter_draw(self):
            self.draws += 1

    fake = FakeAnnotate()
    win._annotate = fake
    assert win.close() is False
    QTest.qWait(40)
    prompt = win._close_prompt
    assert prompt is not None
    assert prompt.isWindow() is False          # 主窗子控件，不是系统窗口
    assert prompt.parentWidget() is win
    assert prompt.cancel_button.text() == "取消"

    QTest.keyClick(prompt, Qt.Key_Escape)
    QTest.qWait(20)
    assert win._close_prompt is None
    assert win.isVisible() is True
    assert win.isEnabled() is True
    assert fake.draws == 0
    win._annotate = None


def test_ensure_interactive_restores_disabled_window(win):
    """回归 2026-10-24：主窗被 OS 层 Disable（只能拖边框、内部无响应）后，
    _ensure_interactive() 必须无条件把它拉回可交互。

    直接用 setEnabled(False) 模拟该症状（真实场景是 Windows 的
    EnableWindow(FALSE)）；关键是不影响验证"能重新 Enable 回来"。
    """
    win.setEnabled(False)
    assert win.isEnabled() is False

    win._ensure_interactive()

    assert win.isEnabled() is True            # 兜底强制恢复了主窗交互


def test_tray_choice_hides(win):
    _fake_prompt(win, "tray")
    assert win.isVisible() is False


def test_quit_choice_routes_to_quit_all(win, monkeypatch):
    called = []
    monkeypatch.setattr(win, "quit_all_apps", lambda: called.append(True))
    _fake_prompt(win, "quit")
    assert called == [True]


def test_no_prompt_uses_saved_choice(win):
    win.controller.settings.close_prompt = False
    win.controller.settings.close_to_tray = True
    win.tray = object()                     # 只用于 closeEvent 的“有退路”判断
    asked = []
    win._show_close_confirm = lambda can_recover, extra: asked.append(1)

    assert win.close() is False
    assert asked == []
    assert win.isVisible() is False
    win.tray = None
