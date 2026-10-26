"""可撤销动作：一次落笔、一次橡皮、一次清屏。本模块不依赖 Qt。"""
from __future__ import annotations

from sketchpad.annotate.core.strokes import Document, Stroke


class AddStroke:
    def __init__(self, doc: Document, stroke: Stroke) -> None:
        self._doc = doc
        self._stroke = stroke

    def apply(self) -> None:
        self._doc.strokes.append(self._stroke)

    def unapply(self) -> None:
        self._doc.strokes.remove(self._stroke)


class EraseStrokes:
    """一次划过删掉的所有条 = 1 个撤销单位。"""

    def __init__(self, doc: Document, strokes: list[Stroke]) -> None:
        self._doc = doc
        self._targets = list(strokes)
        self._removed: list[tuple[int, Stroke]] = []

    def apply(self) -> None:
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
                del pending[hit]
                removed.append((index, s))
        self._doc.strokes[:] = kept
        self._removed = removed

    def unapply(self) -> None:
        for index, s in self._removed:
            self._doc.strokes.insert(index, s)
        self._removed = []


class ClearAll:
    """一键清屏：整张列表进快照，撤销时原地换回。"""

    def __init__(self, doc: Document) -> None:
        self._doc = doc
        self._snapshot: list[Stroke] = []

    def apply(self) -> None:
        self._snapshot = list(self._doc.strokes)
        self._doc.strokes.clear()

    def unapply(self) -> None:
        self._doc.strokes[:] = self._snapshot
