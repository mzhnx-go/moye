"""截图流程控制器：冻结屏幕 → 全屏遮罩内选区和标注 → 复制/保存/贴图/OCR/历史。

与批注控制器分开：批注层管「在屏幕上写」，这里管「把屏幕变成一张可编辑的图」。
全屏遮罩（`ScreenshotOverlay`）是生产路径；`open_editor()` 保留同名 API，
用于历史重新编辑，也打开同一个遮罩（图居中显示、初始选区=整张图）。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from sketchpad.screenshot.capture import (grab_fullscreen, grab_screen,
                                          screen_at, window_rect_logical_at)
from sketchpad.screenshot.constants import CAPTURE_MODES
from sketchpad.screenshot.history import ScreenshotHistory
from sketchpad.screenshot.history_ui import HistoryDialog
from sketchpad.screenshot.ocr import ocr_available, recognize
from sketchpad.screenshot.overlay import ScreenshotOverlay
from sketchpad.screenshot.pin import PinWindow

_CAPTURE_DELAY_MS = 80        # 等窗口从合成树里掉下去，否则会截到自己


class ScreenshotController(QObject):
    captured = Signal(QImage)               # 兼容旧接线；新流程不自动发
    finished = Signal()                     # 一次截图流程结束（主窗可恢复）
    editorClosed = Signal()

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        data_dir = Path(window.controller.settings_path).parent
        self.history = ScreenshotHistory(data_dir / "screenshots")
        self._last_region_path = data_dir / "screenshots" / "last_region.json"
        self._last_region: QRect | None = self._read_last_region()
        self._editor: ScreenshotOverlay | None = None
        self._editor_source = "region"
        self._pins: list[PinWindow] = []
        self._suspended_pins: list[PinWindow] = []
        self._history_dialog: HistoryDialog | None = None
        self._capture_active = False
        self._restore_mode: str | None = None
        self._window_was_visible = False
        self._finished_once = False
        self._shutting_down = False

    # ---------- 截图入口 ----------

    def start(self, mode: str = "region") -> bool:
        if self._capture_active:
            return False
        if mode not in dict(CAPTURE_MODES):
            mode = "region"
        old_editor = self._editor
        if old_editor is not None:
            self._editor = None
            try:
                old_editor.cancel()
            except RuntimeError:
                pass
        self._capture_active = True
        self._finished_once = False
        self._editor_source = mode
        self._hide_annotate()
        self._window_was_visible = self.window.isVisible()
        if self._window_was_visible:
            self.window.hide()
        QTimer.singleShot(_CAPTURE_DELAY_MS, lambda: self._grab(mode))
        return True

    def _grab(self, mode: str) -> None:
        if not self._capture_active:
            return
        try:
            self._grab_mode(mode)
        except Exception as exc:              # 抓屏/建遮罩失败时别把主窗锁在隐藏态
            self._note(f"截图失败：{exc}")
            self._cancel_capture()

    def _grab_mode(self, mode: str) -> None:
        if mode == "color":
            screen = screen_at(QCursor.pos())
            if screen is None:
                self._cancel_capture("没有可用屏幕")
                return
            image = grab_screen(screen)
            if not self._check(image):
                return
            self._show_overlay(image, screen.geometry(), None, "color",
                               color_mode=True)
            return

        if mode == "window":
            rect = window_rect_logical_at(QCursor.pos())
            if rect is None:
                self._note("没有找到可截的窗口，改为区域截图")
                self._grab_region()
                return
            screen = screen_at(rect.center()) or screen_at(QCursor.pos())
            if screen is None:
                self._cancel_capture("没有可用屏幕")
                return
            image = grab_screen(screen)
            if not self._check(image):
                return
            initial = self._rect_to_scene(rect, screen, image)
            self._show_overlay(image, screen.geometry(), initial, "window")
            return

        if mode == "last":
            if self._last_region is None:
                self._note("还没有上一区域，改为区域截图")
                self._grab_region()
                return
            screen = screen_at(self._last_region.center()) or screen_at(QCursor.pos())
            if screen is None:
                self._cancel_capture("没有可用屏幕")
                return
            image = grab_screen(screen)
            if not self._check(image):
                return
            initial = self._rect_to_scene(self._last_region, screen, image)
            self._show_overlay(image, screen.geometry(), initial, "last")
            return

        if mode == "fullscreen":
            image = grab_fullscreen()
            if not self._check(image):
                return
            screen = screen_at(QCursor.pos())
            geometry = (screen.geometry() if screen is not None
                        else QRect(0, 0, image.width(), image.height()))
            self._show_overlay(image, geometry, QRectF(image.rect()),
                               "fullscreen")
            return

        self._grab_region()

    def _grab_region(self) -> None:
        screen = screen_at(QCursor.pos())
        if screen is None:
            self._cancel_capture("没有可用屏幕")
            return
        image = grab_screen(screen)
        if not self._check(image):
            return
        self._show_overlay(image, screen.geometry(), None, "region")

    def _rect_to_scene(self, rect: QRect, screen, image: QImage) -> QRectF:
        geometry = screen.geometry()
        local = rect.translated(-geometry.topLeft())
        sx = image.width() / max(1, geometry.width())
        sy = image.height() / max(1, geometry.height())
        return QRectF(local.x() * sx, local.y() * sy,
                      max(1.0, local.width() * sx),
                      max(1.0, local.height() * sy))

    def _show_overlay(self, image: QImage, geometry: QRect,
                      initial_rect: QRectF | None, source: str,
                      color_mode: bool = False) -> ScreenshotOverlay:
        overlay = ScreenshotOverlay(
            image, geometry, initial_rect, color_mode=color_mode,
            ocr_enabled=ocr_available(), ocr_func=recognize, source=source)
        self._editor = overlay
        self._editor_source = source
        overlay.copied.connect(self._on_copied)
        overlay.saved.connect(self._on_saved)
        overlay.pinned.connect(self._on_pinned)
        overlay.colorPicked.connect(
            lambda value: self._note(self._color_message(value)))
        overlay.closed.connect(self._on_editor_closed)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        return overlay

    # ---------- 遮罩动作 ----------

    def open_editor(self, image: QImage, source: str = "region",
                    initial_rect: QRectF | None = None):
        """历史重新编辑/兼容入口：同一个遮罩，图居中，初始选区=整张图。"""
        if image is None or image.isNull():
            return None
        old = self._editor
        if old is not None:
            self._editor = None
            was_active = self._capture_active
            self._capture_active = False
            try:
                old.cancel()
            except RuntimeError:
                pass
            self._capture_active = was_active
        screen = screen_at(QCursor.pos()) or QGuiApplication.primaryScreen()
        geometry = (screen.geometry() if screen is not None
                    else QRect(0, 0, image.width(), image.height()))
        if initial_rect is None:
            initial_rect = QRectF(image.rect())
        return self._show_overlay(image, geometry, initial_rect, source)

    def _deliver(self, image: QImage, source: str,
                 region: QRect | None = None) -> None:
        """兼容测试/旧调用：把一张现成图交给遮罩继续编辑。"""
        if region is not None and region.width() > 1 and region.height() > 1:
            self._last_region = QRect(region)
            self._write_last_region(region)
        self.open_editor(image, source)

    def _on_copied(self, image: QImage) -> None:
        self._remember_overlay_region()
        self._note(f"已复制标注后的截图（{image.width()}×{image.height()}）")
        self.history.add(image, self._editor_source)

    def _on_saved(self, image: QImage, path: str) -> None:
        self._remember_overlay_region()
        self._note(f"截图已保存：{path}")
        self.history.add(image, self._editor_source)

    def _on_pinned(self, image: QImage) -> None:
        position = None
        overlay = self._editor
        if overlay is not None:
            selection = overlay.selection_rect()
            if selection is not None:
                position = overlay.viewport().mapToGlobal(
                    overlay.mapFromScene(selection.topLeft()))
        self._remember_overlay_region()
        self.history.add(image, self._editor_source)
        self.pin(image, position)

    def _remember_overlay_region(self) -> None:
        """把遮罩里最终调整过的选区记成“上一区域”（逻辑屏幕坐标）。"""
        overlay = self._editor
        if overlay is None:
            return
        selection = overlay.selection_rect()
        if selection is None:
            return
        top_left = overlay.mapFromScene(selection.topLeft())
        bottom_right = overlay.mapFromScene(selection.bottomRight())
        global_top_left = overlay.viewport().mapToGlobal(top_left)
        global_bottom_right = overlay.viewport().mapToGlobal(bottom_right)
        rect = QRect(global_top_left, global_bottom_right).normalized()
        if rect.width() > 1 and rect.height() > 1:
            self._last_region = rect
            self._write_last_region(rect)

    def _on_editor_closed(self) -> None:
        overlay = self._editor
        self._editor = None
        if overlay is not None:
            overlay.deleteLater()
        self.editorClosed.emit()
        if self._capture_active:
            self._end_capture()

    @staticmethod
    def _color_message(value: str) -> str:
        color = QColor(value)
        return (f"颜色 {value} 已复制到剪贴板"
                f"（RGB {color.red()}, {color.green()}, {color.blue()}）")

    def _check(self, image: QImage) -> bool:
        if image is None or image.isNull():
            self._cancel_capture("截图失败")
            return False
        return True

    def _cancel_capture(self, message: str | None = None) -> None:
        if message:
            self._note(message)
        self._end_capture()

    # ---------- 上一区域持久化 ----------

    def _read_last_region(self) -> QRect | None:
        try:
            payload = json.loads(self._last_region_path.read_text(encoding="utf-8"))
            return QRect(int(payload["x"]), int(payload["y"]),
                         int(payload["width"]), int(payload["height"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_last_region(self, rect: QRect) -> None:
        try:
            self._last_region_path.parent.mkdir(parents=True, exist_ok=True)
            self._last_region_path.write_text(
                json.dumps({"x": rect.x(), "y": rect.y(),
                            "width": rect.width(), "height": rect.height()}),
                encoding="utf-8")
        except OSError:
            pass

    # ---------- 贴图 ----------

    def pin(self, image: QImage, position=None) -> PinWindow | None:
        if image is None or image.isNull():
            return None
        pin = PinWindow(image, position or QCursor.pos())
        self._pins.append(pin)
        pin.closed.connect(self._remove_pin)
        self._note("截图已贴到屏幕上：拖动移动，滚轮缩放，右键更多")
        return pin

    def _remove_pin(self, pin: PinWindow) -> None:
        if pin in self._pins:
            self._pins.remove(pin)
        pin.deleteLater()

    def close_pins(self) -> None:
        self._suspended_pins.clear()
        for pin in list(self._pins):
            pin.close()

    def suspend_topmost(self) -> None:
        """关窗确认等模态框出现前，把置顶贴图暂时藏起来，避免挡住按钮。"""
        self._suspended_pins = [pin for pin in self._pins if pin.isVisible()]
        for pin in self._suspended_pins:
            pin.hide()

    def resume_topmost(self) -> None:
        pins, self._suspended_pins = self._suspended_pins, []
        for pin in pins:
            try:
                pin.show()
            except RuntimeError:
                pass

    # ---------- 历史 ----------

    def show_history(self) -> HistoryDialog:
        if self._history_dialog is None:
            dialog = HistoryDialog(self.history, parent=self.window)
            dialog.pinRequested.connect(self.pin)
            dialog.editRequested.connect(self.open_editor)
            dialog.finished.connect(self._clear_history_dialog)
            self._history_dialog = dialog
        self._history_dialog.refresh()
        self._history_dialog.show()
        self._history_dialog.raise_()
        self._history_dialog.activateWindow()
        return self._history_dialog

    def _clear_history_dialog(self, *_args) -> None:
        dialog = self._history_dialog
        self._history_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    # ---------- 收尾 ----------

    def _end_capture(self) -> None:
        if not self._capture_active:
            return
        self._capture_active = False
        if self._shutting_down:
            return
        self._restore_annotate()
        if self._window_was_visible:
            self._window_was_visible = False
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            # 回到主窗后把焦点交给当前画布：紧接着 Ctrl+V 粘截图才有着落
            view = getattr(self.window, "view", None)
            if view is not None:
                try:
                    view.setFocus(Qt.FocusReason.OtherFocusReason)
                except RuntimeError:
                    pass
        if not self._finished_once:
            self._finished_once = True
            self.finished.emit()

    def _hide_annotate(self) -> None:
        annotate = getattr(self.window, "_annotate", None)
        overlay = getattr(annotate, "overlay", None)
        mode = getattr(overlay, "mode", None)
        self._restore_mode = mode
        if annotate is not None and mode not in (None, "hidden"):
            try:
                annotate.hide()
            except Exception:
                pass

    def _restore_annotate(self) -> None:
        mode, self._restore_mode = self._restore_mode, None
        annotate = getattr(self.window, "_annotate", None)
        if annotate is None or mode in (None, "hidden"):
            return
        try:
            if mode == "passthrough":
                annotate.enter_passthrough()
            elif mode == "draw":
                annotate.enter_draw()
        except Exception:
            pass

    def shutdown(self) -> None:
        self._shutting_down = True
        self._suspended_pins.clear()
        if self._editor is not None:
            self._editor.cancel()
            self._editor = None
        if self._history_dialog is not None:
            self._history_dialog.close()
            self._history_dialog = None
        self.close_pins()

    def _note(self, text: str) -> None:
        note = getattr(self.window, "_note", None)
        if callable(note):
            note(text)


def install(window) -> ScreenshotController:
    controller = ScreenshotController(window)
    tray = getattr(window, "tray", None)
    menu = tray.contextMenu() if tray is not None else None
    if menu is not None:
        menu.addAction("截图（区域）").triggered.connect(
            lambda _checked=False: controller.start("region"))
        menu.addAction("截图历史…").triggered.connect(
            lambda _checked=False: controller.show_history())
        menu.addSeparator()
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(controller.shutdown)
    return controller
