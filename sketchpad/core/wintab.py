"""Windows 数位板压力直取（WinTab）——Qt 平板事件不可用时的兜底通道。

为什么需要它：Qt6 在部分国产板（高漫/绘王等）上把笔识别成 'core pointer'
（Generic 普通鼠标），收不到 QTabletEvent，合成鼠标事件里也没有压力字段；
Windows Pointer API（WM_POINTER* + GetPointerPenInfo）同样一条都收不到。
而板子驱动其实在系统里挂出了可用的 **WinTab** 服务（默认 context 名形如
`Gaomon_DEFCONTEXT`）——Lorien（Godot 配 pen_tablet/driver.windows="wintab"）
走的正是这条，实测能取到压力。

做法：LoadLibrary wintab32.dll，开一个 context，在鼠标事件里同步调用
WTPacketsGet 拉队列，取最新一个 packet 的 pkNormalPressure 归一化（0–1）。
不常驻线程/定时器；DLL 缺失、没有板子、context 打不开时一律返回 None，
绝不抛异常（调用方拿不到就按恒定宽度画，行为与以前一致）。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

# --- WinTab 常量 ---
WTI_DEFCONTEXT = 3
WTI_DEVICES = 100
DVC_NPRESSURE = 23
CXO_MESSAGES = 0x0004
WT_DEFBASE = 0x7FF0
WANT_PKT = 0x3FFF          # 订阅全部字段位 → packet 布局 = 完整 PACKET(72B)
DEFAULT_PRESSURE_MAX = 1023.0
_STALE_SEC = 0.2           # 超过这么久没新包就认为笔已离板，不再沿用旧压力
_DEBUG_ENV = "SKETCHPAD_PRESSURE_DEBUG"
_debug_path: Path | None = None


def _debug(msg: str) -> None:
    """排查开关：SKETCHPAD_PRESSURE_DEBUG=1 时把关键步骤写进 data/pressure_debug.log。

    程序是 pythonw 启动的，没有控制台，print 看不见 —— 只能落文件。
    """
    global _debug_path
    if not os.environ.get(_DEBUG_ENV):
        return
    try:
        if _debug_path is None:
            _debug_path = Path(__file__).resolve().parents[2] / "data" \
                / "pressure_debug.log"
            _debug_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_debug_path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


class AXIS(ctypes.Structure):
    _fields_ = [("axMin", ctypes.c_int32), ("axMax", ctypes.c_int32),
                ("axUnits", ctypes.c_uint32), ("axResolution", ctypes.c_int32)]


class LOGCONTEXTA(ctypes.Structure):
    _fields_ = [
        ("lcName", ctypes.c_char * 40),
        ("lcOptions", ctypes.c_uint32), ("lcStatus", ctypes.c_uint32),
        ("lcLocks", ctypes.c_uint32), ("lcMsgBase", ctypes.c_uint32),
        ("lcDevice", ctypes.c_uint32), ("lcPktRate", ctypes.c_uint32),
        ("lcPktData", ctypes.c_uint32), ("lcPktMode", ctypes.c_uint32),
        ("lcMoveMask", ctypes.c_uint32),
        ("lcBtnDnMask", ctypes.c_uint32), ("lcBtnUpMask", ctypes.c_uint32),
        ("lcInOrgX", ctypes.c_int32), ("lcInOrgY", ctypes.c_int32),
        ("lcInOrgZ", ctypes.c_int32),
        ("lcInExtX", ctypes.c_int32), ("lcInExtY", ctypes.c_int32),
        ("lcInExtZ", ctypes.c_int32),
        ("lcOutOrgX", ctypes.c_int32), ("lcOutOrgY", ctypes.c_int32),
        ("lcOutOrgZ", ctypes.c_int32),
        ("lcOutExtX", ctypes.c_int32), ("lcOutExtY", ctypes.c_int32),
        ("lcOutExtZ", ctypes.c_int32),
        ("lcSensX", ctypes.c_int32), ("lcSensY", ctypes.c_int32),
        ("lcSensZ", ctypes.c_int32),
        ("lcSysMode", ctypes.c_int32),
        ("lcSysOrgX", ctypes.c_int32), ("lcSysOrgY", ctypes.c_int32),
        ("lcSysExtX", ctypes.c_int32), ("lcSysExtY", ctypes.c_int32),
        ("lcSysSensX", ctypes.c_int32), ("lcSysSensY", ctypes.c_int32),
    ]


class PACKET(ctypes.Structure):
    _fields_ = [
        ("pkContext", ctypes.c_uint32), ("pkStatus", ctypes.c_uint32),
        ("pkTime", ctypes.c_int32), ("pkChanged", ctypes.c_uint32),
        ("pkSerialNumber", ctypes.c_uint32), ("pkCursor", ctypes.c_uint32),
        ("pkButtons", ctypes.c_uint32),
        ("pkX", ctypes.c_uint32), ("pkY", ctypes.c_uint32),
        ("pkZ", ctypes.c_uint32),
        ("pkNormalPressure", ctypes.c_uint32),
        ("pkTangentPressure", ctypes.c_uint32),
        ("pkOrientation", ctypes.c_int32 * 3),
        ("pkRotation", ctypes.c_int32 * 3),
    ]


class WinTabSource:
    """单个 WinTab context；不可用时所有方法安静返回 None/False。"""

    def __init__(self) -> None:
        self._dll = None
        self._hctx = None
        self._ax_max = DEFAULT_PRESSURE_MAX
        self._gave_up = False        # 试过且不可用，不再重试（避免反复 LoadLibrary）
        self._last_value: float | None = None
        self._last_time = 0.0
        self._hwnd = 0               # 当前 context 绑定的窗口句柄（用于失效检测）
        self._user32 = None          # ctypes.windll.user32（IsWindow 失效检测）
        try:
            self._user32 = ctypes.windll.user32
            self._user32.IsWindow.argtypes = [ctypes.c_void_p]
            self._user32.IsWindow.restype = ctypes.c_int
        except Exception:
            self._user32 = None
        # 最近 drained 的包 (pkCursor, pkButtons, pkNormalPressure)，供滤波
        self._recent: list[tuple[int, int, float]] = []
        self._buf = (PACKET * 32)()

    # --- 生命周期 ---
    def _load(self):
        if self._dll is not None:
            return self._dll
        if self._gave_up or sys.platform != "win32":
            _debug(f"跳过加载：gave_up={self._gave_up} platform={sys.platform}")
            return None
        try:
            dll = ctypes.WinDLL("wintab32")
        except OSError as exc:
            self._gave_up = True
            _debug(f"LoadLibrary wintab32 失败: {exc}")
            return None
        _debug("wintab32.dll 加载成功")
        try:
            dll.WTInfoA.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
            dll.WTInfoA.restype = ctypes.c_uint
            dll.WTOpenA.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
            dll.WTOpenA.restype = ctypes.c_void_p
            dll.WTClose.argtypes = [ctypes.c_void_p]
            dll.WTClose.restype = ctypes.c_int
            dll.WTPacketsGet.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                         ctypes.c_void_p]
            dll.WTPacketsGet.restype = ctypes.c_int
        except AttributeError:
            self._gave_up = True
            return None
        self._dll = dll
        return dll

    def ensure(self, hwnd: int) -> bool:
        """打开 context（幂等）。hwnd 用画布/主窗的 winId()。

        hwnd 变了（窗口重建/换屏/DPI 变化）就关旧的开新的，避免 context
        一直绑在已失效的窗口句柄上，导致后续 WTPacketsGet 触发的驱动级崩溃。
        """
        hwnd = int(hwnd)
        if self._hctx is not None:
            if self._hwnd == hwnd:
                return True
            self.close()                 # HWND 变了 → 先收掉旧的
        self._hwnd = hwnd
        dll = self._load()
        _debug(f"ensure(hwnd={hwnd}) dll={dll is not None}")
        if dll is None or not hwnd:
            return False
        try:
            if not dll.WTInfoA(0, 0, None):
                _debug("WTInfoA(0,0,NULL)=0 → 无可用 WinTab 服务")
                return False                      # 系统里没有可用的 WinTab 服务
            ctx = LOGCONTEXTA()
            if dll.WTInfoA(WTI_DEFCONTEXT, 0, ctypes.byref(ctx)):
                axis = AXIS()
                if dll.WTInfoA(WTI_DEVICES, DVC_NPRESSURE, ctypes.byref(axis)) \
                        and axis.axMax > 0:
                    self._ax_max = float(axis.axMax)
            # 纯轮询取包：不挂 CXO_MESSAGES，避免 WinTab 向窗口投递消息带来的
            # 句柄绑定风险（窗口失效时驱动内部崩溃通常无 Python 回溯）。
            ctx.lcOptions = 0
            ctx.lcPktData = WANT_PKT
            ctx.lcPktMode = 0
            ctx.lcMoveMask = 0x3FFF
            ctx.lcBtnUpMask = ctx.lcBtnDnMask = 0xFFFFFFFF
            ctx.lcMsgBase = WT_DEFBASE            # 显式：消息号从 0x7FF0 起
            hctx = dll.WTOpenA(ctypes.c_void_p(int(hwnd)), ctypes.byref(ctx), 1)
        except Exception as exc:
            _debug(f"ensure 异常: {exc!r}")
            return False
        _debug(f"WTOpenA -> {hctx}")
        if not hctx:
            return False
        self._hctx = hctx
        _debug(f"context 已打开 ax_max={self._ax_max}")
        return True

    def close(self) -> None:
        if self._hctx is not None and self._dll is not None:
            try:
                self._dll.WTClose(ctypes.c_void_p(self._hctx))
            except Exception:
                pass
        self._hctx = None
        self._last_value = None

    # --- 取压力 ---
    def pressure(self, hwnd: int = 0) -> float | None:
        """最近一次笔尖压力（0–1）。取不到返回 None。

        hwnd 只在首次打开 context 时需要；之后可传 0。
        """
        if hwnd and not self.ensure(hwnd):
            return None
        # 窗口句柄可能在运行时失效（Qt 重建原生窗口 / 换屏 / DPI 变化）→
        # 旧 context 绑着死句柄，下一步 WTPacketsGet 会触发驱动级崩溃。
        # 主动检测并在失效时关掉，下次 ensure 会用新 HWND 重建。
        if self._hctx is not None and self._hwnd and self._user32 is not None:
            try:
                if not self._user32.IsWindow(ctypes.c_void_p(self._hwnd)):
                    _debug(f"HWND {self._hwnd} 已失效，关闭 context 待重建")
                    self.close()
            except Exception:
                pass
        if self._hctx is None:
            return None
        try:
            n = self._dll.WTPacketsGet(ctypes.c_void_p(self._hctx), 32, self._buf)
        except Exception as exc:
            _debug(f"WTPacketsGet 异常: {exc!r}")
            return None
        self._tick = getattr(self, "_tick", 0) + 1
        if n > 0 and self._tick % 25 == 1:    # 采样记包明细，避免刷爆日志
            detail = " ".join(
                f"c{self._buf[j].pkCursor:x}/b{self._buf[j].pkButtons:x}:"
                f"{self._buf[j].pkNormalPressure}" for j in range(n)) or "-"
            _debug(f"pressure n={n} last={self._last_value} pk[{detail}]")
        if n > 0:
            # 防御：驱动偶尔会报超出请求大小的包数，越界访问 buf 会直接崩
            for j in range(min(int(n), len(self._buf))):
                pkt = self._buf[j]
                self._recent.append((int(pkt.pkCursor), int(pkt.pkButtons),
                                     float(pkt.pkNormalPressure)))
            if len(self._recent) > 16:        # 只保留最近 16 包（原写法会误清空）
                self._recent = self._recent[-16:]
            value = self._value_from_recent()
            if value is not None:
                self._last_value = value
                self._last_time = time.monotonic()
                return value
        # 本次没新包：短时间内沿用上一个值（笔慢速移动），超时则认为笔已离板
        if self._last_value is not None and \
                (time.monotonic() - self._last_time) <= _STALE_SEC:
            return self._last_value
        return None

    def _value_from_recent(self) -> float | None:
        """从最近 drained 的包里算压力（0–1）；没有可用包返回 None。

        实测（高漫 1060Pro，2026-10-25 data/pressure_debug.log）：队列里混着
        第二路压力包（~满量程），和真实笔压逐包 1:1 交替，"取末包/取均值"
        都会让线宽逐事件在 0.38x↔0.98x 之间横跳——就是"颗粒感/断断续续"。
        两层过滤：
        1. 只认笔尖按下的包（pkButtons bit0 = TBN_TIP）：悬停包压力恒 0、
           杂流包不带 tip 位，一并排除；
        2. 奇数窗（最近 5 个）取中位数：就算还有交替残流，中位数也稳定。
        """
        tip = [raw for _cur, btn, raw in self._recent if btn & 1 and raw > 0.0]
        pool = tip or [raw for _cur, _btn, raw in self._recent if raw > 0.0]
        if not pool:
            return None
        window = sorted(pool[-5:])
        raw = window[len(window) // 2]
        return min(1.0, max(0.0, raw / self._ax_max))

    def reset(self) -> None:
        """抬笔后调用：清缓存 + 排空队列，避免下一笔沿用上一笔的力度/残包。"""
        self._recent = []
        self._last_value = None
        if self._hctx is not None and self._dll is not None:
            try:                              # 排空残留包（上一笔尾部/悬停包）
                self._dll.WTPacketsGet(ctypes.c_void_p(self._hctx), 32, self._buf)
            except Exception:
                pass


_source = WinTabSource()


def pen_pressure(hwnd: int = 0) -> float | None:
    """模块级入口：取当前笔压（0–1），不可用返回 None。

    ⚠️ 2026-10-25 按用户要求**停用**：稳定性优先，压感取数链路暂不使用。
    直接返回 None，调用方（canvas / pdfview 的 _mouse_pressure）退化为无压感
    路径——与"决定使用 wintab 之前"的行为完全一致。

    这样确保：不再 LoadLibrary wintab32、不再打开/绑定 context、不再有任何
    WTPacketsGet 调用，彻底消除此前"窗口句柄失效触发驱动级崩溃"的风险。
    `WinTabSource` 的完整实现保留在下方，便于将来在稳定前提下恢复压感。
    """
    return None


def reset_pressure() -> None:
    _source.reset()


def close_source() -> None:
    _source.close()


def debug_log(msg: str) -> None:
    """对外别名：给 UI 层记录排查信息（受同一个环境变量开关控制）。"""
    _debug(msg)


# 开关打开时，模块一载入就落一行：用来确认环境变量确实进了进程、且跑的是新代码。
_debug(f"wintab 模块已载入（{_DEBUG_ENV}={os.environ.get(_DEBUG_ENV)!r} "
       f"platform={sys.platform}）")
