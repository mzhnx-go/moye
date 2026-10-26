"""草稿纸弹窗与托盘（设计文档 §15、R9）。

草稿窗只有内存文档：不碰 draft.json，也不接主控的自动保存；
进程退出即丢，这是规格里写死的行为（不是漏做存盘）。

草稿纸可以整块停靠进主窗右栏（文档左 / 草稿右），见 `ScratchDock`；
停靠只搬动工具栏与画布，不复制文档，草稿内容/撤销栈原样保留。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea,
                               QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget)

from sketchpad.ui import theme
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.frameless import (EdgeResizeFilter, apply_rounded_corners, enable_native_resize,
                           native_hit_test, install_window_border)
from sketchpad.core.settings import Settings
from sketchpad.core.channel import APP_NAME
from sketchpad.core.strokes import Document
from sketchpad.ui.titlebar import TitleBar
from sketchpad.ui.toolbox import Toolbox

SCRATCH_TITLE = "草稿纸"
SCRATCH_WIDTH, SCRATCH_HEIGHT = 940, 600
DOCK_HEADER_HEIGHT = 28


def clamp_to_area(area: QRect, width: int, height: int, x: int, y: int) -> tuple[int, int]:
    """把窗口左上角钳进可用区；窗口比可用区大时贴左上，保证标题栏不跑到屏外。"""
    x = max(area.left(), min(int(x), area.right() - width + 1))
    y = max(area.top(), min(int(y), area.bottom() - height + 1))
    return x, y


def _tray_icon() -> QIcon:
    """托盘图标：与主窗、任务栏一致的应用图标（素材缺失时 icons 会回退到现画）。"""
    from sketchpad.ui import icons      # 惰性导入：避免图标模块与草稿窗互相牵连

    return icons.app_icon()


_DOCK_QSS = f"""
QWidget#ScratchDock {{ background: {theme.WINDOW_BG}; }}
QWidget#ScratchDockHeader {{
    background: {theme.TOOLBAR_BG};
    border-bottom: 1px solid {theme.BORDER};
}}
QLabel#ScratchDockTitle {{ color: {theme.TEXT}; font-size: 12px; font-weight: 600; }}
QToolButton#ScratchUndock {{
    background: {theme.SURFACE};
    color: {theme.TEXT};
    border: 1px solid {theme.BORDER};
    border-radius: 4px;
    padding: 1px 8px;
    font-size: 11px;
}}
QToolButton#ScratchUndock:hover {{ background: {theme.SURFACE_HOVER}; }}
QToolButton#ScratchUndock:pressed {{ background: {theme.SURFACE_PRESSED}; }}
QScrollArea#ScratchDockToolbar {{ background: {theme.TOOLBAR_BG}; border: none; }}
QScrollArea#ScratchDockToolbar QScrollBar:horizontal {{
    background: {theme.TOOLBAR_BG}; height: 10px; margin: 0;
}}
QScrollArea#ScratchDockToolbar QScrollBar::handle:horizontal {{
    background: {theme.BORDER}; min-width: 24px; border-radius: 4px;
}}
QScrollArea#ScratchDockToolbar QScrollBar::add-line,
QScrollArea#ScratchDockToolbar QScrollBar::sub-line {{ width: 0; height: 0; }}
"""


class ScratchDock(QWidget):
    """主窗右栏的草稿纸：标题行 + 草稿工具栏 + 画布（都是浮窗里的同一批控件）。

    只搬控件、不复制：停靠前后草稿文档、撤销栈、工具状态都不变。
    工具栏布局最小宽 936，放进横向滚动区，右栏窄时也不会把 PDF 挤没。
    """

    undockRequested = Signal()

    def __init__(self, toolbox: Toolbox, view: SketchView, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ScratchDock")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_DOCK_QSS)
        self.setMinimumWidth(300)
        self.toolbox = toolbox
        self.view = view
        # 已经在右栏了，工具栏里的「分屏」按钮没有意义；还原走标题行按钮
        toolbox.split_doc_button.hide()

        self.header = QWidget(self)
        self.header.setObjectName("ScratchDockHeader")
        self.header.setAttribute(Qt.WA_StyledBackground, True)
        self.header.setFixedHeight(DOCK_HEADER_HEIGHT)
        title = QLabel("草稿纸", self.header)
        title.setObjectName("ScratchDockTitle")
        title.setFocusPolicy(Qt.NoFocus)
        self.undock_button = QToolButton(self.header)
        self.undock_button.setObjectName("ScratchUndock")
        self.undock_button.setText("还原浮窗")
        self.undock_button.setToolTip("把草稿纸还原成置顶浮窗")
        self.undock_button.setCursor(Qt.PointingHandCursor)
        self.undock_button.setFocusPolicy(Qt.NoFocus)
        self.undock_button.clicked.connect(
            lambda _checked=False: self.undockRequested.emit())
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(10, 0, 6, 0)
        header_row.setSpacing(6)
        header_row.addWidget(title, 1)
        header_row.addWidget(self.undock_button)

        self.toolbar_area = QScrollArea(self)
        self.toolbar_area.setObjectName("ScratchDockToolbar")
        self.toolbar_area.setFrameShape(QFrame.NoFrame)
        self.toolbar_area.setWidgetResizable(True)
        self.toolbar_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.toolbar_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.toolbar_area.setFixedHeight(
            max(38, toolbox.minimumSizeHint().height() + 14))
        self.toolbar_area.setWidget(toolbox)

        self.view_host = QWidget(self)
        self.view_layout = QVBoxLayout(self.view_host)
        self.view_layout.setContentsMargins(0, 0, 0, 0)
        self.view_layout.setSpacing(0)
        self.view_layout.addWidget(view)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.header)
        column.addWidget(self.toolbar_area)
        column.addWidget(self.view_host, 1)

    def replace_view(self, old_view: SketchView, view: SketchView) -> None:
        """停靠期间「新建草稿」：只换画布，工具栏与标题行留下。"""
        self.view_layout.replaceWidget(old_view, view)
        view.show()
        self.view = view

    def take_view(self) -> SketchView:
        view = self.view
        self.view_layout.removeWidget(view)
        view.setParent(None)
        return view

    def take_toolbox(self) -> Toolbox:
        toolbox = self.toolbar_area.takeWidget()
        return toolbox or self.toolbox

    def focus_view(self) -> None:
        self.view.setFocus(Qt.OtherFocusReason)


class ScratchWindow(QWidget):
    """置顶草稿窗：自绘标题栏 + 精简工具栏 + 独立内存文档；Esc 隐藏，关闭也只隐藏。"""

    settingsRequested = Signal()
    dockRequested = Signal()        # 分屏按钮：请主窗把整块草稿纸停靠到右栏
    undockRequested = Signal()      # 停靠栏「还原浮窗」

    def __init__(self, settings: Settings | None = None) -> None:
        # 无边框 + 自绘标题栏：与主窗同一套外壳（置顶是设计前提，不提供开关）
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.settings = settings or Settings()
        self.document = Document()
        self._tool = "pen"
        self._color = self.settings.color
        self._highlighter_color = self.settings.highlighter_color
        self._width = float(self.settings.width)
        self._clear_key = self.settings.clear_key
        self._synced_clear_key = self.settings.clear_key   # 上次从主窗取到的键

        self.setWindowTitle(SCRATCH_TITLE)
        self.resize(SCRATCH_WIDTH, SCRATCH_HEIGHT)
        self.setObjectName("ScratchRoot")            # 草稿窗自己的外边框 QSS
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.titlebar = TitleBar(SCRATCH_TITLE, self, show_settings=False)
        self.titlebar.pin_button.hide()          # 草稿纸常驻置顶，用户不能关掉
        self.titlebar.close_button.setToolTip("关闭（隐藏草稿纸）")
        self.titlebar.minimizeRequested.connect(self.showMinimized)
        self.titlebar.maximizeRequested.connect(self._toggle_maximized)
        self.titlebar.closeRequested.connect(self.hide)

        self.toolbox = Toolbox(self.settings, self)
        # 精简版：收紧按钮内边距；工具栏布局最小宽 936，窗口 940 留 4px
        self.toolbox.setStyleSheet("QPushButton { padding: 2px 4px; }")
        self.toolbox.set_select_enabled(True)        # 草稿窗：鼠标工具=手形平移
        self.toolbox.set_text_enabled(True)          # 草稿窗：T 落点输入文字
        self.toolbox.set_new_note_enabled(False)     # 草稿窗不参与主窗笔记标签
        self.toolbox.set_capture_enabled(False)      # 截图走主窗/全局热键
        self.toolbox.set_annotate_enabled(False)     # 全屏批注只在主窗
        self.toolbox.set_note_enabled(False)         # 草稿窗没有 PDF 页可放笔记
        self.view = SketchView(SketchScene(self.document), self.settings)
        self.view.set_tool(self._tool)
        self.view.set_color(self._color)
        self.view.set_highlighter_color(self._highlighter_color)
        self.view.set_width(self._width)

        column = QVBoxLayout(self)
        column.setContentsMargins(1, 1, 1, 1)   # 给无边框外框留 1px
        column.setSpacing(0)
        column.addWidget(self.titlebar)
        column.addWidget(self.toolbox)
        column.addWidget(self.view, 1)
        self.column = column

        self._dock: ScratchDock | None = None

        self.toolbox.colorPicked.connect(self._on_color)
        self.toolbox.widthChosen.connect(self._on_width)
        self.toolbox.toolChosen.connect(self._on_tool)
        self.toolbox.clearRequested.connect(self._on_clear)
        self.toolbox.undoRequested.connect(self._on_undo)
        self.toolbox.redoRequested.connect(self._on_redo)
        self.toolbox.settingsRequested.connect(self.settingsRequested.emit)
        self.toolbox.set_split_doc_available(True)
        self.toolbox.splitDocRequested.connect(self.dockRequested.emit)
        self.toolbox.split_doc_button.setToolTip("在主窗分屏（文档左 / 草稿右）")
        self.toolbox.settings_button.setToolTip("设置")
        self.view.changed.connect(self._on_view_changed)
        self._history_shortcuts = self._install_history_shortcuts(self.view)

        self._esc = QShortcut(QKeySequence("Esc"), self)
        self._esc.setContext(Qt.WidgetWithChildrenShortcut)
        self._esc.activated.connect(self.hide)
        # 边缘缩放：与主窗同一个应用级过滤器
        self._edge_resize_filter = EdgeResizeFilter.install(self)
        self._window_border = install_window_border(self)

    # --- 键盘撤销/重做（快捷键挂在视图实例上，换文档时随旧视图一起走）---

    def _install_history_shortcuts(self, view: SketchView) -> list[QShortcut]:
        """Ctrl+Z/Y 绑到这份视图的 History；reset_document 换视图后要再装一次。"""
        shortcuts = []
        for sequence, slot in (("Ctrl+Z", view.history.pop_undo),
                               ("Ctrl+Y", view.history.pop_redo)):
            shortcut = QShortcut(QKeySequence(sequence), view)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            shortcuts.append(shortcut)          # 留引用：QShortcut 被回收就失效
        return shortcuts

    def _on_view_changed(self) -> None:
        """键盘改工具/粗细：草稿窗没有状态栏，只反射工具栏与色板。"""
        self.toolbox.reflect_color(self.view.color, "pen")
        self.toolbox.reflect_color(self.view.highlighter_color, "highlighter")
        self.toolbox.reflect_tool(self.view.tool)
        self.toolbox.reflect_width(self.view.width)

    # --- 工具栏 → 当前视图（换文档后不需要重新接线）---

    def _on_color(self, color: str) -> None:
        """色块只改当前工具的颜色：荧光笔与画笔各存各的。"""
        if self.view.tool == "highlighter":
            self._highlighter_color = color
            self.view.set_highlighter_color(color)
            self.settings.highlighter_color = color
        else:
            self._color = color
            self.view.set_color(color)
            self.settings.color = color

    def _on_width(self, width: float) -> None:
        self._width = float(width)
        self.view.set_width(width)

    def _on_tool(self, tool: str) -> None:
        self._tool = tool
        self.view.set_tool(tool)      # changed → 色板切到该工具记住的颜色

    def _on_clear(self) -> None:
        """工具栏垃圾桶：清空草稿画布（一步可撤销），不碰主稿。"""
        self.view.clear_all()

    def _on_undo(self) -> None:
        self.view.history.pop_undo()

    def _on_redo(self) -> None:
        self.view.history.pop_redo()

    # --- 显示时对齐主窗配置 ---

    def showEvent(self, ev) -> None:
        """主窗可能在草稿窗建好之后才改清屏键：每次显示时对一次。"""
        super().showEvent(ev)
        self._sync_clear_key()
        enable_native_resize(self)
        apply_rounded_corners(self)

    def _sync_clear_key(self) -> None:
        """只在主窗的键变过时覆盖，本窗自己改的键不会被每次显示冲掉。"""
        key = self.settings.clear_key
        if key == self._synced_clear_key:
            return
        self._synced_clear_key = key
        self.view.set_clear_key(key)
        self._clear_key = self.view.clear_key

    def apply_settings(self, color: str, width: float, clear_key: str,
                       ink_smoothing: str | None = None,
                       highlighter_color: str | None = None,
                       pressure_enabled: bool | None = None) -> None:
        """设置面板保存后把颜色/粗细/清屏键/平滑/压感灌进本窗（不碰配置对象）。"""
        self.view.set_color(color)
        if highlighter_color is not None:
            self.view.set_highlighter_color(highlighter_color)
        self.view.set_width(width)
        self.view.set_clear_key(clear_key)          # 非法键由视图回退默认
        if ink_smoothing is not None:
            self.view.set_ink_smoothing(ink_smoothing)
        if pressure_enabled is not None:
            self.view.set_pressure_enabled(pressure_enabled)
        self._color = self.view.color
        self._highlighter_color = self.view.highlighter_color
        self._width = self.view.width
        self._clear_key = self.view.clear_key
        self._synced_clear_key = self.view.clear_key
        self.toolbox.reflect_color(self.view.color, "pen")
        self.toolbox.reflect_color(self.view.highlighter_color, "highlighter")
        self.toolbox.reflect_tool(self.view.tool)
        self.toolbox.reflect_width(self.view.width)

    # --- 最大化 / 还原 ---

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, ev) -> None:
        if ev.type() == QEvent.WindowStateChange:
            self.titlebar.set_maximized(self.isMaximized())
        super().changeEvent(ev)

    def nativeEvent(self, event_type, message):
        """边缘缩放光标：与主窗共用同一套 WM_NCHITTEST 判定。"""
        hit = native_hit_test(self, event_type, message)
        if hit is not None:
            return True, hit
        return super().nativeEvent(event_type, message)

    # --- 新建草稿 ---

    def reset_document(self) -> None:
        """换一份空文档：旧视图连同撤销栈一起丢弃，旧命令不会回灌到新文档。"""
        old = self.view
        self.document = Document()
        view = SketchView(SketchScene(self.document), self.settings)
        view.set_tool(self._tool)
        view.set_color(self._color)
        view.set_highlighter_color(self._highlighter_color)
        view.set_width(self._width)
        view.set_clear_key(self._clear_key)
        view.changed.connect(self._on_view_changed)
        if self._dock is not None:
            self._dock.replace_view(old, view)      # 停靠中：只换右栏画布
        else:
            index = self.column.indexOf(old)
            self.column.removeWidget(old)
            old.setParent(None)
            self.column.insertWidget(index, view, 1)
            view.show()
        old.deleteLater()
        self.view = view
        # 快捷键随视图走：旧视图的随它销毁，这里给新视图再装一套
        self._history_shortcuts = self._install_history_shortcuts(view)

    # --- 停靠到主窗右栏 ---

    def is_docked(self) -> bool:
        return self._dock is not None

    def build_dock(self, parent: QWidget | None = None) -> ScratchDock:
        """把工具栏与画布搬进停靠容器（不复制）；重复调用返回同一个。"""
        if self._dock is None:
            self._dock = ScratchDock(self.toolbox, self.view, parent)
            self._dock.undockRequested.connect(self.undockRequested.emit)
        return self._dock

    def release_dock(self) -> None:
        """从停靠容器里把工具栏与画布搬回浮窗；不删除文档/画布。"""
        dock = self._dock
        if dock is None:
            return
        self._dock = None
        toolbox = dock.take_toolbox()
        view = dock.take_view()
        self.column.addWidget(toolbox)
        self.column.addWidget(view, 1)
        toolbox.split_doc_button.show()
        toolbox.show()
        view.show()
        dock.setParent(None)
        dock.deleteLater()

    def closeEvent(self, ev) -> None:
        ev.ignore()
        self.hide()


class ScratchController(QObject):
    """懒创建草稿窗；toggle 显隐、show_near_cursor 定位、new_draft 清空。不落盘。"""

    settingsRequested = Signal()
    dockRequested = Signal()
    undockRequested = Signal()

    def __init__(self, settings: Settings | None = None,
                 main_document: Document | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings or Settings()
        self.main_document = main_document
        self._window: ScratchWindow | None = None
        self._placed = False

    @property
    def window(self) -> ScratchWindow:
        """懒创建：没人弹草稿就不建窗口（托盘/热键失败时也不占资源）。"""
        if self._window is None:
            self._window = ScratchWindow(self.settings)
            self._window.settingsRequested.connect(self.settingsRequested.emit)
            self._window.dockRequested.connect(self.dockRequested.emit)
            self._window.undockRequested.connect(self.undockRequested.emit)
        return self._window

    @property
    def view(self) -> SketchView:
        return self.window.view

    def apply_settings(self, color: str, width: float, clear_key: str,
                       ink_smoothing: str | None = None,
                       highlighter_color: str | None = None,
                       pressure_enabled: bool | None = None) -> None:
        """设置面板变了：窗口已建就同步；没建的话下次创建会从 settings 里读到。"""
        if self._window is not None:
            self._window.apply_settings(color, width, clear_key, ink_smoothing,
                                        highlighter_color=highlighter_color,
                                        pressure_enabled=pressure_enabled)

    def is_visible(self) -> bool:
        # Qt 里最小化窗口的 isVisible() 仍为 True，但用户看不见；不能算“已显示”，
        # 否则下一次热键会走 hide 分支，之后 show() 又保持最小化，看起来再也呼不出。
        return (self._window is not None and self._window.isVisible()
                and not self._window.isMinimized())

    def toggle(self) -> None:
        if self.is_visible():
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        """首次弹出放鼠标所在屏中心；之后记住上次位置（设计文档 §15）。"""
        if not self._placed:
            self.show_near_cursor()
            return
        win = self.window
        if win.isMinimized():
            win.showNormal()          # 最小化后直接 show() 仍会保持最小化，必须还原
        else:
            win.show()
        win.raise_()
        win.activateWindow()

    def show_near_cursor(self) -> None:
        win = self.window
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        area = screen.availableGeometry()
        w, h = win.width(), win.height()
        x, y = clamp_to_area(area, w, h,
                             area.center().x() - w // 2, area.center().y() - h // 2)
        win.move(x, y)
        win.show()
        win.raise_()
        win.activateWindow()
        self._placed = True

    def hide(self) -> None:
        if self._window is not None:
            self._window.hide()

    def new_draft(self) -> None:
        """托盘「新建草稿」：换空文档并弹出来；停靠时只换右栏画布。"""
        self.window.reset_document()
        if self.window.is_docked():
            return
        self.show()


class TrayIcon(QSystemTrayIcon):
    """托盘三项：显示主窗 / 新建草稿 / 退出；动作只发信号，由 main.py 接线。"""

    showMainRequested = Signal()
    newDraftRequested = Signal()
    quitRequested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(_tray_icon(), parent)
        self.setToolTip(APP_NAME)
        self.menu = QMenu()                     # 留引用，否则菜单被回收
        self.show_main_action = self.menu.addAction("显示主窗")
        self.new_draft_action = self.menu.addAction("新建草稿")
        self.quit_action = self.menu.addAction("退出全部实例")
        self.setContextMenu(self.menu)
        self.show_main_action.triggered.connect(lambda: self.showMainRequested.emit())
        self.new_draft_action.triggered.connect(lambda: self.newDraftRequested.emit())
        self.quit_action.triggered.connect(lambda: self.quitRequested.emit())
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.showMainRequested.emit()
