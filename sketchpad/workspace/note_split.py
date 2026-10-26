"""文档会话容器：PDF 左 / 笔记右，真正的 ``QSplitter`` 分屏。

与旧做法（往会话布局里插一块固定宽度的面板）不同，这里的分隔条是 Qt 的
``QSplitter``：可以拖、可以双击复位，比例按“PDF 占第一栏的比例”记录。
压缩到窗口很窄时两侧各守 360 / 340 的最小宽度，宁可让分屏内部横向溢出，
也不让 PDF 被笔记盖住。

组件只依赖 Qt 与 ``ui.theme``，不认主窗、不认 ``DocumentSession``：
`show_note(card)` 只负责把卡片摆进右栏，`hide_note()` 把卡片摘下来**归还**
给调用方（不删除），删除/保存/切标签都归接线层。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sketchpad.ui import theme

HANDLE_WIDTH = 6                      # 分隔条宽度（悬停高亮，鼠标左右箭头）
TITLE_ROW_HEIGHT = 26                 # 左栏标题行高度（与标签栏同量级）
PDF_MIN_WIDTH = 360                   # PDF 侧最小宽度
NOTE_MIN_WIDTH = 340                  # 笔记侧最小宽度
DEFAULT_RATIO = 0.62                  # PDF 占比默认值（笔记拿剩下的 0.38）
FIT_TEXT = "适应分屏"
FIT_TOOLTIP = "按分屏宽度适应（Ctrl+9）"

# 只按 objectName 命中：本样式表挂在会话 pane 上，会级联到 PDF/笔记的整棵子树，
# 任何不带 # 的规则都会改写画布/按钮的外观（theme.py 里有同样的告诫）。
_NOTE_SPLIT_QSS = f"""
QWidget#NoteSplitPane {{ background: {theme.WINDOW_BG}; }}
QSplitter#NoteSplitSplitter {{ background: {theme.WINDOW_BG}; }}
QSplitter#NoteSplitSplitter::handle {{ background: {theme.BORDER}; }}
QSplitter#NoteSplitSplitter::handle:hover {{ background: {theme.ACCENT}; }}
QFrame#NoteSplitPdfFrame, QFrame#NoteSplitNoteFrame {{
    background: {theme.WINDOW_BG};
    border: none;
}}
QWidget#NoteSplitTitle {{
    background: {theme.TOOLBAR_BG};
    border-bottom: 1px solid {theme.BORDER};
}}
QLabel#NoteSplitFileName {{
    color: {theme.TEXT};
    font-size: 12px;
    font-weight: 600;
}}
QToolButton#NoteSplitFit {{
    background: {theme.SURFACE};
    color: {theme.TEXT};
    border: 1px solid {theme.BORDER};
    border-radius: 4px;
    padding: 1px 8px;
    font-size: 11px;
}}
QToolButton#NoteSplitFit:hover {{ background: {theme.SURFACE_HOVER}; }}
QToolButton#NoteSplitFit:pressed {{ background: {theme.SURFACE_PRESSED}; }}
"""


class NoteSplitPane(QWidget):
    """会话容器：一个横向 splitter，左边 PDF（带标题行），右边笔记卡。

    比例语义：``ratio`` 是 **PDF 占的第一栏比例**（默认 0.62，笔记拿 0.38）。
    拖动分隔条只会更新当前比例并发 ``ratioChanged``；双击则回到最近一次
    ``show_note`` / ``set_ratio`` 给出的“记忆比例”（默认 0.62），方便用户
    把拖乱的分屏一键拉回会话保存的那个比例。
    """

    ratioChanged = Signal(float)
    fitRequested = Signal()

    def __init__(self, view: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._card: QWidget | None = None
        self._ratio = DEFAULT_RATIO
        self._remembered_ratio = DEFAULT_RATIO

        self.setObjectName("NoteSplitPane")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_NOTE_SPLIT_QSS)

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setObjectName("NoteSplitSplitter")
        self.splitter.setHandleWidth(HANDLE_WIDTH)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setOpaqueResize(True)
        self.splitter.setMinimumWidth(PDF_MIN_WIDTH)

        self.pdf_frame = self._build_pdf_frame()
        self.note_frame = self._build_note_frame()
        self.splitter.addWidget(self.pdf_frame)
        self.splitter.addWidget(self.note_frame)
        self.note_frame.hide()

        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        # 双击分隔条复位：QSplitterHandle 自己不发信号，只能过滤它的双击事件。
        handle = self.splitter.handle(1)
        if handle is not None:
            handle.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.splitter, 1)

        # 首次 show_note 可能发生在布局给出真实宽度之前；延迟到事件循环再按
        # 真实宽度算尺寸（见 show_note）。
        self._ratio_timer = QTimer(self)
        self._ratio_timer.setSingleShot(True)
        self._ratio_timer.timeout.connect(self._apply_deferred_ratio)

    # --- 构建 ---

    def _build_pdf_frame(self) -> QFrame:
        frame = QFrame(self.splitter)
        frame.setObjectName("NoteSplitPdfFrame")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        frame.setMinimumWidth(PDF_MIN_WIDTH)

        self.title_row = QWidget(frame)
        self.title_row.setObjectName("NoteSplitTitle")
        self.title_row.setAttribute(Qt.WA_StyledBackground, True)
        self.title_row.setFixedHeight(TITLE_ROW_HEIGHT)

        self.file_label = QLabel(self.title_row)
        self.file_label.setObjectName("NoteSplitFileName")
        self.file_label.setText(self._default_title())
        self.file_label.setToolTip(self.file_label.text())
        self.file_label.setFocusPolicy(Qt.NoFocus)

        self.fit_button = QToolButton(self.title_row)
        self.fit_button.setObjectName("NoteSplitFit")
        self.fit_button.setText(FIT_TEXT)
        self.fit_button.setToolTip(FIT_TOOLTIP)
        self.fit_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.fit_button.setFocusPolicy(Qt.NoFocus)
        self.fit_button.setCursor(Qt.PointingHandCursor)
        # 只表达意图：真正 fit_width 由主窗决定（分屏不自动改 PDF 缩放）。
        self.fit_button.clicked.connect(
            lambda _checked=False: self.fitRequested.emit())

        title_layout = QHBoxLayout(self.title_row)
        title_layout.setContentsMargins(10, 0, 6, 0)
        title_layout.setSpacing(6)
        title_layout.addWidget(self.file_label, 1)
        title_layout.addWidget(self.fit_button)
        self.title_row.hide()             # 未分屏时不占高度，viewer 铺满

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_row)
        layout.addWidget(self._view, 1)
        return frame

    def _build_note_frame(self) -> QFrame:
        frame = QFrame(self.splitter)
        frame.setObjectName("NoteSplitNoteFrame")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        frame.setMinimumWidth(NOTE_MIN_WIDTH)
        self.note_layout = QVBoxLayout(frame)
        self.note_layout.setContentsMargins(0, 0, 0, 0)
        self.note_layout.setSpacing(0)
        return frame

    def _default_title(self) -> str:
        try:
            path = getattr(self._view, "pdf_path", None)
        except Exception:
            path = None
        return path.name if path is not None else "PDF"

    # --- 只读状态 ---

    @property
    def view(self) -> QWidget:
        return self._view

    @property
    def is_note_open(self) -> bool:
        return self._card is not None

    @property
    def ratio(self) -> float:
        """当前 PDF 占比；以 splitter 的真实尺寸为准（窄窗被最小宽度夹过也如实反映）。"""
        if self._card is None:
            return self._ratio
        sizes = self.splitter.sizes()
        total = sum(sizes)
        if total <= 0:
            return self._ratio
        return sizes[0] / float(total)

    # --- 显示 / 隐藏 ---

    def show_note(self, card: QWidget, ratio: float = DEFAULT_RATIO) -> None:
        """把卡片放进右栏并按比例分栏；可重复调用（同一张卡只更新比例/标题）。"""
        ratio = self._normalize_ratio(ratio)
        self._remembered_ratio = ratio
        self._ratio = ratio
        if self._card is card:
            self._request_ratio()
            return
        if self._card is not None:
            # 旧卡不在这里销毁：接线层负责关闭/复用，只把它摘下来。
            self.hide_note()

        self._card = card
        self.note_layout.addWidget(card)
        card.show()
        self.note_frame.show()
        self.title_row.show()
        self.splitter.setMinimumWidth(
            PDF_MIN_WIDTH + NOTE_MIN_WIDTH + self.splitter.handleWidth())
        self._request_ratio()

    def hide_note(self) -> QWidget | None:
        """把当前卡片摘下来并返回（调用方负责删除/复用）；不销毁卡片。"""
        card = self._card
        if card is None:
            return None
        self._card = None
        self._ratio_timer.stop()
        self.note_layout.removeWidget(card)
        card.setParent(None)              # 只摘父，不 delete、不 deleteLater
        self.note_frame.hide()
        self.title_row.hide()
        self.splitter.setMinimumWidth(PDF_MIN_WIDTH)
        return card

    def set_note_title(self, text: str) -> None:
        title = str(text or "")
        self.file_label.setText(title)
        self.file_label.setToolTip(title)

    # --- 比例 ---

    def set_ratio(self, ratio: float) -> None:
        """显式设置比例：立即应用并让双击复位记住这个值。"""
        ratio = self._normalize_ratio(ratio)
        previous = self.ratio
        self._remembered_ratio = ratio
        self._ratio = ratio
        self._request_ratio()
        if abs(self.ratio - previous) > 1e-9:
            self.ratioChanged.emit(self.ratio)

    def _normalize_ratio(self, ratio: float) -> float:
        ratio = float(ratio)
        if not (0.0 < ratio < 1.0):
            return DEFAULT_RATIO
        return ratio

    def _request_ratio(self) -> None:
        if self._card is None:
            return
        if self.isVisible() and self.splitter.width() > self.splitter.handleWidth() + 1:
            self._apply_ratio(self._ratio)
        else:
            self._ratio_timer.start(0)

    def _apply_deferred_ratio(self) -> None:
        if self._card is not None:
            self._apply_ratio(self._ratio)

    def _apply_ratio(self, ratio: float) -> None:
        if self._card is None:
            return
        available = self.splitter.width() - self.splitter.handleWidth()
        if available <= 1:
            return
        pdf = int(round(available * ratio))
        # 两侧都要守住最小宽度；窗口装不下时宁可溢出，也不让一边被压没。
        if available >= PDF_MIN_WIDTH + NOTE_MIN_WIDTH:
            pdf = max(PDF_MIN_WIDTH, min(available - NOTE_MIN_WIDTH, pdf))
            note = available - pdf
        else:
            pdf = max(PDF_MIN_WIDTH, pdf)
            note = max(NOTE_MIN_WIDTH, available - pdf)
        self.splitter.setSizes([pdf, note])
        self._ratio = ratio

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        if self._card is None:
            return
        ratio = self.ratio
        self._ratio = ratio
        self.ratioChanged.emit(ratio)

    def _reset_ratio(self) -> None:
        previous = self.ratio
        self._ratio = self._remembered_ratio
        self._apply_ratio(self._remembered_ratio)
        if abs(self.ratio - previous) > 1e-9:
            self.ratioChanged.emit(self.ratio)

    def eventFilter(self, obj, ev) -> bool:
        handle = self.splitter.handle(1) if self.splitter.count() > 1 else None
        if (obj is handle and ev.type() == QEvent.MouseButtonDblClick
                and ev.button() == Qt.LeftButton):
            self._reset_ratio()
            ev.accept()
            return True
        return super().eventFilter(obj, ev)

    # --- 尺寸 ---

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        # 布局第一次给出真实宽度后补一次：show_note 早于 show() 时只会排队等这次。
        if self._card is not None and not self._ratio_timer.isActive():
            self._apply_ratio(self._ratio)


__all__ = ["NoteSplitPane", "DEFAULT_RATIO", "PDF_MIN_WIDTH", "NOTE_MIN_WIDTH"]
