"""世界↔屏幕坐标算术：screen = world * scale + origin。本模块不依赖 Qt。

origin 是世界原点 (0,0) 落在屏幕上的位置（设计文档 §5/§10）。所有函数返回新的
Viewport，绝不改动传入的那个。
"""
from __future__ import annotations

from sketchpad.core.strokes import Point, Viewport

MIN_SCALE = 0.08
MAX_SCALE = 16.0
WHEEL_ZOOM_STEP = 1.08
WHEEL_PAN_RATIO = 0.10


def clamp_scale(scale: float) -> float:
    """把缩放钳进 8%–1600%（设计文档 §6.1）；触边即停，不报错。"""
    return min(MAX_SCALE, max(MIN_SCALE, scale))


def world_to_screen(vp: Viewport, p: Point) -> Point:
    """世界点 → 屏幕点：先乘 scale，再加 origin。"""
    return (p[0] * vp.scale + vp.origin[0], p[1] * vp.scale + vp.origin[1])


def screen_to_world(vp: Viewport, p: Point) -> Point:
    """屏幕点 → 世界点：world_to_screen 的逆，先减 origin 再除 scale。"""
    return ((p[0] - vp.origin[0]) / vp.scale, (p[1] - vp.origin[1]) / vp.scale)


def pan_by(vp: Viewport, dx: float, dy: float) -> Viewport:
    """平移是纯屏幕位移：只挪 origin，scale 不变。"""
    return Viewport(vp.scale, (vp.origin[0] + dx, vp.origin[1] + dy))


def zoom_at(vp: Viewport, screen_pos: Point, factor: float) -> Viewport:
    """以 screen_pos 为锚缩放：该屏幕位置下的世界点变换后仍在这里。

    锚点（缩放前 screen_pos 底下的世界点）在缩放中固定不动，于是新 origin 由变换式
    直接解出：screen_pos = anchor * new_scale + new_origin
    → new_origin = screen_pos - anchor * new_scale。
    钳制后的 scale 同样代进这个式子，所以触边时锚点也不跑。
    """
    new_scale = clamp_scale(vp.scale * factor)
    anchor = screen_to_world(vp, screen_pos)
    return Viewport(new_scale, (screen_pos[0] - anchor[0] * new_scale,
                                screen_pos[1] - anchor[1] * new_scale))
