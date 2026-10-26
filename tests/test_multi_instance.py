"""多开：副实例无托盘/全局热键/全屏批注；同一 PDF 不允许两个实例同时写。"""
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QLabel

import sketchpad.app as app_module
from sketchpad.app import AppController, MainWindow, apply_instance_features
from sketchpad.services.doclock import DocumentLock
from sketchpad.services.hotkey import GlobalHotkey
from sketchpad.ui.dialogs import ClosePrompt

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "multi instance")
    doc.save(path)
    doc.close()
    return path


def _make_window(qapp, base: Path, *, primary_instance: bool = True) -> MainWindow:
    ctrl = AppController(base, persist_settings=primary_instance)
    win = MainWindow(ctrl, primary_instance=primary_instance)
    win.show()
    qapp.processEvents()
    _ALIVE.append(win)
    return win


# ---------- 文档写锁 ----------

_HELPER = r"""
import sys, time
sys.path.insert(0, sys.argv[2])
from sketchpad.services.doclock import DocumentLock
lock = DocumentLock(sys.argv[1])
print("ready" if lock.acquire() else "blocked", flush=True)
time.sleep(30)
"""


def _run_lock_helper(pdf: Path, *, hold: bool) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(app_module.__file__).resolve().parent.parent)
    proc = subprocess.Popen(
        [sys.executable, "-c", _HELPER, str(pdf), env["PYTHONPATH"]],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    if hold:
        assert proc.stdout.readline().strip() == "ready"
    return proc


def test_document_lock_is_reentrant_in_process_and_blocks_other_process(tmp_path):
    pdf = _make_pdf(tmp_path / "共享.pdf")
    first = DocumentLock(pdf)
    assert first.acquire() is True
    try:
        second = DocumentLock(tmp_path / "sub" / ".." / "共享.pdf")
        assert second.acquire() is True          # 同进程窗口共享一把锁
        second.release()

        helper = _run_lock_helper(pdf, hold=False)
        try:
            assert helper.stdout.readline().strip() == "blocked"
        finally:
            helper.kill()
            helper.wait()
    finally:
        first.release()
    third = DocumentLock(pdf)
    assert third.acquire() is True
    third.release()
    other = DocumentLock(tmp_path / "另一份.pdf")
    assert other.acquire() is True
    other.release()


def test_same_pdf_blocked_while_another_process_holds(qapp, tmp_path):
    pdf = _make_pdf(tmp_path / "共享.pdf")
    helper = _run_lock_helper(pdf, hold=True)
    win = _make_window(qapp, tmp_path / "cfg")
    try:
        assert win._enter_document(pdf) is False
        assert "另一个" in win.message_label.text()
        assert win._active_session is None
    finally:
        helper.kill()
        helper.wait()
        try:
            assert win._enter_document(pdf) is True
        finally:
            win._quitting = True
            win.close()


def test_same_pdf_focuses_existing_window_in_same_process(qapp, tmp_path):
    """同进程多窗口：同一 PDF 不再开第二份，直接切到已有窗口。"""
    import types
    pdf = _make_pdf(tmp_path / "共享.pdf")
    first = _make_window(qapp, tmp_path / "a")
    second = _make_window(qapp, tmp_path / "b")
    second.window_registry = types.SimpleNamespace(windows=[first, second])
    try:
        assert first._enter_document(pdf) is True
        entry = first.tabs.current_entry
        assert second._enter_document(pdf) is True
        assert second._active_session is None
        assert len(second.tabs.entries) == 1      # 仍只有初始笔记
        assert first.tabs.current_entry is entry
    finally:
        for win in (first, second):
            win._quitting = True
            win.close()


# ---------- 实例角色 ----------

def test_secondary_window_hides_global_features(qapp, tmp_path, monkeypatch):
    win = _make_window(qapp, tmp_path / "cfg", primary_instance=False)
    try:
        assert win.primary_instance is False
        assert "副实例" in win.windowTitle()
        assert win.toolbox.annotate_button.isHidden() is True
        assert win.menubar.fullscreen_annotate_action.isEnabled() is False

        calls = []
        monkeypatch.setattr(
            GlobalHotkey, "register",
            lambda self, hwnd, sequence: calls.append(sequence) or True)
        assert win._register_scratch_hotkey("Alt+Q") is False
        assert win._register_main_hotkey("Alt+W") is False
        assert win.start_screenshot_hotkey() is False
        assert calls == []
    finally:
        win._quitting = True
        win.close()


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _FakeAnnotate:
    def __init__(self):
        self.captured = _Signal()
        self.captureDone = _Signal()
        self.overlay = type("Overlay", (), {"modeChanged": _Signal()})()


def test_apply_instance_features_gates_global_features(qapp, tmp_path, monkeypatch):
    primary = _make_window(qapp, tmp_path / "p")
    secondary = _make_window(qapp, tmp_path / "s", primary_instance=False)
    started = []
    for name in ("start_hotkey", "start_main_hotkey",
                 "start_screenshot_hotkey", "start_tray"):
        monkeypatch.setattr(primary, name, lambda n=name: started.append(n) or True)
        monkeypatch.setattr(secondary, name, lambda n=name: started.append(n) or True)
    fake_annotate = _FakeAnnotate()
    monkeypatch.setattr(app_module, "install", lambda window: fake_annotate)
    shot = object()
    monkeypatch.setattr(app_module, "install_screenshot", lambda window: shot)

    try:
        apply_instance_features(secondary, False)
        assert started == []
        assert secondary._annotate is None
        assert secondary._screenshot is shot

        apply_instance_features(primary, True)
        assert started == ["start_hotkey", "start_main_hotkey",
                           "start_screenshot_hotkey", "start_tray"]
        assert primary._annotate is fake_annotate
        assert primary._screenshot is shot
    finally:
        for win in (primary, secondary):
            win._quitting = True
            win.close()


# ---------- 退出全部实例 ----------

def test_quit_all_broadcasts_then_quits_primary(qapp, tmp_path, monkeypatch):
    win = _make_window(qapp, tmp_path / "quit-primary")
    calls = []
    monkeypatch.setattr(app_module, "broadcast_quit_all",
                        lambda: calls.append("broadcast") or True)
    monkeypatch.setattr(win, "quit_app", lambda: calls.append("quit_app"))
    try:
        win.quit_all_apps()
        assert calls == ["broadcast", "quit_app"]
    finally:
        win._quitting = True
        win.close()


def test_quit_all_broadcasts_then_quits_secondary(qapp, tmp_path, monkeypatch):
    win = _make_window(qapp, tmp_path / "quit-secondary", primary_instance=False)
    calls = []
    monkeypatch.setattr(app_module, "broadcast_quit_all",
                        lambda: calls.append("broadcast") or True)
    monkeypatch.setattr(win, "quit_app", lambda: calls.append("quit_app"))
    try:
        win.quit_all_apps()
        assert calls == ["broadcast", "quit_app"]
    finally:
        win._quitting = True
        win.close()


def test_quit_broadcast_message_ignores_self_and_reaches_other_process(
        qapp, tmp_path, monkeypatch):
    if not app_module._QUIT_ALL_MSG_ID:
        pytest.skip("仅 Windows 有跨进程窗口消息")
    win = _make_window(qapp, tmp_path / "quit-msg")
    seen = []
    monkeypatch.setattr(win, "_quit_from_other_instance", lambda: seen.append(1))
    import ctypes
    from ctypes import wintypes
    msg = wintypes.MSG()
    msg.message = app_module._QUIT_ALL_MSG_ID
    msg.wParam = os.getpid()
    try:
        assert win.nativeEvent(b"windows_generic_MSG",
                               ctypes.addressof(msg)) == (True, 0)
        qapp.processEvents()
        assert seen == []                     # 自己发的广播不处理

        msg.wParam = os.getpid() + 1
        assert win.nativeEvent(b"windows_generic_MSG",
                               ctypes.addressof(msg)) == (True, 0)
        qapp.processEvents()
        assert seen == [1]
    finally:
        win._quitting = True
        win.close()


def test_quit_from_other_instance_only_once(qapp, tmp_path, monkeypatch):
    win = _make_window(qapp, tmp_path / "quit-once")
    calls = []
    monkeypatch.setattr(app_module, "_QUIT_ALL_HANDLING", False)

    def fake_quit():
        calls.append(True)
        win._quitting = True

    monkeypatch.setattr(win, "quit_app", fake_quit)
    try:
        win._quit_from_other_instance()
        win._quit_from_other_instance()
        assert calls == [True]
    finally:
        win._quitting = True
        win.close()


# ---------- 配置落盘 ----------

def test_secondary_does_not_write_settings(tmp_path):
    data = tmp_path / "data"
    secondary = AppController(data, persist_settings=False)
    path = secondary.settings_path
    secondary.settings.color = "#123456"
    secondary.save_settings()
    assert path.exists() is False

    primary = AppController(data, persist_settings=True)
    primary.settings.color = "#654321"
    primary.save_settings()
    assert path.exists() is True
    assert "#654321" in path.read_text(encoding="utf-8")


# ---------- 关窗提示 ----------

def test_close_prompt_without_tray_mentions_secondary(qapp):
    prompt = ClosePrompt("PageFlow", can_minimize=False)
    _ALIVE.append(prompt)
    try:
        assert prompt.minimize_button.isEnabled() is False
        texts = [label.text() for label in prompt.findChildren(QLabel)]
        assert any("副实例" in text for text in texts)
    finally:
        prompt.deleteLater()
