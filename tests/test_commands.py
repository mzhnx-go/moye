"""三种可撤销动作的行为测试：加一条、橡皮一批、清屏。含交错与重做分支。"""
from sketchpad.core.commands import AddStroke, ClearAll, EraseStrokes
from sketchpad.core.strokes import Document, Stroke


def _doc(n):
    # 计划原文的 f"#100b0{i:02x}" 会多出一位（#100b000），与它自己断言的
    # "#100b00"/"#100b02"/"#100b04" 对不上；按断言口径取 6 位十六进制色值
    return Document([Stroke([(float(i), 0.0)], f"#100b{i:02x}", 3.0) for i in range(n)])


def test_add_then_undo_removes_exactly_that_stroke():
    d = _doc(0)
    s = Stroke([(1.0, 1.0)], "#000000", 6.0)
    AddStroke(d, s).apply()
    assert d.strokes == [s]
    AddStroke(d, s).unapply()      # 新对象但同值，验证按值移除
    assert d.strokes == []


def test_add_unapply_matches_by_equality_not_identity():
    d = _doc(1)
    original = d.strokes[0]
    twin = Stroke(list(original.points), original.color, original.width, original.tool)
    assert twin is not original and twin == original
    AddStroke(d, twin).unapply()
    assert d.strokes == []


def test_add_stroke_redo_reapplies_the_same_object():
    d = _doc(0)
    s = Stroke([(2.0, 3.0)], "#2980b9", 1.5)
    cmd = AddStroke(d, s)
    cmd.apply()
    cmd.unapply()
    assert d.strokes == []
    cmd.apply()                    # redo 分支再走 apply，不能一次性失效
    assert d.strokes == [s]


def test_clear_all_then_undo_restores_identical_list_in_order():
    d = _doc(300)
    before = list(d.strokes)
    cmd = ClearAll(d)
    cmd.apply()
    assert d.strokes == []
    cmd.unapply()
    assert d.strokes == before          # 条数、顺序、每个字段都一致


def test_clear_all_undo_after_further_edits():
    d = _doc(3)
    clear_cmd = ClearAll(d)
    clear_cmd.apply()
    extra = Stroke([(9.0, 9.0)], "#ff0000", 1.5)
    add_cmd = AddStroke(d, extra)
    add_cmd.apply()
    assert [s.color for s in d.strokes] == ["#ff0000"]
    add_cmd.unapply()                   # 按 LIFO 先撤后加的，再撤清屏
    assert d.strokes == []
    clear_cmd.unapply()
    assert d.strokes == list(_doc(3).strokes)


def test_two_clears_in_a_row_undo_one_step_at_a_time():
    # 设计文档 §9：清屏后立刻再清屏，第二次仍入栈，撤销一步回「空」再一步回「有」
    d = _doc(2)
    first = ClearAll(d)
    first.apply()
    second = ClearAll(d)
    second.apply()
    assert d.strokes == []
    second.unapply()
    assert d.strokes == []
    first.unapply()
    assert d.strokes == list(_doc(2).strokes)


def test_clear_all_redo_clears_again():
    d = _doc(4)
    before = list(d.strokes)
    cmd = ClearAll(d)
    cmd.apply()
    cmd.unapply()
    assert d.strokes == before
    cmd.apply()                    # 同一对象重做：重新快照并清空
    assert d.strokes == []
    cmd.unapply()
    assert d.strokes == before


def test_erase_strokes_records_indices_and_restores_them():
    d = _doc(5)
    targets = [d.strokes[1], d.strokes[3]]
    cmd = EraseStrokes(d, targets)
    cmd.apply()
    assert [s.color for s in d.strokes] == ["#100b00", "#100b02", "#100b04"]
    cmd.unapply()
    assert d.strokes == list(_doc(5).strokes)


def test_erase_one_command_is_one_undo_unit_for_whole_swipe():
    # 设计文档 §6.1：一次划过删掉的所有条 = 1 个撤销单位
    d = _doc(6)
    cmd = EraseStrokes(d, [d.strokes[i] for i in (0, 2, 4, 5)])
    cmd.apply()
    assert [s.color for s in d.strokes] == ["#100b01", "#100b03"]
    cmd.unapply()
    assert [s.color for s in d.strokes] == [f"#100b{i:02x}" for i in range(6)]


def test_erase_skips_absent_target_and_still_restores_present_ones():
    d = _doc(4)
    ghost = Stroke([(7.0, 7.0)], "#abcdef", 3.0)
    cmd = EraseStrokes(d, [d.strokes[2], ghost, d.strokes[0]])   # 目标乱序 + 文档外条目
    cmd.apply()
    assert [s.color for s in d.strokes] == ["#100b01", "#100b03"]
    cmd.unapply()
    assert [s.color for s in d.strokes] == ["#100b00", "#100b01", "#100b02", "#100b03"]


def test_erase_redo_removes_again_and_restores_order():
    d = _doc(5)
    cmd = EraseStrokes(d, [d.strokes[1], d.strokes[3]])
    cmd.apply()
    cmd.unapply()
    cmd.apply()                    # 重做：再删一次同一批
    assert [s.color for s in d.strokes] == ["#100b00", "#100b02", "#100b04"]
    cmd.unapply()
    assert d.strokes == list(_doc(5).strokes)
