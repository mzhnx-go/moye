"""多开与“主实例”身份。

允许多开：每次启动都建自己的窗口，不再把第二次启动转发给已运行实例。
但托盘、全局热键（Alt+Q / Alt+W / Alt+A 等）和全屏批注是**进程级独占**能力，
只有第一个启动的实例（主实例）注册；后来的是副实例，只保留窗口内功能。

主实例锁**跨渠道**（源码/打包共用一把），否则两版同时开会抢同一组全局热键。
配置目录固定为 `data/`（见 `channel.py`）。
"""
from __future__ import annotations

import ctypes
import sys
import time

PRIMARY_LOCK_NAME = "PageFlow.Primary.v1.lock"
ERROR_ALREADY_EXISTS = 183


class InstanceLock:
    """进程级独占锁；拿不到就是副实例，不是错误。

    Windows 用命名互斥体：`QLocalServer` 在 Windows 上允许同名多监听，不能当锁用；
    进程崩溃时内核会自动释放互斥体，不会留下“开不了第二次”的死锁。
    非 Windows 用 QLocalServer 占位（Unix 上同名监听会 AddressInUse）。
    """

    def __init__(self, name: str = PRIMARY_LOCK_NAME) -> None:
        self._name = name
        self._handle = None
        self._server = None

    def acquire(self) -> bool:
        # 主实例刚退出时内核对象可能晚几十毫秒销毁；短重试，避免把新启动
        # 误判成副实例。真正的副实例会 3 次都失败，最多多等 160ms。
        for attempt in range(3):
            created = self._create()
            if created is None:
                return True             # 互斥体创建失败：不拦用户启动
            if created:
                return True
            if attempt < 2:
                time.sleep(0.08)
        return False

    def _create(self) -> bool | None:
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                              ctypes.c_wchar_p)
            kernel32.SetLastError(0)
            handle = kernel32.CreateMutexW(None, 0, "Local\\" + self._name)
            if not handle:
                return None
            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            return True
        from PySide6.QtNetwork import QLocalServer

        self._server = QLocalServer()
        self._server.listen(self._name)
        if self._server.isListening():
            return True
        self._server = None
        return False

    def release(self) -> None:
        if self._handle is not None:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._server is not None:
            from PySide6.QtNetwork import QLocalServer

            self._server.close()
            QLocalServer.removeServer(self._name)
            self._server = None
