"""Win32 全局热键：parse_hotkey 是纯函数；注册在专属线程的 message-only 窗口里完成。"""
from __future__ import annotations

import ctypes
import os
import sys
import threading

from PySide6.QtCore import QObject, Signal

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HWND_MESSAGE = -3           # 消息专用父窗口
HOTKEY_ID = 0xB0B1          # 本进程内唯一即可，也是 WM_HOTKEY 的 wParam

_MODIFIERS = {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL, "ALT": MOD_ALT,
              "SHIFT": MOD_SHIFT, "WIN": MOD_WIN}
_FUNCTION_KEYS = frozenset(f"F{i}" for i in range(1, 25))    # F1–F24 可单独成热键
_NAMED_VK = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D,
    "ESC": 0x1B, "ESCAPE": 0x1B, "SPACE": 0x20,
    "PAGEUP": 0x21, "PGUP": 0x21, "PAGEDOWN": 0x22, "PGDN": 0x22,
    "END": 0x23, "HOME": 0x24, "LEFT": 0x25, "UP": 0x26,
    "RIGHT": 0x27, "DOWN": 0x28, "INSERT": 0x2D,
    "DELETE": 0x2E, "DEL": 0x2E,
    **{name: 0x6F + int(name[1:]) for name in _FUNCTION_KEYS},
}

_ERROR_HINTS = {1409: "该热键已被其他程序占用"}   # ERROR_HOTKEY_ALREADY_REGISTERED


def parse_hotkey(text: str, allow_bare: bool = False) -> tuple[int, int]:
    """把 "Ctrl+Alt+S" 解析成 (mods, vk)；任何不合法形式抛 ValueError。

    默认全局热键至少需要一个修饰键（只有 F1–F24 可单独用）。`allow_bare=True`
    只给“截图序列第二步”这类临时热键用：按下后立刻注销，不会长期占用单键。
    """
    if not isinstance(text, str):
        raise ValueError(f"无效热键: {text!r}")
    parts = [part.strip().upper() for part in text.split("+")]
    if any(not part for part in parts):
        raise ValueError(f"无效热键: {text!r}")
    *mod_names, key_name = parts
    mods = 0
    for name in mod_names:
        bit = _MODIFIERS.get(name)
        if bit is None:
            raise ValueError(f"未知修饰键: {name}")
        if mods & bit:
            raise ValueError(f"重复修饰键: {name}")
        mods |= bit
    if key_name in _NAMED_VK:
        vk = _NAMED_VK[key_name]
    elif len(key_name) == 1 and key_name.isascii() and key_name.isalnum():
        vk = ord(key_name)
    else:
        raise ValueError(f"无效按键: {key_name}")
    if mods == 0 and key_name not in _FUNCTION_KEYS and not allow_bare:
        raise ValueError(f"全局热键需要修饰键（F1–F24 除外）: {text!r}")
    return mods, vk


# ---------- 延迟加载的 Win32 层（导入期不碰 ctypes.windll / wintypes） ----------

_WIN: dict = {"api": None, "proc_type": None, "class_type": None, "proc": None}
_WINDOWS: dict[int, "GlobalHotkey"] = {}     # hwnd -> 实例，窗口过程据此分发
_CLASS_LOCK = threading.Lock()
_CLASS_NAME: str | None = None


