"""Standard-only: local document conversion services (no Qt UI).

Every operation writes a new file and never overwrites an input. Heavy work is
page-by-page so the dialog worker can report progress and cancel.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pymupdf

from sketchpad.core.channel import DATA_DIR
from sketchpad.word import converter as word_converter

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
MARKDOWN_SUFFIXES = (".md", ".markdown")
HTML_SUFFIXES = (".html", ".htm")


class ConvertError(ValueError):
    pass


def _open_pdf(path) -> pymupdf.Document:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        raise ConvertError(f"不是 PDF：{path.name}")
    try:
        return pymupdf.open(str(path))
    except Exception as exc:
        raise ConvertError(f"无法打开 PDF：{path.name}（{exc}）") from exc


def _progress(progress, done: int, total: int, message: str = "") -> None:
    if progress is not None:
        progress(done, total, message)


def _cancelled(should_cancel, partial: Path | None = None) -> None:
    if partial is not None:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
    raise ConvertError("已取消")


def merge_pdfs(inputs, output, progress=None, should_cancel=None) -> Path:
    output = Path(output)
    documents = [_open_pdf(item) for item in inputs]
    merged = pymupdf.open()
    try:
        total = len(documents)
        for index, document in enumerate(documents):
            if should_cancel is not None and should_cancel():
                _cancelled(should_cancel, output)
            merged.insert_pdf(document)
            _progress(progress, index + 1, total, f"合并 {document.name}")
        merged.save(str(output), garbage=4, deflate=True)
    finally:
        merged.close()
        for document in documents:
            document.close()
    return output


def split_pdf(source, output_dir, ranges: str = "", progress=None,
              should_cancel=None) -> list[Path]:
    source = Path(source)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    document = _open_pdf(source)
    results: list[Path] = []
    try:
        groups = _parse_ranges(ranges, document.page_count)
        for index, (first, last) in enumerate(groups):
            if should_cancel is not None and should_cancel():
                for item in results:
                    item.unlink(missing_ok=True)
                _cancelled(should_cancel)
            part = pymupdf.open()
            try:
                part.insert_pdf(document, from_page=first, to_page=last)
                suffix = f"{first + 1}" if first == last else f"{first + 1}-{last + 1}"
                target = _unique_path(output_dir / f"{source.stem}-{suffix}.pdf")
                part.save(str(target), garbage=4, deflate=True)
                results.append(target)
            finally:
                part.close()
            _progress(progress, index + 1, len(groups), target.name)
    finally:
        document.close()
    return results


def _parse_ranges(text: str, page_count: int) -> list[tuple[int, int]]:
    text = (text or "").strip()
    if not text:
        return [(index, index) for index in range(page_count)]
    groups: list[tuple[int, int]] = []
    for token in re.split(r"[,，;；\s]+", text):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if match is None:
            raise ConvertError(f"页码范围写法不对：{token}（例：1-3,5）")
        first = int(match.group(1)) - 1
        last = int(match.group(2) or match.group(1)) - 1
        first = max(0, min(first, page_count - 1))
        last = max(0, min(last, page_count - 1))
        if first > last:
            first, last = last, first
        groups.append((first, last))
    if not groups:
        raise ConvertError("没有可拆分的页码")
    return groups


def rotate_pdf(source, output, angle: int = 90, progress=None,
               should_cancel=None) -> Path:
    document = _open_pdf(source)
    output = Path(output)
    try:
        for index, page in enumerate(document):
            if should_cancel is not None and should_cancel():
                _cancelled(should_cancel, output)
            page.set_rotation((page.rotation + int(angle)) % 360)
            _progress(progress, index + 1, document.page_count, f"第 {index + 1} 页")
        document.save(str(output), garbage=4, deflate=True)
    finally:
        document.close()
    return output


def compress_pdf(source, output, progress=None, should_cancel=None) -> Path:
    document = _open_pdf(source)
    output = Path(output)
    try:
        if should_cancel is not None and should_cancel():
            _cancelled(should_cancel, output)
        _progress(progress, 0, 1, "清理对象与压缩流")
        document.save(str(output), garbage=4, deflate=True,
                      deflate_images=True, deflate_fonts=True, clean=True)
        _progress(progress, 1, 1, output.name)
    finally:
        document.close()
    return output


def pdf_to_images(source, output_dir, dpi: int = 144, fmt: str = "png",
                  progress=None, should_cancel=None) -> list[Path]:
    document = _open_pdf(source)
    source = Path(source)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fmt = (fmt or "png").lower()
    if fmt not in ("png", "jpg", "jpeg"):
        raise ConvertError(f"不支持的图片格式：{fmt}")
    results: list[Path] = []
    try:
        for index, page in enumerate(document):
            if should_cancel is not None and should_cancel():
                for item in results:
                    item.unlink(missing_ok=True)
                _cancelled(should_cancel)
            target = output_dir / f"{source.stem}-{index + 1:03d}.{fmt}"
            page.get_pixmap(dpi=max(72, min(600, int(dpi)))).save(str(target))
            results.append(target)
            _progress(progress, index + 1, document.page_count, target.name)
    finally:
        document.close()
    return results


def images_to_pdf(inputs, output, progress=None, should_cancel=None) -> Path:
    output = Path(output)
    document = pymupdf.open()
    try:
        total = len(inputs)
        for index, item in enumerate(inputs):
            if should_cancel is not None and should_cancel():
                _cancelled(should_cancel, output)
            try:
                image = pymupdf.open(str(item))
                pdf_bytes = image.convert_to_pdf()
                image.close()
                part = pymupdf.open("pdf", pdf_bytes)
                document.insert_pdf(part)
                part.close()
            except Exception as exc:
                raise ConvertError(f"图片无法读取：{Path(item).name}（{exc}）") from exc
            _progress(progress, index + 1, total, Path(item).name)
        document.save(str(output), garbage=4, deflate=True)
    finally:
        document.close()
    return output


def pdf_to_text(source, output, progress=None, should_cancel=None) -> Path:
    document = _open_pdf(source)
    output = Path(output)
    try:
        chunks = []
        for index, page in enumerate(document):
            if should_cancel is not None and should_cancel():
                _cancelled(should_cancel, output)
            chunks.append(page.get_text("text"))
            _progress(progress, index + 1, document.page_count, f"第 {index + 1} 页")
        output.write_text("\n\f\n".join(chunks), encoding="utf-8")
    finally:
        document.close()
    return output


def pdf_to_docx(source, output, progress=None, should_cancel=None) -> Path:
    from docx import Document as DocxDocument

    document = _open_pdf(source)
    output = Path(output)
    docx_doc = DocxDocument()
    try:
        total = document.page_count
        for index, page in enumerate(document):
            if should_cancel is not None and should_cancel():
                _cancelled(should_cancel, output)
            text = page.get_text("text").strip()
            if text:
                for line in text.splitlines():
                    docx_doc.add_paragraph(line)
            if index < total - 1:
                docx_doc.add_page_break()
            _progress(progress, index + 1, total, f"第 {index + 1} 页")
        docx_doc.save(str(output))
    finally:
        document.close()
    return output


def html_to_pdf_text(html: str, output, *, title: str = "") -> Path:
    """HTML → 带文本层的 PDF（PyMuPDF Story，和 Word 缓存同一渲染器）。"""
    output = Path(output)
    css = """
