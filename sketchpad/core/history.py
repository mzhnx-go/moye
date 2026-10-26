"""撤销/重做栈。存命令对象本身（不复制笔迹），超上限丢最老的。本模块不依赖 Qt。"""
from __future__ import annotations

from typing import Protocol


class Command(Protocol):
    """一个可撤销单位：apply 正向做，unapply 反向撤。"""

    def apply(self) -> None: ...
    def unapply(self) -> None: ...


class History:
    """undo / redo 两条分支。push 只记命令不重复 apply；新动作作废 redo。"""

    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def push(self, cmd: Command) -> None:
        # 调用方已 apply，这里只记账；顺手清掉 redo（设计文档 §10）
        self._undo.append(cmd)
        if len(self._undo) > self.limit:
            del self._undo[:-self.limit]      # 超深丢最老的
        self._redo.clear()

    def pop_undo(self) -> Command | None:
        if not self._undo:
            return None                       # 空栈不抛
        cmd = self._undo.pop()
        cmd.unapply()
        self._redo.append(cmd)
        return cmd

    def pop_redo(self) -> Command | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.apply()
        self._undo.append(cmd)
        return cmd

    def clear(self) -> None:
        """丢弃整段撤销/重做：草稿关闭后重开不能把旧内容撤回来。"""
        self._undo.clear()
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def depth(self) -> int:
        return len(self._undo)
