"""可撤销动作：一次落笔、一次橡皮、一次清屏。unapply 负责放回原位。本模块不依赖 Qt。"""
from __future__ import annotations

from sketchpad.core.strokes import Document, Stroke, TextBlock


class AddStroke:
    """抬笔固化的一条笔迹。"""

    def __init__(self, doc: Document, stroke: Stroke) -> None:
        self._doc = doc
        self._stroke = stroke

    def apply(self) -> None:
        self._doc.strokes.append(self._stroke)

    def unapply(self) -> None:
        # list.remove 按 == 比较：另造一个同值命令也能撤掉这一条
        self._doc.strokes.remove(self._stroke)


class EraseStrokes:
    """一次划过删掉的所有条 = 1 个撤销单位（设计文档 §6.1）。"""

    def __init__(self, doc: Document, strokes: list[Stroke]) -> None:
        self._doc = doc
        self._targets = list(strokes)
        self._removed: list[tuple[int, Stroke]] = []

    def apply(self) -> None:
        # 下标在一次正向扫描里对着原始列表定好；边删边取 index 会因移位失真
        pending = list(self._targets)
        kept: list[Stroke] = []
        removed: list[tuple[int, Stroke]] = []
        for index, s in enumerate(self._doc.strokes):
            hit = None
            for pos, t in enumerate(pending):
                if s == t:
                    hit = pos
                    break
            if hit is None:
                kept.append(s)
            else:
                del pending[hit]      # 一个目标只吃掉一条，文档外目标自然落空
                removed.append((index, s))
        self._doc.strokes[:] = kept
        self._removed = removed       # 扫描即升序，无需再排序

    def unapply(self) -> None:
        for index, s in self._removed:
            self._doc.strokes.insert(index, s)
        self._removed = []


class ClearAll:
    """一键清屏：整张列表进快照，撤销时原地换回（设计文档 R3）。"""

    def __init__(self, doc: Document) -> None:
        self._doc = doc
        self._snapshot: list[Stroke] = []

    def apply(self) -> None:
        self._snapshot = list(self._doc.strokes)
        self._doc.strokes.clear()

    def unapply(self) -> None:
        # 整段替换而非追加：unapply 可重复执行，重做时 apply 会重新取快照
        self._doc.strokes[:] = self._snapshot


class AddText:
    """一段落点文字；撤销 = 从模型摘掉（按身份，不做值比较）。"""

    def __init__(self, doc: Document, block: TextBlock) -> None:
        self._doc = doc
        self._block = block

    def apply(self) -> None:
        self._doc.texts.append(self._block)

    def unapply(self) -> None:
        for index, item in enumerate(self._doc.texts):
            if item is self._block:
                del self._doc.texts[index]
                return