@font-face {font-family: docfont; src: url(C:/Windows/Fonts/msyh.ttc);}
* {font-family: docfont; font-size: 11pt; line-height: 1.6; color: #111;}
h1 {font-size: 22pt; margin: 16pt 0 8pt 0;}
h2 {font-size: 17pt; margin: 12pt 0 6pt 0;}
h3 {font-size: 14pt; margin: 10pt 0 5pt 0;}
p {margin: 5pt 0;}
pre, code {font-family: Consolas; background: #f2f2f2;}
pre {padding: 6pt;}
table {border-collapse: collapse; width: 100%;}
td, th {border: 1px solid #bbb; padding: 4pt;}
blockquote {margin: 8pt 18pt; color: #444;}
img {max-width: 100%;}
"""
    story = pymupdf.Story(
        html=f"<html><head><style>{css}</style></head><body>{html}</body></html>")
    writer = pymupdf.DocumentWriter(str(output))
    page_rect = pymupdf.paper_rect("a4")
    text_rect = page_rect + (56, 56, -56, -56)
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
        blank.save(str(output))
        blank.close()
    return output


def markdown_to_pdf(source, output, progress=None, should_cancel=None) -> Path:
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise ConvertError("缺少 markdown-it-py，无法转换 Markdown") from exc
    text = Path(source).read_text(encoding="utf-8", errors="replace")
    engine = MarkdownIt("commonmark", {"html": False})
    engine.enable("table").enable("strikethrough")
    _progress(progress, 0, 1, "排版 Markdown")
    return html_to_pdf_text(engine.render(text), output)


def docx_to_pdf(source, output, progress=None, should_cancel=None) -> Path:
    assets = Path(tempfile.mkdtemp(prefix="sketchpad-convert-"))
    try:
        if should_cancel is not None and should_cancel():
            _cancelled(should_cancel)
        _progress(progress, 0, 1, "转换 Word")
        word_converter.render_docx(Path(source), Path(output), assets)
        _progress(progress, 1, 1, Path(output).name)
    except word_converter.WordConversionError as exc:
        raise ConvertError(str(exc)) from exc
    finally:
        try:
            for item in assets.glob("*"):
                item.unlink(missing_ok=True)
            assets.rmdir()
        except OSError:
            pass
    return Path(output)


def add_page_numbers(source, output, position: str = "bottom-center",
                     progress=None, should_cancel=None) -> Path:
    document = _open_pdf(source)
    output = Path(output)
    try:
        total = document.page_count
        for index, page in enumerate(document):
            if should_cancel is not None and should_cancel():
                _cancelled(should_cancel, output)
            text = str(index + 1)
            width, height = page.rect.width, page.rect.height
            text_width = pymupdf.get_text_length(text, fontname="helv", fontsize=9)
            x = width / 2 - text_width / 2
            y = height - 24
            if position == "bottom-right":
                x = width - text_width - 28
            elif position == "top-center":
                y = 24
            page.insert_text((x, y), text, fontname="helv", fontsize=9,
                             color=(0.35, 0.35, 0.35))
            _progress(progress, index + 1, total, f"第 {index + 1} 页")
        document.save(str(output), garbage=4, deflate=True)
    finally:
        document.close()
    return output


def add_watermark(source, output, text: str = "草稿", progress=None,
                  should_cancel=None) -> Path:
    document = _open_pdf(source)
    output = Path(output)
    text = (text or "").strip() or "草稿"
    try:
        total = document.page_count
        for index, page in enumerate(document):
            if should_cancel is not None and should_cancel():
                _cancelled(should_cancel, output)
            width, height = page.rect.width, page.rect.height
            fontsize = max(24, min(72, width / max(4, len(text))))
            text_width = pymupdf.get_text_length(text, fontname="china-s",
                                                 fontsize=fontsize)
            point = ((width - text_width) / 2, height / 2)
            page.insert_text(point, text, fontname="china-s", fontsize=fontsize,
                             color=(0.55, 0.55, 0.55), overlay=True)
            _progress(progress, index + 1, total, f"第 {index + 1} 页")
        document.save(str(output), garbage=4, deflate=True)
    finally:
        document.close()
    return output


def encrypt_pdf(source, output, user_password: str = "",
                owner_password: str = "", progress=None,
                should_cancel=None) -> Path:
    if not user_password and not owner_password:
        raise ConvertError("加密至少需要一个打开密码或权限密码")
    document = _open_pdf(source)
    output = Path(output)
    try:
        if should_cancel is not None and should_cancel():
            _cancelled(should_cancel, output)
        document.save(
            str(output), encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=user_password or None, owner_pw=owner_password or None,
            permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY)
        _progress(progress, 1, 1, output.name)
    finally:
        document.close()
    return output


def decrypt_pdf(source, output, password: str = "", progress=None,
                should_cancel=None) -> Path:
    try:
        document = pymupdf.open(str(source))
    except Exception as exc:
        raise ConvertError(f"无法打开 PDF：{exc}") from exc
    output = Path(output)
    try:
        if document.needs_pass and not document.authenticate(password):
            raise ConvertError("密码不正确，无法解密")
        if should_cancel is not None and should_cancel():
            _cancelled(should_cancel, output)
        document.save(str(output), garbage=4, deflate=True)
        _progress(progress, 1, 1, output.name)
    finally:
        document.close()
    return output


def ocr_pdf(source, output, progress=None, should_cancel=None) -> Path:
    """给扫描 PDF 加不可见文字层（RapidOCR，中英文）；原页面外观不变。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ConvertError("缺少 OCR 组件（rapidocr-onnxruntime）") from exc
    engine = RapidOCR()
    document = _open_pdf(source)
    output = Path(output)
    result_doc = pymupdf.open()
    try:
        total = document.page_count
        for index, page in enumerate(document):
            if should_cancel is not None and should_cancel():
                result_doc.close()
                _cancelled(should_cancel, output)
            result_doc.insert_pdf(document, from_page=index, to_page=index)
            target = result_doc[-1]
            pixmap = page.get_pixmap(dpi=200)
            import numpy as np

            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n)
            if pixmap.n == 4:
                image = image[:, :, :3]
            boxes, _ = engine(image)
            scale_x = page.rect.width / pixmap.width
            scale_y = page.rect.height / pixmap.height
            for box, text, _score in boxes or []:
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                x0, x1 = min(xs) * scale_x, max(xs) * scale_x
                y1 = max(ys) * scale_y
                height = (max(ys) - min(ys)) * scale_y
                if not text.strip() or height <= 0:
                    continue
                try:
                    target.insert_text(
                        (x0, y1 - height * 0.15), text,
                        fontname="china-s", fontsize=max(4, height * 0.85),
                        render_mode=3, overlay=True)
                except Exception:
                    continue
            _progress(progress, index + 1, total, f"OCR 第 {index + 1} 页")
        result_doc.save(str(output), garbage=4, deflate=True)
    finally:
        result_doc.close()
        document.close()
    return output


def unique_dir(path: Path) -> Path:
    """目录已存在就加 -2/-3，转换输出绝不覆盖已有结果。"""
    path = Path(path)
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def output_dir_default(source: Path) -> Path:
    return Path(source).resolve().parent


def data_converted_dir() -> Path:
    return Path(DATA_DIR)
