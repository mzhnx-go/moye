# tests/test_history.py
from sketchpad.core.history import History

class FakeCmd:
    def __init__(self):
        self.applied = self.unapplied = 0
    def apply(self): self.applied += 1
    def unapply(self): self.unapplied += 1

def test_push_then_undo_calls_unapply_once():
    h = History(limit=100)
    c = FakeCmd(); c.apply(); h.push(c)
    got = h.pop_undo()
    assert got is c
    assert c.unapplied == 1
    assert h.can_undo is False

def test_empty_stack_pop_returns_none_without_raising():
    h = History()
    assert h.pop_undo() is None
    assert h.pop_redo() is None

def test_new_action_clears_redo_branch():
    h = History()
    a, b, c = FakeCmd(), FakeCmd(), FakeCmd()
    for x in (a, b):
        x.apply(); h.push(x)
    h.pop_undo()                 # b 进 redo
    c.apply(); h.push(c)         # 新动作，redo 分支作废
    assert h.can_redo is False

def test_limit_drops_oldest_and_depth_stays_at_limit():
    h = History(limit=3)
    cmds = [FakeCmd() for _ in range(5)]
    for c in cmds:
        c.apply(); h.push(c)
    assert h.depth == 3
    assert h.pop_undo() is cmds[4]
    assert h.pop_undo() is cmds[3]
    assert h.pop_undo() is cmds[2]   # cmds[0], cmds[1] 已被丢
    assert h.can_undo is False

def test_undo_returns_commands_in_reverse_order():
    h = History(limit=10)
    cmds = [FakeCmd() for _ in range(4)]
    for c in cmds:
        h.push(c)
    assert [h.pop_undo() for _ in range(4)][::-1] == cmds
