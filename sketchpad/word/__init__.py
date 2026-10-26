"""Standard-only: Word annotation via cached PDF and in-app DOCX editing."""
from __future__ import annotations


def install() -> None:
    """Register `.docx`/`.doc` viewers and the `.docx` in-app editor (idempotent)."""
    from sketchpad.word.editor import WordEditorWindow
    from sketchpad.word.view import WordView
    from sketchpad.workspace.viewers import register_editor, register_viewer

    register_viewer((".docx", ".doc"), WordView, kind="pdf")

    def open_editor(path, parent=None):
        return WordEditorWindow(path, parent)

    register_editor((".docx",), open_editor)
