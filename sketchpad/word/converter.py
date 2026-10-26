"""Standard-only: Word (.docx) → cached PDF for annotation.

Why a cache: the annotation stack (PdfView/PdfSource/sidecar/export) already
works on PDF. Converting a Word file once and binding annotations to the
original file keeps the source untouched and reuses handwriting, notes,
search and export.

The converter is self-contained (Mammoth → HTML → PyMuPDF Story): no Word,
LibreOffice or pandoc is required. Story writes a real text layer, so search,
text selection and text highlights work on the cached PDF; Qt's PDF printer
was rejected because it emitted glyph outlines with no text layer.
Layout fidelity is basic-document level; headers/footers and complex section
layouts may differ from Word.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import mammoth
import pymupdf

from sketchpad.core.channel import DATA_DIR

SUFFIXES = (".docx",)
LEGACY_SUFFIX = ".doc"
PAGE_MARGIN = 56                 # PDF 点，约 20mm
BODY_POINT_SIZE = 11

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)


class WordConversionError(ValueError):
    """Conversion failed with a message meant for the status bar."""


def _font_css() -> str:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return (f"@font-face {{font-family: docfont; "
                    f"src: url({candidate});}}\n")
    return ""


_CSS = """
* { font-family: docfont; font-size: 11pt; line-height: 1.5; color: #111111; }
h1 { font-size: 20pt; margin: 14pt 0 8pt 0; }
h2 { font-size: 16pt; margin: 12pt 0 6pt 0; }
h3 { font-size: 13pt; margin: 10pt 0 5pt 0; }
p { margin: 4pt 0; }
pre, code { font-family: Consolas; background: #f2f2f2; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; }
td, th { border: 1px solid #bbbbbb; padding: 4pt; vertical-align: top; }
th { background: #f0f0f0; }
blockquote { margin: 6pt 16pt; color: #444444; }
img { margin: 4pt 0; }
"""


def cache_base() -> Path:
    """`data/converted` next to the exe (frozen) or repo root (source runs)."""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parents[2]
    return root / DATA_DIR / "converted"


def fingerprint(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cached_pdf(path, base: Path | None = None) -> Path:
    """缓存位置只由文件内容的 sha256 决定；同一份 docx 永远复用同一份 PDF。"""
    path = Path(path)
    return Path(base or cache_base()) / fingerprint(path) / f"{path.stem}.pdf"


def ensure_cached_pdf(path, base: Path | None = None) -> Path:
    """返回可批注的缓存 PDF；缺失或为空时现场转换（写临时文件再原子替换）。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == LEGACY_SUFFIX:
        raise WordConversionError(
            "旧版 .doc 无法直接转换：请先在 Word/WPS 里另存为 .docx 再打开")
    if suffix not in SUFFIXES:
        raise WordConversionError(f"不支持的 Word 格式：{path.suffix or path.name}")
    if not path.is_file():
        raise WordConversionError(f"找不到 Word 文件：{path}")
    target = cached_pdf(path, base)
    try:
        if target.is_file() and target.stat().st_size > 0:
            return target
    except OSError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        _convert_docx(path, partial, target.parent / "assets")
        if partial.stat().st_size <= 0:
            raise WordConversionError("转换结果为空，Word 文件可能已损坏")
        partial.replace(target)
    except WordConversionError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise WordConversionError(f"Word 转换失败：{exc}") from exc
    _write_metadata(target, path)
    return target


def _write_metadata(pdf: Path, source: Path) -> None:
    meta = {"source": str(source), "pdf": pdf.name}
    try:
        (pdf.parent / "source.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def render_docx(source, out_pdf, assets_dir) -> Path:
    """Public entry for the conversion center: render a .docx into a PDF file."""
    out_pdf = Path(out_pdf)
    _convert_docx(Path(source), out_pdf, Path(assets_dir))
    return out_pdf


def _convert_docx(source: Path, out_pdf: Path, assets_dir: Path) -> None:
    assets_dir.mkdir(parents=True, exist_ok=True)
    counter = {"value": 0}

    def save_image(image) -> dict:
        counter["value"] += 1
        content_type = (image.content_type or "image/png").split("/")[-1]
        extension = "jpg" if content_type in ("jpeg", "jpg") else content_type
        target = assets_dir / f"image-{counter['value']}.{extension}"
        with image.open() as handle, open(target, "wb") as out:
            out.write(handle.read())
        return {"src": target.name}

    # mammoth 内部打开 docx 后不关 zip；传文件对象并由 with 关闭，
    # 否则缓存转换后原 Word 文件仍被锁住。
    with open(source, "rb") as handle:
        result = mammoth.convert_to_html(
            handle, convert_image=mammoth.images.img_element(save_image),
            style_map="u => u\nstrike => s")
    html = (f"<html><head><style>{_font_css()}{_CSS}</style></head>"
            f"<body>{result.value}</body></html>")

    story = pymupdf.Story(html=html, archive=str(assets_dir))
    writer = pymupdf.DocumentWriter(str(out_pdf))
    page_rect = pymupdf.paper_rect("a4")
    text_rect = page_rect + (PAGE_MARGIN, PAGE_MARGIN, -PAGE_MARGIN, -PAGE_MARGIN)
    pages = 0
    try:
        while True:
            more, filled = story.place(text_rect)
            if not filled:
                break
            device = writer.begin_page(page_rect)
            story.draw(device)
            writer.end_page()
            pages += 1
            if not more:
                break
    finally:
        writer.close()
    if pages == 0:
        blank = pymupdf.open()
        blank.new_page(width=page_rect.width, height=page_rect.height)
        blank.save(str(out_pdf))
        blank.close()
