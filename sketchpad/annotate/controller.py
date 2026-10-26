"""批注层在 PageFlow 里的控制器：不建托盘、不建单实例、不自己退出程序。

并入 PageFlow 时删掉的是「重复的那套基础设施」：批注原来有自己的 InstanceLock、
SingleInstanceServer、托盘和 GlobalHotkey，而 PageFlow 全都有且已跑着。这里只保留
批注层本身（OverlayWindow / Canvas / Toolbar）和它自己的配置文件。

热键路由（本文件的重点）：`Ctrl+Alt+D` 同时要服务两种场景——
前台是 PageFlow 时优先满足 PageFlow（把主窗调到前台，去用它自己的批注/草稿），
前台是别的程序时才呼出全屏批注层。判定只看一个事实：前台窗口的进程 id 是不是本进程。
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from sketchpad.annotate.capture import CaptureOverlay
from sketchpad.annotate.config import CONFIG_NAME, Config
from sketchpad.annotate.overlay import OverlayWindow
from sketchpad.services.hotkey import GlobalHotkey

_USER32 = ctypes.windll.user32 if os.name == "nt" else None


def foreground_belongs_to_us() -> bool:
    """当前前台窗口是不是本进程的？仅用于热键二选一，别处不要依赖它。

    `GetForegroundWindow` 必须当下取值：批注层自己在前台时也返回 True，
    所以批注层激活后不要再触发这条路（否则会自己路由给自己）。
    """
    if _USER32 is None:
        return False
    hwnd = _USER32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = ctypes.c_ulong(0)
    _USER32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return bool(pid.value) and pid.value == os.getpid()


class AnnotateController(QObject):
    """把全屏批注层挂到已建好的 MainWindow 上；窗口销毁它跟着销毁。"""

    captured = Signal(QImage)              # 框选完成：主窗可插进笔记
    captureDone = Signal()                 # 复制/保存/取消都发一次（主窗恢复显示）

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        # 数据目录跟着墨页走（源码=data/，打包=exe 同级）：
        # AppController 已经解析过 settings 的真实路径，取它的父目录最稳。
        data_dir = Path(window.controller.settings_path).parent
        self.config = Config.load(data_dir / CONFIG_NAME)
        self.overlay = OverlayWindow(self.config)
        # 批注层的提示走 PageFlow 状态栏，不再自己弹托盘气泡
        self.overlay.trayMessage.connect(self._on_message)
        self.overlay.toolbar.captureRequested.connect(self.start_capture)
        self._capture_restore = "draw"
        self._capture_overlay: CaptureOverlay | None = None
        self._hotkeys: list[GlobalHotkey] = []
        self._capture_second_hotkeys: list[GlobalHotkey] = []
        self._capture_second_key = "S"
        self._capture_arm_timer = QTimer(self)
        self._capture_arm_timer.setSingleShot(True)
        self._capture_arm_timer.setInterval(1500)
        self._capture_arm_timer.timeout.connect(self._disarm_capture)
        self._register_hotkeys()
        # 退出接线：不碰 MainWindow.quit_app，靠应用级信号收尾
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # ---------- 热键 ----------

    def _register_hotkeys(self) -> None:
        """批注热键；截图支持两步序列（Alt+A → S），失败只提示不拦启动。"""
        self._hotkeys = [
            self._register_one(self.config.toggle_hotkey, self.on_toggle),
            self._register_one(self.config.hide_hotkey, self.on_hide),
        ]
        for sequence, slot in (
                (getattr(self.config, "passthrough_hotkey", "Alt+1"),
                 self.on_passthrough),
                (getattr(self.config, "pen_hotkey", "Alt+2"), self.on_pen),
                (getattr(self.config, "eraser_hotkey", "Alt+3"), self.on_eraser),
                (getattr(self.config, "clear_hotkey", "Alt+4"), self.on_clear)):
            self._hotkeys.append(self._register_one(sequence, slot))
        capture = getattr(self.config, "capture_hotkey", "Alt+A+S")
        parts = [part.strip() for part in str(capture).split("+") if part.strip()]
        if len(parts) >= 3:
            # 第一步注册成普通全局热键；第二步在 1.5s 窗口内临时注册（含裸键）
            self._capture_second_key = parts[-1]
            first = "+".join(parts[:-1])
            hotkey = self._register_one(first, self._arm_capture)
            self._hotkeys.append(hotkey)
        else:
            self._hotkeys.append(self._register_one(capture, self.start_capture))

    def _register_one(self, sequence: str, slot) -> GlobalHotkey:
        hotkey = GlobalHotkey(self)
        try:
            registered = hotkey.register(0, sequence)
        except Exception:
            registered = False
        if registered:
            hotkey.activated.connect(slot)
        else:
            reason = getattr(hotkey, "error_message", "") or "注册失败"
            self._on_message(f"批注热键 {sequence} 不可用（{reason}）；"
                             f"可用托盘菜单「全屏批注」")
        return hotkey

    def _arm_capture(self) -> None:
        """Alt+A 已按下：注册第二步 1.5 秒（S / Alt+S 都接受）。"""
        self._disarm_capture()
        key = self._capture_second_key
        for sequence, allow_bare in ((f"Alt+{key}", False), (key, True)):
            hotkey = GlobalHotkey(self)
            try:
                registered = hotkey.register(0, sequence, allow_bare=allow_bare)
            except TypeError:
                registered = False
            except Exception:
                registered = False
            if registered:
                hotkey.activated.connect(self._on_capture_second)
                self._capture_second_hotkeys.append(hotkey)
            else:
                hotkey.deleteLater()
        if not self._capture_second_hotkeys:
            self._on_message("截图第二步不可用；请用工具栏截图按钮")
            return
        self._capture_arm_timer.start()
        # 不再往状态栏塞“1.5 秒内按 S”的提示：这是熟练操作，提示反而挡持久状态。

    def _on_capture_second(self) -> None:
        self._disarm_capture()
        self.start_capture()

    def _disarm_capture(self) -> None:
        self._capture_arm_timer.stop()
        for hotkey in self._capture_second_hotkeys:
            try:
                hotkey.unregister()
            except Exception:
                pass
            hotkey.deleteLater()
        self._capture_second_hotkeys.clear()

    def on_toggle(self) -> None:
        """`Ctrl+Alt+D`：前台是 PageFlow → 优先把它调到前台；否则呼出全屏批注层。"""
        if foreground_belongs_to_us():
            self._raise_main_window()
            return
        # 打开默认穿透：先不拦截鼠标，用户按 Alt+2 / 钢笔再开始画
        if self.overlay.mode == "hidden":
            self.overlay.enter_passthrough_mode()
        else:
            self.overlay.hide_overlay()

    def on_passthrough(self) -> None:
        """Alt+1：穿透模式（笔迹仍可见，点击落到底层应用）。"""
        self.overlay.enter_passthrough_mode()

    def on_pen(self) -> None:
        """Alt+2：切钢笔并进入批注模式。"""
        self.overlay.activate_tool("pen")

    def on_eraser(self) -> None:
        """Alt+3：切橡皮并进入批注模式。"""
        self.overlay.activate_tool("eraser")

    def on_clear(self) -> None:
        """Alt+4：清空全屏批注层（可撤销）。"""
        self.overlay.clear_canvas()

    def on_hide(self) -> None:
        """`Ctrl+Alt+H`：批注层显隐；穿透状态按一下也直接收起。"""
        if self.overlay.mode == "hidden":
            self.overlay.enter_passthrough_mode()
        else:
            self.overlay.hide_overlay()

    # ---------- 对外动作 ----------

    def enter_draw(self) -> None:
        self.overlay.enter_draw_mode()

    def enter_passthrough(self) -> None:
        self.overlay.enter_passthrough_mode()

    def hide(self) -> None:
        self.overlay.hide_overlay()

    def toggle(self) -> None:
        self.overlay.toggle_mode()

    # ---------- 截图 ----------

    def start_capture(self) -> None:
        """框选屏幕并进入截图遮罩。

        正常启动时统一委托给 `ScreenshotController`（新 in-place 遮罩负责
        冻结屏/选区/标注/恢复主窗）；没有截图控制器时保留旧的框选-复制流程
        （测试/极简启动），避免批注层单独使用时报错。
        """
        screenshot = getattr(self.window, "_screenshot", None)
        if screenshot is not None:
            screenshot.start("region")
            return
        if self._capture_overlay is not None:
            try:
                self._capture_overlay._cancel()
            except Exception:
                pass
            self._capture_overlay = None
        self._capture_restore = self.overlay.mode
        self.overlay.hide_overlay()
        # 隐藏要等一帧才真正从合成树里掉下去，否则抓到的还是带工具栏的画面
        QTimer.singleShot(80, self._grab_and_show)

    def _grab_and_show(self) -> None:
        # 鼠标在哪块屏就截哪块：多屏时不会把副屏截成黑块
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            self._restore_after_capture()
            return
        shot = screen.grabWindow(0)               # QPixmap：整个主屏
        picker = CaptureOverlay(shot.toImage(), screen.geometry())
        # 必须留强引用：parent=None 的 QWidget 一旦局部变量出作用域就会被回收，
        # 表现就是“点了截图按钮但选择界面一闪/根本不出现”。
        self._capture_overlay = picker
        picker.finished.connect(
            lambda image, p=picker: self._finish_picker(p) or self._on_captured(image))
        picker.cancelled.connect(lambda p=picker: self._finish_picker(p) or
                                 self._restore_after_capture())
        picker.destroyed.connect(lambda _obj=None, p=picker: self._finish_picker(p))
        picker.show()
        picker.raise_()
        picker.activateWindow()

    def _finish_picker(self, picker: CaptureOverlay) -> None:
        if self._capture_overlay is picker:
            self._capture_overlay = None

    def _on_captured(self, image: QImage) -> None:
        if self._capture_overlay is not None:
            self._capture_overlay = None
        clipboard = QGuiApplication.clipboard()
        clipboard.setImage(image)
        self.captured.emit(image)
        self._on_message(f"截图已复制到剪贴板（{image.width()}×{image.height()}）")
        self._restore_after_capture()

    def _restore_after_capture(self) -> None:
        self.captureDone.emit()
        if self._capture_restore == "passthrough":
            self.overlay.enter_passthrough_mode()
        elif self._capture_restore == "draw":
            self.overlay.enter_draw_mode()
        else:
            self.overlay.hide_overlay()

    # ---------- 内部 ----------

    def _raise_main_window(self) -> None:
        """把 PageFlow 主窗调到前台（与托盘「显示主窗」同义）。"""
        window = self.window
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()

    def _on_message(self, text: str) -> None:
        note = getattr(self.window, "_note", None)
        if callable(note):
            note(text)
        # 主窗可能缩在托盘里，状态栏看不到；独占全屏这类“需用户处理”的
        # 提示额外弹一次托盘气泡，但不为普通热键提示刷屏。
        if "独占全屏" in text:
            tray = getattr(self.window, "tray", None)
            if tray is not None and hasattr(tray, "showMessage"):
                try:
                    tray.showMessage("全屏批注", text,
                                     QSystemTrayIcon.MessageIcon.Information,
                                     5000)
                except Exception:
                    pass

    def shutdown(self) -> None:
        """注销热键、收掉覆盖层；可重复调用。"""
        self._disarm_capture()
        for hotkey in self._hotkeys:
            try:
                hotkey.unregister()
            except Exception:
                pass
        self._hotkeys.clear()
        self.overlay.hide_overlay()
