"""启动优化：跳过 PyMuPDF 2.0 自动加载但本项目不用的 layout/onnxruntime。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_disable_pymupdf_layout_keeps_pdf_io_working():
    code = (
        "import sys\n"
        "from sketchpad.startup import disable_pymupdf_layout\n"
        "disable_pymupdf_layout()\n"
        "import pymupdf\n"
        "assert sys.modules.get('pymupdf.layout') is None\n"
        "doc = pymupdf.open()\n"
        "page = doc.new_page()\n"
        "page.insert_text((72, 72), 'layout blocked')\n"
        "assert 'layout blocked' in page.get_text()\n"
        "assert page.get_pixmap().width > 0\n"
        "doc.close()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
