"""笔尖光标的屏幕像素尺寸。

所有画笔粗细统一用同一个光标（用户 2026-10-24 要求：原圆点随 1.5px 细笔
缩到 4px 直径，太难点；以 4 档为基准再放大到一眼可见）。光标只表示笔尖
位置，不表示实际落墨宽度；实际线宽仍由工具栏/压感决定。
"""
from __future__ import annotations

CURSOR_RADIUS_PX = 6.0      # 空闲/定位时的 12px 直径（大，好找笔尖）
WRITING_CURSOR_RADIUS_PX = 2.5   # 落笔期间缩到 5px 直径，不挡字
CURSOR_RESTORE_MS = 3000    # 停笔 3 秒后才恢复大光标
ERASER_RADIUS_PX = 12.0     # 橡皮圆环维持 24px 直径
ERASER_RING_COLOR = "#ff453a"    # 橡皮轮廓：红色，白/深底都不容易丢
ERASER_HALO_COLOR = "#ffffff"    # 轮廓外再垫一圈白色，深色/花背景也能看清
