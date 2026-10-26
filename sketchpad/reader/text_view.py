"""Standard-only: 纯文本 / Markdown 阅读视图。

与 PDF 视图共用同一套主窗接口（信号、缩放、搜索高亮、阅读位置），
但内容只有一页：`page` 恒为 0，位置用文档纵向滚动比例表示。
编辑控件只读，工具类接口全部保留为空实现，主窗不必按 `kind` 分支。
"""
from __future__ import annotations

import math
from pathlib import Path

from markdown_it import MarkdownIt
from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QFrame, QPlainTextEdit, QTextBrowser, QTextEdit,
                               QVBoxLayout, QWidget)

from sketchpad.core.settings import HIGHLIGHTER_COLOR, Settings
from sketchpad.ui import theme

MIN_ZOOM = 0.25
MAX_ZOOM = 4.0
MAX_TEXT_WIDTH = 860                 # 宽屏下正文最大宽度（像素），再多只是空边
DOC_MARGIN = 18
HIT_FILL = QColor(255, 214, 0, 70)          # 搜索命中：半透明黄（与 PDF 视图同色）
HIT_CURRENT_FILL = QColor(255, 152, 0, 170)  # 当前命中：橙黄

_MARKDOWN = (MarkdownIt("commonmark", {"html": False})
             .enable("table")
             .enable("strikethrough"))


def _markdown_css() -> str:
    """HTML 内联样式：字号一律用 em / 百分比，跟着文档默认字体一起缩放。"""
    return f"""
    body {{ font-size: 100%; line-height: 1.6; color: {theme.TEXT};
            max-width: {MAX_TEXT_WIDTH}px; }}
    p {{ margin: 0.5em 0; }}
    h1, h2, h3, h4, h5, h6 {{ font-weight: 600; margin: 0.8em 0 0.4em 0; }}
    h1 {{ font-size: 1.75em; }}
    h2 {{ font-size: 1.45em; }}
    h3 {{ font-size: 1.25em; }}
    h4 {{ font-size: 1.1em; }}
    h5 {{ font-size: 1em; }}
    h6 {{ font-size: 0.95em; color: {theme.TEXT_DIM}; }}
    a {{ color: {theme.ACCENT}; }}
    code {{ font-family: Consolas; font-size: 0.92em; background-color: {theme.SURFACE}; }}
    pre {{ font-family: Consolas; background-color: #262629; padding: 0.6em; }}
    blockquote {{ margin: 0.6em 0; padding-left: 0.9em;
                  border-left: 3px solid {theme.BORDER}; color: {theme.TEXT_DIM}; }}
    ul, ol {{ margin: 0.4em 0; }}
    li {{ margin: 0.15em 0; }}
    table {{ border-collapse: collapse; margin: 0.6em 0; }}
    th, td {{ border: 1px solid {theme.BORDER}; padding: 0.3em 0.7em; }}
    th {{ background-color: {theme.SURFACE}; }}
    hr {{ border: 1px solid {theme.BORDER}; }}
    """


