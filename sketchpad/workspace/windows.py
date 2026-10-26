"""同进程二级工作区窗口：标签拖出主窗后不丢会话、不重读 PDF。

主窗（primary）独占托盘/全局热键/草稿纸/单实例；二级窗口只装文档标签。
拖拽控制器只认 tabs 对象与全局坐标；这里负责把窗口 id、窗口集合和
"移入另一个窗口 / 合并 / 拖出" 三个动作接起来。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, Signal
from PySide6.QtGui import QGuiApplication


class WindowRegistry(QObject):
    """所有工作区窗口的登记处；窗口本身由主窗提供的工厂创建。"""

    windowCreated = Signal(object)
    windowClosed = Signal(object)

    def __init__(self, primary, factory, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.primary = primary
        self._factory = factory
        self._windows: dict[int, object] = {}
        self._next_id = 1
        self.register(primary)

    # --- 登记 ---

    def register(self, window) -> int:
        window_id = int(getattr(window, "window_id", 0) or 0)
        if not window_id:
            window_id = self._next_id
        self._next_id = max(self._next_id, window_id + 1)
        window.window_id = window_id
        self._windows[window_id] = window
        try:
            window.tabs.set_window_id(window_id)
        except (AttributeError, RuntimeError):
            pass
        return window_id

    def unregister(self, window) -> None:
        for key, item in list(self._windows.items()):
            if item is window:
                self._windows.pop(key, None)
                self.windowClosed.emit(window)
                return

    def find(self, window_id: int):
        return self._windows.get(int(window_id))

    def window_for_tabs(self, tabs):
        for window in self._windows.values():
            if getattr(window, "tabs", None) is tabs:
                return window
        return None

    @property
    def windows(self) -> list:
        return list(self._windows.values())

    @property
    def secondaries(self) -> list:
        return [w for w in self._windows.values() if w is not self.primary]

    # --- 命中共 ---

    def window_at(self, global_pos: QPoint):
        """光标落在哪个工作区窗口上（用于拖出判定，取最上面一个）。"""
        for window in reversed(self.windows):
            try:
                if window.isVisible() and window.frameGeometry().contains(global_pos):
                    return window
            except RuntimeError:
                continue
        return None

    def inside_any(self, global_pos: QPoint, margin: int = 0) -> bool:
        for window in self.windows:
            try:
                if window.isVisible() and window.frameGeometry().adjusted(
                        -margin, -margin, margin, margin).contains(global_pos):
                    return True
            except RuntimeError:
                continue
        return False

    # --- 动作 ---

    def create_window(self, global_pos: QPoint | None = None):
        window = self._factory()
        self.register(window)
        if global_pos is not None:
            self._place(window, global_pos)
        window.show()
        window.raise_()
        window.activateWindow()
        self.windowCreated.emit(window)
        return window

    def _place(self, window, global_pos: QPoint) -> None:
        """让光标落在新窗标题栏偏左，避免新窗完全盖住原窗。"""
        screen = QGuiApplication.screenAt(global_pos)
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1200, 800)
        width = min(900, max(520, area.width() // 2))
        height = min(720, max(420, area.height() - 80))
        x = min(max(area.left(), global_pos.x() - 80), area.right() - width)
        y = min(max(area.top(), global_pos.y() - 28), area.bottom() - height)
        window.resize(width, height)
        window.move(QPoint(x, y))

    def detach(self, source_window, entry, global_pos: QPoint):
        """拖出窗口：新建二级窗口并移入该标签。草稿/空标签不动。"""
        if entry is None or getattr(entry, "draft", False):
            return None
        target = self.create_window(global_pos)
        moved = source_window._move_entry_to_window(entry, target)
        if moved is None:
            target.close()
            return None
        return target

    def transfer(self, source_window, entry, target_window, index: int | None = None):
        """跨窗口插入：保持面板/会话原样，只换父窗口。"""
        if source_window is target_window:
            return getattr(source_window, "tabs").move_entry_to(entry, index) if index is not None else entry
        return source_window._move_entry_to_window(entry, target_window, index)

    def merge(self, source_window, source_entry, target_window, target_entry):
        """合并预览落地：同窗直接合并；跨窗先移入目标窗，再走原有合并逻辑。"""
        if source_window is target_window:
            target_window._merge_document_entries(source_entry, target_entry)
            return True
        moved = source_window._move_entry_to_window(source_entry, target_window)
        if moved is None:
            return False
        target_window._merge_document_entries(moved, target_entry)
        return True

    def close_all(self) -> bool:
        """退出前逐个关二级窗口；任一窗口取消保存就中止。"""
        for window in list(self.secondaries):
            try:
                if not window.close():
                    return False
            except RuntimeError:
                continue
        return True


__all__ = ["WindowRegistry"]
