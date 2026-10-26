r"""构建墨页（单一完整产品）。

Usage: `.venv\Scripts\python.exe tools\build_app.py`
Output: `dist/墨页/`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "墨页"
ENTRY = ROOT / "launch.py"


def build(name: str = APP_NAME) -> Path:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed", "--onedir",
        "--name", name,
        "--paths", str(ROOT),
        "--exclude-module", "tkinter",
        "--icon", str(ROOT / "sketchpad" / "assets" / "app.ico"),
        "--add-data", f"{ROOT / 'sketchpad' / 'assets'}{os.pathsep}sketchpad/assets",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "app"),
        "--specpath", str(BUILD / "app"),
    ]
    # 这些模块在功能模块里是延迟导入的，PyInstaller 静态分析收不到；
    # 不收齐会导致 Word/OCR/转换在打包版里报“缺少组件”。
    for package in ("pymupdf", "markdown_it", "mammoth", "docx",
                    "openpyxl", "pptx", "rapidocr_onnxruntime"):
        args += ["--collect-all", package]
    args.append(str(ENTRY))
    print(" ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)
    target = DIST / name
    (target / "使用说明.txt").write_text(
        f"{name} 便携版\n"
        "1. 解压整个文件夹后双击 exe。\n"
        "2. 配置、自动暂存与启动日志写在 exe 同目录的 data/ 里。\n"
        "3. 分享给他人前请删除 data/（里面有本机最近打开与个人设置）。\n"
        "4. 若杀毒软件拦截，请选择允许（PyInstaller 程序常见误报）。\n",
        encoding="utf-8",
    )
    print(f"built: {target}", flush=True)
    return target


if __name__ == "__main__":
    build()
