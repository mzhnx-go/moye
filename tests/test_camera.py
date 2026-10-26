"""sketchpad/core/camera.py 的测试：变换约定、平移、锚点缩放与上下限钳制。"""
import pytest

from sketchpad.core.camera import (MAX_SCALE, MIN_SCALE, WHEEL_PAN_RATIO,
                        WHEEL_ZOOM_STEP, Viewport, clamp_scale, pan_by,
                        screen_to_world, world_to_screen, zoom_at)


def test_constants_are_the_spec_numbers():
    assert (MIN_SCALE, MAX_SCALE, WHEEL_ZOOM_STEP, WHEEL_PAN_RATIO) == (0.08, 16.0, 1.08, 0.10)


def test_world_to_screen_scales_then_offsets():
    assert world_to_screen(Viewport(2.0, (10.0, -5.0)), (3.0, 4.0)) == (16.0, 3.0)


def test_screen_to_world_offsets_then_divides():
    assert screen_to_world(Viewport(2.0, (10.0, -5.0)), (16.0, 3.0)) == (3.0, 4.0)


def test_transform_round_trip_is_exact_enough():
    vp = Viewport(2.5, (13.0, -4.0))
    p = (101.0, 77.0)
    assert screen_to_world(vp, world_to_screen(vp, p)) == pytest.approx(p, abs=1e-9)


def test_pan_moves_origin_and_leaves_scale():
    vp = Viewport(1.0, (0.0, 0.0))
    after = pan_by(vp, 0.0, 100.0)
    assert after.origin == (0.0, 100.0)
    assert after.scale == 1.0


def test_pan_shifts_the_visible_world_by_exactly_the_screen_delta():
    # 屏幕挪 N 像素，光标处看到的世界点就该挪回原点：位移量除以 scale
    vp = Viewport(2.0, (0.0, 0.0))
    after = pan_by(vp, 10.0, -20.0)
    assert screen_to_world(after, (10.0, -20.0)) == (0.0, 0.0)


def test_pan_by_never_mutates_its_input():
    vp = Viewport(3.0, (10.0, 20.0))
    after = pan_by(vp, -2.5, 4.5)
    assert vp == Viewport(3.0, (10.0, 20.0))
    assert after == Viewport(3.0, (7.5, 24.5))


def test_zoom_at_keeps_the_world_point_under_the_cursor():
    # 设计文档 §12「camera」的锚点不变量
    vp = Viewport(1.0, (0.0, 0.0))
    cursor = (400.0, 300.0)
    before = screen_to_world(vp, cursor)
    after = zoom_at(vp, cursor, 1.08 ** 3)
    assert screen_to_world(after, cursor) == pytest.approx(before, abs=1e-6)
    assert after.scale == pytest.approx(1.08 ** 3)


def test_zoom_at_anchor_holds_with_offset_origin_and_shrinking():
    # 非平凡锚点：origin 不在 0、scale<1、方向是缩小，两个方向都验
    vp = Viewport(0.5, (-120.0, 75.0))
    cursor = (233.0, 411.0)
    anchor = screen_to_world(vp, cursor)
    assert anchor == (706.0, 672.0)
    after = zoom_at(vp, cursor, 1.0 / 1.08 ** 4)
    assert after.scale == pytest.approx(0.5 / 1.08 ** 4)
    assert world_to_screen(after, anchor) == pytest.approx(cursor, abs=1e-9)
    assert screen_to_world(after, cursor) == pytest.approx(anchor, abs=1e-9)


def test_zoom_at_with_factor_one_changes_nothing():
    # scale 取二进制整齐的 2.0，锚点解出的 origin 与原来逐位相等
    vp = Viewport(2.0, (13.0, -4.0))
    after = zoom_at(vp, (400.0, 300.0), 1.0)
    assert (after.scale, after.origin) == (2.0, (13.0, -4.0))


def test_zoom_clamps_at_max_and_min():
    vp = Viewport(15.9, (0.0, 0.0))
    assert zoom_at(vp, (0.0, 0.0), 1000.0).scale == MAX_SCALE
    # 计划此处写 == 1.0，但 15.9×(1/1000)=0.0159 只能钳到下限，1.0 无从出现
    assert zoom_at(vp, (0.0, 0.0), 1.0 / 1000.0).scale == MIN_SCALE
    assert clamp_scale(0.001) == MIN_SCALE
    assert clamp_scale(999.0) == MAX_SCALE
    assert (clamp_scale(0.08), clamp_scale(16.0), clamp_scale(1.0)) == (0.08, 16.0, 1.0)


def test_zoom_at_the_edge_stops_moving_yet_keeps_the_anchor():
    # 设计文档 §9「缩放触边」：到 1600% 后继续放大不漂移画面
    vp = Viewport(15.9, (0.0, 0.0))
    cursor = (400.0, 300.0)
    once = zoom_at(vp, cursor, 1.08)
    twice = zoom_at(once, cursor, 1.08)
    assert (once.scale, twice.scale) == (MAX_SCALE, MAX_SCALE)
    assert twice.origin == pytest.approx(once.origin, abs=1e-9)
    assert world_to_screen(twice, screen_to_world(vp, cursor)) == pytest.approx(cursor, abs=1e-6)


def test_zoom_at_never_mutates_its_input():
    vp = Viewport(1.0, (0.0, 0.0))
    zoom_at(vp, (400.0, 300.0), 1.08 ** 3)
    assert vp == Viewport(1.0, (0.0, 0.0))
