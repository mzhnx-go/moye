"""笔记卡片模型：卡片设计坐标 + 页面锚点。本模块不依赖 Qt。"""
from __future__ import annotations

from dataclasses import dataclass, field

from sketchpad.core.strokes import Stroke

NOTE_W, NOTE_H = 560.0, 720.0
NOTE_PAPERS = ("dark", "light")

DEFAULT_TEXT_SIZE = 16.0
DEFAULT_TEXT_COLOR = "#e8eaed"


@dataclass
class TextBlock:
    """笔记里的一段文字：x/y/w 是设计坐标（相对笔记卡左上角）。

    `text` 允许为空串（用户新建了一块还没输入字），但长度上限由
    pdfstore 校验时把关；`color` 一律 `#rrggbb`。
    """

    id: str
    x: float
    y: float
    w: float
    text: str
    size: float = DEFAULT_TEXT_SIZE
    color: str = DEFAULT_TEXT_COLOR
    # 整段格式（Phase B）：放在最后并带默认值，旧的位置参数调用完全不受影响
    bold: bool = False
    italic: bool = False
    underline: bool = False


@dataclass
class NoteImage:
    """笔记里的一张图片；`asset` 是 assets 目录内的纯文件名（不含路径）。"""

    id: str
    x: float
    y: float
    w: float
    h: float
    asset: str


@dataclass
class NoteAttachment:
    """笔记里的一个附件标记；附件本体存在 assets 目录，sidecar 只记元数据。

    `name` 是给用户看的原始文件名，`asset` 是 assets 目录内的实际文件名，
    `size` 是字节数（未知时 0）。
    """

    id: str
    x: float
    y: float
    asset: str
    name: str
    size: int = 0


@dataclass
class Note:
    """一张手写笔记卡：x/y 是卡片左上角在 PDF 页面上的锚点（PDF 点），
    w/h 是卡片在设计坐标下的尺寸，strokes 的坐标相对于卡片自身。

    name 是用户自定义的笔记名称（空 = 用「笔记 N」兜底）；texts/images/
    attachments 是卡片里的富内容。三者排在最后，旧代码
    `Note(id, x, y, w, h, paper, strokes)` 的位置参数完全不受影响。
    """

    id: str
    x: float
    y: float
    w: float = NOTE_W
    h: float = NOTE_H
    paper: str = "dark"
    strokes: list[Stroke] = field(default_factory=list)
    name: str = ""
    texts: list[TextBlock] = field(default_factory=list)
    images: list[NoteImage] = field(default_factory=list)
    attachments: list[NoteAttachment] = field(default_factory=list)


def new_item_id(prefix: str, existing: set[str]) -> str:
    """分配当前最小的空闲 `<prefix>-<k>`（k 从 1 起）。

    prefix 是内容类型（"t" / "img" / "att"）；每种内容的 id 空间互相独立，
    所以调用方只需要传同类型的 id 集合。
    """
    index = 1
    while f"{prefix}-{index}" in existing:
        index += 1
    return f"{prefix}-{index}"


def note_has_rich_content(note: Note) -> bool:
    """卡片里是否有文字/图片/附件（决定 sidecar 写 v3 还是 v2）。"""
    return bool(getattr(note, "texts", None)
                or getattr(note, "images", None)
                or getattr(note, "attachments", None))


def text_block_has_format(block: TextBlock) -> bool:
    """这块文字是否带粗体/斜体/下划线（无格式走可搜索的纯文本导出路径）。

    用 getattr 兜底：调用方可能传旧版对象或测试替身，缺字段按"无格式"处理。
    """
    return bool(getattr(block, "bold", False)
                or getattr(block, "italic", False)
                or getattr(block, "underline", False))


def note_display_name(note: Note, number: int = 1) -> str:
    """笔记对外显示的名字：自定义名（去空白）优先，其余回退「笔记 N」。

    `number` 是该笔记在本页里的序号（从 1 起），由调用方按 notes 顺序算；
    纯函数，不依赖 Qt，图钉标签与卡片标题共用同一套规则。
    """
    name = (getattr(note, "name", "") or "").strip()
    return name if name else f"笔记 {number}"


def new_note_id(existing: set[str]) -> str:
    """分配当前最小的空闲 `n-<k>`。"""
    index = 1
    while f"n-{index}" in existing:
        index += 1
    return f"n-{index}"


def clamp_note_anchor(x: float, y: float, page_w: float, page_h: float) -> tuple[float, float]:
    """把锚点钳在页面矩形内，避免卡片整张被拖出可视区。"""
    return (min(max(float(x), 0.0), float(page_w)),
            min(max(float(y), 0.0), float(page_h)))