def _winapi():
    """取 (user32, kernel32)，带原型与 use_last_error；非 Windows 返回 None。"""
    if sys.platform != "win32":
        return None
    if _WIN["api"] is not None:
        return _WIN["api"]
    import ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    lresult = ctypes.c_ssize_t

    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wt.DWORD

    user32.CreateWindowExW.argtypes = [
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.CreateWindowExW.restype = wt.HWND
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.DestroyWindow.restype = wt.BOOL
    user32.RegisterClassW.argtypes = [ctypes.c_void_p]
    user32.RegisterClassW.restype = wt.UINT
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = lresult
    user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    user32.RegisterHotKey.restype = wt.BOOL
    user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wt.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.TranslateMessage.restype = wt.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = lresult
    user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostThreadMessageW.restype = wt.BOOL

    _WIN["api"] = (user32, kernel32)
    return _WIN["api"]


def _wndproc_type():
    """WNDPROC 回调类型；延迟创建以避开非 Windows 的 wintypes。"""
    if _WIN["proc_type"] is None:
        import ctypes.wintypes as wt

        _WIN["proc_type"] = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    return _WIN["proc_type"]


def _wndclass_type():
    """WNDCLASSW 结构；lpfnWndProc 字段必须用 WNDPROC 类型。"""
    if _WIN["class_type"] is None:
        import ctypes.wintypes as wt

        proc = _wndproc_type()

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", proc),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

        _WIN["class_type"] = WNDCLASSW
    return _WIN["class_type"]


def _last_error() -> int:
    """取 Win32 最后错误码；非 Windows 给 0。"""
    if sys.platform != "win32":
        return 0
    return ctypes.get_last_error()


def _hotkey_wndproc(hwnd, msg, wparam, lparam):
    """窗口过程：WM_HOTKEY 转成 activated，其余交给 DefWindowProcW。"""
    try:
        if msg == WM_HOTKEY and wparam == HOTKEY_ID:
            target = _WINDOWS.get(hwnd)
            if target is not None:
                target._emit_activated()
    except Exception:               # 回调里绝不能让异常穿进 C
        pass
    api = _winapi()
    if api is None:
        return 0
    return api[0].DefWindowProcW(hwnd, msg, wparam, lparam)


def _ensure_class(hinst) -> str | None:
    """每进程只注册一次窗口类（名字带 pid），返回类名；失败返回 None。"""
    global _CLASS_NAME
    with _CLASS_LOCK:
        if _CLASS_NAME is not None:
            return _CLASS_NAME
        user32 = _winapi()[0]
        proc = _wndproc_type()(_hotkey_wndproc)     # 必须留引用，回调不能被回收
        name = f"PageFlowHotkeyWnd_{os.getpid()}"
        wc = _wndclass_type()()
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.lpszClassName = name
        if not user32.RegisterClassW(ctypes.byref(wc)) and _last_error() != 1410:
            return None                             # 1410 = 类已存在，可直接复用
        _WIN["proc"] = proc
        _CLASS_NAME = name
        return name


def _create_message_window(name: str, hinst) -> int:
    """建 message-only 窗口（父窗口 HWND_MESSAGE），返回 HWND，失败给 0。"""
    user32 = _winapi()[0]
    hwnd = user32.CreateWindowExW(
        0, name, "", 0, 0, 0, 0, 0,
        ctypes.c_void_p(HWND_MESSAGE), None, hinst, None)
    return int(hwnd or 0)


class GlobalHotkey(QObject):
    """注册/注销一个全局热键；注册在专属守护线程的消息窗口里完成。"""

    activated = Signal()
    HOTKEY_ID = HOTKEY_ID

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hwnd = 0
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._registered = False
        self.last_error = 0
        self.error_message = ""
        try:
            self.destroyed.connect(self._on_destroyed)
        except Exception:
            pass

    @property
    def is_registered(self) -> bool:
        return self._registered

    @property
    def window_handle(self) -> int:
        """仅供测试：消息窗口的 HWND；未注册时为 0。"""
        return self._hwnd

    @property
    def thread_alive(self) -> bool:
        """消息线程是否还在跑；未起线程或已退出为 False。"""
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def register(self, hwnd: int, sequence: str, *, allow_bare: bool = False) -> bool:
        """注册热键；hwnd 仅为兼容旧接口，已忽略（内部自建消息窗口）。

        序列不合法、平台不支持或 Win32 冲突都返回 False（不抛异常），
        原因写在 error_message / last_error 里。
        """
        self.unregister()
        try:
            mods, vk = parse_hotkey(sequence, allow_bare=allow_bare)
        except ValueError as exc:
            self.last_error = 0
            self.error_message = str(exc)
            return False
        if _winapi() is None:
            self.last_error = 0
            self.error_message = "仅 Windows 支持全局热键"
            return False
        if not self._start(mods, vk):
            return False
        self._registered = True
        self.last_error = 0
        self.error_message = ""
        return True

    def unregister(self) -> None:
        """注销热键并停掉专属线程；可重复调用，未注册时只清理残留。"""
        if self._thread is None and not self._registered:
            self._hwnd = 0
            return
        self._stop()

    # ---------- 线程 ----------

    def _start(self, mods: int, vk: int) -> bool:
        """起线程并同步等它注册完（≤3 秒）；失败写好错误信息并收尾。"""
        ready = threading.Event()
        fail: list[int] = []
        self._thread = threading.Thread(
            target=self._thread_main, args=(mods, vk, ready, fail),
            name="PageFlowHotkey", daemon=True)
        self._thread.start()
        if not ready.wait(3.0):
            self.last_error = 0
            self.error_message = "热键线程未在 3 秒内就绪"
            self._stop()
            return False
        if fail:
            err = fail[0]
            hint = _ERROR_HINTS.get(err)
            self.last_error = err
            self.error_message = f"注册热键失败（错误码 {err}）" + (f"：{hint}" if hint else "")
            self._stop()
            return False
        return True

    def _stop(self) -> None:
        """投 WM_QUIT 让循环退出，并等线程自行注销热键、销毁窗口。"""
        thread = self._thread
        tid, self._thread_id = self._thread_id, 0
        self._registered = False
        if thread is not None and thread.is_alive():
            api = _winapi()
            try:
                if api is not None and tid:
                    api[0].PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
            if thread is not threading.current_thread():
                thread.join(2.0)
        if thread is None or not thread.is_alive():
            self._thread = None
            self._hwnd = 0

    def _thread_main(self, mods: int, vk: int,
                     ready: threading.Event, fail: list[int]) -> None:
        """线程体：建窗口 → 注册热键 → 跑消息循环；退出前自行注销并毁窗。"""
        user32 = _winapi()[0]
        kernel32 = _winapi()[1]
        import ctypes.wintypes as wt

        hwnd = 0
        registered = False
        try:
            hinst = kernel32.GetModuleHandleW(None)
            name = _ensure_class(hinst)
            if not name:
                fail.append(_last_error())
                return
            hwnd = _create_message_window(name, hinst)
            if not hwnd:
                fail.append(_last_error())
                return
            _WINDOWS[hwnd] = self
            self._hwnd = hwnd
            self._thread_id = int(kernel32.GetCurrentThreadId())
            ctypes.set_last_error(0)
            if not user32.RegisterHotKey(hwnd, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                fail.append(_last_error())
                return
            registered = True
            ready.set()
            msg = wt.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except BaseException:
            if not fail:
                fail.append(0)
        finally:
            if hwnd:
                if registered:
                    user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                _WINDOWS.pop(hwnd, None)
                user32.DestroyWindow(hwnd)
                self._hwnd = 0
            ready.set()

    def _emit_activated(self) -> None:
        """在热键线程里发信号；Qt 按接收方线程排队投递，同线程直连。"""
        try:
            self.activated.emit()
        except Exception:           # QObject 已销毁 / 解释器收尾
            pass

    # ---------- 销毁 ----------

    def _on_destroyed(self, *_args) -> None:
        """Qt 对象被销毁时尽力注销。"""
        try:
            self.unregister()
        except Exception:
            pass

    def __del__(self):
        """解释器收尾时尽力注销，绝不抛异常。"""
        try:
            if not sys.is_finalizing():
                self.unregister()
        except Exception:
            pass