def _decode(raw: bytes) -> tuple[str, str]:
    """按 BOM / UTF-8 / GB18030 / UTF-16 / Latin-1 的顺序识别编码。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    return raw.decode("latin-1"), "latin-1"


class TextViewer(QWidget):
    """TXT/Markdown 阅读器：一个只读内嵌编辑器 + 与 PdfView 对齐的信号面。"""

    kind = "text"
    SUFFIXES = (".txt", ".md", ".markdown")

    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    changed = Signal()
    strokesChanged = Signal()
    selectionCopied = Signal(int)
    notePlacementRequested = Signal(int, float, float)
    noteRequested = Signal(int, str)
    noteDeleteRequested = Signal(int, str)
    notesChanged = Signal(int)
    noteContextRequested = Signal(int, str, QPoint)

    def __init__(self, settings: Settings | None = None, parent=None) -> None:
        super().__init__(parent)
        settings = settings or Settings()
        self.tool = "select"
        self.color = settings.color
        self.highlighter_color = getattr(settings, "highlighter_color",
                                         HIGHLIGHTER_COLOR)
        self.width = float(settings.width)
        self.page_mode = getattr(settings, "page_mode", "normal")
        self.ink_smoothing = getattr(settings, "ink_smoothing", "low")
        self.pressure_enabled = getattr(settings, "pressure_enabled", True)

        self._path: Path | None = None
        self._zoom = 1.0
        self._markdown = False
        self.encoding = "utf-8"
        self._hit_selections: list[QTextEdit.ExtraSelection] = []
        self._foreign_selections: list[QTextEdit.ExtraSelection] | None = None
        self._match_length = 1

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._editor = self._create_editor(markdown=False)
        self._layout.addWidget(self._editor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocusProxy(self._editor)

        self._base_font = QFont(self._editor.font())
        size = self._base_font.pointSizeF()
        self._base_point_size = size if size > 0 else 10.0

    # --- 构建 ---

    def _create_editor(self, *, markdown: bool):
        if markdown:
            editor = QTextBrowser(self)
            editor.setOpenExternalLinks(True)
            editor.setLineWrapMode(QTextEdit.WidgetWidth)
        else:
            editor = QPlainTextEdit(self)
            editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        editor.setReadOnly(True)
        editor.setFrameShape(QFrame.NoFrame)
        editor.setAutoFillBackground(False)
        editor.viewport().setAutoFillBackground(False)
        palette = editor.palette()
        palette.setColor(QPalette.Base, Qt.transparent)
        palette.setColor(QPalette.Window, Qt.transparent)
        palette.setColor(QPalette.Text, QColor(theme.TEXT))
        editor.setPalette(palette)
        editor.document().setDocumentMargin(DOC_MARGIN)
        editor.installEventFilter(self)
        return editor

    def _replace_editor(self, *, markdown: bool) -> None:
        if isinstance(self._editor, QTextBrowser) == markdown:
            return
        old = self._editor
        self._layout.removeWidget(old)
        old.deleteLater()
        self._editor = self._create_editor(markdown=markdown)
        self._layout.addWidget(self._editor)
        self.setFocusProxy(self._editor)
        self._hit_selections = []
        self._foreign_selections = None
        self._apply_font()

    def _apply_font(self) -> None:
        font = QFont(self._base_font)
        font.setPointSizeF(self._base_point_size * self._zoom)
        self._editor.setFont(font)
        # QTextBrowser 的 HTML 相对字号（em/%）按文档默认字体解析，必须一起改
        self._editor.document().setDefaultFont(font)

    # --- 打开 / 关闭 ---

    def open(self, path: Path, *, ignore_sidecar: bool = False,
             document_override=None) -> None:
        """读取文本；ignore_sidecar/document_override 仅为对齐 PdfView 的调用签名。"""
        path = Path(path)
        raw = path.read_bytes()                 # 读不了直接抛 OSError，旧内容保持原样
        text, encoding = _decode(raw)
        markdown = path.suffix.lower() in (".md", ".markdown")
        self._replace_editor(markdown=markdown)
        self.clear_hits()
        self._path = path
        self.encoding = encoding
        self._markdown = markdown
        if markdown:
            body = _MARKDOWN.render(text)
            self._editor.setHtml(
                f"<html><head><style>{_markdown_css()}</style></head>"
                f"<body>{body}</body></html>")
        else:
            self._editor.setPlainText(text)
        self._apply_font()
        self._editor.verticalScrollBar().setValue(0)
        self.pageChanged.emit(0)
        self.zoomChanged.emit(self._zoom)
        self.changed.emit()

    def close_document(self) -> None:
        self.clear_hits()
        self._editor.clear()
        self._path = None
        self.pageChanged.emit(0)

    @property
    def pdf_path(self) -> Path | None:
        return self._path

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def source(self):
        return None

    @property
    def document(self):
        return None

    def text(self) -> str:
        return self._editor.toPlainText()

    # --- 大纲 / 搜索 ---

    def outline(self) -> list[tuple[int, str, int]]:
        if not self._markdown:
            return []
        result: list[tuple[int, str, int]] = []
        document = self._editor.document()
        block = document.begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()  # Qt 6 从 h1-h6 带出的层级
            title = block.text().strip()
            if level > 0 and title:
                result.append((level, title, block.position()))
            block = block.next()
        return result

    def search(self, text: str, max_hits: int = 500) -> list[tuple[int, int]]:
        if not text or max_hits <= 0:
            return []
        self._match_length = len(text)
        document = self._editor.document()
        original = QTextCursor(self._editor.textCursor())
        flags = QTextDocument.FindFlags()       # 空 flags = 不区分大小写
        hits: list[tuple[int, int]] = []
        cursor = QTextCursor(document)
        while len(hits) < max_hits:
            found = document.find(text, cursor, flags)
            if found.isNull():
                break
            hits.append((0, found.selectionStart()))
            cursor.setPosition(found.selectionEnd())
        self._editor.setTextCursor(original)    # 搜索不挪用户光标
        return hits

    def show_hits(self, page: int, offsets: list[int], current: int = 0) -> None:
        document = self._editor.document()
        if self._foreign_selections is None:
            self._foreign_selections = list(self._editor.extraSelections())
        limit = max(0, document.characterCount() - 1)
        selections: list[QTextEdit.ExtraSelection] = []
        current_cursor: QTextCursor | None = None
        for index, offset in enumerate(offsets):
            try:
                offset = int(offset)
            except (TypeError, ValueError):
                continue
            if not 0 <= offset < limit:
                continue
            cursor = QTextCursor(document)
            cursor.setPosition(offset)
            cursor.setPosition(min(limit, offset + max(1, self._match_length)),
                               QTextCursor.KeepAnchor)
            if cursor.selectionStart() == cursor.selectionEnd():
                # 文档末尾的单字符匹配选不中，退回前一个字
                cursor.setPosition(max(0, offset - 1))
                cursor.setPosition(offset, QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(
                HIT_CURRENT_FILL if index == current else HIT_FILL)
            selections.append(selection)
            if index == current:
                current_cursor = QTextCursor(cursor)
        self._hit_selections = selections
        self._editor.setExtraSelections(
            [*self._foreign_selections, *selections])
        if current_cursor is not None:
            saved = QTextCursor(self._editor.textCursor())
            self._editor.setTextCursor(current_cursor)
            self._editor.ensureCursorVisible()
            self._editor.setTextCursor(saved)

    def clear_hits(self) -> None:
        if not self._hit_selections and self._foreign_selections is None:
            return
        self._editor.setExtraSelections(list(self._foreign_selections or []))
        self._hit_selections = []
        self._foreign_selections = None

    # --- 位置 / 缩放 ---

    def goto_page(self, anchor: int) -> None:
        document = self._editor.document()
        try:
            position = int(anchor)
        except (TypeError, ValueError, OverflowError):
            position = 0
        position = min(max(position, 0), max(0, document.characterCount() - 1))
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        self._editor.setTextCursor(cursor)
        self._editor.ensureCursorVisible()

    def current_position(self) -> dict:
        bar = self._editor.verticalScrollBar()
        maximum = bar.maximum()
        offset = bar.value() / maximum if maximum > 0 else 0.0
        return {"page": 0, "offset": min(1.0, max(0.0, offset)),
                "zoom": self._zoom, "auto_fit": False}

    def restore_position(self, page, offset, zoom, auto_fit) -> None:
        try:
            zoom = float(zoom)
        except (TypeError, ValueError):
            zoom = 1.0
        if not math.isfinite(zoom):
            zoom = 1.0
        self.set_zoom(zoom)
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            offset = 0.0
        if not math.isfinite(offset):
            offset = 0.0
        offset = min(1.0, max(0.0, offset))
        bar = self._editor.verticalScrollBar()
        bar.setValue(round(offset * bar.maximum()))

    def scroll_to_top(self) -> None:
        self._editor.verticalScrollBar().setValue(0)

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        try:
            zoom = float(zoom)
        except (TypeError, ValueError):
            return
        if not math.isfinite(zoom):
            return
        zoom = min(MAX_ZOOM, max(MIN_ZOOM, zoom))
        if zoom == self._zoom:
            return
        self._zoom = zoom
        self._apply_font()
        self.zoomChanged.emit(zoom)
        self.changed.emit()

    def fit_width(self) -> None:
        self.set_zoom(1.0)

    def status_text(self) -> str:
        label = "Markdown" if self._markdown else "文本"
        count = len(self._editor.document().toPlainText())
        return f"{label} · {count} 字 · 缩放 {round(self._zoom * 100)}%"

    def verticalScrollBar(self):
        return self._editor.verticalScrollBar()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_text_width()

    def eventFilter(self, watched, event) -> bool:
        # 布局把编辑器撑宽时才会触发它自己的 Resize；包装层的 Resize 早于子控件定宽
        if watched is self._editor and event.type() == QEvent.Resize:
            self._fit_text_width()
        return super().eventFilter(watched, event)

    def _fit_text_width(self) -> None:
        if not isinstance(self._editor, QTextBrowser):
            return
        # 宽屏时用视口外边距把正文夹到 MAX_TEXT_WIDTH 并居中
        side = max(0, (self._editor.size().width() - MAX_TEXT_WIDTH) // 2)
        margins = self._editor.viewportMargins()
        if margins.left() != side:
            self._editor.setViewportMargins(side, 0, side, 0)

    # --- 主窗兼容空实现（文本没有笔迹/笔记/选字复制） ---

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        if tool == "select":
            # 文本阅读器原生支持拖选；点「鼠标」时把焦点还给编辑器
            self._editor.viewport().setCursor(Qt.IBeamCursor)
            self._editor.setFocus(Qt.OtherFocusReason)

    def set_color(self, color: str) -> None:
        self.color = color

    def set_highlighter_color(self, color: str) -> None:
        self.highlighter_color = color

    def set_width(self, width: float) -> None:
        try:
            self.width = float(width)
        except (TypeError, ValueError):
            pass

    def set_pressure_enabled(self, enabled: bool) -> None:
        self.pressure_enabled = bool(enabled)

    def set_ink_smoothing(self, level: str) -> None:
        self.ink_smoothing = level

    def set_page_mode(self, mode: str) -> None:
        self.page_mode = mode

    def clear_selection(self) -> None:
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            cursor.clearSelection()
            self._editor.setTextCursor(cursor)

    def copy_selection(self) -> bool:
        return False
