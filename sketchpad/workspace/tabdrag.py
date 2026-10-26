"""标签拖拽状态机（方案书第 11 节）。

三种落点：同栏重排、悬停满 3s 合并预览、拖出所有窗口后松开拆出新窗口。
本模块只负责判定与信号，真正改 session/迁窗口由主窗接线（``windows.py``）。

真实拖拽用 ``QDrag``：MIME ``application/x-pageflow-tab``，负载
``{"window_id": .., "entry_id": ..}``。``QDrag.exec`` 会阻塞，因此悬停/计时
更新靠 30ms 的 ``QTimer`` 轮询 ``QCursor.pos()``，这样指针在桌面上也照样判定。
测试不需要真拖拽：``press()`` / ``move()`` / ``release(canceled=)`` 与
``fire_merge_timer()`` 就是真实鼠标处理器调用的同一套方法。
"""
from __future__ import annotations

import ctypes
import json
import sys
import weakref
from typing import TYPE_CHECKING

import shiboken6

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QDrag, QCursor
from PySide6.QtWidgets import QApplication, QLabel, QTabBar, QWidget

if TYPE_CHECKING:                                   # 只给类型用，运行时不反向依赖
    from sketchpad.workspace.tabs import TabEntry, WorkspaceTabs

MIME_TYPE = "application/x-pageflow-tab"
DRAG_THRESHOLD = 6              # 移动超过这个像素才算开始拖
MERGE_DELAY_MS = 3000           # 悬停同一目标标签多久进入合并预览
OUTSIDE_MARGIN = 24             # 距任何窗口超过这个距离才算“拖出”
POLL_INTERVAL_MS = 30           # QDrag.exec 期间的轮询间隔
DETACH_HINT_TEXT = "松开 → 新建窗口"
VK_ESCAPE = 0x1B


def _escape_is_down() -> bool:
    """QDrag 期间 Qt 不一定把 Esc 交给控件，Windows 上直接读键状态。"""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
    except Exception:                               # pragma: no cover - 极端兜底
        return False

# --- 同进程窗口/tabbar 登记（弱引用，避免拖拽引擎揣着窗口不放） ---

_TABS_BY_BAR: dict[int, weakref.ReferenceType] = {}
_ALL_TABS: weakref.WeakSet = weakref.WeakSet()


def _cpp_id(widget: QWidget) -> int:
    """QTabBar 的 C++ 地址：PySide 可能给出不同 wrapper，但底层对象不变。"""
    try:
        from shiboken6 import getCppPointer
        return int(getCppPointer(widget)[0])
    except Exception:                               # pragma: no cover - 极端兜底
        return id(widget)


def register_tabs(tabs: WorkspaceTabs) -> None:
    """构造 WorkspaceTabs 时登记，供跨窗口命中测试使用。"""
    _ALL_TABS.add(tabs)
    _TABS_BY_BAR[_cpp_id(tabs.tabbar)] = weakref.ref(tabs)


def registered_tabs() -> list:
    return [tabs for tabs in list(_ALL_TABS)]


def tabs_for_tabbar(bar: QTabBar | None) -> WorkspaceTabs | None:
    if not isinstance(bar, QTabBar) or not shiboken6.isValid(bar):
        return None
    try:
        key = _cpp_id(bar)
    except RuntimeError:
        return None
    ref = _TABS_BY_BAR.get(key)
    if ref is not None:
        tabs = ref()
        try:
            if tabs is not None and shiboken6.isValid(tabs) and _cpp_id(tabs.tabbar) == key:
                return tabs
        except RuntimeError:
            pass
        _TABS_BY_BAR.pop(key, None)                 # 已销毁或地址被复用
    for tabs in list(_ALL_TABS):
        try:
            if tabs is not None and shiboken6.isValid(tabs) and _cpp_id(tabs.tabbar) == key:
                return tabs
        except RuntimeError:
            continue
    return None


