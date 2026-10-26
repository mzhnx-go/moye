# tests/test_hotkey.py
"""全局热键测试：parse_hotkey 逐值断言；真机注册走专属线程的消息窗口。"""
import ctypes
import sys
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import sketchpad.services.hotkey as hotkey_mod
from sketchpad.services.hotkey import (MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN,
                        GlobalHotkey, parse_hotkey)

WIN = sys.platform == "win32"
win_only = pytest.mark.skipif(not WIN, reason="RegisterHotKey 仅 Windows")


# ---------- parse_hotkey：纯函数 ----------

def test_ctrl_alt_s():
    assert parse_hotkey("Ctrl+Alt+S") == (MOD_CONTROL | MOD_ALT, 0x53)
    # 截图序列第二步需要临时注册裸键；默认仍拒绝
    assert parse_hotkey("S", allow_bare=True) == (0, ord("S"))


def test_shift_and_letter_lowercase():
    assert parse_hotkey("shift+x") == (MOD_SHIFT, 0x58)


def test_special_keys_map_to_vk():
    assert parse_hotkey("Ctrl+Delete") == (MOD_CONTROL, 0x2E)
    assert parse_hotkey("Ctrl+Esc") == (MOD_CONTROL, 0x1B)
    assert parse_hotkey("Alt+Tab") == (MOD_ALT, 0x09)
    assert parse_hotkey("Shift+Space") == (MOD_SHIFT, 0x20)
    assert parse_hotkey("F1") == (0, 0x70)
    assert parse_hotkey("F12") == (0, 0x7B)
    assert parse_hotkey("F24") == (0, 0x87)
    assert parse_hotkey("Ctrl+E") == (MOD_CONTROL, 0x45)


def test_modifier_aliases_and_win_bit():
    assert parse_hotkey("Control+S") == (MOD_CONTROL, 0x53)
    assert parse_hotkey("ctrl+S") == (MOD_CONTROL, 0x53)
    assert parse_hotkey("win+Alt+s") == (MOD_WIN | MOD_ALT, 0x53)


def test_whitespace_around_tokens_tolerated():
    assert parse_hotkey(" ctrl + alt + s ") == (MOD_CONTROL | MOD_ALT, 0x53)


def test_digit_key_and_modifier():
    assert parse_hotkey("Ctrl+5") == (MOD_CONTROL, 0x35)
    assert parse_hotkey("Ctrl+Tab") == (MOD_CONTROL, 0x09)
    assert parse_hotkey("Ctrl+PgUp") == (MOD_CONTROL, 0x21)
    assert parse_hotkey("Ctrl+PageDown") == (MOD_CONTROL, 0x22)
    assert parse_hotkey("Ctrl+Home") == (MOD_CONTROL, 0x24)
    assert parse_hotkey("Ctrl+End") == (MOD_CONTROL, 0x23)
    assert parse_hotkey("Ctrl+Left") == (MOD_CONTROL, 0x25)
    assert parse_hotkey("Ctrl+Return") == (MOD_CONTROL, 0x0D)


def test_bare_non_function_key_needs_a_modifier():
    # v2.4：全局热键至少一个修饰键；A/Space/Delete 这类裸键一律拒收
    for bare in ("A", "Space", "Delete", "5", "Esc", "a", "space",
                 "PageUp", "Home", "Left"):
        with pytest.raises(ValueError):
            parse_hotkey(bare)


def test_function_keys_may_stand_alone():
    assert parse_hotkey("F5") == (0, 0x74)
    for n in range(1, 25):
        assert parse_hotkey(f"F{n}") == (0, 0x6F + n)
    assert parse_hotkey("Shift+F2") == (MOD_SHIFT, 0x71)     # 带修饰照旧


def test_v24_hotkeys_still_valid():
    assert parse_hotkey("Ctrl+Alt+D") == (MOD_CONTROL | MOD_ALT, 0x44)
    assert parse_hotkey("F9") == (0, 0x78)
    assert parse_hotkey("Ctrl+Shift+F") == (MOD_CONTROL | MOD_SHIFT, 0x46)
    assert parse_hotkey("Ctrl+F") == (MOD_CONTROL, 0x46)


