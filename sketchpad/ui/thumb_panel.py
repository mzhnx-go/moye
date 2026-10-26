"""页面缩略图侧栏：占位项一次建好，缩略图围绕当前页窗口按 tick 懒渲染。

`set_source` 围绕第 0 页、`set_current` 围绕当前页各把 ±WINDOW_RADIUS 页排进
队列（已渲/在队的跳过），每 tick 渲 `MAX_PER_TICK` 页，排空即停表。总图标数
封顶 `MAX_RENDERED_TOTAL`：到顶不再排队，未渲页保留页码占位与"已省略"提示。
点击/回车只发 pageRequested(0 起页码)，翻页由接线层处理；坏页跳过不中断。
"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from sketchpad.ui import theme
from sketchpad.pdf.document import PdfError, PdfSource
from sketchpad.pdf.pagemode import PAGE_MODES, apply_page_mode

PANEL_WIDTH = 150          # 侧栏固定宽度
THUMB_W, THUMB_H = 120, 90  # 图标框（页面按等比缩进来）
TICK_MS = 10               # 每个 tick 的间隔
MAX_PER_TICK = 4           # 每 tick 最多渲染几页
WINDOW_RADIUS = 30         # 围绕当前页前后各排这么多页进渲染队列
MAX_RENDERED_TOTAL = 400   # 硬预算：总图标数封顶（400 页 × 120×90×4B ≈ 17MB 图标）
SKIP_TIP = "页数较多，缩略图已省略"   # 预算到顶后未渲页保留页码占位，悬停给这句提示


def _thumb_pixmap(image: QImage) -> QPixmap:
    """QImage → 等比缩进 120×90 的位图；竖版高瘦、横版压扁，都不拉伸。"""
    return QPixmap.fromImage(image).scaled(
        THUMB_W, THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )


class ThumbPanel(QWidget):
    """左侧页面缩略图栏；渲染与翻页都不碰文档，只读 PdfSource。"""

    pageRequested = Signal(int)   # 0 起页码

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ThumbPanel")
        # QWidget 子类默认不吃 QSS 的 background，必须显式打开（同上工具栏）
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(PANEL_WIDTH)

        self._source: PdfSource | None = None
        self._page_mode = "normal"           # 与 PdfView 同一套底图阅读模式
        self._rendered: set[int] = set()     # 已有图标的页号（0 起）
        self._queue: deque[int] = deque()    # 待渲染页，按距当前页远近入队
        self._pending: set[int] = set()      # _queue 的镜像，入队去重用

        # 增量渲染：10ms 一跳，每跳最多 4 页；排空后自己停
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._render_batch)

        self._build()
        self._apply_style()

    def _build(self) -> None:
        column = QVBoxLayout(self)
        column.setContentsMargins(4, 6, 4, 4)
        column.setSpacing(4)

        self.title_label = QLabel("页面", self)
        self.title_label.setObjectName("ThumbTitle")
        column.addWidget(self.title_label)

        self.list = QListWidget(self)
        self.list.setObjectName("ThumbList")
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list.setWordWrap(False)
        self.list.setUniformItemSizes(True)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.setSpacing(4)
        self.list.setFocusPolicy(Qt.NoFocus)
        # 单击和回车/双击都算“要去这一页”；双击会连着发两次，翻页是幂等的
        self.list.itemClicked.connect(self._request_page)
        self.list.itemActivated.connect(self._request_page)
        column.addWidget(self.list, 1)

    def _apply_style(self) -> None:
        """按 objectName 命中，别写成全局 QListWidget 规则去罩别的控件。"""
        self.setStyleSheet(f"""
