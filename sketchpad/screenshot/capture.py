"""抓屏与截图模式：区域 / 窗口 / 全屏 / 上一区域。

区域框选沿用 `sketchpad.annotate.capture.CaptureOverlay`（已有高 DPI/多屏
换算与实测）；窗口模式用 Win32 `WindowFromPoint` + DWM 边框，拿不到就退回
全屏，不让截图失败。HTML 里不做任何“猜窗口”的启发式。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCursor, QGuiApplication, QImage

from sketchpad.screenshot import imageops

_GA_ROOT = 2
_MONITOR_DEFAULTTONEAREST = 2
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


if os.name == "nt":
    _user32 = ctypes.windll.user32
    _dwmapi = ctypes.windll.dwmapi
    _user32.WindowFromPoint.argtypes = [wintypes.POINT]
    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND,
                                      ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _dwmapi.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
else:
    _user32 = None
    _dwmapi = None


def screen_at(point: QPoint | None = None):
    return (QGuiApplication.screenAt(point or QCursor.pos())
            or QGuiApplication.primaryScreen())


def grab_screen(screen) -> QImage:
    if screen is None:
        return QImage()
    return imageops.normalize(screen.grabWindow(0).toImage())


def grab_fullscreen(point: QPoint | None = None) -> QImage:
    return grab_screen(screen_at(point))


def _window_handle_at(point: QPoint) -> int | None:
    """鼠标下最外层窗口句柄；属本进程/不可见时返回 None。"""
    if _user32 is None:
        return None
    handle = _user32.WindowFromPoint(wintypes.POINT(int(point.x()), int(point.y())))
    if not handle:
        return None
    root = _user32.GetAncestor(handle, _GA_ROOT) or handle
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    if pid.value == os.getpid():
        return None                 # 不截自己（墨页窗口）
    if not _user32.IsWindowVisible(root):
        return None
    return int(root)


def _physical_window_rect(handle: int) -> QRect | None:
    rect = wintypes.RECT()
    ok = 0
    if _dwmapi is not None:
        try:
            ok = _dwmapi.DwmGetWindowAttribute(
                handle, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                ctypes.sizeof(rect))
        except OSError:
            ok = 1
    if ok != 0:
        if not _user32.GetWindowRect(handle, ctypes.byref(rect)):
            return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 8 or height < 8:
        return None
    return QRect(rect.left, rect.top, width, height)


def window_rect_at(point: QPoint) -> QRect | None:
    """鼠标下顶层窗口矩形（Win32 物理坐标，仅作兜底用）。"""
    handle = _window_handle_at(point)
    if handle is None:
        return None
    return _physical_window_rect(handle)


def window_rect_logical_at(point: QPoint) -> QRect | None:
    """窗口矩形换算到 Qt 逻辑坐标（高 DPI 下窗口模式初始选区用）。

    Win32 矩形是物理像素；用 MonitorFromWindow + MONITORINFOEX 的物理显示器
    矩形和 QScreen 的 DPR/逻辑矩形换算。找不到对应 QScreen 时退回物理矩形。
    """
    handle = _window_handle_at(point)
    if handle is None:
        return None
    physical = _physical_window_rect(handle)
    if physical is None:
        return None
    if _user32 is None:
        return physical
    monitor = _user32.MonitorFromWindow(handle, _MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return physical
    device = info.szDevice
    screen = next((item for item in QGuiApplication.screens()
                   if item.name() == device), None)
    if screen is None:
        return physical
    dpr = max(0.1, float(screen.devicePixelRatio()))
    monitor_rect = info.rcMonitor
    local_x = (physical.left() - monitor_rect.left) / dpr
    local_y = (physical.top() - monitor_rect.top) / dpr
    geometry = screen.geometry()
    return QRect(round(geometry.left() + local_x),
                 round(geometry.top() + local_y),
                 max(1, round(physical.width() / dpr)),
                 max(1, round(physical.height() / dpr)))


def grab_window_at(point: QPoint) -> QImage:
    """直接让 Qt 抓指定窗口，避免 Win32 物理坐标与 Qt 逻辑坐标在高 DPI 下错位。

    Qt 抓不到（部分受保护/最小化窗口）时，才退回“抓整屏再按矩形裁”。
    """
    handle = _window_handle_at(point)
    if handle is None:
        return QImage()
    screen = screen_at(point)
    if screen is not None:
        pixmap = screen.grabWindow(handle)
        if not pixmap.isNull():
            return imageops.normalize(pixmap.toImage())
    rect = window_rect_at(point)
    if rect is None:
        return QImage()
    return crop_from_screen(rect, point)


def crop_from_screen(rect: QRect, point: QPoint | None = None) -> QImage:
    """从当前屏幕抓图里裁出逻辑矩形；多屏按矩形中心选屏并换算物理像素。"""
    if rect.width() < 1 or rect.height() < 1:
        return QImage()
    screen = screen_at(point or rect.center())
    if screen is None:
        return QImage()
    image = grab_screen(screen)
    if image.isNull():
        return QImage()
    geometry = screen.geometry()
    local = rect.translated(-geometry.topLeft())
    sx = image.width() / max(1, geometry.width())
    sy = image.height() / max(1, geometry.height())
    source = QRect(round(local.x() * sx), round(local.y() * sy),
                   max(1, round(local.width() * sx)),
                   max(1, round(local.height() * sy)))
    source = source.intersected(image.rect())
    if source.isEmpty():
        return QImage()
    return image.copy(source)
