"""离屏截取方向 A 的顶栏验收图；不显示到桌面，不操控鼠标。"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    # The Windows offscreen QPA plugin finds no fonts unless a font directory is
    # given, and would silently render every label as tofu boxes.
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymupdf
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main
from sketchpad.ui import theme


def _make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()


def _grab(window, out: Path, name: str) -> None:
    QTest.qWait(250)
    target = out / name
    window.grab().save(str(target))
    print(target)


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/plans/assets/2026-10-26-topchrome")
    parser.add_argument("--scale", default="100")
    args = parser.parse_args()

    app = QApplication([])
    if not QFontDatabase.families():
        print(
            "ERROR: Qt font database is empty; refusing to write tofu screenshots.\n"
            "Set QT_QPA_FONTDIR to a directory containing fonts, e.g.\n"
            r'  QT_QPA_FONTDIR="C:/Windows/Fonts" python tools/ui_snap_topchrome.py',
            file=sys.stderr,
        )
        sys.exit(1)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app.setStyleSheet(theme.QSS)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        controller = main.AppController(root / "cfg")
        window = main.MainWindow(controller)
        window.resize(1160, 760)
        window.show()
        QTest.qWaitForWindowExposed(window)
        _grab(window, out, f"draft_1160_{args.scale}.png")

        window.resize(792, 760)
        _grab(window, out, f"draft_792_{args.scale}.png")

        pdf = root / "sample.pdf"
        _make_pdf(pdf)
        window.resize(1160, 760)
        window._enter_document(str(pdf))
        _grab(window, out, f"pdf_1160_{args.scale}.png")

        window._quitting = True
        window.close()

        empty = main.MainWindow(controller, primary=False)
        empty.resize(1160, 760)
        empty.show()
        QTest.qWaitForWindowExposed(empty)
        _grab(empty, out, f"empty_1160_{args.scale}.png")
        empty._quitting = True
        empty.close()


if __name__ == "__main__":
    run()