QWidget#ThumbPanel {{ background: {theme.WINDOW_BG}; }}
QLabel#ThumbTitle {{ color: {theme.TEXT_DIM}; font-size: 12px; padding: 0 4px; }}
QListWidget#ThumbList {{
    background: {theme.WINDOW_BG};
    border: none;
    color: {theme.TEXT_DIM};
    outline: none;
}}
QListWidget#ThumbList::item {{ padding: 4px 0; border-radius: 5px; }}
QListWidget#ThumbList::item:hover {{ background: {theme.SURFACE_HOVER}; }}
QListWidget#ThumbList::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}
""")

    # --- 数据源 ---

    def set_page_mode(self, mode: str) -> None:
        """切换缩略图底图模式：清掉旧图标并按当前页重新排队渲染。"""
        if mode not in PAGE_MODES:
            raise ValueError(f"未知阅读模式：{mode!r}（只支持 {PAGE_MODES}）")
        if mode == self._page_mode:
            return
        self._page_mode = mode
        source = self._source
        if source is None:
            return
        row = self.list.currentRow()
        self.set_source(source)
        if row >= 0:
            self.set_current(row)

    def set_source(self, source: PdfSource | None) -> None:
        """换文档：先清空，再一次性建好页码占位项，缩略图围绕首页窗口慢慢补。"""
        self.clear()
        if source is None:
            return
        try:
            count = source.page_count
        except PdfError:
            return                      # 已关闭的源当空文档处理
        self._source = source
        for index in range(count):
            item = QListWidgetItem(str(index + 1))
            item.setTextAlignment(Qt.AlignCenter)
            item.setToolTip(f"第 {index + 1} 页")
            self.list.addItem(item)
        if self._rendering_allowed():
            self._queue_window(0)

    def _rendering_allowed(self) -> bool:
        """主窗可见但侧栏被收起时不渲染缩略图；离屏测试/独立面板保持原行为。"""
        window = self.window()
        return self.isVisible() or not window.isVisible()

    def showEvent(self, ev) -> None:
        """侧栏打开时才真正开始画缩略图；隐藏时排队但不消耗 UITimer。"""
        super().showEvent(ev)
        if self._source is not None:
            row = max(0, self.list.currentRow())
            self._queue_window(row)

    def hideEvent(self, ev) -> None:
        self._timer.stop()
        super().hideEvent(ev)

    def clear(self) -> None:
        """停表、丢源、清列表与渲染状态。"""
        self._timer.stop()
        self._source = None
        self._rendered.clear()
        self._queue.clear()
        self._pending.clear()
        self.list.clear()

    # --- 渲染 ---

    def _queue_window(self, center: int) -> None:
        """把 center ±WINDOW_RADIUS 内未渲、未排队的页按距 center 远近入队。

        预算到顶就不再排新活，只把未渲页的提示统一改成"已省略"，内存不再涨。
        """
        if self._source is None or self.list.count() == 0:
            return
        if len(self._rendered) >= MAX_RENDERED_TOTAL:
            self._mark_skipped()
            return
        low = max(0, center - WINDOW_RADIUS)
        high = min(self.list.count() - 1, center + WINDOW_RADIUS)
        for index in sorted(range(low, high + 1), key=lambda i: abs(i - center)):
            if index in self._rendered or index in self._pending:
                continue                # 已有图标或已在队里，重复点击不重排
            self._queue.append(index)
            self._pending.add(index)
        if self._rendering_allowed() and self._queue and not self._timer.isActive():
            self._timer.start()

    def _render_batch(self) -> int:
        """跑一个 tick：从队头最多渲染 MAX_PER_TICK 页，返回本次成功页数；0 表示已排空。

        预算 `MAX_RENDERED_TOTAL` 到顶就把剩下的活扔了，停表并给未渲页"已省略"提示，
        避免 996 页文档翻到哪里就无限攒图标。侧栏隐藏时停表，排队保留到再次显示。
        """
        if not self._rendering_allowed():
            self._timer.stop()
            return 0
        source = self._source
        if source is None:
            return 0
        done = 0
        while done < MAX_PER_TICK and self._queue:
            if len(self._rendered) >= MAX_RENDERED_TOTAL:
                self._queue.clear()     # 预算到顶：剩下的活不干了
                self._pending.clear()
                break
            index = self._queue.popleft()
            self._pending.discard(index)
            if index in self._rendered:
                continue                # 保险：已有图标的页不重渲
            try:
                width, _ = source.page_size(index)
                zoom = THUMB_W / width if width > 0 else 1.0
                image = apply_page_mode(source.render(index, zoom), self._page_mode)
            except PdfError:
                continue                # 坏页跳过，后面的照常补图
            item = self.list.item(index)
            if item is None:            # 列表与队列失配时的防御，理论上不该发生
                continue
            item.setIcon(QIcon(_thumb_pixmap(image)))
            self._rendered.add(index)
            done += 1
        if not self._queue:
            self._timer.stop()          # 排空（或预算到顶）即停表
            if len(self._rendered) >= MAX_RENDERED_TOTAL:
                self._mark_skipped()
        return done

    def _mark_skipped(self) -> None:
        """预算到顶：没有图标的页保留页码占位，提示统一改成"已省略"；已渲页不动。"""
        for index in range(self.list.count()):
            if index in self._rendered:
                continue
            item = self.list.item(index)
            if item.toolTip() != SKIP_TIP:
                item.setToolTip(SKIP_TIP)

    # --- 选中 ---

    def _request_page(self, item: QListWidgetItem) -> None:
        """列表的点击/激活 → 0 起页码。"""
        self.pageRequested.emit(self.list.row(item))

    def set_current(self, index: int) -> None:
        """高亮某页但不发 pageRequested；越界就清空选中。顺带把该页窗口排进渲染队列。"""
        row = index if 0 <= index < self.list.count() else -1
        was_blocked = self.list.blockSignals(True)
        try:
            self.list.setCurrentRow(row)
        finally:
            self.list.blockSignals(was_blocked)
        if row >= 0:
            self.list.scrollToItem(self.list.item(row))
            self._queue_window(row)