def test_bad_input_raises_valueerror():
    for bad in ("", "   ", "Ctrl+", "+S", "++", "Ctrl+Alt+NoSuchKey", "F25",
                "Ctrl+Ctrl+S", "ctrl+Control+s", "Ctrl+Shift+", None):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


def test_modifier_constants_match_win32():
    assert (MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT) == (
        0x0001, 0x0002, 0x0004, 0x0008, 0x4000)


# ---------- 事件循环 / 键位探测 ----------

@pytest.fixture(scope="module")
def app():
    """activated 是队列信号，需要事件循环；本文件不建窗口。"""
    app = QApplication.instance() or QApplication([])
    yield app


def _user32():
    """测试侧 user32：显式原型，免得 Win64 上窗口句柄被截成 32 位。"""
    import ctypes.wintypes as wt

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    u.RegisterHotKey.restype = wt.BOOL
    u.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    u.UnregisterHotKey.restype = wt.BOOL
    u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    u.PostMessageW.restype = wt.BOOL
    return u


_PROBE_ID = 0x7E57          # 一次性探测用 id，与被测模块的 HOTKEY_ID 无关


def _is_free(sequence: str) -> bool:
    """能注册说明空闲；无论成败都立刻注销，不占系统键位。"""
    mods, vk = parse_hotkey(sequence)
    u = _user32()
    if not u.RegisterHotKey(None, _PROBE_ID, mods | MOD_NOREPEAT, vk):
        return False
    u.UnregisterHotKey(None, _PROBE_ID)
    return True


def _free_sequence() -> str:
    """优先 Ctrl+Alt+F11；被占就换别的 Alt+Ctrl+F 键，全占则跳过。"""
    for n in (11, 10, 12, 9, 8, 7):
        seq = f"Ctrl+Alt+F{n}"
        if _is_free(seq):
            return seq
    pytest.skip("Ctrl+Alt+F 键位都被占用")


# ---------- GlobalHotkey：坏序列不抛异常 ----------

def test_register_invalid_sequence_returns_false():
    h = GlobalHotkey()
    assert h.register(0, "Ctrl+Nope") is False
    assert h.is_registered is False
    assert h.error_message != ""
    assert h.window_handle == 0


def test_register_bare_key_is_rejected_before_win32(app):
    # 裸键在解析层就被拒：连消息线程都不该起
    h = GlobalHotkey()
    try:
        assert h.register(0, "A") is False
        assert h.is_registered is False
        assert h.thread_alive is False
        assert "修饰键" in h.error_message
    finally:
        h.unregister()


# ---------- 真机注册：消息窗口往返 ----------

def _drain_signal(ms: int = 200) -> None:
    """QTest.qWait 全程持 GIL，热键线程会被饿死；先 sleep 放 GIL，再 qWait 收队列信号。"""
    time.sleep(0.1)
    QTest.qWait(ms)


@win_only
def test_real_registration_round_trip(app):
    seq = _free_sequence()
    h = GlobalHotkey()
    got = []
    h.activated.connect(lambda: got.append(1))
    try:
        assert h.register(0, seq) is True
        assert h.is_registered is True
        assert h.window_handle != 0
        assert h.error_message == ""
        assert _user32().PostMessageW(h.window_handle, hotkey_mod.WM_HOTKEY,
                                      GlobalHotkey.HOTKEY_ID, 0) != 0
        _drain_signal()
        assert got == [1]
    finally:
        h.unregister()
    assert h.is_registered is False
    assert h.window_handle == 0


# ---------- 同一序列第二份注册必须失败 ----------

@win_only
def test_conflict_with_second_instance_returns_false(app):
    seq = _free_sequence()
    holder = GlobalHotkey()
    second = GlobalHotkey()
    try:
        assert holder.register(0, seq) is True
        assert second.register(0, seq) is False
        assert second.is_registered is False
        assert second.error_message != ""
        assert second.last_error == 1409        # ERROR_HOTKEY_ALREADY_REGISTERED
    finally:
        second.unregister()
        holder.unregister()
    assert holder.is_registered is False


# ---------- 注销后线程要真的退出 ----------

@win_only
def test_unregister_stops_thread(app):
    h = GlobalHotkey()
    try:
        assert h.register(0, _free_sequence()) is True
        assert h.thread_alive is True
    finally:
        h.unregister()
    assert h.is_registered is False
    assert h.thread_alive is False
