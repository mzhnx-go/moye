"""PDF 引擎：按页渲染位图、把笔迹烧成矢量折线另存新文件。

坐标一律 PDF 点（1pt = 1/72 英寸）。**唯一坐标系 = 显示/页框坐标**（即
MuPDF `page.rect` 那一套，PROGRESS §4.1「旋转口径」）：笔迹、搜索命中、
sidecar 全用它。只在两个 PDF I/O 边界换算——
- 搜索：`search_for` 给未旋转 rect，乘 `page.rotation_matrix` 换到显示；
- 导出：显示坐标的笔迹乘 `page.derotation_matrix` 换回未旋转再画。
"""
from __future__ import annotations

import os
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from sketchpad.core import noteassets
from sketchpad.core.notes import (NOTE_PAPERS, Note, NoteAttachment, NoteImage,
                                  TextBlock, text_block_has_format)
from sketchpad.core.strokes import (HIGHLIGHTER_OPACITY, TEXT_HIGHLIGHT, Point,
                                    Stroke, has_pressure, is_highlight,
                                    pressure_widths, ribbon_polygon,
                                    text_highlight_rects)
from sketchpad.pdf import richtext

# 笔记纸面视觉常量：数值必须与 sketchpad/ui/note_canvas.py 的 PAPER_BG /
# PAPER_LINE / LINE_GAP / MARGIN_X 保持一致。PDF 引擎不 import UI 模块，
# 这里保留一份独立副本，改纸面样式时两处一起改。
NOTE_PAPER_BG = {"dark": "#2b2c2f", "light": "#f6f1e6"}
NOTE_PAPER_LINE = {"dark": "#4a4b50", "light": "#d8cdb8"}
NOTE_LINE_GAP = 28.0
NOTE_MARGIN_X = 42.0

# 富内容的视觉参数（全部是设计坐标，落页面时乘 scale）
NOTE_TAG_WIDTH = 240.0
NOTE_TAG_HEIGHT = 22.0
NOTE_TAG_FILL = {"dark": "#3a3c40", "light": "#e7dfcd"}
NOTE_TAG_LINE = {"dark": "#5a5c63", "light": "#c4b79c"}
NOTE_TAG_TEXT = {"dark": "#d7dae0", "light": "#3a3a3c"}
NOTE_TAG_FONT = 9.0
NOTE_TAG_NAME_MAX = 40          # 附件标签里最多显示的字符数
NOTE_MISSING_FILL = "#d9d9d9"
NOTE_MISSING_LINE = "#9a9a9a"

# 锚点页上的笔记标记：14pt 蓝色圆 + 白色序号，盖住的内容尽量少
NOTE_MARKER_COLOR = "#2f6fdf"
NOTE_MARKER_RADIUS = 7.0


class PdfError(Exception):
    """PDF 打不开、页码越界、参数非法或导出被拒。"""


