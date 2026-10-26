"""撤销栈：AddStroke / EraseStrokes / ClearAll 与 History 的分支语义。"""
from sketchpad.annotate.core.commands import AddStroke, ClearAll, EraseStrokes
from sketchpad.annotate.core.history import History
from sketchpad.annotate.core.strokes import Document, Stroke


def make_stroke(x: float) -> Stroke:
    return Stroke(points=[(x, 0.0), (x + 10.0, 0.0)], color="#ffffff", width=2.0)


def test_add_stroke_撤销后笔迹消失():
    s0 = make_stroke(0.0)
    doc = Document(strokes=[s0])
    history = History()

    s1 = make_stroke(20.0)
    cmd = AddStroke(doc, s1)
    cmd.apply()
    history.push(cmd)

    assert doc.strokes == [s0, s1]
    assert history.can_undo is True

    history.pop_undo()
    assert doc.strokes == [s0]
    assert history.can_undo is False


def test_erase_strokes_多条一次撤销且顺序不变():
    s0, s1, s2 = make_stroke(0.0), make_stroke(20.0), make_stroke(40.0)
    doc = Document(strokes=[s0, s1, s2])
    history = History()

    cmd = EraseStrokes(doc, [s0, s2])
    cmd.apply()
    history.push(cmd)

    assert doc.strokes == [s1]
    assert history.depth == 1  # 一次擦两条只占一个撤销单位

    history.pop_undo()
    assert doc.strokes == [s0, s1, s2]  # 撤销后顺序必须回到原位


def test_erase_strokes_只擦命中目标不误伤():
    s0, s1, s2 = make_stroke(0.0), make_stroke(20.0), make_stroke(40.0)
    doc = Document(strokes=[s0, s1, s2])

    EraseStrokes(doc, [s1]).apply()
    assert doc.strokes == [s0, s2]

    # 目标不在文档里时不应删掉任何东西
    EraseStrokes(doc, [make_stroke(99.0)]).apply()
    assert doc.strokes == [s0, s2]


def test_clear_all_撤销恢复全部且顺序不变():
    s0, s1, s2 = make_stroke(0.0), make_stroke(20.0), make_stroke(40.0)
    doc = Document(strokes=[s0, s1, s2])
    history = History()

    cmd = ClearAll(doc)
    cmd.apply()
    history.push(cmd)
    assert doc.strokes == []

    history.pop_undo()
    assert doc.strokes == [s0, s1, s2]


def test_连续动作按相反顺序撤销():
    doc = Document()
    history = History()

    first = AddStroke(doc, make_stroke(0.0))
    first.apply()
    history.push(first)

    second = AddStroke(doc, make_stroke(20.0))
    second.apply()
    history.push(second)
    assert len(doc.strokes) == 2

    history.pop_undo()
    assert [s.points[0][0] for s in doc.strokes] == [0.0]
    history.pop_undo()
    assert doc.strokes == []


def test_push_新动作清空redo():
    doc = Document()
    history = History()

    first = AddStroke(doc, make_stroke(20.0))
    first.apply()
    history.push(first)
    history.pop_undo()
    assert history.can_redo is True

    second = AddStroke(doc, make_stroke(40.0))
    second.apply()
    history.push(second)
    assert history.can_redo is False
    assert history.depth == 1


def test_空栈弹出返回None():
    history = History()
    assert history.pop_undo() is None
    assert history.pop_redo() is None
    assert history.can_undo is False
    assert history.can_redo is False
