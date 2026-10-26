"""Standard-only: the viewer contract shared by text/Markdown/Word viewers.

Only structural typing: the PDF viewer predates this interface and the main
window duck-types it, so a Protocol keeps both sides honest without forcing
an inheritance change on `PdfView`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class DocumentViewer(Protocol):
    """Minimal surface the workspace needs from a non-PDF document view.

    PDF-only members (`source`, `document`, note signals) stay on `PdfView`;
    shared code branches on `kind`.
    """

    kind: str

    def open(self, path: Path, **kwargs) -> None: ...

    def close_document(self) -> None: ...

    @property
    def pdf_path(self) -> Path | None: ...

    def outline(self) -> list[tuple[int, str, int]]:
        """(level, title, anchor) in document order; anchor feeds goto_page()."""

    def search(self, text: str, max_hits: int = 500) -> list[tuple[int, int]]:
        """(0, character offset) pairs; the page component stays 0 for text."""

    def show_hits(self, page: int, offsets: list[int], current: int = 0) -> None: ...

    def clear_hits(self) -> None: ...

    def goto_page(self, anchor: int) -> None: ...

    def current_position(self) -> dict | None: ...

    def restore_position(self, page, offset, zoom, auto_fit) -> None: ...

    def scroll_to_top(self) -> None: ...

    @property
    def zoom(self) -> float: ...

    def set_zoom(self, zoom: float) -> None: ...

    def fit_width(self) -> None: ...

    def status_text(self) -> str: ...
