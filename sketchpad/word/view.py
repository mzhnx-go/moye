"""Word viewer: annotate a cached PDF, keep all sidecars next to the .docx.

`PdfView.pdf_path` returns the original Word path (sidecar/title/recent), while
`search_path` points at the cached PDF used for rendering and text search.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sketchpad.core import pdfstore
from sketchpad.ui.pdfview import PdfView
from sketchpad.word import converter


class WordView(PdfView):
    """PDF annotation surface for `.docx` / `.doc` (legacy raises a clear error)."""

    kind = "pdf"

    def __init__(self, settings=None, parent=None, cache_root: Path | None = None) -> None:
        super().__init__(settings, parent)
        self._cache_root = Path(cache_root) if cache_root is not None else None
        self._fingerprint = ""
        self.conversion_note = ""

    def open(self, path: Path, *, ignore_sidecar: bool = False,
             document_override=None) -> None:
        path = Path(path)
        pdf = converter.ensure_cached_pdf(path, self._cache_root)
        fingerprint = pdf.parent.name
        stale = False if ignore_sidecar else self._stale_source(path, fingerprint)
        if stale:
            self._backup_stale_sidecar(path)
        super().open(pdf, ignore_sidecar=ignore_sidecar or stale,
                     document_override=document_override, anchor_path=path)
        self._fingerprint = fingerprint
        if stale:
            self.conversion_note = (
                f"{path.name} 的内容已修改：旧批注可能错位，已备份为 "
                f"{self._stale_name(path)} 后按新内容重新打开")
        else:
            self.conversion_note = (
                f"已把 {path.name} 转换为缓存 PDF 供批注；原 Word 文件不会被修改")

    def mark_sidecar_saved(self) -> None:
        """记下这份 sidecar 对应的 Word 内容指纹，下次打开才能发现源文件变化。"""
        if not self._fingerprint:
            return
        try:
            self._marker_path().write_text(self._fingerprint, encoding="utf-8")
        except OSError:
            pass

    def _marker_path(self, path: Path | None = None) -> Path:
        path = Path(path) if path is not None else self.pdf_path
        return path.with_name(path.name + ".sketch.source")

    def _stale_name(self, path: Path) -> str:
        return pdfstore.sidecar_path(path).name + ".stale"

    def _stale_source(self, path: Path, fingerprint: str) -> bool:
        sidecar = pdfstore.sidecar_path(path)
        marker = self._marker_path(path)
        if not sidecar.is_file() or not marker.is_file():
            return False
        try:
            return marker.read_text(encoding="utf-8").strip() != fingerprint
        except OSError:
            return False

    def _backup_stale_sidecar(self, path: Path) -> None:
        sidecar = pdfstore.sidecar_path(path)
        if not sidecar.is_file():
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = sidecar.with_name(f"{sidecar.name}.stale-{stamp}")
        try:
            sidecar.replace(target)
        except OSError:
            return
        marker = self._marker_path(path)
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass

