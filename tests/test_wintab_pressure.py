"""WinTab 压感通道：取不到时必须安静降级（返回 None），绝不抛异常。

背景：Qt6 在部分国产板上收不到 QTabletEvent，压力改从驱动挂出的 WinTab 直取。
没有板子 / DLL 缺失 / context 打不开时，画布必须退回恒定宽度（旧行为），
不能因为取压力而把画笔搞崩。
"""
from __future__ import annotations

import ctypes
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sketchpad.core import wintab


def test_struct_sizes_match_wintab_spec():
    """结构尺寸必须和 Windows x64 的 wintab 定义一致，否则会读错字段。"""
    assert ctypes.sizeof(wintab.AXIS) == 16
    assert ctypes.sizeof(wintab.LOGCONTEXTA) == 172
    assert ctypes.sizeof(wintab.PACKET) == 72
    assert wintab.PACKET.pkNormalPressure.offset == 40


def test_pressure_is_none_without_context():
    """没开 context（也没给 hwnd）时必须返回 None，不抛异常。"""
    src = wintab.WinTabSource()
    assert src.pressure() is None


def test_pressure_is_none_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    src = wintab.WinTabSource()
    assert src.pressure() is None
    assert src.ensure(12345) is False


def test_module_entry_points_never_raise():
    wintab.reset_pressure()
    wintab.close_source()
    assert wintab.pen_pressure() is None


def test_close_and_reset_are_safe():
    src = wintab.WinTabSource()
    src.close()
    src.reset()
    assert src.pressure() is None


def test_ensure_without_hwnd_does_not_open(monkeypatch):
    """hwnd 为 0/None 时不应去开 context（避免绑到错误窗口）。"""
    src = wintab.WinTabSource()
    assert src.ensure(0) is False
    assert src.pressure() is None


def test_median_filter_rejects_interleaved_phantom_stream():
    """实测队列里混着第二路压力包与真实笔压 1:1 交替：只认笔尖按下的包。

    背景：2026-10-25 data/pressure_debug.log 里线宽逐事件在 0.38x↔0.98x
    横跳（颗粒感/断续的来源）。tip 包 buttons bit0=1，杂流包没有。
    """
    src = wintab.WinTabSource()
    src._recent = [(0, 1, 55.0), (0, 0, 1010.0), (0, 1, 56.0),
                   (0, 0, 1012.0), (0, 1, 54.0)]
    value = src._value_from_recent()
    assert value is not None
    assert abs(value - 55.0 / 1023.0) < 1e-6


def test_value_from_recent_ignores_hover_zero_packets():
    """悬停包（压力恒 0、无 tip 位）不得把压力拉成 0。"""
    src = wintab.WinTabSource()
    src._recent = [(0, 0, 0.0), (0, 0, 0.0), (0, 1, 100.0)]
    assert abs(src._value_from_recent() - 100.0 / 1023.0) < 1e-6


def test_value_from_recent_falls_back_to_nonzero_without_tip_bit():
    """驱动不置 tip 位时退回"非零包中位数"，行为不比旧版差。"""
    src = wintab.WinTabSource()
    src._recent = [(0, 0, 200.0), (0, 0, 210.0), (0, 0, 220.0)]
    assert abs(src._value_from_recent() - 210.0 / 1023.0) < 1e-6


def test_value_from_recent_empty_returns_none():
    src = wintab.WinTabSource()
    assert src._value_from_recent() is None
    src._recent = [(0, 0, 0.0), (0, 0, 0.0)]      # 全是悬停包 → None（走沿用逻辑）
    assert src._value_from_recent() is None


def test_reset_clears_recent_and_keeps_safe():
    src = wintab.WinTabSource()
    src._recent = [(0, 1, 500.0)]
    src.reset()
    assert src._recent == []
    assert src.pressure() is None                  # 没开 context，仍安静返回 None