def _tabbar_at(global_pos: QPoint) -> QTabBar | None:
    """光标下的 QTabBar：优先 widgetAt 往上走，失败时退回几何命中。

    offscreen 后端不实现 widgetAt，几何命中还能覆盖“窗口被别的窗口挡住”的情况。
    """
    app = QApplication.instance()
    widget = app.widgetAt(global_pos) if app is not None else None
    while widget is not None:
        if isinstance(widget, QTabBar):
            bar = widget
            tabs = tabs_for_tabbar(bar)
            if tabs is not None and bar.rect().contains(bar.mapFromGlobal(global_pos)):
                return bar
            break
        widget = widget.parentWidget()
    for tabs in registered_tabs():
        try:
            if not shiboken6.isValid(tabs):
                continue
            bar = tabs.tabbar
            if bar is None or not shiboken6.isValid(bar) or not bar.isVisible():
                continue
            if bar.rect().contains(bar.mapFromGlobal(global_pos)):
                return bar
        except RuntimeError:
            continue                            # 窗口刚销毁：跳过这个标签栏
    return None


def _top_level_windows() -> list[QWidget]:
    app = QApplication.instance()
    if app is None:
        return []
    windows = []
    for widget in app.topLevelWidgets():
        if not widget.isVisible() or not widget.isWindow():
            continue
        # 工具提示/弹出菜单不算“窗口”，否则鼠标落在桌面提示上会误判为还在应用内
        if widget.windowType() in (Qt.ToolTip, Qt.Popup, Qt.Tool):
            continue
        windows.append(widget)
    return windows


