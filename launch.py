"""墨页启动器（由 run.bat 双击调用）。

职责：
1. 在导入 app 之前装好全局异常钩子：任何未捕获异常都写进 data/startup.log
   （不可写则回退到 %TEMP%/Moye-startup.log），并弹一个原生 Windows
   消息框告诉用户日志在哪。pythonw.exe 没有控制台，没有这层兜底就只剩
   “双击没反应”。
2. ``launch.py --check``：只做导入检查（不建 QApplication、不建窗口、不写设置），
   供 run.bat 在后台启动前同步跑一遍，失败时把原因打到控制台。
3. 普通模式：装好钩子后 ``import main`` 并调用 ``main.main()``，返回它的退出码。
"""

from __future__ import annotations

import ctypes
import datetime
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))
from sketchpad.core.channel import DATA_DIR  # noqa: E402
from sketchpad.startup import disable_pymupdf_layout  # noqa: E402

disable_pymupdf_layout()

LOG_PATH = REPO_DIR / DATA_DIR / "startup.log"
FALLBACK_LOG_PATH = Path(tempfile.gettempdir()) / "Moye-startup.log"

MB_ICONERROR = 0x00000010
MB_TOPMOST = 0x00040000


def _install_features() -> None:
    """补上共享 app 不会自动注册的查看器/编辑器/转换器。"""
    from sketchpad.convert import install as install_convert
    from sketchpad.reader import install as install_readers
    from sketchpad.word import install as install_word

    install_readers()
    install_word()
    install_convert()


def _append_log(title: str, detail: str) -> Path:
    """把一条带时间戳的报告追加进日志，返回实际写入的文件。"""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = "\n".join(
        (
            "=" * 78,
            f"[{stamp}] {title}",
            f"Python: {sys.version}",
            f"Executable: {sys.executable}",
            f"Repo: {REPO_DIR}",
            f"Cwd: {os.getcwd()}",
            "",
            detail.rstrip("\n"),
            "",
        )
    )
    for candidate in (LOG_PATH, FALLBACK_LOG_PATH):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8") as fh:
                fh.write(report + "\n")
            return candidate
        except OSError:
            continue
    return LOG_PATH


def _show_error_box(text: str) -> None:
    """弹原生消息框；任何失败都吞掉，绝不能盖住原始异常。"""
    try:
        ctypes.windll.user32.MessageBoxW(
            None, text, "墨页 启动失败", MB_ICONERROR | MB_TOPMOST
        )
    except Exception:
        pass


def _report_failure(title: str, detail: str) -> None:
    try:
        log_path = _append_log(title, detail)
    except Exception:
        log_path = LOG_PATH
    _show_error_box(
        "墨页 启动时发生错误，程序已退出。\n\n"
        f"错误详情已写入日志：\n{log_path}\n\n"
        "请把该日志文件发给开发者排查。"
    )


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    try:
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        detail = f"{exc_type!r}: {exc_value!r}"
    _report_failure("未捕获异常（主线程）", detail)


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
    _report_failure(f"未捕获异常（线程 {name}）", detail)


def install_exception_hooks() -> None:
    """必须在 ``import main`` 之前调用。"""
    import faulthandler
    # 关键：pythonw 没有控制台，C 级崩溃（ctypes 访问违规 / Qt 渲染崩溃）
    # 不会走 sys.excepthook，必须靠 faulthandler 落盘，否则只剩"双击没反应"。
    try:
        _fault = LOG_PATH.parent / "fault.log"
        faulthandler.enable(file=_fault.open("a", encoding="utf-8"))
    except Exception:
        try:
            faulthandler.enable()
        except Exception:
            pass
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def _package_versions() -> list[tuple[str, str]]:
    import PySide6
    import PySide6.QtCore
    import pymupdf

    return [
        ("Python", f"{sys.version.split()[0]} ({sys.executable})"),
        ("PySide6", PySide6.__version__),
        ("Qt", PySide6.QtCore.qVersion()),
        ("PyMuPDF", pymupdf.__version__),
    ]


def _check_optional_features() -> None:
    """打包/部署自检：Word、转换、OCR 的延迟导入包必须真的在。"""
    import importlib.util

    required = ("markdown_it", "mammoth", "docx", "openpyxl", "pptx")
    missing = [name for name in required
               if importlib.util.find_spec(name) is None]
    ocr = importlib.util.find_spec("rapidocr_onnxruntime")
    if ocr is None:
        missing.append("rapidocr_onnxruntime")
    elif not any(Path(path).is_dir()
                 and any((Path(path) / "models").glob("*.onnx"))
                 for path in (ocr.submodule_search_locations or ())):
        missing.append("rapidocr models")
    if missing:
        raise RuntimeError("缺少功能依赖：" + "、".join(missing))
    print("  features : Word / 转换 / OCR 依赖齐全")


def run_check() -> int:
    """--check：只验证版本与导入，不建 QApplication、不建窗口、不写设置。"""
    print("墨页 self-check")
    for label, value in _package_versions():
        print(f"  {label:<8}: {value}")
    print("  importing main ...")
    _install_features()
    import main  # noqa: F401  只验证导入，不调用 main()

    if not callable(getattr(main, "main", None)):
        raise RuntimeError("main.py 里没有可调用的 main()")
    _check_optional_features()
    print("OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # 脚本所在目录必须在 sys.path 首位，保证 import main 命中的是本仓库
    if str(REPO_DIR) not in sys.path:
        sys.path.insert(0, str(REPO_DIR))

    if "--check" in argv:
        # 自检由 run.bat 同步调用：失败把 traceback 打进临时日志供 type 显示，
        # 不弹消息框（弹框会把批处理卡死）。
        try:
            return run_check()
        except Exception:
            traceback.print_exc()
            return 1

    install_exception_hooks()
    splash = None
    try:
        # 先把启动动画放出来，再导入很重的 pymupdf/应用模块；否则冷启动前
        # 1–2 秒只有空白，用户会以为没打开。
        from PySide6.QtWidgets import QApplication
        from sketchpad.ui.splash import show_launch_splash

        QApplication.instance() or QApplication(sys.argv)
        try:
            splash = show_launch_splash()
        except Exception:
            splash = None
        import main as app_main
    except Exception:
        if splash is not None:
            try:
                splash.close()
            except Exception:
                pass
        _excepthook(*sys.exc_info())
        return 1
    try:
        return app_main.main(splash=splash)
    except Exception:
        if splash is not None:
            try:
                splash.close()
            except Exception:
                pass
        _excepthook(*sys.exc_info())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