def _rgb(color: str) -> tuple[float, float, float]:
    """`#rrggbb` / `#rgb` → 0–1 三元组；认不出来就抛 PdfError，不猜颜色。"""
    text = str(color).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise PdfError(f"颜色格式非法：{color!r}")
    try:
        red, green, blue = (int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError as exc:
        raise PdfError(f"颜色格式非法：{color!r}") from exc
    return (red, green, blue)


class PdfSource:
    """一份只读 PDF；尺寸按旋转后的页面矩形算，关闭后只留 path。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._doc = None
        try:
            doc = pymupdf.open(str(self.path))
        except Exception as exc:
            # 缺文件、损坏、无权限都收成一句带路径的中文，绝不让调用方拿到裸异常
            raise PdfError(f"无法打开 PDF：{self.path}（{exc}）") from exc
        if doc.is_encrypted and doc.needs_pass:
            doc.close()
            raise PdfError(f"PDF 已加密，需要密码：{self.path}")
        self._doc = doc

    def _doc_or_raise(self):
        """取打开的文档；已关闭抛 PdfError。"""
        doc = self._doc
        if doc is None:
            raise PdfError(f"PDF 已关闭：{self.path}")
        return doc

    def _page(self, index: int):
        """取页对象；已关闭或页码越界都抛 PdfError。"""
        doc = self._doc_or_raise()
        if isinstance(index, bool) or not isinstance(index, int):
            raise PdfError(f"页码必须是整数：{index!r}")
        if not 0 <= index < doc.page_count:
            raise PdfError(f"页码越界：{index}（共 {doc.page_count} 页）")
        return doc[index]

    @property
    def page_count(self) -> int:
        return self._doc_or_raise().page_count

    def get_toc(self) -> list[tuple[int, str, int]]:
        """目录条目 (层级, 标题, 0 起页码)；无效页码钳进 0..page_count-1，无目录为空。"""
        doc = self._doc_or_raise()
        if doc.page_count == 0:
            return []
        last = doc.page_count - 1
        return [
            (int(level), str(title), min(max(int(page) - 1, 0), last))
            for level, title, page in doc.get_toc()
        ]

    def page_size(self, index: int) -> tuple[float, float]:
        """旋转后的页面尺寸（PDF 点）。"""
        rect = self._page(index).rect
        return (rect.width, rect.height)

    def rotation(self, index: int) -> int:
        """该页的显示旋转角（0/90/180/270），写进 sidecar 的 `r`。"""
        return int(self._page(index).rotation) % 360

    def render(self, index: int, zoom: float) -> QImage:
        """渲染整页为 RGB32 位图；无内容处是白底。"""
        page = self._page(index)
        if not isinstance(zoom, (int, float)) or not zoom > 0:
            raise PdfError(f"缩放必须大于 0：{zoom!r}")
        rect = page.rect
        target_w = max(1, round(rect.width * zoom))
        target_h = max(1, round(rect.height * zoom))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        # MuPDF 的 RGB 样本是 3 字节/像素，QImage 只认 Format_RGB888 + stride；copy 脱离 pix 生命周期
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
        img = img.copy().convertToFormat(QImage.Format_RGB32)
        if img.width() != target_w or img.height() != target_h:
            # MuPDF 取整与 Python round 在 .5 处可能差 1px；按契约钳到目标尺寸
            img = img.scaled(
                target_w, target_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            ).convertToFormat(QImage.Format_RGB32)
        return img

    def page_words(self, index: int) -> list[tuple[str, float, float, float, float, int, int]]:
        """页内全部词，按阅读顺序返回 `(文本, x0, y0, x1, y1, 块号, 行号)`。

        矩形是**显示坐标**（旋转页乘 `page.rotation_matrix`），与视图、搜索命中
        同一套坐标；选择和复制都基于这份数据。空白页/纯图片页返回空列表。
        """
        page = self._page(index)
        matrix = page.rotation_matrix
        words: list[tuple[str, float, float, float, float, int, int]] = []
        for raw in page.get_text("words") or []:
            rect = pymupdf.Rect(raw[:4]) * matrix
            words.append((str(raw[4]), float(rect.x0), float(rect.y0),
                          float(rect.x1), float(rect.y1),
                          int(raw[5]), int(raw[6])))
        return words

    def page_chars(self, index: int) -> list[tuple[str, float, float, float, float, int, int]]:
        """页内全部字符，按阅读顺序返回 `(字符, x0, y0, x1, y1, 块号, 行号)`。

        MuPDF 的 "words" 以空格切词，中文没有空格，一整行会被当成一个词，
        拖选就是"一选一大段"。这里用 rawdict 的逐字符 bbox，中文可以一个字
        一个字选；空白字符不返回（复制时按字符间距补空格/换行）。
        矩形是**显示坐标**（旋转页乘 `page.rotation_matrix`），与视图同一套。
        """
        page = self._page(index)
        matrix = page.rotation_matrix
        chars: list[tuple[str, float, float, float, float, int, int]] = []
        raw = page.get_text("rawdict") or {}
        for block_no, block in enumerate(raw.get("blocks", [])):
            if block.get("type") != 0:          # 1 = 图片块，没有字符
                continue
            for line_no, line in enumerate(block.get("lines", [])):
                for span in line.get("spans", []):
                    for char in span.get("chars", []):
                        text = str(char.get("c", ""))
                        if not text or text.isspace():
                            continue
                        rect = pymupdf.Rect(char["bbox"]) * matrix
                        chars.append((text, float(rect.x0), float(rect.y0),
                                      float(rect.x1), float(rect.y1),
                                      block_no, line_no))
        return chars

    def search_page(self, index: int, text: str) -> list[tuple[float, float, float, float]]:
        """页内文本搜索（不区分大小写），返回**显示坐标**下的矩形 (x0, y0, x1, y1)，单位 PDF 点。

        MuPDF 的 `search_for` 一律按未旋转页给 rect；这里乘 `page.rotation_matrix`
        换到显示/页框坐标，与视图、笔迹、sidecar 同一套（PROGRESS §4.1）。
        """
        page = self._page(index)
        if not isinstance(text, str):
            raise PdfError(f"搜索文本必须是字符串：{text!r}")
        if not text.strip():
            return []
        # search_for 对空串返回 None；空白已提前挡掉，这里仍兜一层。
        # rotation_matrix 对 0° 是单位阵，非旋转页结果与之前逐值相同。
        matrix = page.rotation_matrix
        return [
            (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
            for rect in (raw * matrix for raw in page.search_for(text) or [])
        ]

    def search_document(
        self, text: str, max_hits: int = 500
    ) -> list[tuple[int, tuple[float, float, float, float]]]:
        """全文按页顺序搜索，最多 max_hits 条；每条是 (0 起页码, 命中矩形)。"""
        doc = self._doc_or_raise()
        if not isinstance(text, str):
            raise PdfError(f"搜索文本必须是字符串：{text!r}")
        if not text.strip() or max_hits <= 0:
            return []
        hits: list[tuple[int, tuple[float, float, float, float]]] = []
        for index in range(doc.page_count):
            for rect in self.search_page(index, text):
                hits.append((index, rect))
                if len(hits) >= max_hits:
                    return hits
        return hits

    def close(self) -> None:
        doc, self._doc = self._doc, None
        if doc is not None:
            doc.close()

    def __enter__(self) -> "PdfSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def _fill_polygon(page, points: list[pymupdf.Point], color: tuple[float, float, float],
                  opacity: float) -> None:
    """填充闭合多边形（无描边）。

    PyMuPDF 2.0 去掉了 `Page.draw_polygon`，这里优先用它、没有就用
    `draw_polyline(..., closePath=True)`——两者的落盘结果都是 fill 路径。
    """
    polygon = getattr(page, "draw_polygon", None)
    if polygon is not None:
        polygon(points, color=None, fill=color, fill_opacity=opacity, width=0)
    else:
        page.draw_polyline(points, color=None, fill=color, fill_opacity=opacity,
                           width=0, closePath=True)


def _draw_pressure_stroke(page, points: list[Point], widths: list[float],
                          color: tuple[float, float, float],
                          opacity: float) -> None:
    """压感笔迹：变宽 ribbon 多边形填充 + 首尾半宽实心圆（圆头）。

    `points` 是显示坐标、`widths` 是逐点完整线宽（PDF 点口径）；调用方已把
    线宽换算好，这里只乘 derotation 换回未旋转页坐标。
    """
    matrix = page.derotation_matrix
    if len(points) == 1:
        # 单点压感：按该点宽度画实心圆
        center = pymupdf.Point(float(points[0][0]), float(points[0][1])) * matrix
        page.draw_circle(center, radius=widths[0] / 2.0, color=color, fill=color,
                         fill_opacity=opacity, stroke_opacity=opacity)
        return
    polygon = ribbon_polygon(points, widths)
    if polygon is None:      # 长度对不上，宁可不画也不画错位
        return
    _fill_polygon(page,
                  [pymupdf.Point(float(x), float(y)) * matrix for x, y in polygon],
                  color, opacity)
    # 端点补圆头：半径 = 端点半宽，与 ribbon 同色同透明度
    for point, width in ((points[0], widths[0]), (points[-1], widths[-1])):
        center = pymupdf.Point(float(point[0]), float(point[1])) * matrix
        page.draw_circle(center, radius=float(width) / 2.0, color=None, fill=color,
                         fill_opacity=opacity, width=0)


def _draw_stroke(page, page_index: int, stroke: Stroke) -> None:
    """一笔落到页面上：≥2 点画折线，1 点画实心圆，空笔迹无事可画。

    笔迹存的是显示坐标；`draw_*` 吃的是未旋转页坐标，故逐点乘
    `page.derotation_matrix` 换回去（PROGRESS §4.1）。线宽/半径不随旋转变。
    荧光笔用 `HIGHLIGHTER_OPACITY` 半透明落墨，底下的字仍看得见。

    带压感且不是选字荧光时改画变宽 ribbon（见 `_draw_pressure_stroke`）；
    无压感完全走原来的折线/圆点/矩形路径，导出结果逐值不变。
    """
    if not stroke.points:
        return
    matrix = page.derotation_matrix
    color = _rgb(stroke.color)
    if stroke.tool == TEXT_HIGHLIGHT:
        # 选字荧光：每两个点一个矩形；页面可能旋转，取四角变换后的包围盒
        for x0, y0, x1, y1 in text_highlight_rects(stroke):
            corners = [pymupdf.Point(float(x), float(y)) * matrix
                       for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
            rect = pymupdf.Rect(min(p.x for p in corners), min(p.y for p in corners),
                                max(p.x for p in corners), max(p.y for p in corners))
            page.draw_rect(rect, color=None, fill=color, width=0,
                           fill_opacity=HIGHLIGHTER_OPACITY)
        return
    points = [pymupdf.Point(float(x), float(y)) * matrix for x, y in stroke.points]
    width = float(stroke.width)
    opacity = HIGHLIGHTER_OPACITY if stroke.tool == "highlighter" else 1.0
    if has_pressure(stroke):
        _draw_pressure_stroke(page, list(stroke.points), pressure_widths(stroke),
                              color, opacity)
        return
    if len(points) == 1:
        page.draw_circle(points[0], radius=width / 2, color=color, fill=color,
                         fill_opacity=opacity, stroke_opacity=opacity)
    else:
        page.draw_polyline(points, color=color, width=width,
                           stroke_opacity=opacity)


def _highlights_first(strokes: list[Stroke]) -> list[Stroke]:
    """荧光笔先画、画笔后画（稳定排序，不改调用方的列表）。

    半透明荧光必须在墨迹底下，否则导出后高亮会蒙住同一页上的字和笔迹。
    """
    return sorted(strokes, key=lambda s: 0 if is_highlight(s) else 1)


def export_with_strokes(
    src: PdfSource, pages_strokes: list[list[Stroke]], out_path: Path
) -> Path:
    """把每页笔迹画进副本再保存；源文件只读，导出绝不覆盖它。"""
    out = Path(out_path)
    # normcase：Windows 文件名大小写不敏感，"SRC.PDF" 写回 "src.pdf" 也得拦住
    if os.path.normcase(str(out.resolve())) == os.path.normcase(str(src.path.resolve())):
        raise PdfError(f"导出不能覆盖原文件：{out}")
    try:
        doc = pymupdf.open(str(src.path))
    except Exception as exc:
        # 源文件可能已被删/改名；这里重新打开，不复用 src 里可能已关闭的句柄
        raise PdfError(f"无法打开 PDF：{src.path}（{exc}）") from exc
    try:
        if doc.is_encrypted and doc.needs_pass:
            raise PdfError(f"PDF 已加密，需要密码：{src.path}")
        if len(pages_strokes) != doc.page_count:
            raise PdfError(
                f"页数不一致：笔迹 {len(pages_strokes)} 页，PDF {doc.page_count} 页"
            )
        for index, strokes in enumerate(pages_strokes):
            page = doc[index]
            for stroke in _highlights_first(strokes):
                _draw_stroke(page, index, stroke)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            doc.save(str(out))
        except Exception as exc:
            raise PdfError(f"导出失败：{out}（{exc}）") from exc
    finally:
        doc.close()
    return out


# ---- 笔记页导出 ----


def _validate_notes(pages_notes: list[list[Note]]) -> None:
    """导出前先检查所有笔记尺寸：坏尺寸直接报错，别等写半个文件才发现。"""
    for page_index, notes in enumerate(pages_notes):
        for number, note in enumerate(notes, 1):
            where = f"第 {page_index + 1} 页第 {number} 张笔记"
            try:
                width = float(note.w)
                height = float(note.h)
            except (TypeError, ValueError) as exc:
                raise PdfError(
                    f"{where}尺寸非法：{note.w!r}×{note.h!r}"
                ) from exc
            if width <= 0.0 or height <= 0.0:
                raise PdfError(f"{where}尺寸必须大于 0：{width}×{height}")


def _draw_note_marker(page, note: Note, number: int) -> None:
    """在锚点页上画 14pt 蓝色圆 + 白色序号（序号从 1 起）。

    锚点是**显示坐标**；`draw_*` / `insert_text` 吃未旋转页坐标，所以圆心和
    文字起点都先乘 `page.derotation_matrix`。旋转页不要求序号跟着转，只要求
    标记落在锚点位置。
    """
    center = pymupdf.Point(float(note.x), float(note.y)) * page.derotation_matrix
    color = _rgb(NOTE_MARKER_COLOR)
    page.draw_circle(center, radius=NOTE_MARKER_RADIUS, color=color, fill=color,
                     width=0.5)
    label = str(number)
    font_size = 7.0
    text_width = pymupdf.get_text_length(label, fontsize=font_size)
    origin = pymupdf.Point(center.x - text_width / 2.0,
                           center.y + font_size * 0.35)
    page.insert_text(origin, label, fontsize=font_size, color=(1.0, 1.0, 1.0))


def _scale_strokes(strokes: list[Stroke], scale: float) -> list[Stroke]:
    """笔记设计坐标 → 笔记页坐标：点与线宽同乘 scale，tool/压力保留；荧光先画。

    压力是无量纲比值（0..1），不随缩放变化，原样带过去。
    """
    scaled = [
        Stroke([(x * scale, y * scale) for x, y in stroke.points],
               stroke.color, float(stroke.width) * scale, stroke.tool,
               None if stroke.pressures is None else list(stroke.pressures))
        for stroke in strokes
    ]
    return _highlights_first(scaled)


def _note_font(text: str) -> str:
    """含非 ASCII 字符时用内置中文简体的 CJK 字体，纯 ASCII 用 Helvetica。

    Base-14 字体没有 CJK 字形，直接写中文会变成方框；而 CJK 字体对拉丁文同样
    能排，所以只在需要时才换，避免无谓地嵌入一套字体子集。
    """
    return "china-s" if any(ord(ch) > 127 for ch in text) else "helv"


def _rect_from_design(x: float, y: float, w: float, h: float,
                      scale: float, page) -> pymupdf.Rect | None:
    """设计坐标 × scale → 页面矩形；退化（宽高 ≤0、出了页面）返回 None。

    富内容可能来自手改的 sidecar 或旧的半成品，渲染层只求"别崩"：画不出来
    就跳过，不抛异常打断整份导出。矩形裁剪进页面，避免超大 rect 撑坏页面框。
    """
    try:
        width = float(w) * scale
        height = float(h) * scale
        x0 = float(x) * scale
        y0 = float(y) * scale
    except (TypeError, ValueError):
        return None
    if width <= 0.0 or height <= 0.0 or scale <= 0.0:
        return None
    bounds = page.rect
    left = min(max(x0, bounds.x0), bounds.x1)
    top = min(max(y0, bounds.y0), bounds.y1)
    right = min(max(x0 + width, bounds.x0), bounds.x1)
    bottom = min(max(y0 + height, bounds.y0), bounds.y1)
    if right - left <= 0.0 or bottom - top <= 0.0:
        return None
    return pymupdf.Rect(left, top, right, bottom)


def _missing_asset_placeholder(page, rect: pymupdf.Rect, label: str = "") -> None:
    """资源文件丢了：画一个浅灰占位框，导出继续（绝不因缺附件失败）。"""
    page.draw_rect(rect, color=_rgb(NOTE_MISSING_LINE),
                   fill=_rgb(NOTE_MISSING_FILL), width=1.0)
    if label:
        font_size = max(5.0, min(rect.height * 0.6, 18.0))
        try:
            page.insert_text((rect.x0 + 3.0, rect.y0 + rect.height * 0.7), label,
                             fontsize=font_size, color=_rgb("#6b6b6b"))
        except Exception:
            pass        # 占位框已经画出来了，文字标不出来也不影响导出


def _draw_note_image(page, image: NoteImage, scale: float,
                     pdf_path: Path) -> None:
    """一张笔记图片：按设计坐标铺到笔记页；文件缺失画占位框，不抛。"""
    rect = _rect_from_design(image.x, image.y, image.w, image.h, scale, page)
    if rect is None:
        return
    try:
        path = noteassets.asset_path(pdf_path, image.asset)
    except ValueError:
        _missing_asset_placeholder(page, rect, "!")
        return
    if not path.is_file():
        _missing_asset_placeholder(page, rect, "!")
        return
    try:
        # 保持宽高比：设计框与图片比例不同时留边，不拉伸变形
        page.insert_image(rect, filename=str(path), keep_proportion=True)
    except Exception:
        # 损坏/格式不支持：同样降级成占位框，导出不能因此失败
        _missing_asset_placeholder(page, rect)


def _rich_text_png(block: TextBlock, width: float, max_height: float):
    """带格式的文字 → `(PNG 字节, 设计坐标高度)`；渲染不可用返回 None。

    格式判断在调用方做；这里只把模型字段搬给 `pdf.richtext`，让"导出用的
    排版"与"屏幕投影用的排版"（QTextDocument + 同一字体/字号/限宽）完全同源。
    """
    try:
        _rgb(block.color)               # 手改 sidecar 的坏颜色：退回默认色
        color = block.color
    except PdfError:
        color = "#e8eaed"
    return richtext.render_block_png(
        block.text,
        width=width,
        size=float(block.size),
        color=color,
        bold=getattr(block, "bold", False),
        italic=getattr(block, "italic", False),
        underline=getattr(block, "underline", False),
        max_height=max_height,
    )


def _insert_png(page, rect: pymupdf.Rect, png: bytes) -> bool:
    """把透明 PNG 铺进页面矩形；失败返回 False，让调用方回退纯文本。"""
    try:
        page.insert_image(rect, stream=png, keep_proportion=False)
        return True
    except Exception:
        return False


def _draw_note_text(page, block: TextBlock, scale: float) -> None:
    """一块笔记文字：`insert_textbox` 自动换行，矩形给足剩余页高。

    溢出不崩：`insert_textbox` 排不下会返回负数，这里不把它当错误——
    用户宁可看到被截断的文字，也不想整份导出失败。

    带粗/斜/下划线的文字改走 QTextDocument → 透明 PNG（约 300dpi）贴回
    同一设计矩形：这样中文粗斜下划线与屏幕一致，代价是这块不再是文本层。
    """
    if not block.text:
        return
    font_size = float(block.size) * scale
    if font_size <= 0.0:
        return
    x = float(block.x) * scale
    y = float(block.y) * scale
    width = float(block.w) * scale
    if width <= 0.0 or x >= page.rect.width:
        return
    height = page.rect.height - y
    if height <= font_size * 0.5:
        return
    if scale > 0.0 and text_block_has_format(block):
        rendered = _rich_text_png(block, float(block.w),
                                  page.rect.height / scale - float(block.y))
        if rendered is not None:
            png, rich_height = rendered
            rect = _rect_from_design(block.x, block.y, block.w, rich_height,
                                     scale, page)
            if rect is not None and _insert_png(page, rect, png):
                return
        # 渲染不可用（没有 QGuiApplication 等）：回退纯文本，格式丢了但内容还在
    # 手改的 sidecar 可能给出负坐标：钳进页面，rect 非法会让 insert_textbox 抛错
    left = max(x, page.rect.x0)
    right = min(x + width, page.rect.x1)
    top = max(y, page.rect.y0)
    bottom = min(y + height, page.rect.y1)
    if right - left <= 0.0 or bottom - top < font_size * 0.5:
        return
    rect = pymupdf.Rect(left, top, right, bottom)
    try:
        color = _rgb(block.color)
    except PdfError:
        color = _rgb("#e8eaed")
    fontname = _note_font(block.text)
    try:
        page.insert_textbox(rect, block.text, fontsize=font_size, color=color,
                            fontname=fontname)
    except Exception:
        try:
            page.insert_textbox(rect, block.text, fontsize=font_size, color=color)
        except Exception:
            pass


def _draw_note_attachment(page, attachment: NoteAttachment, scale: float,
                          paper: str) -> None:
    """附件的视觉标记：圆角小方块 + 截断后的文件名（不嵌入附件本体）。"""
    rect = _rect_from_design(attachment.x, attachment.y, NOTE_TAG_WIDTH,
                             NOTE_TAG_HEIGHT, scale, page)
    if rect is None:
        return
    radius = 4.0 * scale
    # draw_rect 的 radius 是"短边的比例"（0..0.5），不是绝对长度
    radius_fraction = min(0.5, max(0.01, radius / max(1e-6, min(rect.width,
                                                                rect.height))))
    page.draw_rect(rect, color=_rgb(NOTE_TAG_LINE[paper]),
                   fill=_rgb(NOTE_TAG_FILL[paper]), width=0.6,
                   radius=radius_fraction)
    label = str(attachment.name)[:NOTE_TAG_NAME_MAX]
    if not label:
        return
    font_size = max(1.0, NOTE_TAG_FONT * scale)
    try:
        page.insert_text((rect.x0 + 4.0 * scale,
                          rect.y0 + (rect.height + font_size * 0.72) / 2.0),
                         label, fontsize=font_size,
                         color=_rgb(NOTE_TAG_TEXT[paper]),
                         fontname=_note_font(label))
    except Exception:
        pass        # 标签框已在，文件名排不出来也不该中断导出


def _append_note_page(out_doc, note: Note, page_width: float,
                      pdf_path: Path) -> None:
    """按锚点页宽度追加一张笔记页。

    层序（自下而上）：纸面/横线 → 图片 → 文字 → 附件标签 → 笔迹。
    笔迹永远最上层，手写注释不会被后加的内容盖住。
    """
    paper = note.paper if note.paper in NOTE_PAPERS else "dark"
    width = float(page_width)
    scale = width / float(note.w)
    height = width * float(note.h) / float(note.w)
    page = out_doc.new_page(width=width, height=height)

    background = _rgb(NOTE_PAPER_BG[paper])
    line_color = _rgb(NOTE_PAPER_LINE[paper])
    page.draw_rect(page.rect, color=None, fill=background, width=0)

    gap = NOTE_LINE_GAP * scale
    y = gap
    while y < height:
        page.draw_line(pymupdf.Point(0.0, y), pymupdf.Point(width, y),
                       color=line_color, width=0.5)
        y += gap
    margin_x = NOTE_MARGIN_X * scale
    page.draw_line(pymupdf.Point(margin_x, 0.0),
                   pymupdf.Point(margin_x, height), color=line_color, width=0.5)

    # 富内容（自下而上：图片 → 文字 → 附件标签）
    for image in getattr(note, "images", None) or ():
        _draw_note_image(page, image, scale, pdf_path)
    for block in getattr(note, "texts", None) or ():
        _draw_note_text(page, block, scale)
    for attachment in getattr(note, "attachments", None) or ():
        _draw_note_attachment(page, attachment, scale, paper)

    # 新页没有旋转，derotation_matrix 是单位阵，_draw_stroke 直接可用
    for stroke in _scale_strokes(note.strokes, scale):
        _draw_stroke(page, 0, stroke)


def _page_rect_from_display(page, x: float, y: float, w: float,
                            h: float) -> pymupdf.Rect | None:
    """页面级富内容的显示坐标 → 未旋转页坐标的外接矩形（旋转页取四角包围盒）。"""
    try:
        x, y, w, h = float(x), float(y), float(w), float(h)
    except (TypeError, ValueError):
        return None
    if w <= 0.0 or h <= 0.0:
        return None
    matrix = page.derotation_matrix
    corners = [pymupdf.Point(px, py) * matrix
               for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
    rect = pymupdf.Rect(min(p.x for p in corners), min(p.y for p in corners),
                        max(p.x for p in corners), max(p.y for p in corners))
    bounds = page.rect
    clipped = pymupdf.Rect(max(rect.x0, bounds.x0), max(rect.y0, bounds.y0),
                           min(rect.x1, bounds.x1), min(rect.y1, bounds.y1))
    if clipped.width <= 0.0 or clipped.height <= 0.0:
        return None
    return clipped


def _draw_page_text(page, block: TextBlock) -> None:
    """页面级文字：与笔记文字同一套换行/字体策略，坐标是页面点（scale=1）。

    带格式时同笔记文字：QTextDocument 排版 → 透明 PNG 贴进同一页面矩形，
    不再进入可搜索文本层（保真换来的取舍）；无格式仍走 insert_textbox。
    """
    if not block.text:
        return
    font_size = float(block.size)
    if font_size <= 0.0:
        return
    height = max(font_size * 1.4, page.rect.height - float(block.y))
    if text_block_has_format(block):
        rendered = _rich_text_png(block, float(block.w),
                                  page.rect.height - float(block.y))
        if rendered is not None:
            png, rich_height = rendered
            rect = _page_rect_from_display(page, block.x, block.y, block.w,
                                           rich_height)
            if rect is not None and _insert_png(page, rect, png):
                return
        # 渲染不可用：回退纯文本路径，格式丢了但内容不丢
    rect = _page_rect_from_display(page, block.x, block.y, block.w, height)
    if rect is None:
        return
    try:
        color = _rgb(block.color)
    except PdfError:
        color = _rgb("#e8eaed")
    fontname = _note_font(block.text)
    try:
        page.insert_textbox(rect, block.text, fontsize=font_size, color=color,
                            fontname=fontname)
    except Exception:
        try:
            page.insert_textbox(rect, block.text, fontsize=font_size, color=color)
        except Exception:
            pass


def _draw_page_image(page, image: NoteImage, pdf_path: Path) -> None:
    """页面级图片：资源从 assets 取；缺失/损坏画占位框，不中断导出。"""
    rect = _page_rect_from_display(page, image.x, image.y, image.w, image.h)
    if rect is None:
        return
    try:
        path = noteassets.asset_path(pdf_path, image.asset)
    except ValueError:
        _missing_asset_placeholder(page, rect, "!")
        return
    if not path.is_file():
        _missing_asset_placeholder(page, rect, "!")
        return
    try:
        page.insert_image(rect, filename=str(path), keep_proportion=True)
    except Exception:
        _missing_asset_placeholder(page, rect)


def export_with_notes(
    src: PdfSource,
    pages_strokes: list[list[Stroke]],
    pages_notes: list[list[Note]],
    out_path: Path,
    pages_texts: list[list[TextBlock]] | None = None,
    pages_images: list[list[NoteImage]] | None = None,
    assets_path: Path | None = None,
) -> Path:
    """按原页序重建 PDF：每页原内容 + 文字/图片 + 笔迹 + 锚点标记，笔记页插在锚点页后。

    页序为「原页 0 → 页 0 的笔记页 → 原页 1 → 页 1 的笔记页 …」。源文件只读，
    导出绝不覆盖它；`pages_strokes` / `pages_notes` 都必须与源页数一致。

    页面级文字/图片（v4）可选传入；资源目录默认取 `src.path`，Word 传原 docx
    路径即可。资源缺失或损坏只画占位框，绝不让导出失败。
    """
    out = Path(out_path)
    # normcase：Windows 文件名大小写不敏感，"SRC.PDF" 写回 "src.pdf" 也得拦住
    if os.path.normcase(str(out.resolve())) == os.path.normcase(str(src.path.resolve())):
        raise PdfError(f"导出不能覆盖原文件：{out}")
    try:
        src_doc = pymupdf.open(str(src.path))
    except Exception as exc:
        # src 里可能已经关闭；重新打开，与 export_with_strokes 同一套错误口径
        raise PdfError(f"无法打开 PDF：{src.path}（{exc}）") from exc
    out_doc = None
    try:
        if src_doc.is_encrypted and src_doc.needs_pass:
            raise PdfError(f"PDF 已加密，需要密码：{src.path}")
        if len(pages_strokes) != src_doc.page_count:
            raise PdfError(
                f"页数不一致：笔迹 {len(pages_strokes)} 页，PDF {src_doc.page_count} 页"
            )
        if len(pages_notes) != src_doc.page_count:
            raise PdfError(
                f"页数不一致：笔记 {len(pages_notes)} 页，PDF {src_doc.page_count} 页"
            )
        if pages_texts is not None and len(pages_texts) != src_doc.page_count:
            raise PdfError(
                f"页数不一致：文字 {len(pages_texts)} 页，PDF {src_doc.page_count} 页"
            )
        if pages_images is not None and len(pages_images) != src_doc.page_count:
            raise PdfError(
                f"页数不一致：图片 {len(pages_images)} 页，PDF {src_doc.page_count} 页"
            )
        _validate_notes(pages_notes)
        asset_root = Path(assets_path) if assets_path is not None else src.path
        out_doc = pymupdf.open()
        for index in range(src_doc.page_count):
            out_doc.insert_pdf(src_doc, from_page=index, to_page=index)
            page = out_doc[-1]
            for image in (pages_images[index] if pages_images is not None else ()):
                _draw_page_image(page, image, asset_root)
            for block in (pages_texts[index] if pages_texts is not None else ()):
                _draw_page_text(page, block)
            for stroke in _highlights_first(pages_strokes[index]):
                _draw_stroke(page, index, stroke)
            notes = pages_notes[index]
            for number, note in enumerate(notes, 1):
                _draw_note_marker(page, note, number)
            width = src_doc[index].rect.width
            for note in notes:
                _append_note_page(out_doc, note, width, src.path)

        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_doc.save(str(out))
        except Exception as exc:
            raise PdfError(f"导出失败：{out}（{exc}）") from exc
    finally:
        if out_doc is not None:
            out_doc.close()
        src_doc.close()
    return out
