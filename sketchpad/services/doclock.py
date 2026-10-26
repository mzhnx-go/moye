"""同一份 PDF/Word 的跨进程写锁。

多开后两个实例同时打开同一份文档，各自读 sidecar、各自自动暂存，后关的会
把先关的批注覆盖掉。这里用 Windows 命名互斥体挡住第二个实例：锁随进程退出
自动释放，不会留下需要手工清理的锁文件。名称按规范化路径哈希，同一文件
（大小写/相对路径不同）也只算一份。

只给有 sidecar 的文档用（PDF / Word）；TXT/导图是只读查看，允许多开。
非 Windows 暂不拦（返回 True），与旧行为一致。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import time
from pathlib import Path

ERROR_ALREADY_EXISTS = 183
LOCK_PREFIX = "Local\\PageFlow.Doc.v1."

# 进程内已持锁计数/互斥体句柄：多个窗口在同一进程打开同一文件时共享一把锁，
# 跨进程仍然只允许第一个进程。句柄只在计数归零时关闭，避免提前释放。
_HELD: dict[str, int] = {}
_HANDLES: dict[str, int] = {}


def lock_name_for(path) -> str:
    """规范化路径 → 互斥体名；大小写、相对路径、.. 都归一到同一个。"""
    raw = Path(path)
    try:
        resolved = raw.resolve()
    except OSError:
        resolved = raw.absolute()
    key = os.path.normcase(str(resolved))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return LOCK_PREFIX + digest


class DocumentLock:
    """文档独占锁；acquire() False 表示另一个进程正开着这份文档。

    同一进程内可重复获取（引用计数），因为 PageFlow 的多个窗口共享进程；
    UI 本身已经按路径去重，这里只是不让同进程的第二个窗口被误判成“另一个实例”。
    """

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._name = lock_name_for(path)
        self._owned = False

    def acquire(self) -> bool:
        if self._owned:
            return True
        if self._name in _HELD:
            _HELD[self._name] += 1
            self._owned = True
            return True
        # 另一个进程刚退出时，内核对象可能晚几十毫秒才销毁；短暂重试，
        # 不把“刚好在关窗”误判成“还在另一个实例开着”。
        for attempt in range(3):
            created = self._create()
            if created is None:
                return True             # 创建失败：不拦用户打开文档
            if created:
                self._owned = True
                return True
            if attempt < 2:
                time.sleep(0.08)
        return False

    def _create(self) -> bool | None:
        if sys.platform != "win32":
            _HELD[self._name] = 1
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_wchar_p)
        # 必须先清 GetLastError：CreateMutex 成功且新建时不会主动清零，
        # 上一次 ERROR_ALREADY_EXISTS 会残留，导致第二次永远误判“已存在”。
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, 0, self._name)
        if not handle:
            return None
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _HELD[self._name] = 1
        _HANDLES[self._name] = handle
        return True

    def release(self) -> None:
        if not self._owned:
            return
        self._owned = False
        count = _HELD.get(self._name, 1) - 1
        if count > 0:
            _HELD[self._name] = count
            return
        _HELD.pop(self._name, None)
        handle = _HANDLES.pop(self._name, None)
        if handle:
            kernel32 = ctypes.windll.kernel32
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel32.CloseHandle(handle)
