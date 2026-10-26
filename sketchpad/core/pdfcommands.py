"""按页可撤销动作：给某页加一笔、删若干笔、清空该页。本模块不依赖 Qt。

与 sketchpad/core/commands.py 同一纪律：命令里记原始下标/快照，unapply 把列表恢复成原样
（顺序一致），并公开 page_index 让视图只重建受影响的那一页。
"""
from __future__ import annotations

from sketchpad.core.pdfstore import PdfDocument
from sketchpad.core.strokes import Stroke


class AddStrokeToPage:
    """某页抬笔固化的一条笔迹。"""

    def __init__(self, doc: PdfDocument, page_index: int, stroke: Stroke) -> None:
        self._doc = doc
        self.page_index = page_index
        self._stroke = stroke

    def apply(self) -> None:
        self._doc.pages[self.page_index].strokes.append(self._stroke)

    def unapply(self) -> None:
        # list.remove 按 == 比较：同值的另一条也能撤掉
        self._doc.pages[self.page_index].strokes.remove(self._stroke)


class EraseStrokesFromPage:
    """某页一次划过删掉的所有条 = 1 个撤销单位；unapply 按原下标插回。"""

    def __init__(self, doc: PdfDocument, page_index: int, strokes: list[Stroke]) -> None:
        self._doc = doc
        self.page_index = page_index
        self._targets = list(strokes)
        self._removed: list[tuple[int, Stroke]] = []

    def apply(self) -> None:
        # 下标在一次正向扫描里对着原始列表定好；边删边取 index 会因移位失真
        pending = list(self._targets)
        kept: list[Stroke] = []
        removed: list[tuple[int, Stroke]] = []
        for index, s in enumerate(self._doc.pages[self.page_index].strokes):
            hit = None
            for pos, t in enumerate(pending):
                if s == t:
                    hit = pos
                    break
            if hit is None:
                kept.append(s)
            else:
                del pending[hit]      # 一个目标只吃掉一条，页外目标自然落空
                removed.append((index, s))
        self._doc.pages[self.page_index].strokes[:] = kept
        self._removed = removed       # 扫描即升序，无需再排序

    def unapply(self) -> None:
        strokes = self._doc.pages[self.page_index].strokes
        for index, s in self._removed:
            strokes.insert(index, s)
        self._removed = []


class ClearPage:
    """清空本页：整张列表进快照，撤销时原地换回（顺序不变）。"""

    def __init__(self, doc: PdfDocument, page_index: int) -> None:
        self._doc = doc
        self.page_index = page_index
        self._snapshot: list[Stroke] = []

    def apply(self) -> None:
        self._snapshot = list(self._doc.pages[self.page_index].strokes)
        self._doc.pages[self.page_index].strokes.clear()

    def unapply(self) -> None:
        # 整段替换而非追加：unapply 可重复执行，重做时 apply 会重新取快照
        self._doc.pages[self.page_index].strokes[:] = self._snapshot


class ReplacePageItems:
    """页面级文字/图片的整表快照：新增、编辑、移动、删除都走这一条。

    `field` 只能是 "texts" / "images"；before/after 在构造时各自拷一份列表，
    调用方之后改原列表不会影响撤销内容。
    """

    def __init__(self, doc: PdfDocument, page_index: int, field: str,
                 before: list, after: list) -> None:
        if field not in ("texts", "images"):
            raise ValueError(f"未知页面富内容类型：{field!r}")
        self._doc = doc
        self.page_index = page_index
        self._field = field
        self._before = list(before)
        self._after = list(after)

    def apply(self) -> None:
        setattr(self._doc.pages[self.page_index], self._field, list(self._after))

    def unapply(self) -> None:
        setattr(self._doc.pages[self.page_index], self._field, list(self._before))


class ReplaceMany:
    """一次提交多个 (page, field, before, after) 快照：多选移动/删除/微调用一条撤销。

    `page_index = None` 是有意为之：条目可能跨页、跨 texts/images，视图的历史回调
    看到 None 就重绑全部页（`PdfView._after_history_change` 既有逻辑）。unapply
    按逆序恢复，即便调用方传了同一页同一字段的多个条目也能还原。
    """

    page_index = None

    def __init__(self, doc: PdfDocument, entries) -> None:
        self._doc = doc
        self._entries: list[tuple[int, str, list, list]] = []
        for page_index, field, before, after in entries:
            if field not in ("texts", "images"):
                raise ValueError(f"未知页面富内容类型：{field!r}")
            self._entries.append((page_index, field, list(before), list(after)))

    def apply(self) -> None:
        for page_index, field, _before, after in self._entries:
            setattr(self._doc.pages[page_index], field, list(after))

    def unapply(self) -> None:
        for page_index, field, before, _after in reversed(self._entries):
            setattr(self._doc.pages[page_index], field, list(before))
