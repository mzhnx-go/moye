"""Standard-only: local conversion center (PDF/images/Office/Markdown/OCR)."""
from __future__ import annotations


def install() -> None:
    """Register the conversion-center dialog with the shared workspace."""
    from sketchpad.convert.dialog import ConvertCenterDialog
    from sketchpad.workspace.viewers import register_converter

    register_converter(lambda parent=None: ConvertCenterDialog(parent))
