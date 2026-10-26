"""查找条：输入框 + 上一个/下一个 + 计数 + 关闭。只发信号，不碰文档。

**输入不搜索**：打字只作废旧结果（发一次空查询清高亮、取消在途 worker），
回车才用当前文本启动搜索；已有结果时回车 = 下一个命中，Shift+回车 = 上一个。
点「上一个/下一个」时若查询改过，也先搜新词，不踏进旧命中。Esc 自清自关。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from sketchpad.ui import icons


class FindBar(QWidget):
    """PDF 内查找条，默认隐藏。"""

    searchRequested = Signal(str)
    nextRequested = Signal()
    prevRequested = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FindBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._last_query = ""

        self.input = QLineEdit(self)
        self.input.setPlaceholderText("在 PDF 中查找（回车搜索 / 下一个）")
        self.input.setToolTip("查找：输入后回车搜索；再回车下一个，Shift+回车上一个")
        self.input.textChanged.connect(self._on_text_changed)
        self.input.installEventFilter(self)

        self.search_btn = QPushButton(self)
        self.search_btn.setObjectName("FindSearch")
        self.search_btn.setIcon(icons.search_icon())
        self.search_btn.setIconSize(QSize(15, 15))
        self.search_btn.setFixedSize(26, 24)
        self.search_btn.setToolTip("查找（回车）")
        self.search_btn.setFocusPolicy(Qt.NoFocus)
        self.search_btn.clicked.connect(self._search_clicked)

        self.prev_btn = QPushButton("上一个", self)
        self.prev_btn.setToolTip("上一个命中（Shift+回车）")
        self.prev_btn.clicked.connect(lambda: self._step_or_search(-1))
        self.next_btn = QPushButton("下一个", self)
        self.next_btn.setToolTip("下一个命中（回车）")
        self.next_btn.clicked.connect(lambda: self._step_or_search(1))

        self.count_label = QLabel("0/0", self)
        self.count_label.setObjectName("FindCount")
        self.count_label.setMinimumWidth(44)
        self.count_label.setAlignment(Qt.AlignCenter)

        # 用绘制图标而不是 "✕" 文本：部分字体没有该字形，按钮会变成一个空方块
        self.close_btn = QPushButton(self)
        self.close_btn.setObjectName("FindClose")
        self.close_btn.setIcon(icons.close_icon())
        self.close_btn.setIconSize(QSize(14, 14))
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setToolTip("关闭查找")
        self.close_btn.clicked.connect(self._close)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.search_btn)
        layout.addWidget(self.prev_btn)
        layout.addWidget(self.next_btn)
        layout.addWidget(self.count_label)
        layout.addWidget(self.close_btn)

        self.hide()

    def focus_input(self) -> None:
        """把焦点放到输入框。"""
        self.input.setFocus()

    def set_count(self, current: int, total: int, truncated: bool = False) -> None:
        if truncated:
            self.count_label.setText(f"{current}/{total}+")
            self.count_label.setToolTip("只显示前 500 条")
        else:
            self.count_label.setText(f"{current}/{total}")
            self.count_label.setToolTip("")

    def set_searching(self, done: int, total: int) -> None:
        """异步搜索进行中的进度读数（如「搜索中 123/996」）；不动命中计数。

        总数未知（worker 还没打开文档）时只显示「搜索中…」。
        """
        if total > 0:
            self.count_label.setText(f"搜索中 {done}/{total}")
        else:
            self.count_label.setText("搜索中…")
        self.count_label.setToolTip("")

    def reset(self) -> None:
        """清空输入、上次查询和计数，不发任何信号（切换文档时用）。"""
        with QSignalBlocker(self.input):
            self.input.clear()
        self._last_query = ""
        self.set_count(0, 0)

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if obj is self.input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                # 清空交由 _close() 用信号屏蔽完成，避免清空再触发一次搜索
                self._close()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._activate(bool(event.modifiers() & Qt.ShiftModifier))
                return True
        return super().eventFilter(obj, event)

    def _activate(self, backward: bool = False) -> None:
        """回车：查询改过就先搜新词；否则上一个/下一个。"""
        text = self.input.text()
        if not text.strip():
            self._invalidate("")
            return
        if text != self._last_query:
            self._last_query = text
            self.searchRequested.emit(text)
            return
        if backward:
            self.prevRequested.emit()
        else:
            self.nextRequested.emit()

    def _search_clicked(self) -> None:
        """放大镜按钮：用当前文本发起查找（不依赖回车，也不管是否搜过）。"""
        text = self.input.text()
        if not text.strip():
            self._invalidate("")
            return
        self._last_query = text
        self.searchRequested.emit(text)

    def _step_or_search(self, delta: int) -> None:
        """上一个/下一个按钮：查询改过先搜，不拿旧命中步进。"""
        text = self.input.text()
        if text.strip() and text != self._last_query:
            self._last_query = text
            self.searchRequested.emit(text)
            return
        if delta < 0:
            self.prevRequested.emit()
        else:
            self.nextRequested.emit()

    def start_search(self) -> bool:
        """启动“已输入但尚未搜索”的查询；返回是否真的发起了搜索。"""
        text = self.input.text()
        if not text.strip() or text == self._last_query:
            return False
        self._last_query = text
        self.searchRequested.emit(text)
        return True

    def _invalidate(self, query: str) -> None:
        """输入变了：旧命中/在途搜索立即作废，但**不发起新的全文搜索**。"""
        if query == self._last_query or not self._last_query:
            return                  # 没有旧结果时，打字不该发任何搜索信号
        self._last_query = ""
        self.set_count(0, 0)
        self.searchRequested.emit("")

    def _on_text_changed(self, text: str) -> None:
        # 打字只清旧结果；真正搜索由回车/上一个/下一个触发（大 PDF 每键一搜会卡）
        self._invalidate(text)

    def _close(self) -> None:
        # 清空输入不能触发 textChanged -> searchRequested（关闭时不再搜索）
        with QSignalBlocker(self.input):
            self.input.clear()
        self._last_query = ""
        self.set_count(0, 0)
        self.closed.emit()
        self.hide()
