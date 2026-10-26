"""一个打开文档的独立会话状态。

标签页/分屏只负责“显示哪个会话”；撤销历史、缩放/滚动、自动暂存、
笔记卡、查找结果、阅读位置都归会话自己，切标签不会互相串。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QWidget

from sketchpad.workspace.note_split import NoteSplitPane


@dataclass(eq=False)
class NoteSession:
    """一个独立笔记标签：自己的 Document/SketchView/撤销栈，不写盘。

    与 DocumentSession 一样提供 pane/tab_title/display_name 三个鸭子类型，
    因此可以一起进 QSplitter（笔记 + PDF 分屏），但 PDF 专属的自动保存、
    侧栏、阅读位置字段不存在。
    """

    title: str
    view: Any
    document: Any
    pane: QWidget
    path: Path | None = None
    kind: str = "note"
    dirty: bool = False
    saved_at: str | None = None
    view_shortcuts: list = field(default_factory=list)

    @property
    def tab_title(self) -> str:
        return self.title

    @property
    def display_name(self) -> str:
        return self.title

    def close_view(self) -> None:
        try:
            self.view.setFocusPolicy(self.view.focusPolicy())
        except Exception:
            pass


@dataclass(eq=False)
class DocumentSession:
    """一个文档标签的全部可变状态（view 内部还持有撤销栈/缩放/滚动）。"""

    path: Path
    view: Any                       # PdfView；Standard 后续可换成通用 viewer
    autosave: Any                   # PdfAutoSaver
    pane: QWidget                   # 承载 view + 文档内笔记分屏
    dirty: bool = False
    saved_at: str | None = None
    doc_lock: Any | None = None     # 多开时同一 PDF/Word 只能一个实例持有

    # 笔记：独立窗口 / PDF+笔记分屏（浮层卡片形态已取消，2026-10-23）
    note_windows: dict = field(default_factory=dict)
    note_split_card: Any | None = None
    note_split_pane: QWidget | None = None
    note_split_key: tuple[int, str] | None = None
    split_prev_auto_fit: bool | None = None
    # 独立笔记窗口的几何：{note_id: QRect}，只在会话内记住，不写 sidecar
    note_window_geometry: dict = field(default_factory=dict)
    note_split_ratio: float = 0.62       # PDF 占比；切标签再回来还按这个比例分栏

    # 查找（worker 不跨会话保留；命中状态可恢复）
    find_hits: list = field(default_factory=list)
    find_index: int = -1
    find_truncated: bool = False
    find_query: str = ""
    view_shortcuts: list = field(default_factory=list)

    @property
    def tab_title(self) -> str:
        name = self.path.name if self.path is not None else "未命名"
        return f"● {name}" if self.dirty else name

    @property
    def display_name(self) -> str:
        return self.path.name if self.path is not None else "未命名"

    def close_view(self) -> None:
        """释放 PDF/源文件句柄；不影响 pane 的显示状态。"""
        try:
            self.view.close_document()
        except Exception:
            pass
        try:
            self.autosave.cancel()
        except Exception:
            pass


def make_session_pane(view: QWidget) -> NoteSplitPane:
    """会话容器：真正的 QSplitter 分屏（PDF 左 / 笔记右）。"""
    return NoteSplitPane(view)
