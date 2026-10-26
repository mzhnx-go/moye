"""Viewer registry.

The shared core must not import `sketchpad.reader`/`word`/`convert` (Lite
forbids them), so product entries install extra viewers here at startup.
PDF stays the default and needs no registration.

`kind` tells the workspace which capabilities apply: "pdf" (annotatable,
page-based) or "text" (read-only reader). Word is registered as "pdf" because
it annotates a cached PDF through `WordView`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from sketchpad.ui.pdfview import PdfView

_FACTORIES: dict[str, tuple[Callable[[Any], Any], str]] = {}
_EDITORS: dict[str, Callable[..., Any]] = {}
_CONVERTER_FACTORY: Callable[..., Any] | None = None


def register_viewer(suffixes, factory: Callable[[Any], Any], kind: str = "text") -> None:
    """Register a viewer factory for each suffix (case-insensitive, leading dot)."""
    if kind not in ("pdf", "text"):
        raise ValueError(f"未知查看器类型：{kind!r}")
    for suffix in suffixes:
        _FACTORIES[str(suffix).lower()] = (factory, kind)


def clear_viewers() -> None:
    """Test hook: drop Standard registrations."""
    global _CONVERTER_FACTORY
    _FACTORIES.clear()
    _EDITORS.clear()
    _CONVERTER_FACTORY = None


def register_editor(suffixes, factory: Callable[..., Any]) -> None:
    """Register an in-app editor factory (Word/other editable formats)."""
    for suffix in suffixes:
        _EDITORS[str(suffix).lower()] = factory


def supports_edit(path) -> bool:
    return Path(path).suffix.lower() in _EDITORS


def create_editor(path, parent=None):
    factory = _EDITORS.get(Path(path).suffix.lower())
    return factory(Path(path), parent) if factory else None


def supported_suffixes() -> tuple[str, ...]:
    return (".pdf", *sorted(_FACTORIES))


def is_supported_document(path) -> bool:
    return Path(path).suffix.lower() in supported_suffixes()


def viewer_kind(path) -> str:
    """PDF and Word are "pdf"; registered text readers are "text"."""
    entry = _FACTORIES.get(Path(path).suffix.lower())
    return entry[1] if entry else "pdf"


def create_viewer(path, settings) -> Any:
    suffix = Path(path).suffix.lower()
    entry = _FACTORIES.get(suffix)
    if entry is None:
        return PdfView(settings)
    factory = entry[0]
    return factory(settings)


def register_converter(factory: Callable[..., Any]) -> None:
    """Register the Standard conversion-center dialog factory."""
    global _CONVERTER_FACTORY
    _CONVERTER_FACTORY = factory


def has_converter() -> bool:
    return _CONVERTER_FACTORY is not None


def create_converter(parent=None):
    return _CONVERTER_FACTORY(parent) if _CONVERTER_FACTORY else None
