"""打包版启动兜底：未捕获异常写日志并弹原生错误框。

源码运行时由根目录 `launch.py` 负责；PyInstaller 版没有控制台，靠这里把
"闪退"变成一条可读的日志 + 一个提示框。
"""
from __future__ import annotations

import ctypes
import datetime
import os
import sys
import threading
import traceback
from pathlib import Path

MB_ICONERROR = 0x00000010
MB_TOPMOST = 0x00040000


def app_base_dir() -> Path:
    """与 sketchpad.app.app_base_dir 同口径：打包后 = exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _append_log(title: str, detail: str) -> Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = "\n".join(
        (
            "=" * 78,
            f"[{stamp}] {title}",
            f"Python: {sys.version}",
            f"Executable: {sys.executable}",
            f"Cwd: {os.getcwd()}",
            "",
            detail.rstrip("\n"),
            "",
        )
    )
    primary = app_base_dir() / "data" / "startup.log"
    fallback = Path(os.environ.get("TEMP", str(Path.home()))) / "Moye-startup.log"
    for candidate in (primary, fallback):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8") as fh:
                fh.write(report + "\n")
            return candidate
        except OSError:
            continue
    return primary


def _report(title: str, detail: str) -> None:
    try:
        log_path = _append_log(title, detail)
    except Exception:
        log_path = app_base_dir() / "data" / "startup.log"
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "墨页 启动/运行出错，程序已退出。\n\n"
            f"错误详情已写入：\n{log_path}\n\n请把该日志发给开发者。",
            "墨页 错误",
            MB_ICONERROR | MB_TOPMOST,
        )
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    try:
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        detail = f"{exc_type!r}: {exc_value!r}"
    _report("未捕获异常（主线程）", detail)


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    try:
        name = args.thread.name if args.thread is not None else "unknown"
    except Exception:
        name = "unknown"
    try:
        detail = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
    except Exception:
        detail = f"{args.exc_type!r}: {args.exc_value!r}"
    _report(f"未捕获异常（线程 {name}）", detail)


def install_exception_hooks() -> None:
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