class TabDragController(QObject):
    """WorkspaceTabs 的拖拽状态机；一个标签栏一个实例。"""

    reorderRequested = Signal(object, int)
    transferRequested = Signal(object, object, object, int)
    mergePreviewChanged = Signal(object, object, str)
    mergeRequested = Signal(object, object, object, object)
    detachRequested = Signal(object, object, QPoint)

    def __init__(self, tabs: WorkspaceTabs, parent: QObject | None = None,
                 native_drag: bool | None = None) -> None:
        super().__init__(parent if parent is not None else tabs)
        self._tabs = tabs
        self._state = "idle"            # idle / pressed / dragging
        self._started = False           # 是否已越过 6px 阈值
        self._entry: TabEntry | None = None
        self._press_pos = QPoint()
        self._last_pos = QPoint()
        self._hover_tabs: WorkspaceTabs | None = None
        self._hover_entry: TabEntry | None = None
        self._merge_active = False
        self._merge_tabs: WorkspaceTabs | None = None
        self._merge_target: TabEntry | None = None
        self._merge_text = ""
        self._marker_tabs: WorkspaceTabs | None = None
        self._outside = False
        self._esc_pressed = False
        self._hint: QLabel | None = None

        if native_drag is None:
            app = QApplication.instance()
            native_drag = app is None or app.platformName() != "offscreen"
        self._native_drag = bool(native_drag)

        self._merge_timer = QTimer(self)
        self._merge_timer.setSingleShot(True)
        self._merge_timer.setInterval(MERGE_DELAY_MS)
        self._merge_timer.timeout.connect(self._activate_merge_preview)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_cursor)

        register_tabs(tabs)
        bar = getattr(tabs, "tabbar", None)
        if bar is not None:
            bar.installEventFilter(self)

    # --- 只读状态（也方便测试断言） ---

    @property
    def state(self) -> str:
        return self._state

    @property
    def entry(self) -> TabEntry | None:
        return self._entry

    @property
    def is_dragging(self) -> bool:
        return self._state == "dragging" and self._started

    @property
    def merge_preview_active(self) -> bool:
        return self._merge_active

    @property
    def hint_visible(self) -> bool:
        return self._hint is not None and self._hint.isVisible()

    @property
    def detach_hint(self) -> QLabel | None:
        return self._hint

    @property
    def hint(self) -> QLabel | None:
        return self._hint

    @property
    def native_drag(self) -> bool:
        return self._native_drag

    def set_native_drag(self, enabled: bool) -> None:
        self._native_drag = bool(enabled)

    # --- 测试/鼠标共用的入口 ---

    def press(self, entry: TabEntry | None, global_pos: QPoint) -> bool:
        """按下标签。草稿、不在本栏的 entry 直接拒绝（不进入 pending 态）。"""
        if self._state != "idle":
            self._finish()
        if entry is None or getattr(entry, "draft", False):
            return False
        if self._tabs.index_of(entry) < 0:
            return False
        self._entry = entry
        self._press_pos = QPoint(global_pos)
        self._last_pos = QPoint(global_pos)
        self._state = "pressed"
        self._started = False
        self._outside = False
        self._esc_pressed = False
        return True

    def move(self, global_pos: QPoint) -> None:
        if self._state == "idle":
            return
        pos = QPoint(global_pos)
        self._last_pos = pos
        if not self._started:
            if (pos - self._press_pos).manhattanLength() < DRAG_THRESHOLD:
                return
            self._started = True
            self._state = "dragging"
            if self._native_drag:
                self._run_native_drag(pos)
                return
        self._update(pos)

    def release(self, global_pos: QPoint, canceled: bool = False) -> None:
        if self._state == "idle":
            return
        if canceled:
            self.cancel()
            return
        if not self._started:                       # 只是点击/抖动，不算拖拽
            self._finish()
            return

        pos = QPoint(global_pos)
        self._last_pos = pos
        self._update(pos)
        entry = self._entry
        src_tabs = self._tabs

        if self._outside:
            self._finish()
            self.detachRequested.emit(src_tabs, entry, QPoint(pos))
            return

        if (self._merge_active and self._hover_tabs is self._merge_tabs
                and self._hover_entry is self._merge_target):
            target_tabs = self._merge_tabs
            target_entry = self._merge_target
            self._finish()
            self.mergeRequested.emit(src_tabs, entry, target_tabs, target_entry)
            return

        tabs = self._hover_tabs
        target = self._hover_entry
        # 同栏落在标签后的空白区也算“插到末尾”；跨窗口必须真落在某个标签上
        if tabs is not None and (target is not None or tabs is src_tabs):
            index = self._target_index(tabs, pos)
            if tabs is src_tabs:
                if index != src_tabs.index_of(entry):
                    self._finish()
                    self.reorderRequested.emit(entry, index)
                    return
            elif target is not None:
                self._finish()
                self.transferRequested.emit(src_tabs, entry, tabs, index)
                return
        self._finish()

    def cancel(self) -> None:
        """Esc / QDrag 返回 IgnoreAction：恢复预览，什么都不发。"""
        if self._state == "idle":
            return
        self._clear_merge_preview(emit=True)
        self._finish()

    def fire_merge_timer(self) -> None:
        """测试用：不等真实 3 秒，直接触发悬停计时器。"""
        if self._merge_timer.isActive():
            self._merge_timer.stop()
        self._activate_merge_preview()

    def mime_data(self, entry: TabEntry | None = None) -> QMimeData:
        entry = entry if entry is not None else self._entry
        payload = {"window_id": int(self._tabs.window_id),
                   "entry_id": int(entry.entry_id) if entry is not None else -1}
        mime = QMimeData()
        mime.setData(MIME_TYPE, json.dumps(payload).encode("utf-8"))
        mime.setText(entry.title if entry is not None else "")
        return mime

    # --- 事件过滤器：真实鼠标入口 ---

    def eventFilter(self, obj, event) -> bool:      # noqa: N802 - Qt 命名
        if obj is getattr(self._tabs, "tabbar", None):
            kind = event.type()
            if (kind == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton):
                bar = self._tabs.tabbar
                index = bar.tabAt(event.position().toPoint())
                self.press(self._tabs._entry_at(index),
                           event.globalPosition().toPoint())
            elif kind == QEvent.MouseMove and self._state != "idle":
                self.move(event.globalPosition().toPoint())
            elif (kind == QEvent.MouseButtonRelease
                    and event.button() == Qt.LeftButton):
                self.release(event.globalPosition().toPoint())
        return super().eventFilter(obj, event)

    # --- 原生 QDrag（阻塞 + 30ms 轮询） ---

    def _run_native_drag(self, pos: QPoint) -> None:
        drag = QDrag(self._tabs)
        drag.setMimeData(self.mime_data(self._entry))
        bar = self._tabs.tabbar
        index = self._tabs.index_of(self._entry)
        if index >= 0:
            rect = bar.tabRect(index)
            if rect.isValid():
                try:
                    drag.setPixmap(bar.grab(rect))
                    drag.setHotSpot(pos - bar.mapToGlobal(rect.topLeft()))
                except Exception:                   # pragma: no cover - 绘制兜底
                    pass
        self._update(pos)
        self._poll_timer.start()
        try:
            result = drag.exec(Qt.MoveAction)
        finally:
            self._poll_timer.stop()
        if self._state == "idle":
            return                                  # 轮询里已经松手并收尾
        # 没有接受拖放的控件时 exec 也返回 IgnoreAction，不能一律当 Esc：
        # Esc 由轮询里的 GetAsyncKeyState 单独识别，其余按“松手位置”收尾。
        if self._esc_pressed:
            self.cancel()
        else:
            self.release(QCursor.pos())

    def _poll_cursor(self) -> None:
        if self._state == "idle":
            self._poll_timer.stop()
            return
        # 松手由 QDrag.exec() 返回后统一处理；这里不查 mouseButtons——
        # Windows 的 DoDragDrop 期间它可能报 NoButton，查了会在拖拽刚开始就误收尾。
        if _escape_is_down():
            self._esc_pressed = True
        self.move(QCursor.pos())

    # --- 命中判定 ---

    def _update(self, pos: QPoint) -> None:
        bar = _tabbar_at(pos)
        tabs = tabs_for_tabbar(bar) if bar is not None else None

        if tabs is None:
            self._set_hover(None, None)
            self._hide_marker()
            if self._window_near(pos):
                self._outside = False
                self._hide_hint()
            else:
                self._outside = True
                self._show_hint(pos)
            return

        self._outside = False
        self._hide_hint()
        local = tabs.tabbar.mapFromGlobal(pos)
        index = tabs.tabbar.tabAt(local)
        target = tabs._entry_at(index) if index >= 0 else None
        self._set_hover(tabs, target)

        if self._merge_active and tabs is self._merge_tabs and target is self._merge_target:
            self._hide_marker()                     # 预览阶段只留 2px 高亮边框
            return
        if self._merge_active:
            self._clear_merge_preview(emit=True)
        self._show_marker(tabs, local, index)

    def _window_near(self, pos: QPoint) -> bool:
        margin = OUTSIDE_MARGIN
        for window in _top_level_windows():
            if window is self._hint:
                continue
            rect = window.frameGeometry().adjusted(-margin, -margin, margin, margin)
            if rect.contains(pos):
                return True
        return False

    def _can_merge(self, tabs: WorkspaceTabs, target: TabEntry) -> bool:
        source = self._entry
        if source is None or target is None or target is source:
            return False
        if source.draft or target.draft:
            return False
        if source.is_split or target.is_split:      # 分屏最多 2 栏，不再合并第三个
            return False
        if self._tabs.index_of(source) < 0:
            return False
        return tabs.index_of(target) >= 0

    def _set_hover(self, tabs: WorkspaceTabs | None, target: TabEntry | None) -> None:
        if (tabs, target) == (self._hover_tabs, self._hover_entry):
            return
        if self._merge_active:
            self._clear_merge_preview(emit=True)
        self._merge_timer.stop()
        self._hover_tabs = tabs
        self._hover_entry = target
        if tabs is not None and target is not None and self._can_merge(tabs, target):
            self._merge_timer.start(MERGE_DELAY_MS)  # 换目标就重新计时

    def _activate_merge_preview(self) -> None:
        if self._state != "dragging" or self._outside or self._entry is None:
            return
        tabs = self._hover_tabs
        target = self._hover_entry
        if tabs is None or target is None or not self._can_merge(tabs, target):
            return
        self._merge_active = True
        self._merge_tabs = tabs
        self._merge_target = target
        self._merge_text = f"{self._entry.title} | {target.title}"
        tabs.show_merge_preview(target, self._merge_text)
        self._hide_marker()
        self.mergePreviewChanged.emit(self._entry, target, self._merge_text)

    def _clear_merge_preview(self, emit: bool) -> None:
        if not self._merge_active:
            return
        tabs = self._merge_tabs
        target = self._merge_target
        source = self._entry
        self._merge_active = False
        self._merge_tabs = None
        self._merge_target = None
        self._merge_text = ""
        if tabs is not None:
            try:
                tabs.clear_merge_preview()
            except RuntimeError:                    # 目标窗口先销毁了
                pass
        if emit:
            self.mergePreviewChanged.emit(source, target, "")

    def _show_marker(self, tabs: WorkspaceTabs, local: QPoint, index: int) -> None:
        bar = tabs.tabbar
        if self._marker_tabs is not None and self._marker_tabs is not tabs:
            try:
                self._marker_tabs.hide_insertion_marker()
            except RuntimeError:                    # pragma: no cover
                pass
        if index >= 0:
            rect = bar.tabRect(index)
            x = rect.left() if local.x() < rect.center().x() else rect.right() + 1
        else:
            x = bar.width()
        x = max(0, min(int(x), max(0, bar.width() - 1)))
        tabs.show_insertion_marker(x)
        self._marker_tabs = tabs

    def _hide_marker(self) -> None:
        tabs = self._marker_tabs
        self._marker_tabs = None
        if tabs is not None:
            try:
                tabs.hide_insertion_marker()
            except RuntimeError:                    # pragma: no cover
                pass

    def _target_index(self, tabs: WorkspaceTabs, pos: QPoint) -> int:
        """落点在目标 entries 里的最终下标（删掉源之后再插入的位置）。"""
        bar = tabs.tabbar
        local = bar.mapFromGlobal(pos)
        entries = tabs.entries
        index = bar.tabAt(local)
        if index < 0:
            insert_at = len(entries)
        else:
            rect = bar.tabRect(index)
            insert_at = index if local.x() < rect.center().x() else index + 1
        if tabs is self._tabs:
            source_index = self._tabs.index_of(self._entry)
            if 0 <= source_index < insert_at:
                insert_at -= 1
        max_index = len(entries) - 1 if tabs is self._tabs else len(entries)
        insert_at = max(0, min(insert_at, max_index))
        if entries and entries[0].draft:
            insert_at = max(1, insert_at)           # 草稿钉在 0，目标下标至少 1
        return int(insert_at)

    # --- 浮层提示 ---

    def _ensure_hint(self) -> QLabel:
        if self._hint is None:
            hint = QLabel(self._tabs)             # 挂到 tabs 上，标签栏销毁时一起回收
            hint.setObjectName("TabDragHint")
            hint.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
            hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            hint.setAttribute(Qt.WA_ShowWithoutActivating, True)
            hint.setStyleSheet(
                "QLabel#TabDragHint { background: #202124; color: #e8eaed;"
                " border: 1px solid #48484a; border-radius: 4px;"
                " padding: 4px 8px; font-size: 12px; }")
            self._hint = hint
        return self._hint

    def _show_hint(self, pos: QPoint) -> None:
        hint = self._ensure_hint()
        hint.setText(DETACH_HINT_TEXT)
        hint.adjustSize()
        hint.move(pos + QPoint(16, 18))
        hint.show()
        hint.raise_()

    def _hide_hint(self) -> None:
        if self._hint is not None and self._hint.isVisible():
            self._hint.hide()

    # --- 收尾 ---

    def _finish(self) -> None:
        self._merge_timer.stop()
        self._poll_timer.stop()
        self._clear_merge_preview(emit=False)
        self._hide_marker()
        self._hide_hint()
        self._state = "idle"
        self._started = False
        self._outside = False
        self._entry = None
        self._hover_tabs = None
        self._hover_entry = None
