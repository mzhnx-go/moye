"""Standard-only: multi-format document viewers (TXT/Markdown/Word/mind maps).

Lite must never import this package. Standard's entry calls `install()` so the
shared workspace knows which suffixes it can open.
"""
from __future__ import annotations


def install() -> None:
    """Register Standard viewers with the shared registry (idempotent)."""
    from sketchpad.reader.mindmap import MindMapView
    from sketchpad.reader.text_view import TextViewer
    from sketchpad.workspace.viewers import register_viewer

    register_viewer(TextViewer.SUFFIXES, TextViewer, kind="text")
    register_viewer(MindMapView.SUFFIXES, MindMapView, kind="text")
