"""接线层：窗口、工具栏、画布、状态栏、草稿纸、托盘、全局热键。

草稿只存在内存里（设计文档 §8：2026-10-20 撤销保存）；配置文件仍然落盘。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import shiboken6

try:
    from PySide6.QtWidgets import QApplication
except ImportError:                 # 设计文档 §9：打一行安装命令就退，不抛栈
    if __name__ != "__main__":
        raise
    print("缺少 PySide6：请运行 pip install PySide6")
    raise SystemExit(1)

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt
from PySide6.QtCore import QUrl
from PySide6.QtGui import (QAction, QColor, QCursor, QDesktopServices,
                           QGuiApplication, QImage, QKeySequence, QPalette,
                           QShortcut)
from PySide6.QtWidgets import (QApplication, QAbstractSpinBox, QDialog, QFileDialog,
                               QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QScrollArea,
                               QSplitter, QStackedWidget, QSystemTrayIcon, QTextEdit,
                               QVBoxLayout, QWidget)

from sketchpad.ui import dialogs
from sketchpad.ui import icons
from sketchpad.annotate import install          # 全屏批注层（子功能）
from sketchpad.screenshot import install as install_screenshot
from sketchpad.core import pdfstore
from sketchpad.core import settings as settings_store
from sketchpad.core.notes import Note, clamp_note_anchor, new_note_id, note_display_name
from sketchpad.ui import theme, tooltips
from sketchpad.ui.canvas import SketchScene, SketchView
from sketchpad.ui.empty_workspace import EmptyWorkspace
from sketchpad.ui.find_bar import FindBar
from sketchpad.ui.shell_background import ShellBackground
from sketchpad.ui.frameless import (apply_rounded_corners, enable_native_resize,
                           native_hit_test, set_native_topmost, install_window_border)
from sketchpad.core.channel import APP_ID, APP_NAME, DATA_DIR
from sketchpad.services import single_instance
from sketchpad.services.doclock import DocumentLock
from sketchpad.services.hotkey import GlobalHotkey
from sketchpad.ui.menubar import Menubar
from sketchpad.ui.note_card import NoteCard, NoteWindow
from sketchpad.services.pdfautosave import (PdfAutoSaver, clear_cache, list_caches,
                                             read_cache, write_cache)
from sketchpad.services.search import PdfSearchWorker
from sketchpad.pdf.document import (PdfError, export_with_notes,
                                    export_with_strokes)
from sketchpad.pdf.pagemode import PAGE_MODE_LABELS
from sketchpad.ui.pdfview import PdfView
from sketchpad.ui.scratch import ScratchController, TrayIcon
from sketchpad.core.settings import (PAGE_MODES, TOOLBAR_POSITIONS, WIDTHS,
                                     pdf_position, push_recent,
                                     remember_pdf_position, remove_recent)
from sketchpad.ui.recent_dialog import RecentFilesDialog
from sketchpad.ui.settings_dialog import SettingsDialog
from sketchpad.ui.splash import show_launch_splash
from sketchpad.ui.status_pill import StatusPill
from sketchpad.core.strokes import Document
from sketchpad.ui.thumb_panel import ThumbPanel
from sketchpad.ui.titlebar import TitleBar
from sketchpad.ui.toc_panel import TocPanel
from sketchpad.ui.toolbox import PALETTE, Toolbox
from sketchpad.ui.top_chrome import TopChrome
from sketchpad.workspace.session import DocumentSession, NoteSession, make_session_pane
from sketchpad.workspace.split import SplitWorkspace
from sketchpad.workspace.tabs import DEFAULT_DRAFT_TITLE, TabEntry, WorkspaceTabs
from sketchpad.workspace.viewers import (create_converter, create_editor,
                                         create_viewer, has_converter,
                                         is_supported_document, supported_suffixes,
                                         supports_edit, viewer_kind)

SETTINGS_NAME = "settings.json"
FALLBACK_DIR = APP_ID
MIN_W, MIN_H = 340, 240        # 允许缩成很小的浮窗（用户 2026-10-20 要求）
EMPTY_HINT_TEXT = "打开 PDF（Ctrl+O） · 直接写 · B 画笔 / Q 橡皮"
TRAY_HINT_TITLE = APP_NAME
TRAY_HINT_BODY = "程序仍在托盘运行；{scratch} 呼出草稿纸，{main} 呼出主窗"
TRAY_HINT_MS = 4000
NOTE_CLEAR_MS = 4000           # 状态栏提示只保留最新一条，4 秒后自动清空
SCREENSHOT_HOTKEY = "Ctrl+1"   # 全局一步呼出区域截图（仅主实例注册）
NOTE_ERROR_COLOR = f"color: {theme.DANGER};"
MAX_WIDGET_SIZE = 16777215     # Qt 的 QWIDGETSIZE_MAX：解掉固定宽/高（换工具栏位置时用）
COLLAPSED_TOOLBAR_SIZE = 22    # 收起态窄条宽度（与 Toolbox.COLLAPSE_SIZE 一致）
CLOSE_DIALOG_TIMEOUT_MS = 90000   # 关闭确认框兜底：超时未关=按取消恢复，防界面锁死
QUIT_ALL_MESSAGE = "PageFlow.QuitAll.v1"   # 跨进程“退出全部实例”广播
_HWND_BROADCAST = 0xFFFF
_QUIT_ALL_MSG_ID = 0
_QUIT_ALL_HANDLING = False
_QUIT_BROADCAST_ENABLED = False   # 只有 app.main() 启动的真实实例才互相通知


def _window_message_id(name: str) -> int:
    """注册跨进程窗口消息（同名字在各进程拿到同一个 ID）。"""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
        user32.RegisterWindowMessageW.restype = ctypes.c_uint
        return int(user32.RegisterWindowMessageW(name))
    except Exception:
        return 0


def broadcast_quit_all() -> bool:
    """通知同用户会话里的其他墨页进程退出；隐藏到托盘的窗口也能收到。"""
    if not _QUIT_BROADCAST_ENABLED or not _QUIT_ALL_MSG_ID:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_size_t, ctypes.c_ssize_t]
        user32.PostMessageW.restype = ctypes.c_bool
        return bool(user32.PostMessageW(ctypes.c_void_p(_HWND_BROADCAST),
                                        _QUIT_ALL_MSG_ID, os.getpid(), 0))
    except Exception:
        return False


_QUIT_ALL_MSG_ID = _window_message_id(QUIT_ALL_MESSAGE)


def local_pdf_paths(mime) -> list[Path]:
    """从拖放数据里挑出本地 PDF；没有则空列表（拖入文件夹/文本一律不接）。

    单独成函数是为了测试能直接喂 QMimeData，不必合成完整拖放事件。
    """
    if mime is None or not mime.hasUrls():
        return []
    paths: list[Path] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        candidate = Path(url.toLocalFile())
        if candidate.suffix.lower() in (".pdf",) and candidate.is_file():
            paths.append(candidate)
    return paths


def local_document_paths(mime, suffixes=None) -> list[Path]:
    """按后缀挑本地文件；缺省取注册表里当前产品支持的全部格式。"""
    if mime is None or not mime.hasUrls():
        return []
    allowed = tuple(str(s).lower() for s in (suffixes or supported_suffixes()))
    paths: list[Path] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        candidate = Path(url.toLocalFile())
        if candidate.suffix.lower() in allowed and candidate.is_file():
            paths.append(candidate)
    return paths


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")
SCRATCH_DOCK_RATIO = 0.5          # 草稿纸停靠时文档占左栏的比例


def local_image_paths(mime) -> list[Path]:
    """拖放/剪贴板里挑出本地图片（草稿插入截图用）。"""
    if mime is None or not mime.hasUrls():
        return []
    paths: list[Path] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        candidate = Path(url.toLocalFile())
        if candidate.suffix.lower() in IMAGE_SUFFIXES and candidate.is_file():
            paths.append(candidate)
    return paths


def _writable(folder: Path) -> bool:
    """试建、试写、再删一个探测文件来判断目录可写。

    Windows 上 os.access(dir, os.W_OK) 对目录几乎恒为真，靠不住，所以直接真写一次。
    """
    folder = Path(folder)
    probe = folder / f".write-probe-{os.getpid()}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink()
        except OSError:
            pass
        return False


def resolve_settings_path(data_dir: Path) -> Path:
    """配置落点：项目 data/ 可写就用它，否则退到 %APPDATA%/<APP_ID>。"""
    data_dir = Path(data_dir)
    if _writable(data_dir):
        return data_dir / SETTINGS_NAME
    base = os.environ.get("APPDATA") or str(Path.home())
    fallback = Path(base) / FALLBACK_DIR
    _writable(fallback)
    return fallback / SETTINGS_NAME


class AppController:
    """内存文档 + 配置读写。没有草稿落盘、没有启动恢复。"""

    def __init__(self, data_dir: Path, persist_settings: bool = True) -> None:
        self.settings_path = resolve_settings_path(Path(data_dir))
        self.settings = settings_store.load(self.settings_path)
        self.document = Document()
        # 副实例只读配置：避免开发实例把正在使用的实例的配置/最近文件覆盖掉
        self.persist_settings = bool(persist_settings)

    def save_settings(self) -> None:
        if not self.persist_settings:
            return
        try:
            settings_store.save(self.settings_path, self.settings)
        except OSError:
            pass                    # 配置存不上不该拖住关窗；草稿本来就不落盘


def _repaint_view(view) -> None:
    """跨窗重挂后的强制重绘：viewport update + 视图自己的重渲入口（有就调）。"""
    try:
        view.viewport().update()
        for name in ("_schedule_render", "_apply_view"):
            hook = getattr(view, name, None)
            if callable(hook):
                hook()
    except RuntimeError:
        pass


class MainWindow(QMainWindow):
    """工具栏在上、画布在下、状态栏在底；只接线，不含业务规则。"""

    STATUS_TEMPLATE = "当前色 {color} · 粗细 {width:g} · 缩放 {zoom:.0f}% · 笔迹 {count}"
    DOC_STATUS_TEMPLATE = "第 {page}/{pages} 页 · 缩放 {zoom:.0f}% · 笔迹 {count}"
    ZOOM_STEP = 1.25

    def __init__(self, controller: AppController, primary: bool = True,
                 registry=None, primary_instance: bool = True) -> None:
        super().__init__()
        self.controller = controller
        self.primary = bool(primary)
        # 主实例 = 第一个启动的进程：托盘/全局热键/全屏批注只归它
        self.primary_instance = bool(primary_instance)
        self.window_registry = registry
        self.window_id = 0
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())       # 任务栏/Alt-Tab 不再用 Qt 默认图标
        # 无边框 + 自绘标题栏：Windows 原生标题栏塞不进"置顶"按钮（用户 2026-10-20）
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(1160, 760)          # 初始尺寸：PDF 模式整条工具栏 1124px，视口 1132px，留 8px
        self.setAcceptDrops(True)       # 拖入 PDF 直接打开（见 dragEnterEvent/dropEvent）

        self.titlebar = TitleBar(APP_NAME, self, show_settings=False, compact=True)
        self.titlebar.pinToggled.connect(self.set_always_on_top)
        self.titlebar.minimizeRequested.connect(self.showMinimized)
        self.titlebar.maximizeRequested.connect(self._toggle_maximized)
        self.titlebar.closeRequested.connect(self.close)

        self.toolbox = Toolbox(controller.settings)
        # 工具栏放进横向滚动区：窗口缩窄时按钮不被裁掉，可以横向滚
        self.toolbar_scroll = QScrollArea(self)
        self.toolbar_scroll.setObjectName("ToolbarScroll")
        # 滚动区与视口都不能吃焦点：点右侧空白/滚动条会把焦点从画布抢走，
        # 之后 B/Q/1-4/方向键全哑（按钮已是 NoFocus，这两层还漏着）
        self.toolbar_scroll.setFocusPolicy(Qt.NoFocus)
        self.toolbar_scroll.viewport().setFocusPolicy(Qt.NoFocus)
        # 顶栏是一块平面：滚动区视口默认白底会把工具栏切成另一层，显式刷成窗口底色
        viewport = self.toolbar_scroll.viewport()
        viewport.setAutoFillBackground(True)
        viewport_palette = viewport.palette()
        viewport_palette.setColor(QPalette.Window, QColor(theme.WINDOW_BG))
        viewport.setPalette(viewport_palette)
        self.toolbar_scroll.setWidget(self.toolbox)
        self.toolbar_scroll.setWidgetResizable(False)
        self.toolbar_scroll.setFrameShape(QFrame.NoFrame)
        self.toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 滚动条晚一步才出现（布局排完才知道装不下）：范围一变就再量一次，
        # 否则固定宽度会把竖排按钮裁掉、或高度少算一行横向滚动条
        for scroll_bar in (self.toolbar_scroll.horizontalScrollBar(),
                           self.toolbar_scroll.verticalScrollBar()):
            scroll_bar.rangeChanged.connect(self._schedule_toolbar_sync)
        self.toolbox.collapsedChanged.connect(self._on_toolbar_collapsed)
        self._sync_toolbar_size()

        self.view = SketchView(SketchScene(controller.document), controller.settings)
        self.view.set_width(controller.settings.width)   # 配置里的自定义粗细优先
        self._initial_note = NoteSession(DEFAULT_DRAFT_TITLE, self.view,
                                          controller.document, self.view)
        self._connected_note_views: set[int] = set()

        # 草稿画布空态提示：叠在视口上的 QLabel，不参与绘制逻辑（不动 SketchView 的 paint）
        self.empty_hint = QLabel(EMPTY_HINT_TEXT, self.view.viewport())
        self.empty_hint.setObjectName("EmptyHint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # 点击穿到画布
        self.empty_hint.setAttribute(Qt.WA_NoSystemBackground, True)
        self.empty_hint.setStyleSheet("color: #6e6e73; font-size: 13px; background: transparent;")
        self.empty_hint.adjustSize()
        self.view.viewport().installEventFilter(self)    # 视口改尺寸时重新居中

        # 多文档工作区：草稿固定第 0 页；每个文档一个独立 session/标签
        self.pdf_view = PdfView(controller.settings)   # 草稿态占位（无文档）
        self.tabs = WorkspaceTabs(self)
        self.stack = self.tabs.stack
        self._draft_entry = (self.tabs.add_draft(
            self.view, DEFAULT_DRAFT_TITLE, self._initial_note)
            if self.primary else None)
        # 空白工作区：最后一个标签关掉才创建，启动时 stack 里仍只有草稿页
        self._empty_page: EmptyWorkspace | None = None
        self._active_session: DocumentSession | None = None
        self._active_entry: TabEntry | None = None
        self.menubar = Menubar(self)
        self.menubar.new_draft_action.setEnabled(self.primary)
        self._detach_menu_shortcuts()
        self.mode = "draft"
        self._word_editable = False
        self._edit_word_shortcut: QShortcut | None = None
        # 笔记卡片覆盖层：同一时间最多一张；None 表示当前没有展开的卡片
        # 独立笔记窗口：(页号, note id) → NoteWindow；同一张笔记只允许一个窗口
        self._note_windows: dict[tuple[int, str], NoteWindow] = {}
        # 主窗内分屏：PDF 在左、笔记在右；同一时间只分屏一张笔记
        self._note_split_card: NoteCard | None = None
        self._note_split_pane: QWidget | None = None
        self._note_split_key: tuple[int, str] | None = None
        self._split_prev_auto_fit: bool | None = None
        self._note_split_fit_slot = None
        self._note_split_ratio_slot = None
        # 草稿纸停靠：文档左 / 草稿右；同一时间只停靠在一个文档会话上
        self._scratch_dock: QWidget | None = None
        self._scratch_dock_pane: QWidget | None = None
        self._scratch_dock_session = None
        self._scratch_dock_prev_auto_fit: bool | None = None
        self._scratch_dock_ratio = SCRATCH_DOCK_RATIO
        self._scratch_dock_fit_slot = None
        self._scratch_dock_ratio_slot = None

        # 侧栏：目录 + 缩略图（默认都隐藏，仅文档模式用）
        self.toc_panel = TocPanel(self)
        self.thumb_panel = ThumbPanel(self)
        self.toc_panel.hide()
        self.thumb_panel.hide()
        # 阅读模式：启动即按配置点亮菜单并同步缩略图（PdfView 构造时已自行读取）
        self.menubar.set_page_mode(controller.settings.page_mode)
        self.thumb_panel.set_page_mode(controller.settings.page_mode)

        self.side_panel = QWidget(self)
        self.side_panel.setObjectName("SidePanel")
        self.side_panel.setAttribute(Qt.WA_StyledBackground, True)
        self.side_panel.setStyleSheet(f"QWidget#SidePanel {{ background: {theme.WINDOW_BG}; }}")
        side_layout = QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        # 目录 + 缩略图上下可拖拽：两者同时显示时高度按需分配，不再平均挤在一起
        self.side_splitter = QSplitter(Qt.Vertical, self.side_panel)
        self.side_splitter.setObjectName("SideSplitter")
        self.side_splitter.setChildrenCollapsible(False)   # 不允许拖到 0 高度消失
        self.side_splitter.setHandleWidth(6)
        self.side_splitter.addWidget(self.toc_panel)
        self.side_splitter.addWidget(self.thumb_panel)
        for panel in (self.toc_panel, self.thumb_panel):
            panel.setMinimumHeight(120)
        self.side_splitter.setSizes([400, 260])
        side_layout.addWidget(self.side_splitter)
        self.side_panel.hide()

        # 查找条（默认隐藏，文档模式 Ctrl+F 唤起）
        self.find_bar = FindBar(self)
        self.find_bar.hide()
        self._find_hits: list = []
        self._find_index = -1
        self._find_truncated = False
        # 查找：输入只作废旧结果，回车/下一个才真正启动后台全文搜索
        # （大 PDF 上每键一次全文扫描会把界面卡成幻灯片——用户实测反馈）
        self._find_query = ""
        # 搜索放到后台线程（worker 带自己的 PdfSource）：代号每作废/重启一次 +1，
        # 迟到的旧 worker 结果按代号丢弃；retired 兜住取消后尚未退场的线程。
        self._find_generation = 0
        self._find_worker: PdfSearchWorker | None = None
        self._find_retired: list[PdfSearchWorker] = []

        central = ShellBackground(self)
        central.setObjectName("Root")
        central.setAttribute(Qt.WA_StyledBackground, True)   # 同上：不吃 QSS 背景
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.top_chrome = TopChrome(central)
        self.top_chrome.add_row(self.titlebar)
        self.top_chrome.add_row(self.menubar)
        self.top_chrome.add_row(self.toolbar_scroll)
        column.addWidget(self.top_chrome)
        column.addWidget(self.find_bar)
        # body 行：左侧栏 + 视图栈；侧栏隐藏时不占位置
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.side_panel)
        body.addWidget(self.tabs, 1)
        column.addLayout(body, 1)
        self.column = column            # 位置切换要把工具栏在 TopChrome 行与 body 之间搬来搬去
        self.body = body
        self.setCentralWidget(central)
        # v2.9：工具栏位置与收起状态都从配置恢复（右排=竖栏，折叠在两种位置都成立）
        self._apply_toolbar_position(self._saved_toolbar_position())
        self.toolbox.set_collapsed(bool(controller.settings.toolbar_collapsed))
        self.toolbox.set_select_enabled(True)       # 草稿画布：鼠标工具=手形平移
        self.toolbox.set_new_note_enabled(self.primary)
        self.toolbox.set_annotate_enabled(self.primary and self.primary_instance)
        self.menubar.fullscreen_annotate_action.setEnabled(self.primary_instance)
        if not self.primary_instance:
            self.menubar.fullscreen_annotate_action.setToolTip(
                "全屏批注只在第一个启动的实例可用")
            self.toolbox.scratch_button.setToolTip(
                "草稿纸（全局热键只在第一个实例生效；点这里打开）")
        self.toolbox.set_note_enabled(False)        # 草稿画布没有 PDF 页可放笔记
        self.toolbox.set_text_enabled(True)         # 草稿画布：T 落点输入文字
        self.toolbox.set_draft_images_enabled(True) # 草稿可插截图/图片
        self.toolbox.set_find_enabled(False)        # 草稿没有 PDF 可查
        self.toolbox.clear_button.setToolTip("清空笔迹（背景图片保留）(E)")

        # PDF 批注自动保存（停笔 debounce）：先写崩溃可恢复的缓存，
        # 正常切换/退出时再提交 sidecar 并清缓存。
        self._dirty = False
        self._autosave_base = self.controller.settings_path.parent
        self.pdf_autosave = PdfAutoSaver(self)
        self.pdf_autosave.saved.connect(
            lambda: self._note(f"批注已自动暂存 {datetime.now().strftime('%H:%M:%S')}"))
        self.pdf_autosave.failed.connect(
            lambda msg: self._note(f"自动保存失败：{msg}", error=True))
        self._empty_pdf_view = self.pdf_view
        self._idle_autosave = self.pdf_autosave
        self._connected_sessions: set[int] = set()
        self._switching_tab = False
        self._configure_autosave()

        self.status_pill = StatusPill(self)
        self.message_label = self.status_pill.message
        self.save_label = self.status_pill.save
        self.status_label = self.status_pill.status
        # 侧栏是否已为某份文档填过内容：隐藏时只填一次，分屏来回切换不再重建
        self._side_seen_sources: set[int] = set()
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setAutoFillBackground(True)
        bar_palette = bar.palette()
        bar_palette.setColor(QPalette.Window, QColor(theme.WINDOW_BG))
        bar_palette.setColor(QPalette.Base, QColor(theme.WINDOW_BG))
        bar.setPalette(bar_palette)
        bar.setStyleSheet(f"QStatusBar {{ background: {theme.WINDOW_BG}; }}")
        bar.setContentsMargins(8, 0, 8, 4)
        bar.addWidget(self.status_pill, 1)
        # 状态栏提示只留最新一条（右侧保存角标才是持久状态）：每次都重启 4 秒清除表
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(NOTE_CLEAR_MS)
        self._note_timer.timeout.connect(self._clear_note)
        # 阅读位置：翻页立即落盘；缩放/滚动停 600ms 后落盘，强杀进程也不丢上一次位置
        self._position_timer = QTimer(self)
        self._position_timer.setSingleShot(True)
        self._position_timer.setInterval(600)
        self._position_timer.timeout.connect(self._save_pdf_position_now)
        self._saved_at: str | None = None          # 上次成功提交 sidecar 的时刻
        self._tray_hint_shown = False              # 首次收进托盘的提示只弹一次
        pending_caches = [item for item in list_caches(self._autosave_base)
                          if item[0].exists()]
        if pending_caches:
            self._note(f"发现 {len(pending_caches)} 份未恢复的批注暂存；"
                       "打开对应 PDF 时会提示恢复")

        self._wire()
        self._connected_note_views.add(id(self.view))   # _wire 已接好首个笔记视图
        self.titlebar.set_center_widget(self.tabs.strip)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.renameRequested.connect(self._rename_entry)
        self.tabs.closeRequested.connect(self._close_entry)
        self.tabs.closeOthersRequested.connect(self._close_other_entries)
        self.tabs.splitRequested.connect(self._start_document_split)
        self.tabs.drag_controller.transferRequested.connect(self._on_tab_transfer)
        self.tabs.drag_controller.mergeRequested.connect(self._on_tab_merge)
        self.tabs.drag_controller.detachRequested.connect(self._on_tab_detach)
        self._install_shortcuts()
        # 清屏键统一由应用级 eventFilter 路由到当前视图；草稿画布内建的
        # QShortcut 关掉，否则同一组合会有两条路径
        self.view.set_clear_shortcut_enabled(False)
        # 应用级按键过滤：焦点被侧栏/滚动条/按钮抢走后，B/Q/1-4/0 与清屏键
        # 仍要落到当前视图上（视图自己的 keyPressEvent 只在焦点在视图里时有效）
        app = QApplication.instance()
        if app is not None and self.primary:
            # 二级窗口不抢应用级按键路由：主窗的过滤器只服务一个活动视图
            app.installEventFilter(self)
        # 草稿纸：懒创建窗口；热键与托盘由 main() 在启动时显式打开
        self.scratch = ScratchController(controller.settings, controller.document, self)
        self.scratch.dockRequested.connect(self._dock_scratch)
        self.scratch.undockRequested.connect(lambda: self._undock_scratch(show_window=True))
        self.scratch.settingsRequested.connect(self._open_settings_from_scratch)
        self.toolbox.scratchRequested.connect(self._toggle_scratch_window)
        self.hotkey = GlobalHotkey(self)
        self.hotkey.activated.connect(self._toggle_scratch_window)
        self.main_hotkey = GlobalHotkey(self)
        self.main_hotkey.activated.connect(self.restore_main)
        self.screenshot_hotkey = GlobalHotkey(self)
        self.screenshot_hotkey.activated.connect(
            lambda: self._start_screenshot("region"))
        self.tray: TrayIcon | None = None
        self.fallback_shortcut: QShortcut | None = None
        self.settings_dialog: SettingsDialog | None = None
        self._annotate = None                 # app.main 装完全屏批注层后挂上
        self._screenshot = None               # app.main 装好截图控制器后挂上
        self.menubar.recent_menu.installEventFilter(self)
        self._screenshot_hidden = False
        self._word_editors: dict[str, QWidget] = {}   # 规范化路径 → 编辑窗口
        self.convert_dialog: QWidget | None = None
        self._quitting = False
        self._close_confirm_pending = False
        self._close_prompt = None
        self._close_dialog = None
        self._close_dialog_restore = None
        self._close_suspended_restore = None
        self._always_on_top = False
        self._refresh_title()
        self.refresh_status()
        # 边缘缩放只走 WM_NCHITTEST（nativeEvent）：应用级按下过滤器按 Qt 逻辑坐标
        # 判边，与 Windows 的物理坐标在高 DPI 下错位，会把右/下大片区域当成边缘。
        # 按已落盘的最近记录初始化菜单（文件可能已不在：条目置灰并挂全路径 tooltip）
        self._rebuild_recent_menu()
        if supported_suffixes() != (".pdf",):
            self.menubar.set_open_label("打开文件…", "PDF / Markdown / 文本")
        self.menubar.set_convert_available(has_converter())
        self._refresh_empty_hint()
        self._window_border = install_window_border(self)

        # 窗口登记：主窗建 registry，二级窗口沿用同一个（共享 settings/最近文件）
        if self.primary:
            from sketchpad.workspace.windows import WindowRegistry
            self.window_registry = WindowRegistry(
                self,
                lambda: MainWindow(self.controller, primary=False,
                                   registry=self.window_registry),
                self)
        elif self.window_registry is not None:
            self.window_registry.register(self)
        if not self.primary:
            self._show_empty_page()

    # --- 工具栏位置与尺寸（v2.9：顶部横排 / 右侧竖排）---

    def _saved_toolbar_position(self) -> str:
        """配置里的位置；坏值一律当顶部（load 已归一，直接改对象的测试也不再炸）。"""
        position = getattr(self.controller.settings, "toolbar_position", "top")
        return "right" if position == "right" else "top"

    def _apply_toolbar_position(self, position: str) -> None:
        """把工具栏搬到顶部顶栏或右侧竖排。

        顶排：工具栏在 TopChrome 的第 3 行；右排：塞进 body 行最右（视图栈之后）。
        两套布局共用同一个 QScrollArea，所以换位置 = 从一套布局摘下来、挂到另一套。
        """
        vertical = position == "right"
        self.toolbox.set_vertical(vertical)
        self.toolbox.set_dock("right" if vertical else "top")
        self.toolbar_scroll.setMinimumWidth(0)
        self.toolbar_scroll.setMaximumWidth(MAX_WIDGET_SIZE)
        self.toolbar_scroll.setMinimumHeight(0)
        self.toolbar_scroll.setMaximumHeight(MAX_WIDGET_SIZE)
        if vertical:
            if self.top_chrome.has_row(self.toolbar_scroll):
                self.top_chrome.remove_row(self.toolbar_scroll)
            self.body.addWidget(self.toolbar_scroll)      # 加在视图栈之后 = 右边缘
            self.toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        else:
            self.body.removeWidget(self.toolbar_scroll)
            if not self.top_chrome.has_row(self.toolbar_scroll):
                self.top_chrome.add_row(self.toolbar_scroll)
            self.toolbar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.toolbar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.toolbar_scroll.updateGeometry()
        self._schedule_toolbar_sync()

    def _schedule_toolbar_sync(self, *_args) -> None:
        """下一轮事件循环再量工具栏尺寸（布局/滚动条变化都走这里，合并同一轮多次请求）。"""
        QTimer.singleShot(0, self._sync_toolbar_size)

    def _sync_toolbar_size(self) -> None:
        """横排固定高度、竖排固定宽度；只在滚动条真的出现时才加它那一行/列。

        收起态用控件自身尺寸（工具栏已把固定边压到 22px），展开态用 sizeHint。
        """
        scroll = self.toolbar_scroll
        # 横排/竖排的 sizeHint 完全不同，而滚动区（widgetResizable=False）不会替我们
        # 改子控件尺寸：换方向、展开后都得自己套一次，否则按钮被裁掉
        self.toolbox.adjustSize()
        if getattr(self.toolbox, "vertical", False):
            bar = scroll.verticalScrollBar()
            extra = bar.sizeHint().width() if bar.isVisible() else 0
            # 收起态 toolbox 已把宽度钉死（22px）：取最小值，别等布局刷新才量到旧宽度
            content = (max(self.toolbox.minimumWidth(), COLLAPSED_TOOLBAR_SIZE)
                       if self.toolbox.collapsed else self.toolbox.sizeHint().width())
            target = content + extra + 2
            if scroll.minimumHeight() != 0 or scroll.maximumHeight() != MAX_WIDGET_SIZE:
                scroll.setMinimumHeight(0)
                scroll.setMaximumHeight(MAX_WIDGET_SIZE)
            if scroll.width() != target or scroll.minimumWidth() != target:
                scroll.setFixedWidth(target)
            return
        bar = scroll.horizontalScrollBar()
        extra = bar.sizeHint().height() if bar.isVisible() else 0
        content = (self.toolbox.height() if self.toolbox.collapsed
                   else self.toolbox.sizeHint().height())
        target = content + extra + 2
        if scroll.minimumWidth() != 0 or scroll.maximumWidth() != MAX_WIDGET_SIZE:
            scroll.setMinimumWidth(0)
            scroll.setMaximumWidth(MAX_WIDGET_SIZE)
        if scroll.height() != target or scroll.minimumHeight() != target:
            scroll.setFixedHeight(target)

    def _sync_toolbar_height(self) -> None:
        """v2.8 的旧名（测试/外部可能还在用）：统一转给 _sync_toolbar_size。"""
        self._sync_toolbar_size()

    def _on_toolbar_collapsed(self, collapsed: bool) -> None:
        """收起/展开后布局尺寸已变：记进配置，下一轮事件循环再量一次尺寸。"""
        self.controller.settings.toolbar_collapsed = bool(collapsed)
        self._schedule_toolbar_sync()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._schedule_toolbar_sync()
        prompt = getattr(self, "_close_prompt", None)
        if prompt is not None:
            prompt.setGeometry(self.rect())

    def _wire(self) -> None:
        box, view, pdf_view = self.toolbox, self.view, self.pdf_view
        # 颜色按当前工具分开存；粗细对两个视图同时生效；工具走 _apply_tool
        box.colorPicked.connect(self._on_color_picked)
        for target in (view, pdf_view):
            box.widthChosen.connect(target.set_width)
        box.toolChosen.connect(self._apply_tool)
        box.widthChosen.connect(self._on_width_chosen)
        box.clearRequested.connect(self._clear_active)   # 工具栏垃圾桶：草稿清整块、文档清当前页
        box.undoRequested.connect(self._undo)      # 撤销/重做只作用于当前模式的视图
        box.redoRequested.connect(self._redo)
        box.editRequested.connect(self._edit_word_document)
        box.imageRequested.connect(self._choose_image)
        box.captureRequested.connect(self._start_screenshot)
        box.captureModeRequested.connect(self._on_capture_mode)
        box.annotateToggled.connect(self._set_fullscreen_annotate)
        box.newNoteRequested.connect(self._new_draft_entry)
        box.findRequested.connect(self._show_find)   # 工具栏查找按钮 = Ctrl+F
        box.splitRequested.connect(
            lambda: self._start_document_split(self.tabs.current_entry))
        box.settingsRequested.connect(self._open_settings)   # 设置齿轮移到工具栏
        view.changed.connect(self._on_view_changed)
        pdf_view.changed.connect(self._on_pdf_changed)
        pdf_view.pageChanged.connect(self._on_pdf_page_changed)
        pdf_view.pageChanged.connect(self.thumb_panel.set_current)  # 翻页同步缩略图高亮
        pdf_view.zoomChanged.connect(self._on_pdf_zoom_changed)
        # 页内滚动只改 offset，不触发 pageChanged：去抖后也落一次
        pdf_view.verticalScrollBar().valueChanged.connect(self._schedule_position_save)
        # 侧栏面板点击 → 翻页
        self.toc_panel.pageRequested.connect(lambda index: self.pdf_view.goto_page(index))
        self.thumb_panel.pageRequested.connect(lambda index: self.pdf_view.goto_page(index))
        # 查找条信号
        self.find_bar.searchRequested.connect(self._queue_find)
        self.find_bar.nextRequested.connect(lambda: self._find_step(1))
        self.find_bar.prevRequested.connect(lambda: self._find_step(-1))
        self.find_bar.closed.connect(self._find_close)
        # 笔迹变化 → 标记未保存并触发自动暂存 debounce
        pdf_view.strokesChanged.connect(self._on_strokes_changed)
        # 笔记：页面上放图标 → 建 Note；点图标 → 展开卡片
        pdf_view.notePlacementRequested.connect(self._create_note)
        pdf_view.noteRequested.connect(self._open_note)
        # 图钉右上角 × → 确认后删除；拖动图钉改了锚点 → 同一条 dirty + 自动暂存路径
        pdf_view.noteDeleteRequested.connect(self._delete_note_requested)
        pdf_view.notesChanged.connect(self._on_notes_changed)
        pdf_view.noteContextRequested.connect(self._show_note_menu)
        pdf_view.selectionCopied.connect(
            lambda count: self._note(f"已复制 {count} 个字符"))
        pdf_view.objectCopied.connect(self._note)
        self.menubar.actionTriggered.connect(self._on_menu_action)
        self.menubar.toolChosen.connect(self._apply_tool)
        self.menubar.widthChosen.connect(self._apply_width)

    def _on_color_picked(self, color: str) -> None:
        """工具栏选色：按当前工具写进各自的颜色，两个视图同步。"""
        if self._active_view().tool == "highlighter":
            self.view.set_highlighter_color(color)
            self.pdf_view.set_highlighter_color(color)
            self.controller.settings.highlighter_color = color
        else:
            self.view.set_color(color)
            self.pdf_view.set_color(color)
            self.controller.settings.color = color
        self.refresh_status()

    def _on_view_changed(self) -> None:
        """视图状态变了（键盘或落笔）：工具栏高亮与状态栏一起跟上。"""
        # 草稿画布的键盘换档（B/H/Q/1-4）也同步给文档视图，切模式后不再各拿一套
        self.pdf_view.set_tool(self.view.tool)
        self.pdf_view.set_width(self.view.width)
        self.pdf_view.set_color(self.view.color)
        self.pdf_view.set_highlighter_color(self.view.highlighter_color)
        self.controller.settings.color = self.view.color
        self.controller.settings.highlighter_color = self.view.highlighter_color
        self.toolbox.reflect_color(self.view.color, "pen")
        self.toolbox.reflect_color(self.view.highlighter_color, "highlighter")
        self.toolbox.reflect_tool(self.view.tool)
        self.controller.settings.width = self.view.width   # 键盘换档也记进配置
        self.toolbox.reflect_width(self.view.width)
        self._reflect_bar(self.view.tool, self.view.width)
        self.refresh_status()
        self._refresh_empty_hint()

    def _on_pdf_changed(self) -> None:
        """文档视图状态变了：文档模式下工具栏/菜单/配置跟住它，状态栏总要刷新。"""
        if self.mode == "document":
            self.controller.settings.color = self.pdf_view.color
            self.controller.settings.highlighter_color = self.pdf_view.highlighter_color
            self.toolbox.reflect_color(self.pdf_view.color, "pen")
            self.toolbox.reflect_color(self.pdf_view.highlighter_color, "highlighter")
            self.toolbox.reflect_tool(self.pdf_view.tool)
            self.toolbox.reflect_width(self.pdf_view.width)
            self.controller.settings.width = self.pdf_view.width
            self._reflect_bar(self.pdf_view.tool, self.pdf_view.width)
        self.refresh_status()

    def _on_pdf_page_changed(self, _index: int) -> None:
        self.refresh_status()
        # 翻页就落盘：不等到关闭文档，强杀/崩溃也不会退回开头
        self._save_pdf_position_now()

    def _on_pdf_zoom_changed(self, _zoom: float) -> None:
        self.refresh_status()
        self._schedule_position_save()

    def _apply_tool(self, tool: str) -> None:
        """菜单选工具：两个视图同时生效，工具栏与菜单都点亮（set_* 不发信号）。"""
        if tool == "note":
            if self.mode != "document":
                return                           # 草稿画布没有 PDF 页可放图标
            self.pdf_view.set_tool("note")
            self.toolbox.reflect_tool("note")
            self.menubar.set_tool("note")
            return
        if tool == "select":
            self.view.set_tool("select")         # 草稿=手形平移；文档=平移/选字
            self.pdf_view.set_tool("select")
        elif tool == "text":
            if self.mode == "document":
                if getattr(self.pdf_view, "kind", "pdf") == "pdf":
                    self.pdf_view.set_tool("text")
                else:
                    return
            else:
                self.view.set_tool("text")       # 文字工具落草稿画布 / PDF 页面
        else:
            self.view.set_tool(tool)
            self.pdf_view.set_tool(tool)
        self.toolbox.reflect_tool(tool)
        self.menubar.set_tool(tool)

    def _apply_width(self, width: float) -> None:
        """菜单选粗细：两个视图同时生效；落在一档预设上就同步勾选。"""
        self.view.set_width(width)
        self.pdf_view.set_width(width)
        self.toolbox.reflect_width(width)
        self.controller.settings.width = float(width)
        self._reflect_bar(self.view.tool, float(width))

    def _reflect_bar(self, tool: str, width: float) -> None:
        """菜单栏的画笔/粗细勾选跟住视图；只 setChecked，不发信号。"""
        self.menubar.set_tool(tool)
        index = self._width_index(width)
        if index is not None:
            self.menubar.set_width_index(index)

    @staticmethod
    def _width_index(width: float) -> int | None:
        """线宽正好落在一档预设上才有档位；自定义粗细不强行勾选。"""
        return WIDTHS.index(width) if width in WIDTHS else None

    def _active_view(self):
        """当前模式的视图；撤销/重做与清屏都走它。"""
        return self.pdf_view if self.mode == "document" else self.view

    def _undo(self) -> None:
        view = self._active_view()
        hook = getattr(view, "commit_text_edit", None)
        if callable(hook):
            hook()
        view.history.pop_undo()

    def _redo(self) -> None:
        view = self._active_view()
        hook = getattr(view, "commit_text_edit", None)
        if callable(hook):
            hook()
        view.history.pop_redo()

    def _clear_active(self) -> None:
        """清屏：草稿清整块画布，文档只清当前页（两者都可撤销）。"""
        if self.mode == "document":
            hook = getattr(self.pdf_view, "commit_text_edit", None)
            if callable(hook):
                hook()
            self.pdf_view.clear_page()
        else:
            self.view.clear_all()

    # --- 笔记卡片生命周期 ---

    # --- 草稿插入截图/图片 ---

    def _choose_image(self) -> bool:
        """工具栏图片按钮：文档模式插到当前 PDF 页，草稿模式插到画布。"""
        if self.mode == "document":
            if getattr(self.pdf_view, "kind", "pdf") != "pdf":
                return False
            path, _filter = QFileDialog.getOpenFileName(
                self, "插入图片", str(self._open_pdf_start_dir()),
                "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)")
            if not path:
                return False
            if not self.pdf_view.insert_image_file(path):
                self._note("插入图片失败", error=True)
                return False
            self._apply_tool("select")      # 插完即可拖拽/缩放
            self._note(f"已把 {Path(path).name} 插入当前页")
            self.pdf_view.setFocus(Qt.OtherFocusReason)
            return True
        return self._choose_image_for_draft()

    def _clipboard_image(self) -> QImage | None:
        """剪贴板里的位图：优先 QClipboard.image，再退回 imageData/常见格式。"""
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                return image
            data = mime.imageData()
            if isinstance(data, QImage) and not data.isNull():
                return data
        if mime is not None:
            for fmt in ("image/png", "image/jpeg", "PNG", "JFIF"):
                if mime.hasFormat(fmt):
                    image = QImage.fromData(mime.data(fmt))
                    if not image.isNull():
                        return image
        return None

    def _clipboard_has_image(self) -> bool:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return False
        return mime.hasImage() or bool(local_image_paths(mime))

    def _paste_image_to_pdf(self) -> bool:
        """PDF 页 Ctrl+V：剪贴板位图或本地图片文件插到当前页。"""
        if self.mode != "document" or getattr(self.pdf_view, "kind", "pdf") != "pdf":
            return False
        if self.pdf_view.document is None:
            return False
        image = self._clipboard_image()
        if image is not None:
            if self.pdf_view.insert_image(image, "剪贴板图片"):
                self._apply_tool("select")      # 插完即可拖拽/缩放
                self._note("已插入剪贴板图片")
                self.pdf_view.setFocus(Qt.OtherFocusReason)
                return True
            self._note(getattr(self.pdf_view, "image_error", "") or "插入图片失败",
                       error=True)
            return False
        mime = QGuiApplication.clipboard().mimeData()
        for path in local_image_paths(mime):
            if self.pdf_view.insert_image_file(path):
                self._apply_tool("select")
                self._note(f"已插入 {path.name}")
                self.pdf_view.setFocus(Qt.OtherFocusReason)
                return True
            self._note(getattr(self.pdf_view, "image_error", "") or "插入图片失败",
                       error=True)
            return False
        self._note("剪贴板里没有图片；可先截图或复制图片文件再试", error=True)
        return False

    def _choose_image_for_draft(self) -> bool:
        if self.mode != "draft":
            return False
        path, _filter = QFileDialog.getOpenFileName(
            self, "插入图片", str(self._open_pdf_start_dir()),
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)")
        if not path:
            return False
        return self._insert_image_to_draft(QImage(path), Path(path).name)

    def _paste_image_to_draft(self) -> bool:
        """Ctrl+V：优先剪贴板位图（截图），其次剪贴板里的本地图片文件。"""
        if self.mode != "draft":
            return False
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                return self._insert_image_to_draft(image, "剪贴板图片")
        for path in local_image_paths(mime):
            image = QImage(str(path))
            if not image.isNull():
                return self._insert_image_to_draft(image, path.name)
        return False

    def _set_fullscreen_annotate(self, enabled: bool) -> bool:
        """工具栏按钮的开关：开=批注模式，关=收起整层。"""
        controller = self._annotate
        if controller is None:
            self.toolbox.set_annotate_active(False)
            if not self.primary_instance:
                self._note("全屏批注只在第一个启动的实例可用", error=True)
            else:
                self._note("全屏批注未启用（只在主程序启动时安装）", error=True)
            return False
        if enabled:
            controller.enter_passthrough()   # 打开默认穿透，不先拦鼠标
        else:
            controller.hide()
        return True

    def _sync_annotate_button(self, mode: str) -> None:
        """批注层被热键/浮窗按钮改模式时，工具栏与菜单同步开关。"""
        active = mode != "hidden"
        self.toolbox.set_annotate_active(active)
        self.menubar.fullscreen_annotate_action.setChecked(active)

    def _toggle_fullscreen_annotate(self) -> bool:
        """菜单/旧入口：按当前模式开或关（不再强制走穿透）。"""
        controller = self._annotate
        if controller is None:
            if not self.primary_instance:
                self._note("全屏批注只在第一个启动的实例可用", error=True)
            else:
                self._note("全屏批注未启用（只在主程序启动时安装）", error=True)
            return False
        active = controller.overlay.mode != "hidden"
        return self._set_fullscreen_annotate(not active)

    def _on_capture_mode(self, mode: str) -> None:
        """工具栏相机下拉：窗口/全屏/上一区域/取色/历史。"""
        if mode == "history":
            controller = getattr(self, "_screenshot", None)
            if controller is None:
                self._note("截图历史不可用", error=True)
                return
            controller.show_history()
            return
        self._start_screenshot(mode)

    def _start_screenshot(self, mode: str = "region") -> bool:
        """工具栏/全局热键：抓屏后进截图编辑器。

        有截图控制器（正常启动）就走区域/窗口/全屏/上一区域；没有时保留旧路径
        （测试或极简启动），退化为批注层框选或整屏抓图。
        """
        controller = getattr(self, "_screenshot", None)
        if controller is not None:
            try:
                return bool(controller.start(mode))
            except Exception as exc:
                self._note(f"截图失败：{exc}", error=True)
                return False
        annotate = self._annotate
        if annotate is not None and hasattr(annotate, "start_capture"):
            try:
                # 工具栏按钮：先把自己藏起来再抓屏，否则截到的是墨页自己
                self._screenshot_hidden = self.isVisible()
                if self._screenshot_hidden:
                    self.hide()
                annotate.start_capture()
                return True
            except Exception as exc:
                if self._screenshot_hidden:
                    self._screenshot_hidden = False
                    self.show()
                self._note(f"截图失败：{exc}", error=True)
                return False
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            self._note("没有可用屏幕，无法截图", error=True)
            return False
        image = screen.grabWindow(0).toImage()
        if image.isNull():
            self._note("截图失败", error=True)
            return False
        self._on_screenshot_captured(image)
        return True

    def _restore_after_screenshot(self) -> None:
        if not self._screenshot_hidden:
            return
        self._screenshot_hidden = False
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_screenshot_captured(self, image: QImage) -> None:
        """截图先复制到剪贴板，再进编辑器继续标注；不自动插入笔记。"""
        if image is None or image.isNull():
            self._note("截图失败", error=True)
            return
        QGuiApplication.clipboard().setImage(image)
        self._note(f"截图已复制到剪贴板（{image.width()}×{image.height()}）；"
                   f"可继续标注")
        controller = getattr(self, "_screenshot", None)
        if controller is not None:
            try:
                controller.open_editor(image, "region")
            except Exception as exc:
                self._note(f"打开截图编辑器失败：{exc}", error=True)

    def _insert_image_to_draft(self, image: QImage, name: str = "图片") -> bool:
        if image.isNull():
            self._note("图片无法读取", error=True)
            return False
        if not self.view.insert_image(image):
            self._note("插入图片失败", error=True)
            return False
        self._note(f"已插入 {name}；可直接在上面写，清屏只清笔迹")
        self.view.setFocus(Qt.OtherFocusReason)
        return True

    def _create_note(self, page: int, x: float, y: float) -> None:
        """在页面落一个笔记图标（N 工具点击页面）：建 Note、加图钉、标记待保存。"""
        document = self.pdf_view.document
        if document is None or not 0 <= page < len(document.pages):
            return
        page_obj = document.pages[page]
        paper = getattr(self.controller.settings, "note_paper", "dark")
        note = Note(new_note_id({item.id for item in page_obj.notes}),
                    *clamp_note_anchor(x, y, page_obj.width, page_obj.height),
                    paper=paper if paper in ("dark", "light") else "dark")
        self.pdf_view.add_note(page, note)
        self._on_notes_changed()
        # 放完笔记立刻回到鼠标工具：继续点页面不会又落一个图钉
        self._apply_tool("select")
        self._note(f"已插入笔记 {len(page_obj.notes)}，点图标展开")

    def _find_note(self, page: int, note_id: str) -> Note | None:
        """按 (页号, id) 找 Note；页号失效或已删除返回 None，不抛。"""
        document = self.pdf_view.document
        if document is None or not 0 <= page < len(document.pages):
            return None
        return next((item for item in document.pages[page].notes
                     if item.id == note_id), None)

    def _note_display(self, page: int, note: Note) -> str:
        """卡片标题/图钉标签用的名字：自定义名优先，空名回退「笔记 N」。"""
        try:
            number = self.pdf_view.document.pages[page].notes.index(note) + 1
        except (AttributeError, ValueError, IndexError):
            number = 1
        return note_display_name(note, number)

    def _open_note(self, page: int, note_id: str) -> None:
        """点图钉：直接开一个能自由拖动的独立小窗口（像子程序，不再有浮层卡片）。

        已开窗 → 聚焦；已在主窗分屏 → 聚焦；否则走 ``_toggle_note_window``
        （内部会先收起分屏，避免同一张笔记两处同时编辑）。
        """
        window = self._note_windows.get((page, note_id))
        if window is not None:
            window.showNormal()
            window.raise_()
            window.activateWindow()
            return
        if self._note_split_key == (page, note_id) and self._note_split_card is not None:
            self._note_split_card.view.setFocus(Qt.OtherFocusReason)
            return
        self._toggle_note_window(page, note_id)

    # --- 独立笔记窗口（放到主窗外自由缩放）---

    def _toggle_note_window(self, page: int, note_id: str) -> None:
        """在独立窗口打开/收回这张笔记；与遮罩卡片互斥，避免两份同时编辑。"""
        key = (page, note_id)
        window = self._note_windows.get(key)
        if window is not None:
            window.close()
            return
        if self._note_split_key == key:
            self._close_note_split()
        note = self._find_note(page, note_id)
        if note is None:
            return
        owner = self._active_session
        window = NoteWindow(note, self.controller.settings,
                            display_name=self._note_display(page, note),
                            pdf_path=self.pdf_view.pdf_path)
        window.contentChanged.connect(
            lambda s=owner: self._on_session_notes_changed(s))
        window.splitRequested.connect(
            lambda p=page, i=note_id, w=window, s=owner:
                self._window_to_split(s, p, i, w))
        window.deleteRequested.connect(
            lambda p=page, i=note_id, s=owner:
                self._note_action_in_session(s, lambda: self._delete_note(p, i)))
        window.renameRequested.connect(
            lambda p=page, i=note_id, s=owner:
                self._note_action_in_session(s, lambda: self._rename_note(p, i)))
        window.closed.connect(
            lambda p=page, i=note_id, w=window, s=owner:
                self._forget_note_window(p, i, w, s))
        self._note_windows[key] = window
        saved = owner.note_window_geometry.get(note_id) if owner is not None else None
        if saved is not None:
            window.setGeometry(saved)
        else:
            self._place_note_window(window)
        window.show()
        window.raise_()
        window.activateWindow()
        window.card.view.setFocus(Qt.OtherFocusReason)   # 打开即可 Ctrl+V 粘图

    def _place_note_window(self, window: NoteWindow) -> None:
        """首次打开：摆到主窗右侧偏移 24px，超出屏幕就钳回来。"""
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        area = screen.availableGeometry() if screen is not None else None
        point = self.frameGeometry().topRight() + QPoint(24, 40)
        if area is not None:
            point.setX(min(max(area.left(), point.x()),
                           area.right() - window.width()))
            point.setY(min(max(area.top(), point.y()),
                           area.bottom() - window.height()))
        window.move(point)

    def _forget_note_window(self, page: int, note_id: str,
                            window: NoteWindow | None = None,
                            session: DocumentSession | None = None) -> None:
        """独立窗口关闭：记住几何，只摘掉自己，别误伤同 key 重开的新窗口。

        不依赖 `self.sender()`：lambda 连接在 PySide 里拿不到可靠的 sender。
        """
        key = (page, note_id)
        if window is not None and session is not None:
            try:
                session.note_window_geometry[note_id] = window.geometry()
            except RuntimeError:
                pass
        if window is None or self._note_windows.get(key) is window:
            self._note_windows.pop(key, None)

    def _close_note_windows(self) -> None:
        """切文档/回草稿/退出前收起所有独立笔记窗口（内容已在共享模型里）。"""
        for window in list(self._note_windows.values()):
            window.close()
        self._note_windows.clear()

    # --- 主窗内分屏（PDF 左 / 笔记右，不改 PDF 缩放）---

    def _open_note_split(self, page: int, note_id: str) -> None:
        """把笔记放进当前会话的 QSplitter 右栏；同一时间只分屏一张。"""
        key = (page, note_id)
        session = self._active_session
        if self._note_split_key == key:
            return
        note = self._find_note(page, note_id)
        if note is None or session is None or session.view.pdf_path is None:
            return
        pane = session.pane
        if not hasattr(pane, "show_note"):
            return
        self._close_note_split()
        # _close_note_split 会把 globals 清空，重新取一次活动会话（关闭流程不换标签）
        session = self._active_session
        if session is None or session.pane is not pane:
            return
        window = self._note_windows.pop(key, None)
        if window is not None:
            window.close()

        card = NoteCard(note, self.controller.settings,
                        display_name=self._note_display(page, note),
                        pdf_path=session.view.pdf_path)
        card.set_resize_enabled(False)
        if hasattr(card, "set_split_mode"):
            card.set_split_mode(True)        # 340px 右栏：工具行横向滚动
        owner = session
        card.contentChanged.connect(
            lambda s=owner: self._on_session_notes_changed(s))
        card.closeRequested.connect(self._close_note_split)
        card.splitRequested.connect(
            lambda p=page, i=note_id: self._split_back_to_overlay(p, i))
        card.windowRequested.connect(
            lambda p=page, i=note_id: self._move_note_to_window(p, i))
        card.renameRequested.connect(
            lambda p=page, i=note_id: self._rename_note(p, i))
        card.deleteRequested.connect(
            lambda p=page, i=note_id: self._delete_note(p, i))

        # 先冻结 PDF 缩放，再分栏：视口变窄不会触发 fit_width
        position = session.view.current_position()
        self._split_prev_auto_fit = session.view.auto_fit
        session.view.set_zoom(session.view.zoom)
        pane.set_note_title(session.display_name)
        self._note_split_fit_slot = lambda s=session: s.view.fit_width()
        self._note_split_ratio_slot = (
            lambda ratio, s=session: setattr(s, "note_split_ratio", float(ratio)))
        pane.fitRequested.connect(self._note_split_fit_slot)
        pane.ratioChanged.connect(self._note_split_ratio_slot)
        pane.show_note(card, session.note_split_ratio)
        # 分屏期间保持冻结缩放：auto_fit 不能再打开
        self._restore_split_position(
            session.view, dict(position, auto_fit=False) if position else None)

        self._note_split_card = card
        self._note_split_pane = pane
        self._note_split_key = key
        session.note_split_card = card
        session.note_split_pane = pane
        session.note_split_key = key
        session.split_prev_auto_fit = self._split_prev_auto_fit
        card.view.setFocus(Qt.OtherFocusReason)

    def _close_note_split(self) -> None:
        """把笔记从右栏摘下来，并按进入前的状态恢复 PDF 适应页宽。"""
        if self._scratch_dock is not None:
            self._undock_scratch(show_window=False)
        pane, card = self._note_split_pane, self._note_split_card
        prev_auto_fit = self._split_prev_auto_fit
        session = self._active_session
        view = getattr(pane, "view", None) or self.pdf_view
        position = view.current_position() if view is not None else None
        self._note_split_pane = None
        self._note_split_card = None
        self._note_split_key = None
        self._split_prev_auto_fit = None
        if pane is not None and hasattr(pane, "hide_note"):
            for signal, slot in ((getattr(pane, "fitRequested", None),
                                  getattr(self, "_note_split_fit_slot", None)),
                                 (getattr(pane, "ratioChanged", None),
                                  getattr(self, "_note_split_ratio_slot", None))):
                if signal is None or slot is None:
                    continue
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            returned = pane.hide_note()
            if returned is not None:
                card = returned
        self._note_split_fit_slot = None
        self._note_split_ratio_slot = None
        if session is not None:
            session.note_split_card = None
            session.note_split_pane = None
            session.note_split_key = None
            session.split_prev_auto_fit = None
        if card is not None:
            card.deleteLater()
        if prev_auto_fit:
            view.fit_width()
        # 分栏/收栏各自会改视口宽度，滚动锚点要按页内比例还原，不能跳页中段
        self._restore_split_position(
            view, dict(position, auto_fit=bool(prev_auto_fit))
            if position else None)
        view.setFocus(Qt.OtherFocusReason)

    @staticmethod
    def _restore_split_position(view, position: dict | None) -> None:
        if view is None or not position:
            return
        def apply() -> None:
            try:
                view.restore_position(position["page"], position["offset"],
                                      position["zoom"], position["auto_fit"])
            except RuntimeError:
                pass
        QTimer.singleShot(0, apply)

    def _split_back_to_overlay(self, page: int, note_id: str) -> None:
        """分屏卡上的分屏按钮：收起分屏，回到浮层卡片。"""
        self._close_note_split()
        self._open_note(page, note_id)

    def _move_note_to_split(self, page: int, note_id: str,
                            window: NoteWindow | None = None) -> None:
        """从独立窗口收回分屏：先摘掉窗口字典再关，避免 closed 再动一次。"""
        key = (page, note_id)
        if window is not None and self._note_windows.get(key) is window:
            self._note_windows.pop(key, None)
            window.close()
        self._open_note_split(page, note_id)

    def _move_note_to_window(self, page: int, note_id: str) -> None:
        """从浮层/分屏切到独立窗口。"""
        if self._note_split_key == (page, note_id):
            self._close_note_split()
        self._toggle_note_window(page, note_id)

    def _rename_note(self, page: int, note_id: str) -> None:
        """重命名笔记：标题与图钉下方标签一起刷新，并走 dirty/自动暂存。"""
        note = self._find_note(page, note_id)
        if note is None:
            return
        current = self._note_display(page, note)
        text, ok = QInputDialog.getText(self, "重命名笔记", "笔记名称：",
                                        text=current)
        if not ok:
            return
        note.name = (text or "").strip()[:60]
        display = self._note_display(page, note)
        if (self._note_split_key == (page, note_id)
                and self._note_split_card is not None):
            self._note_split_card.set_display_name(display)
        window = self._note_windows.get((page, note_id))
        if window is not None:
            window.set_display_name(display)
        self.pdf_view.rebuild_note_pins(page)
        self._on_notes_changed()
        self._note(f"笔记已重命名为 {display}")

    def _apply_highlighter_color(self, color: str) -> None:
        """荧光笔颜色同时落到两个主视图和所有已打开的笔记画布。"""
        self.view.set_highlighter_color(color)
        self.pdf_view.set_highlighter_color(color)
        if self._note_split_card is not None:
            self._note_split_card.view.set_highlighter_color(color)
        for window in self._note_windows.values():
            window.card.view.set_highlighter_color(color)

    def _apply_pressure_setting(self, enabled: bool) -> None:
        """压感开关同时落到主视图、PDF 视图和所有已打开的笔记画布。"""
        enabled = bool(enabled)
        for view in (self.view, self.pdf_view):
            view.set_pressure_enabled(enabled)
        if self._note_split_card is not None:
            self._note_split_card.view.set_pressure_enabled(enabled)
        for window in self._note_windows.values():
            window.card.view.set_pressure_enabled(enabled)

    def _toggle_note_paper(self, page: int, note_id: str) -> None:
        """右键菜单换纸色：模型、已打开的卡片/窗口同步。"""
        note = self._find_note(page, note_id)
        if note is None:
            return
        note.paper = "light" if note.paper == "dark" else "dark"
        if (self._note_split_key == (page, note_id)
                and self._note_split_card is not None):
            self._note_split_card.set_paper(note.paper)
        window = self._note_windows.get((page, note_id))
        if window is not None:
            window.card.set_paper(note.paper)
        self._on_notes_changed()

    def _show_note_menu(self, page: int, note_id: str, global_pos: QPoint) -> None:
        """右键图钉：打开 / 独立窗口 / 重命名 / 纸张 / 删除。"""
        note = self._find_note(page, note_id)
        if note is None:
            return
        menu = QMenu(self)
        act_open = menu.addAction("打开笔记")
        split = self._note_split_key == (page, note_id)
        act_split = menu.addAction("关闭分屏" if split else "分屏打开")
        detached = (page, note_id) in self._note_windows
        act_window = menu.addAction("收回独立窗口" if detached else "在独立窗口打开")
        act_rename = menu.addAction("重命名…")
        act_paper = menu.addAction("切换纸张深浅")
        menu.addSeparator()
        act_delete = menu.addAction("删除笔记…")
        chosen = menu.exec(global_pos)
        if chosen is act_open:
            self._open_note(page, note_id)
        elif chosen is act_split:
            if split:
                self._close_note_split()
            else:
                self._open_note_split(page, note_id)
        elif chosen is act_window:
            self._toggle_note_window(page, note_id)
        elif chosen is act_rename:
            self._rename_note(page, note_id)
        elif chosen is act_paper:
            self._toggle_note_paper(page, note_id)
        elif chosen is act_delete:
            if self._confirm_note_delete(note_id):
                self._delete_note(page, note_id)
                self._note(f"已删除笔记 {note_id}")

    def _on_notes_changed(self) -> None:
        """笔记内容/集合/锚点变了：落到当前活动会话。"""
        if self._active_session is not None:
            self._on_session_notes_changed(self._active_session)

    def _on_session_notes_changed(self, session: DocumentSession | None) -> None:
        """任意会话的笔记变化：只标记/暂存它自己，不污染当前标签。"""
        if session is None:
            return
        session.dirty = True
        try:
            session.autosave.notify_changed()
        except RuntimeError:
            pass
        if session is self._active_session:
            self._dirty = True
            self._refresh_title()
            self._refresh_save_chip()
        else:
            entry = self._entry_for_session(session)
            if entry is not None:
                self.tabs.update_title(entry)

    def _note_action_in_session(self, session: DocumentSession | None, action) -> None:
        """独立笔记窗口的操作属于它的会话：先切回去再执行删除/重命名等。"""
        if session is None:
            action()
            return
        self._activate_session(session, force=True)
        action()

    def _window_to_split(self, session: DocumentSession | None, page: int,
                         note_id: str, window: NoteWindow) -> None:
        if session is not None:
            self._activate_session(session, force=True)
        self._move_note_to_split(page, note_id, window)

    def _delete_note_requested(self, page: int, note_id: str) -> None:
        """点图钉右上角 ×：笔记不可撤销，先确认再删（与卡片里的删除同一个问法）。"""
        if not self._confirm_note_delete(note_id):
            return
        self._delete_note(page, note_id)
        self._note(f"已删除笔记 {note_id}")

    def _confirm_note_delete(self, note_id: str) -> bool:
        """删除确认框：默认按钮是不删。"""
        answer = QMessageBox.question(
            self, "删除笔记", f"确定要删除笔记 {note_id} 吗？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _delete_note(self, page: int, note_id: str) -> None:
        """删除笔记：确认框已在卡片/图钉入口弹过，这里只改模型并收起卡片。"""
        document = self.pdf_view.document
        target = None
        if document is not None and 0 <= page < len(document.pages):
            target = next((item for item in document.pages[page].notes
                           if item.id == note_id), None)
        window = self._note_windows.pop((page, note_id), None)
        if window is not None:
            window.close()
        if self._note_split_key == (page, note_id):
            self._close_note_split()
        self.pdf_view.remove_note(page, note_id)
        self._on_notes_changed()

    def _refresh_title(self) -> None:
        """标题恒为 APP_NAME（2026-10-24 用户要求：当前文档看标签栏高亮，
        不再把文件名拼进窗口/任务栏标题）。脏标记用 ● 前缀保留。幂等，可随时调。"""
        text = APP_NAME
        if getattr(self, "_dirty", False):
            text = f"● {text}"
        if not self.primary_instance:
            text = f"{text}（副实例）"      # 多开时任务栏/标题可区分
        if self._active_session is not None:
            self._active_session.dirty = self._dirty
            self._active_session.saved_at = self._saved_at
        if self._active_entry is not None and not self._active_entry.draft:
            self.tabs.update_title(self._active_entry)
        self.titlebar.set_title(text)
        self.setWindowTitle(text)

    # --- 草稿空态提示 ---

    def _refresh_empty_hint(self) -> None:
        """草稿没有任何笔迹、且不在文档模式时才显示提示；其余一律藏起来。"""
        if not hasattr(self, "empty_hint"):
            return
        show = self.mode == "draft" and not self.controller.document.strokes
        self.empty_hint.setVisible(show)
        if show:
            self._position_empty_hint()

    def _position_empty_hint(self) -> None:
        """居中到草稿视口（QLabel 是 viewport 的子控件，坐标即视口坐标）。"""
        if not hasattr(self, "empty_hint"):
            return
        viewport = self.view.viewport()
        label = self.empty_hint
        label.adjustSize()
        label.move(max(0, (viewport.width() - label.width()) // 2),
                   max(0, (viewport.height() - label.height()) // 2))

    # --- 保存状态 ---

    def _refresh_save_chip(self) -> None:
        """状态栏保存角标：有改动「● 未保存」，提交过则给时刻；草稿模式不显示。"""
        if not hasattr(self, "save_label"):
            return
        dirty = (self.mode == "document" and self.pdf_view.document is not None
                 and getattr(self, "_dirty", False))
        saved_at = getattr(self, "_saved_at", None)
        if self.mode != "document" or self.pdf_view.document is None:
            text = ""
        elif dirty:
            text = "● 未保存"
        elif saved_at:
            text = f"已保存 {saved_at}"
        else:
            text = "已保存"
        self.save_label.setText(text)
        self.save_label.setVisible(bool(text))
        self.save_label.setStyleSheet(f"color: {theme.WARNING};" if dirty else "")
        self.save_label.setToolTip("当前 PDF 有未提交的批注（Ctrl+S 保存）" if dirty else "")

    # --- 应用级按键兜底 ---

    def _focused_in_window(self) -> QWidget | None:
        """焦点控件；不在本窗口（草稿纸窗、模态框）时返回 None，代表"别接管"。"""
        if QApplication.activeModalWidget() is not None:
            return None
        focus = QApplication.focusWidget()
        if focus is None or focus.window() is not self:
            return None
        return focus

    def eventFilter(self, obj, ev) -> bool:
        """焦点不在视图里（侧栏/滚动条/按钮）时也保留 B/Q/1-4/0、撤销/重做、
        缩放与清屏键；另外接管「最近打开」菜单的右键。
        """
        menubar = getattr(self, "menubar", None)
        if menubar is not None and obj is menubar.recent_menu \
                and ev.type() == QEvent.ContextMenu:
            self._on_recent_context_menu(ev.pos())
            return True
        if ev.type() != QEvent.KeyPress:
            view = getattr(self, "view", None)
            if obj is view and ev.type() == QEvent.Resize:
                self._position_empty_hint()          # 窗口缩放/工具栏换行：提示重新居中
            elif (view is not None and shiboken6.isValid(view)
                  and obj is view.viewport() and ev.type() == QEvent.Resize):
                self._position_empty_hint()
            return super().eventFilter(obj, ev)
        focus = self._focused_in_window()
        key = ev.key()
        modifiers = ev.modifiers()
        ctrl_mod = bool(modifiers & Qt.ControlModifier) and not (
            modifiers & (Qt.AltModifier | Qt.MetaModifier))
        # 没有子控件焦点（刚启动/截图遮罩关闭回主窗）时，Ctrl+V 仍要能粘图片；
        # 文本输入框保留系统粘贴，不抢。
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            return False
        if focus is None:
            if ctrl_mod and key == Qt.Key_V and self.isActiveWindow():
                if self.mode == "draft" and self._paste_image_to_draft():
                    ev.accept()
                    return True
                if (self.mode == "document"
                        and getattr(self.pdf_view, "kind", "pdf") == "pdf"
                        and self._paste_image_to_pdf()):
                    ev.accept()
                    return True
            return False
        # 停靠的草稿纸有自己的工具栏/撤销栈：焦点在它里面时按键归它自己，
        # 不能让主窗把 B/Q/数字/Ctrl+Z 作用到左侧文档上
        dock = getattr(self, "_scratch_dock", None)
        if dock is not None and (focus is dock or dock.isAncestorOf(focus)):
            return False
        # 焦点在笔记卡片内：B/Q/1–9/Ctrl+Z/清屏键全部归 NoteView 自己处理，
        # 不能落到 PDF 视图/主窗历史（卡片工具栏和主工具栏各管各的）
        if (self._note_split_card is not None
                and self._note_split_card.isAncestorOf(focus)):
            return False
        # 草稿文字工具正在输入：B/H/Q/T/数字都是文字，不能当工具快捷键吞掉
        if (self.mode == "draft"
                and getattr(self.view, "_text_editing", None) is not None):
            return False
        # PDF 页上的文字编辑：按键（含 Ctrl+Z/Ctrl+V）归 QGraphicsTextItem；
        # 例外是剪贴板里是图片——先提交文字再插图，符合“Ctrl+V 贴图”的直觉
        if (self.mode == "document"
                and getattr(self.pdf_view, "is_text_editing", lambda: False)()):
            if ctrl_mod and key == Qt.Key_V and self._clipboard_has_image():
                self.pdf_view.commit_text_edit()
                if self._paste_image_to_pdf():
                    ev.accept()
                    return True
            return False
        digits = tuple(Qt.Key_1 + i for i in range(len(PALETTE)))
        plain = not (modifiers & (Qt.ControlModifier | Qt.AltModifier
                                  | Qt.MetaModifier))
        text_reader = (self.mode == "document"
                       and getattr(self.pdf_view, "kind", "pdf") != "pdf")
        if ctrl_mod and key == Qt.Key_V and self.mode == "draft":
            if self._paste_image_to_draft():
                ev.accept()
                return True
            return False
        if (ctrl_mod and key == Qt.Key_V and self.mode == "document"
                and not text_reader):
            if self._paste_image_to_pdf():
                ev.accept()
                return True
            return False
        if not text_reader and plain and (key in digits or key in (
                Qt.Key_B, Qt.Key_H, Qt.Key_Q, Qt.Key_V, Qt.Key_N,
                Qt.Key_T, Qt.Key_0)):
            view = self._active_view()
            if key == Qt.Key_B:
                view.set_tool("pen")
            elif key == Qt.Key_H:
                self._apply_tool("highlighter")   # 两个视图同时切，颜色默认规则也在这里
            elif key == Qt.Key_Q:
                view.set_tool("eraser")
            elif key == Qt.Key_V:
                view.set_tool("select")     # 草稿=手形平移画布；文档=平移/选字
            elif key == Qt.Key_N:
                if self.mode != "document":
                    return False            # 草稿画布没有 PDF 页可放笔记
                self._apply_tool("note")
            elif key == Qt.Key_T:
                if self.mode == "draft":
                    self._apply_tool("text")      # 草稿/笔记画布文字
                elif self.mode == "document" and getattr(
                        self.pdf_view, "kind", "pdf") == "pdf":
                    self._apply_tool("text")      # PDF 页面文字
            elif key in digits:
                view.set_color(PALETTE[key - Qt.Key_1])   # 1–9 = 色板第 1–9 色
            else:                                   # Qt.Key_0：100%
                if self.mode == "document":
                    self.pdf_view.set_zoom(1.0)
                else:
                    self.view.reset_zoom()
            ev.accept()
            return True
        # Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z / 缩放：撤销重做只作用于当前模式的视图，
        # 草稿与文档不再各挂一套视图级 QShortcut（同一个组合只能有一条触发路径）。
        # Ctrl+Shift+= 在美式键盘上等价于 Ctrl++，Shift 不参与判定。
        ctrl = bool(modifiers & Qt.ControlModifier) and not (
            modifiers & (Qt.AltModifier | Qt.MetaModifier))
        if ctrl and key == Qt.Key_C:
            if text_reader:
                return False
            if self.mode != "document":
                return False
            if self.pdf_view.copy_selection():     # 没选中就交还给默认处理
                ev.accept()
                return True
            return False
        if ctrl and key in (Qt.Key_Z, Qt.Key_Y, Qt.Key_Equal, Qt.Key_Plus, Qt.Key_Minus):
            if key in (Qt.Key_Z, Qt.Key_Y) and text_reader:
                return False            # 只读阅读器没有画布历史（编辑器自己处理撤销）
            if key == Qt.Key_Z:
                if modifiers & Qt.ShiftModifier:
                    self._redo()
                else:
                    self._undo()
            elif key == Qt.Key_Y:
                self._redo()
            elif self.mode != "document":
                return False            # 草稿模式没有 PDF 缩放：别刷"文档模式"提示
            elif key == Qt.Key_Minus:
                self._zoom_by(1.0 / self.ZOOM_STEP)
            else:
                self._zoom_by(self.ZOOM_STEP)
            ev.accept()
            return True
        # 清屏键允许带修饰键（配置可能是 F9 / Ctrl+E 之类），按序列文本比较
        sequence = QKeySequence(ev.keyCombination()).toString()
        if (not text_reader and sequence
                and sequence == QKeySequence(self.view.clear_key).toString()):
            self._clear_active()
            ev.accept()
            return True
        return False

    def _detach_menu_shortcuts(self) -> None:
        """菜单条目上的组合键只作展示：真正的绑定由窗口级 QShortcut 统一注册。

        QMenuBar 会把条目的快捷键注册成窗口级 WindowShortcut；同一个组合再挂一份
        QShortcut 就成了歧义，Qt 会把两边一起停用（Ctrl+O/PgDown 实测都不触发）。
        """
        for action in self.menubar.findChildren(QAction):
            if not action.shortcut().isEmpty():
                action.setShortcutContext(Qt.WidgetShortcut)

    def _install_shortcuts(self) -> None:
        """窗口级快捷键。

        菜单栏放在布局里而不是 setMenuBar，快捷键统一在这里注册，
        否则同一个组合会被菜单条目和 QShortcut 各接一次。
        document_only=True 的条目只在文档模式启用（_sync_document_shortcuts）。

        撤销/重做（Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z）、缩放（Ctrl+= / Ctrl++ /
        Ctrl+-）与清屏键不在这里注册：它们统一由应用级 eventFilter 路由到当前
        视图，草稿与文档共用一条路径（v2.7 修重复绑定导致的互相停用）。
        """
        self._shortcuts: list[QShortcut] = []
        self._document_shortcuts: list[QShortcut] = []
        specs = (
            (self, "Ctrl+O", self._open_pdf, False),
            (self, "Ctrl+W", self._close_current_entry, False),
            (self, "Ctrl+E", self._edit_word_document, True),
            (self, "Ctrl+Shift+C", self._open_convert_center, False),
            (self, "Ctrl+S", self._save_annotations, True),
            (self, "Ctrl+Shift+E", self._export_pdf, True),
            (self, "PgUp", lambda: self._step_page(-1), True),
            (self, "PgDown", lambda: self._step_page(1), True),   # Qt 的规范名是 PgDown
            # Home/End 挂在文档视图上：查找框里按 Home/End 必须是移动光标，
            # 窗口级 QShortcut 会把这两个键从 QLineEdit 抢走。
            (self.pdf_view, "Home", self._goto_first_page, True),
            (self.pdf_view, "End", self._goto_last_page, True),
            (self, "Ctrl+G", self._goto_page, True),
            (self, "Ctrl+,", self._open_settings, False),
            (self, "Ctrl+F", self._show_find, True),   # 仅文档模式：唤起查找条
            (self, "F3", lambda: self._find_shortcut(1), True),
            (self, "Shift+F3", lambda: self._find_shortcut(-1), True),
            (self, "Ctrl+0", lambda: self._set_pdf_zoom(1.0), True),
            (self, "Ctrl+9", self._fit_page_width, True),
            (self, "F11", self._toggle_fullscreen, False),
            (self, "Ctrl+Tab", self._next_tab, False),
            (self, "Ctrl+Shift+Tab", self._prev_tab, False),
        )
        if not has_converter():
            specs = tuple(item for item in specs if item[1] != "Ctrl+Shift+C")
        for parent, sequence, slot, document_only in specs:
            shortcut = QShortcut(QKeySequence(sequence), parent)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            if sequence == "Ctrl+E":
                self._edit_word_shortcut = shortcut
            # 留引用：QShortcut 被回收就失效
            (self._document_shortcuts if document_only else self._shortcuts).append(shortcut)
        # Esc 只在全屏时启用：平时留给查找框/对话框，别把 Esc 从文本输入里抢走
        self._esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._esc_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self._leave_fullscreen)
        self._esc_shortcut.setEnabled(False)
        self._shortcuts.append(self._esc_shortcut)
        self._sync_document_shortcuts()

    def _sync_document_shortcuts(self) -> None:
        """Ctrl+S / Ctrl+G / 翻页 / 缩放 / 查找 等只在文档模式可用。"""
        enabled = self.mode == "document"
        for shortcut in self._document_shortcuts:
            shortcut.setEnabled(enabled)
        if self._edit_word_shortcut is not None:
            self._edit_word_shortcut.setEnabled(enabled and self._word_editable)

    # --- 模式切换 / 菜单分发 ---

    # --- 多文档会话 / 标签页 ---

    def _session_for_path(self, path: Path) -> DocumentSession | None:
        try:
            target = os.path.normcase(str(Path(path).resolve()))
        except OSError:
            target = os.path.normcase(str(Path(path).absolute()))
        for entry in self.tabs.entries:
            for session in entry.sessions:
                if getattr(session, "path", None) is None:
                    continue            # 笔记标签没有文件路径
                try:
                    current = os.path.normcase(str(session.path.resolve()))
                except OSError:
                    current = os.path.normcase(str(session.path.absolute()))
                if current == target:
                    return session
        return None

    def _entry_for_session(self, session: DocumentSession) -> TabEntry | None:
        for entry in self.tabs.entries:
            if any(item is session for item in entry.sessions):
                return entry
        return None

    def _focus_existing_session_elsewhere(self, path: Path) -> bool:
        """同一进程的另一个窗口已开着这份文档：切过去，不再开第二份。

        同进程多窗口共享进程级文档锁；没有这一步的话两边会各持一个会话，
        批注仍会互相覆盖。跨进程由 DocumentLock 挡。
        """
        registry = self.window_registry
        if registry is None:
            return False
        for window in getattr(registry, "windows", []):
            if window is self:
                continue
            try:
                session = window._session_for_path(path)
            except (AttributeError, RuntimeError):
                continue
            if session is None:
                continue
            entry = window._entry_for_session(session)
            window.restore_main()
            if entry is not None:
                window.tabs.set_current_entry(entry)
                window._activate_session(session, force=True)
            return True
        return False

    def _create_session(self, path: Path, doc_lock=None) -> DocumentSession:
        view = create_viewer(path, self.controller.settings)
        pane = make_session_pane(view)
        saver = PdfAutoSaver(self)
        session = DocumentSession(Path(path), view, saver, pane)
        session.doc_lock = doc_lock
        saver.saved.connect(lambda s=session: self._on_session_autosaved(s))
        saver.failed.connect(lambda msg, s=session: self._on_session_autosave_failed(s, msg))
        saver.configure(self.controller.settings.pdf_autosave,
                        lambda s=session: self._auto_save_cache_for(s))
        return session

    def _destroy_session(self, session: DocumentSession) -> None:
        if self._is_note_session(session):
            self._connected_note_views.discard(id(session.view))
            session.pane.setParent(None)
            session.pane.deleteLater()
            return
        session.close_view()
        session.autosave.deleteLater()
        lock = getattr(session, "doc_lock", None)
        if lock is not None:
            try:
                lock.release()
            finally:
                session.doc_lock = None
        session.pane.setParent(None)
        session.pane.deleteLater()

    def _connect_session_view(self, session: DocumentSession) -> None:
        if self._is_note_session(session):
            self._connect_note_view(session)
            return
        key = id(session)
        if key in self._connected_sessions:
            return
        view = session.view
        view.changed.connect(self._on_pdf_changed)
        view.pageChanged.connect(self._on_pdf_page_changed)
        view.pageChanged.connect(self.thumb_panel.set_current)
        view.zoomChanged.connect(self._on_pdf_zoom_changed)
        view.verticalScrollBar().valueChanged.connect(self._schedule_position_save)
        view.strokesChanged.connect(self._on_strokes_changed)
        view.notePlacementRequested.connect(self._create_note)
        view.noteRequested.connect(self._open_note)
        view.noteDeleteRequested.connect(self._delete_note_requested)
        view.notesChanged.connect(self._on_notes_changed)
        view.noteContextRequested.connect(self._show_note_menu)
        view.selectionCopied.connect(self._on_selection_copied)
        if not session.view_shortcuts and getattr(view, "kind", "pdf") == "pdf":
            for sequence, slot in (("Home", self._goto_first_page),
                                   ("End", self._goto_last_page)):
                shortcut = QShortcut(QKeySequence(sequence), view)
                shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                shortcut.activated.connect(slot)
                session.view_shortcuts.append(shortcut)
        for shortcut in session.view_shortcuts:
            shortcut.setEnabled(True)
        self._connected_sessions.add(key)

    def _disconnect_session_view(self, session: DocumentSession) -> None:
        key = id(session)
        if key not in self._connected_sessions:
            return
        view = session.view
        for signal, slot in (
            (view.changed, self._on_pdf_changed),
            (view.pageChanged, self._on_pdf_page_changed),
            (view.pageChanged, self.thumb_panel.set_current),
            (view.zoomChanged, self._on_pdf_zoom_changed),
            (view.verticalScrollBar().valueChanged, self._schedule_position_save),
            (view.strokesChanged, self._on_strokes_changed),
            (view.notePlacementRequested, self._create_note),
            (view.noteRequested, self._open_note),
            (view.noteDeleteRequested, self._delete_note_requested),
            (view.notesChanged, self._on_notes_changed),
            (view.noteContextRequested, self._show_note_menu),
            (view.selectionCopied, self._on_selection_copied),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        for shortcut in session.view_shortcuts:
            shortcut.setEnabled(False)
        self._connected_sessions.discard(key)

    def _use_session_globals(self, session: DocumentSession) -> None:
        self.pdf_view = session.view
        self.pdf_autosave = session.autosave
        self._dirty = session.dirty
        self._saved_at = session.saved_at
        self._note_windows = session.note_windows
        self._note_split_card = session.note_split_card
        self._note_split_pane = session.note_split_pane
        self._note_split_key = session.note_split_key
        self._split_prev_auto_fit = session.split_prev_auto_fit
        self._find_hits = session.find_hits
        self._find_index = session.find_index
        self._find_truncated = session.find_truncated
        self._find_query = session.find_query
        self._find_worker = None

    def _capture_session(self, session: DocumentSession | None = None) -> None:
        session = session or self._active_session
        if session is None or self._is_note_session(session):
            return
        hook = getattr(session.view, "commit_text_edit", None)
        if callable(hook):
            hook()                      # 正在输入的文字先落模型再记会话状态
        session.dirty = self._dirty
        session.saved_at = self._saved_at
        session.note_windows = self._note_windows
        session.note_split_card = self._note_split_card
        session.note_split_pane = self._note_split_pane
        session.note_split_key = self._note_split_key
        session.split_prev_auto_fit = self._split_prev_auto_fit
        session.find_hits = self._find_hits
        session.find_index = self._find_index
        session.find_truncated = self._find_truncated
        session.find_query = self._find_query

    def _save_active_position(self) -> None:
        self._position_timer.stop()
        if self._active_session is not None and not self._is_note_session(
                self._active_session):
            self._remember_pdf_position()
            # 分屏里来回点两侧会频繁切会话：只记内存，落盘合并到 600ms 定时器，
            # 别让每次切换都同步写一遍 settings.json
            self._position_timer.start()

    @staticmethod
    def _is_note_session(session) -> bool:
        return getattr(session, "kind", "pdf") == "note"

    def _activate_session(self, session: DocumentSession, force: bool = False) -> None:
        if session is None:
            return
        if self._is_note_session(session):
            self._activate_note(session, force=force)
            return
        if session is self._active_session and not force:
            entry = self._entry_for_session(session)
            if entry is not None:
                self.tabs.set_current_entry(entry)
            return
        old = self._active_session
        self._save_active_position()
        self._cancel_find_worker()
        if old is not None and old is not session:
            self._capture_session(old)
            self._disconnect_session_view(old)
        self._active_session = session
        self._active_entry = self._entry_for_session(session)
        self._use_session_globals(session)
        self._connect_session_view(session)
        self._refresh_document_ui()
        entry = self._active_entry
        if entry is not None:
            self.tabs.update_title(entry)
        self._refresh_title()

    def _activate_note(self, session: NoteSession, force: bool = False) -> None:
        """激活一个笔记标签：切到它自己的 SketchView/Document（可与 PDF 分屏）。"""
        if session is None:
            return
        entry = self._entry_for_session(session)
        if session is self._active_session and not force:
            if entry is not None:
                self.tabs.set_current_entry(entry)
            return
        old = self._active_session
        if old is not None and old is not session:
            if self._is_note_session(old):
                pass                     # 笔记状态都在自己的 view/document 里
            else:
                self._save_active_position()
                self._capture_session(old)
                self._disconnect_session_view(old)
        self._cancel_find_worker()
        self._active_session = session
        self._active_entry = entry
        self.view = session.view
        self.controller.document = session.document
        self._connect_note_view(session)
        self.pdf_view = self._empty_pdf_view
        self.pdf_autosave = self._idle_autosave
        self._dirty = False
        self._saved_at = None
        self._note_windows = {}
        self._note_split_card = None
        self._note_split_pane = None
        self._note_split_key = None
        self._split_prev_auto_fit = None
        self._find_hits = []
        self._find_index = -1
        self._find_truncated = False
        self._find_query = ""
        self._find_worker = None
        self.mode = "draft"
        self.menubar.set_mode("draft")
        self.menubar.set_viewer_kind("pdf")
        self.toolbox.set_annotations_enabled(True)
        self.toolbox.set_word_edit_enabled(False)
        self.toolbox.set_split_available(True)      # 笔记也能和 PDF/另一篇笔记分屏
        self.toolbox.set_draft_images_enabled(True)
        self.toolbox.set_find_enabled(False)
        self.toolbox.clear_button.setToolTip("清空笔迹（背景图片保留）(E)")
        self._word_editable = False
        self.toolbox.set_select_enabled(True)
        self.toolbox.set_note_enabled(False)
        self.toolbox.set_text_enabled(True)
        self._sync_document_shortcuts()
        self.find_bar.reset()
        self._find_close()
        self.toc_panel.clear_entries()
        self.thumb_panel.clear()
        self.toc_panel.hide()
        self.thumb_panel.hide()
        self.menubar.toc_action.setChecked(False)
        self.menubar.thumbs_action.setChecked(False)
        self._sync_side_panel()
        self._configure_autosave()
        if hasattr(self, "empty_hint"):
            self.empty_hint.setParent(self.view.viewport())
            self.view.viewport().installEventFilter(self)
        self.toolbox.reflect_color(self.view.color, "pen")
        self.toolbox.reflect_color(self.view.highlighter_color, "highlighter")
        self.toolbox.reflect_tool(self.view.tool)
        self.toolbox.reflect_width(self.view.width)
        self._reflect_bar(self.view.tool, self.view.width)
        self._refresh_title()
        self.refresh_status()
        self._refresh_empty_hint()
        self._sync_split_button(entry)
        self.view.setFocus(Qt.OtherFocusReason)

    def _connect_note_view(self, session: NoteSession) -> None:
        view = session.view
        key = id(view)
        if key in self._connected_note_views:
            return
        view.changed.connect(self._on_view_changed)
        self.toolbox.widthChosen.connect(view.set_width)
        view.viewport().installEventFilter(self)
        view.set_clear_shortcut_enabled(False)
        self._connected_note_views.add(key)

    def _activate_draft(self) -> None:
        if (self._draft_entry is not None and self._draft_entry.sessions
                and self._is_note_session(self._draft_entry.sessions[0])):
            self._activate_note(self._draft_entry.sessions[0])
            return
        old = self._active_session
        if old is not None:
            self._save_active_position()
            self._capture_session(old)
            self._disconnect_session_view(old)
        self._active_session = None
        self._active_entry = self._draft_entry
        self._cancel_find_worker()
        self.pdf_view = self._empty_pdf_view
        self.pdf_autosave = self._idle_autosave
        self._dirty = False
        self._saved_at = None
        self._note_windows = {}
        self._note_split_card = None
        self._note_split_pane = None
        self._note_split_key = None
        self._split_prev_auto_fit = None
        self._find_hits = []
        self._find_index = -1
        self._find_truncated = False
        self._find_query = ""
        self._find_worker = None
        self.mode = "draft"
        self.menubar.set_mode("draft")
        self.menubar.set_viewer_kind("pdf")
        self.toolbox.set_annotations_enabled(True)
        self.toolbox.set_word_edit_enabled(False)
        self.toolbox.set_split_available(False)     # 草稿标签暂不参与分屏
        self.toolbox.set_draft_images_enabled(True)
        self.toolbox.set_find_enabled(False)         # 回到草稿：查找按钮收起
        self.toolbox.clear_button.setToolTip("清空笔迹（背景图片保留）(E)")
        self._word_editable = False
        self.toolbox.set_select_enabled(True)       # 草稿画布：鼠标工具=手形平移
        self.toolbox.set_note_enabled(False)
        self.toolbox.set_text_enabled(True)         # 草稿画布：T 落点输入文字
        self._sync_document_shortcuts()
        self.find_bar.reset()
        self._find_close()
        self.toc_panel.clear_entries()
        self.thumb_panel.clear()
        self.toc_panel.hide()
        self.thumb_panel.hide()
        self.menubar.toc_action.setChecked(False)
        self.menubar.thumbs_action.setChecked(False)
        self._sync_side_panel()
        self.toolbox.reflect_color(self.view.color, "pen")
        self.toolbox.reflect_color(self.view.highlighter_color, "highlighter")
        self.toolbox.reflect_tool(self.view.tool)
        self.toolbox.reflect_width(self.view.width)
        self._reflect_bar(self.view.tool, self.view.width)
        self._configure_autosave()
        self._refresh_title()
        self.refresh_status()
        self._refresh_empty_hint()
        if self._draft_entry is None:
            self._show_empty_page()
            return
        self.view.setFocus(Qt.OtherFocusReason)

    # --- 空白工作区 / 草稿关闭 ---

    def _show_empty_page(self) -> None:
        """没有草稿也没有文档时的占位页（不要逼用户先建草稿）。"""
        if self._empty_page is None:
            self._empty_page = EmptyWorkspace(self, allow_draft=self.primary)
            self._empty_page.newDraftRequested.connect(self._new_draft_entry)
            self._empty_page.openRequested.connect(self._open_pdf)
            self.stack.addWidget(self._empty_page)
        self.stack.setCurrentWidget(self._empty_page)
        if hasattr(self, "empty_hint"):
            self.empty_hint.hide()

    def _drop_empty_page(self) -> None:
        """有标签了：把占位页从 stack 上摘掉（下次空态再建）。"""
        page = self._empty_page
        if page is None:
            return
        self._empty_page = None
        self.stack.removeWidget(page)
        page.deleteLater()

    def _make_note_session(self, title: str) -> NoteSession:
        document = Document()
        view = SketchView(SketchScene(document), self.controller.settings)
        view.set_width(self.controller.settings.width)
        session = NoteSession(title, view, document, view)
        self._connect_note_view(session)
        return session

    def _new_draft_entry(self) -> None:
        """新建一篇独立笔记；没有笔记时第一篇仍作为固定草稿标签。"""
        if not self.primary:
            return
        note_sessions = [session for entry in self.tabs.entries
                         for session in entry.sessions
                         if self._is_note_session(session)]
        note_count = len(note_sessions)
        if self._draft_entry is None and note_count == 0:
            session = self._make_note_session(DEFAULT_DRAFT_TITLE)
            self._draft_entry = self.tabs.add_draft(
                session.pane, session.title, session)
            self._drop_empty_page()
            self.stack.setCurrentWidget(session.pane)
            self._activate_note(session)
            return
        numbers = []
        for item in note_sessions:
            tail = str(item.title).rsplit(" ", 1)[-1]
            if tail.isdigit():
                numbers.append(int(tail))
        session = self._make_note_session(f"笔记 {max(numbers, default=0) + 1}")
        entry = self.tabs.add_entry([session], session.pane)
        self._drop_empty_page()
        self.tabs.set_current_entry(entry)
        self._activate_note(session)
        self._note(f"已新建 {session.title}")

    def _rename_entry(self, entry: TabEntry) -> None:
        """标签重命名：文档和笔记都能改；留空 = 恢复默认名。"""
        if entry is None:
            return
        text, ok = QInputDialog.getText(self, "重命名", "名称：",
                                        QLineEdit.EchoMode.Normal, entry.title)
        if not ok:
            return
        entry.custom_title = text.strip() or None
        self.tabs.update_title(entry)
        if entry is self._draft_entry and entry.custom_title:
            entry.draft_title = entry.custom_title
        self._note(f"已重命名：{entry.title}")

    def _confirm_close_draft(self) -> bool:
        """草稿关掉就清空；有内容才问，空草稿直接关。"""
        box = QMessageBox(self)
        box.setWindowTitle("关闭草稿")
        box.setText("草稿不会保存，关闭后内容会清空。")
        close_button = box.addButton("关闭", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is close_button

    def _reset_draft_canvas(self) -> None:
        """清空草稿的笔迹、背景图和撤销栈（重开草稿不能把旧内容撤回来）。"""
        document = self.controller.document
        if document.strokes:
            document.strokes.clear()
        self.view.clear_images()
        self.view.history.clear()
        self.view.scene.rebind()
        self.view.reset_zoom()
        self.view.changed.emit()

    def _close_draft_entry(self, entry: TabEntry) -> None:
        if entry is not self._draft_entry:
            return
        has_content = bool(self.controller.document.strokes) or self.view.image_count > 0
        if has_content and not self._confirm_close_draft():
            return
        self._reset_draft_canvas()
        self.tabs.remove_entry(entry)
        self._draft_entry = None
        current = self.tabs.current_entry
        if current is None:
            self._activate_draft()            # 没有标签：走空态
        elif current.draft:
            self._activate_draft()
        else:
            # 关的是后台草稿：当前文档不动，只刷新草稿的空态提示
            self._refresh_empty_hint()

    def _refresh_document_ui(self) -> None:
        view = self.pdf_view
        kind = getattr(view, "kind", "pdf")
        pdf = kind == "pdf"
        self.mode = "document"
        self.menubar.set_mode("document")
        self.menubar.set_viewer_kind(kind)
        self.toolbox.set_annotations_enabled(pdf)
        self.toolbox.set_select_enabled(True)        # 所有文档阅读器都保留鼠标/选字
        self.toolbox.set_note_enabled(pdf)
        self.toolbox.set_draft_images_enabled(pdf)   # PDF 页上也能贴图片（Ctrl+V/按钮）
        self.toolbox.set_text_enabled(pdf)           # PDF 页上也能输入文字
        self.toolbox.set_find_enabled(pdf)           # 只有 PDF 能全文搜索
        self.toolbox.clear_button.setToolTip("清空本页 (E)")
        editable = pdf and supports_edit(view.pdf_path)
        self._word_editable = bool(editable)
        self.menubar.set_word_editable(editable)
        self.toolbox.set_word_edit_enabled(editable)
        if pdf:
            self.toolbox.reflect_color(view.color, "pen")
            self.toolbox.reflect_color(view.highlighter_color, "highlighter")
            self.toolbox.reflect_tool(view.tool)
            self.toolbox.reflect_width(view.width)
        self._sync_document_shortcuts()
        self.find_bar.reset()
        self._find_close()
        self.toolbox.set_split_available(True)   # 文档模式才显示工具栏分屏按钮
        if pdf:
            source = view.source
            populate_side = (self.toc_panel.isVisible()
                             or id(source) not in self._side_seen_sources)
            if populate_side and source is not None:
                try:
                    toc = source.get_toc()
                except PdfError:
                    toc = []
                self.toc_panel.set_entries(toc)
                self.thumb_panel.set_source(source)
                self.thumb_panel.set_current(view.current_page)
                self._side_seen_sources.add(id(source))
            elif self.thumb_panel.isVisible():
                self.thumb_panel.set_current(view.current_page)
        else:
            self.thumb_panel.clear()
            self.menubar.thumbs_action.setChecked(False)
            self.thumb_panel.hide()
            self.toc_panel.set_entries(view.outline())
        self._configure_autosave()
        self._refresh_title()
        self.refresh_status()
        self._refresh_empty_hint()

    def _on_tab_changed(self, entry: TabEntry | None) -> None:
        if self._switching_tab:
            return
        self._sync_split_button(entry)
        if entry is not None:
            self._drop_empty_page()
        if entry is None or entry.draft:
            self._activate_draft()
            return
        session = entry.sessions[0]
        if entry.is_split:
            active = getattr(entry.page, "active_session", None)
            session = active or session
        self._activate_session(session)

    def _sync_split_button(self, entry: TabEntry | None) -> None:
        """工具栏分屏按钮：普通/笔记标签都可分屏，已分屏标签不再显示。"""
        available = (entry is not None and not entry.is_split
                     and bool(entry.sessions))
        self.toolbox.set_split_available(available)
        if not available:
            return
        ready = any(item is not entry and not item.is_split and bool(item.sessions)
                    for item in self.tabs.entries)
        self.toolbox.set_split_enabled(ready)

    def _on_selection_copied(self, count: int) -> None:
        self._note(f"已复制 {count} 个字符")

    def _on_session_autosaved(self, session: DocumentSession) -> None:
        if session is self._active_session:
            self._note(f"批注已自动暂存 {datetime.now().strftime('%H:%M:%S')}")

    def _on_session_autosave_failed(self, session: DocumentSession, msg: str) -> None:
        if session is self._active_session:
            self._note(f"自动保存失败：{msg}", error=True)

    def _auto_save_cache_for(self, session: DocumentSession) -> None:
        path = session.path
        document = session.view.document
        if path is None or document is None:
            raise RuntimeError("没有打开的文档，无法自动暂存批注")
        write_cache(path, document, self._autosave_base)

    def _close_entry(self, entry: TabEntry) -> None:
        if entry.draft:
            self._close_draft_entry(entry)
            return
        for session in list(entry.sessions):
            self._activate_session(session, force=True)
            self._close_note_windows()
            self._close_note_split()
            if not self._flush_autosave():
                self.tabs.set_current_entry(entry)
                return
            self._remember_pdf_position()
            self._capture_session(session)
            self._disconnect_session_view(session)
            self._destroy_session(session)
        self._active_session = None
        self._active_entry = None
        if entry.is_split:
            entry.page.setParent(None)
            entry.page.deleteLater()
        self.tabs.remove_entry(entry)
        # 关闭后台标签时当前标签可能没变（不会触发 currentChanged）：主动把
        # 全局状态切回仍存在的标签，避免指向已销毁的 session。
        current = self.tabs.current_entry
        if current is None or current.draft:
            self._activate_draft()
        else:
            session = current.sessions[0]
            if current.is_split:
                session = getattr(current.page, "active_session", None) or session
            self._activate_session(session, force=True)

    def _close_other_entries(self, keep: TabEntry) -> None:
        for entry in list(self.tabs.entries):
            if entry is not keep and not entry.draft:
                self._close_entry(entry)
                if self._entry_for_session(keep.sessions[0]) is None:
                    return

    def _close_current_entry(self) -> None:
        entry = self.tabs.current_entry
        if entry is not None:
            self._close_entry(entry)

    # --- Word 基础编辑 ---

    def _edit_word_document(self) -> bool:
        """打开当前 .docx 的应用内编辑器；同一文件只保留一个窗口。"""
        path = self.pdf_view.pdf_path
        if path is None or not supports_edit(path):
            self._note("当前文档不是可编辑的 Word（仅 .docx）", error=True)
            return False
        key = os.path.normcase(str(Path(path).resolve()))
        window = self._word_editors.get(key)
        if window is None:
            window = create_editor(path, self)
            if window is None:
                self._note("Word 编辑器不可用", error=True)
                return False
            saved = getattr(window, "saved", None)
            if saved is not None:
                saved.connect(self._on_word_saved)
            window.destroyed.connect(
                lambda _obj=None, k=key: self._word_editors.pop(k, None))
            self._word_editors[key] = window
        window.show()
        window.raise_()
        window.activateWindow()
        self._note("已打开 Word 编辑器；保存后重新打开文档可看到新内容")
        return True

    def _on_word_saved(self, target: str) -> None:
        """编辑窗口保存后提示；原文件被覆盖时提醒重新打开以迁移批注。"""
        path = Path(target)
        if self._session_for_path(path) is not None:
            self._note(f"Word 已保存：{path.name}；重新打开可看到新内容（旧批注会先备份）")
        else:
            self._note(f"Word 已保存副本：{path.name}")

    def _open_convert_center(self) -> bool:
        """转换中心：单例窗口；没有注册工厂时给出提示。"""
        if self.convert_dialog is None:
            self.convert_dialog = create_converter(self)
        if self.convert_dialog is None:
            self._note("当前版本没有转换中心（Standard 专属）", error=True)
            return False
        self.convert_dialog.show()
        self.convert_dialog.raise_()
        self.convert_dialog.activateWindow()
        return True

    def _close_convert_center(self) -> None:
        if self.convert_dialog is not None:
            try:
                self.convert_dialog.close()
            except RuntimeError:
                pass
            self.convert_dialog = None

    def _close_word_editors(self) -> bool:
        """退出前收口所有编辑窗口；用户取消保存则中止退出。"""
        for key, window in list(self._word_editors.items()):
            try:
                if not window.close():
                    return False
            except RuntimeError:
                pass
            self._word_editors.pop(key, None)
        return True

    def _close_all_documents(self) -> bool:
        """兼容旧“关闭文档”：关闭所有文档标签；用户取消则返回 False。"""
        for entry in list(self.tabs.entries):
            if entry.draft:
                continue
            before = len(self.tabs.entries)
            self._close_entry(entry)
            if len(self.tabs.entries) == before:
                return False                 # 用户取消保存
        self._activate_draft()
        return True

    def _flush_all_sessions(self) -> bool:
        for entry in list(self.tabs.entries):
            if entry.draft:
                continue
            for session in entry.sessions:
                self._activate_session(session, force=True)
                self._close_note_windows()
                self._close_note_split()
                if not self._flush_autosave():
                    return False
                self._remember_pdf_position()
        return True

    def _next_tab(self) -> None:
        entries = self.tabs.entries
        if len(entries) < 2:
            return
        current = self.tabs.current_entry
        try:
            index = entries.index(current) if current is not None else 0
        except ValueError:
            index = 0
        self.tabs.set_current_entry(entries[(index + 1) % len(entries)])

    def _prev_tab(self) -> None:
        entries = self.tabs.entries
        if len(entries) < 2:
            return
        current = self.tabs.current_entry
        try:
            index = entries.index(current) if current is not None else 0
        except ValueError:
            index = 0
        self.tabs.set_current_entry(entries[(index - 1) % len(entries)])

    def _start_document_split(self, entry: TabEntry | None) -> None:
        """选择另一个标签，与当前标签合并为 `A | B` 双栏分屏（可笔记+PDF）。"""
        if entry is None or entry.is_split or not entry.sessions:
            return
        others = [item for item in self.tabs.entries
                  if not item.is_split and item.sessions and item is not entry]
        if not others:
            self._note("至少打开两个标签才能分屏")
            return
        menu = QMenu(self)
        actions = {}
        for item in others:
            action = menu.addAction(item.title)
            actions[action] = item
        point = self.toolbox.split_button.mapToGlobal(
            QPoint(0, self.toolbox.split_button.height()))
        chosen = menu.exec(point)
        target = actions.get(chosen)
        if target is not None:
            self._merge_document_entries(entry, target)

    def _window_for_tabs(self, tabs):
        if self.window_registry is not None:
            window = self.window_registry.window_for_tabs(tabs)
            if window is not None:
                return window
        return self if tabs is self.tabs else None

    def _on_tab_transfer(self, source_tabs, entry: TabEntry,
                         target_tabs, index: int) -> None:
        source = self._window_for_tabs(source_tabs)
        target = self._window_for_tabs(target_tabs)
        if source is None or target is None:
            return
        if source is target:
            target.tabs.move_entry_to(entry, index)
            return
        self.window_registry.transfer(source, entry, target, index)

    def _on_tab_merge(self, source_tabs, source_entry: TabEntry,
                      target_tabs, target_entry: TabEntry) -> None:
        source = self._window_for_tabs(source_tabs)
        target = self._window_for_tabs(target_tabs)
        if source is None or target is None:
            return
        if self.window_registry is not None:
            self.window_registry.merge(source, source_entry, target, target_entry)
        elif source is target:
            target._merge_document_entries(source_entry, target_entry)

    def _on_tab_detach(self, source_tabs, entry: TabEntry, global_pos: QPoint) -> None:
        source = self._window_for_tabs(source_tabs)
        if source is None or entry is None or entry.draft:
            return
        if self.window_registry is not None:
            self.window_registry.detach(source, entry, global_pos)

    def _merge_document_entries(self, first: TabEntry, second: TabEntry) -> None:
        """把两个标签合并成一个左右分屏标签（PDF/PDF、笔记/PDF、笔记/笔记）。"""
        if (first is second or first.is_split or second.is_split
                or not first.sessions or not second.sessions):
            return
        if (first not in self.tabs.entries) or (second not in self.tabs.entries):
            return
        left = first.sessions[0]
        right = second.sessions[0]
        self._save_active_position()
        self._capture_session()

        split = SplitWorkspace(self)
        self._connect_split_workspace(split)

        self._switching_tab = True
        try:
            if first is self._draft_entry or second is self._draft_entry:
                self._draft_entry = None
            self.tabs.remove_entry(first)
            self.tabs.remove_entry(second)
            split.set_sessions([left, right])
            merged = self.tabs.add_entry([left, right], split)
        finally:
            self._switching_tab = False
        self._active_session = None
        self._active_entry = merged
        self.tabs.set_current_entry(merged)
        self._activate_session(left, force=True)
        self._note(f"已分屏：{left.display_name} | {right.display_name}")

    def _connect_split_workspace(self, split: SplitWorkspace) -> None:
        """分屏容器的两个信号跟着它走：拖到二级窗口后要改接新窗口。"""
        split.activeChanged.connect(self._on_split_active_changed)
        split.unsplitRequested.connect(
            lambda page=split: self._unsplit_document_entry(page))

    def _move_entry_to_window(self, entry: TabEntry, target: "MainWindow",
                              index: int | None = None) -> TabEntry | None:
        """把标签整体移到另一个窗口：只换父窗口，会话/撤销/滚动都不重建。"""
        if (target is self or entry is None or entry.draft
                or not entry.sessions):
            return None
        # 停靠的草稿纸跟着这个会话的 pane 走；换窗口后主窗就管不到它了，
        # 先还原浮窗再搬标签（否则关新窗会连带销毁停靠控件）
        if (self._scratch_dock_session is not None
                and self._scratch_dock_session in entry.sessions):
            self._undock_scratch(show_window=False)
        positions = {}
        for session in entry.sessions:
            try:
                positions[id(session)] = session.view.current_position()
            except Exception:
                positions[id(session)] = None

        self.tabs.remove_entry(entry)
        for session in entry.sessions:
            if id(session) in self._connected_sessions:
                self._disconnect_session_view(session)

        adopted = target.tabs.add_entry(entry.sessions, entry.page)
        if index is not None:
            target.tabs.move_entry_to(adopted, index)
        for session in entry.sessions:
            target._connect_session_view(session)
        # 跨窗重挂保险（同 eb689a7 分屏空白的教训）：removeWidget 会 hide pane，
        # re-add 后真实窗口下可能不自动显示/重绘——显式 show + 延迟强制重绘
        entry.page.show()
        if entry.is_split:
            target._connect_split_workspace(entry.page)

        active = None
        if entry.is_split:
            active = getattr(entry.page, "active_session", None)
        active = active or entry.sessions[0]
        target._activate_session(active, force=True)

        # 换父窗口会触发 resize；等布局定下来再还原阅读位置/缩放
        for session in entry.sessions:
            position = positions.get(id(session))
            if not position:
                continue
            QTimer.singleShot(
                0, lambda view=session.view, p=position:
                view.restore_position(p["page"], p["offset"], p["zoom"],
                                      p["auto_fit"]))
        QTimer.singleShot(0, lambda: [
            _repaint_view(session.view) for session in entry.sessions])

        if not self.primary and not self.tabs.entries:
            QTimer.singleShot(0, self.close)
        return adopted

    def _on_split_active_changed(self, session: DocumentSession) -> None:
        if self._active_entry is None or not self._active_entry.is_split:
            return
        self._activate_session(session)

    def _unsplit_document_entry(self, split: SplitWorkspace) -> None:
        """取消分屏：拆回两个普通标签，保留各自会话状态。"""
        entry = next((item for item in self.tabs.entries if item.page is split), None)
        if entry is None or not entry.is_split:
            return
        sessions = split.detach_sessions()
        self._switching_tab = True
        try:
            self.tabs.remove_entry(entry)
            for session in sessions:
                self.tabs.add_entry([session], session.pane)
        finally:
            self._switching_tab = False
        split.setParent(None)
        split.deleteLater()
        self._active_session = None
        self._active_entry = None
        if sessions:
            first = next(item for item in self.tabs.entries
                         if item.sessions and item.sessions[0] is sessions[0])
            self.tabs.set_current_entry(first)
            self._activate_session(sessions[0], force=True)
        self._note("已取消分屏")

    def _enter_document(self, path) -> bool:
        """打开文档：已在标签里就激活；否则按格式新建独立 session/标签。"""
        path = Path(path)
        if not is_supported_document(path):
            self._note(f"不支持的文档格式：{path.name}", error=True)
            return False
        if viewer_kind(path) != "pdf":
            return self._enter_text_document(path)
        existing = self._session_for_path(path)
        if existing is not None:
            entry = self._entry_for_session(existing)
            if entry is not None:
                self.tabs.set_current_entry(entry)
                self._activate_session(existing, force=True)
                self.pdf_view.setFocus()
            return True
        if self._focus_existing_session_elsewhere(path):
            return True

        # 多开：同一份 PDF/Word 只允许一个实例持写锁，否则两边自动暂存/关窗
        # 会互相覆盖批注。只读阅读器（TXT/导图）不锁，允许多开。
        # 必须在收口/断开当前文档之前判断，失败时当前文档原样不动。
        doc_lock = None
        if viewer_kind(path) == "pdf":
            doc_lock = DocumentLock(path)
            if not doc_lock.acquire():
                self._note(f"{path.name} 已在另一个墨页实例中打开；"
                           "请先在那里关闭，避免批注互相覆盖", error=True)
                return False

        previous = self._active_session
        if previous is not None:
            if not self._flush_autosave():
                if doc_lock is not None:
                    doc_lock.release()
                return False
            self._remember_pdf_position()
            self._capture_session(previous)
            self._disconnect_session_view(previous)

        try:
            session = self._create_session(path, doc_lock=doc_lock)
        except Exception:
            if doc_lock is not None:
                doc_lock.release()
            raise
        self._active_session = None
        self._use_session_globals(session)
        self._dirty = False
        self._saved_at = None
        self._note_windows = {}
        self._note_split_card = None
        self._note_split_pane = None
        self._note_split_key = None
        self._split_prev_auto_fit = None
        self._find_hits = []
        self._find_index = -1
        self._find_truncated = False
        self._find_query = ""
        self._find_worker = None

        override = None
        cached = read_cache(path, self._autosave_base)
        if cached is not None:
            cached_doc, saved_at = cached
            sidecar = pdfstore.sidecar_path(path)
            try:
                stale = sidecar.exists() and sidecar.stat().st_mtime > saved_at.timestamp()
            except OSError:
                stale = False
            if stale:
                clear_cache(path, self._autosave_base)
                cached = None
            elif self._ask_recover_cache(saved_at):
                override = cached_doc
            else:
                clear_cache(path, self._autosave_base)

        try:
            mode = getattr(self.controller.settings, "page_mode", "normal")
            if mode not in PAGE_MODES:
                mode = "normal"
            self.pdf_view.set_page_mode(mode)
            self.thumb_panel.set_page_mode(mode)
            self.pdf_view.open(path, document_override=override)
        except pdfstore.PdfFormatError as exc:
            if override is not None:
                clear_cache(path, self._autosave_base)
                self._note(f"自动暂存与当前 PDF 不一致，已丢弃：{exc}", error=True)
                try:
                    self.pdf_view.open(path)
                except (PdfError, pdfstore.PdfFormatError, ValueError, OSError) as exc2:
                    self._show_open_error(exc2)
                    self._destroy_session(session)
                    self._restore_after_open_failure(previous)
                    return False
                override = None
            else:
                if not self._ask_ignore_sidecar(exc):
                    self._show_open_error(exc)
                    self._destroy_session(session)
                    self._restore_after_open_failure(previous)
                    return False
                backup = self._backup_bad_sidecar(path)
                try:
                    self.pdf_view.open(path, ignore_sidecar=True)
                except (PdfError, pdfstore.PdfFormatError, ValueError, OSError) as exc2:
                    self._show_open_error(exc2)
                    self._destroy_session(session)
                    self._restore_after_open_failure(previous)
                    return False
                if backup is not None:
                    self._note(f"损坏的批注文件已备份为 {backup.name}")
        except (PdfError, ValueError, OSError) as exc:
            self._show_open_error(exc)
            self._destroy_session(session)
            self._restore_after_open_failure(previous)
            return False

        session.dirty = override is not None
        session.saved_at = None
        self._dirty = session.dirty
        self._saved_at = None
        entry = self.tabs.add_entry([session], session.pane)
        self._active_session = session
        self._active_entry = entry
        self._connect_session_view(session)
        self._refresh_document_ui()

        resolved = pdf_position(self.controller.settings, path)
        if resolved is not None:
            self.pdf_view.restore_position(resolved["page"], resolved["offset"],
                                           resolved["zoom"], resolved["auto_fit"])
        else:
            self.pdf_view.scroll_to_top()
        self.pdf_view.setFocus()
        self._reflect_bar(self.pdf_view.tool, self.pdf_view.width)
        self._note(getattr(self.pdf_view, "conversion_note", "")
                   or f"已打开 {path.name}")
        self.controller.settings.recent_files = push_recent(
            self.controller.settings.recent_files, str(path))
        self.controller.save_settings()
        self._rebuild_recent_menu()
        self._configure_autosave()
        self._refresh_title()
        self.refresh_status()
        self._refresh_empty_hint()
        return True

    def _enter_text_document(self, path: Path) -> bool:
        """TXT/Markdown：只读查看器，无 sidecar、无自动暂存，阅读位置沿用 pdf_positions。"""
        existing = self._session_for_path(path)
        if existing is not None:
            entry = self._entry_for_session(existing)
            if entry is not None:
                self.tabs.set_current_entry(entry)
                self._activate_session(existing, force=True)
                self.pdf_view.setFocus()
            return True
        if self._focus_existing_session_elsewhere(path):
            return True

        previous = self._active_session
        if previous is not None:
            if not self._flush_autosave():
                return False
            self._remember_pdf_position()
            self._capture_session(previous)
            self._disconnect_session_view(previous)

        try:
            session = self._create_session(path)
        except Exception as exc:
            # 第三方阅读器构造失败（例如损坏/未知的思维导图）不能把异常抛进
            # Qt 事件循环，否则会话停在一半，后面每个操作都会继续报错。
            self._show_open_error(exc)
            self._restore_after_open_failure(previous)
            return False
        self._active_session = None
        self._use_session_globals(session)
        try:
            self.pdf_view.open(path)
        except Exception as exc:
            self._show_open_error(exc)
            self._destroy_session(session)
            self._restore_after_open_failure(previous)
            return False

        entry = self.tabs.add_entry([session], session.pane)
        self._active_session = session
        self._active_entry = entry
        self._connect_session_view(session)
        self._refresh_document_ui()
        resolved = pdf_position(self.controller.settings, path)
        if resolved is not None:
            self.pdf_view.restore_position(resolved["page"], resolved["offset"],
                                           resolved["zoom"], resolved["auto_fit"])
        else:
            self.pdf_view.scroll_to_top()
        self.pdf_view.setFocus()
        self._note(f"已打开 {path.name}")
        self.controller.settings.recent_files = push_recent(
            self.controller.settings.recent_files, str(path))
        self.controller.save_settings()
        self._rebuild_recent_menu()
        self._configure_autosave()
        self._refresh_title()
        self.refresh_status()
        self._refresh_empty_hint()
        return True

    def _restore_after_open_failure(self, previous: DocumentSession | None) -> None:
        """打开失败：恢复之前的会话（或回到草稿），不留下半个 session。"""
        self._active_session = None
        self._active_entry = None
        if previous is not None:
            self._activate_session(previous, force=True)
            return
        self._activate_draft()

    def _show_open_error(self, exc: Exception) -> None:
        """打开失败：状态栏红字，不弹栈、不改当前文档。"""
        self._note(str(exc), error=True)

    def _ask_recover_cache(self, saved_at) -> bool:
        """发现崩溃残留的自动暂存：默认恢复，用户可拒绝。"""
        choice = QMessageBox.question(
            self, "发现未保存的批注",
            f"上次退出前有未正常保存的批注（{saved_at:%Y-%m-%d %H:%M:%S}）。\n"
            "是否恢复这些批注？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        return choice == QMessageBox.Yes

    def _ask_ignore_sidecar(self, exc: Exception) -> bool:
        """批注文件坏了：询问是否忽略批注打开，坏文件会先备份。"""
        choice = QMessageBox.question(
            self, "批注文件无法读取",
            f"{exc}\n\n是否忽略批注直接打开 PDF？\n"
            "原批注文件会先备份为 .corrupt-时间 后缀。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return choice == QMessageBox.Yes

    def _backup_bad_sidecar(self, path: Path) -> Path | None:
        """把读不出来的 sidecar 改名留证，避免下一次保存直接覆盖。"""
        sidecar = pdfstore.sidecar_path(path)
        if not sidecar.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = sidecar.with_name(f"{sidecar.name}.corrupt-{stamp}")
        try:
            sidecar.replace(backup)
        except OSError as exc:
            self._note(f"备份损坏批注失败：{exc}", error=True)
            return None
        return backup

    def _enter_draft(self) -> None:
        """兼容旧“关闭文档”：关闭全部文档标签并回到草稿。"""
        if not self._close_all_documents():
            return
        if self._draft_entry is None:
            if self.primary:
                self._new_draft_entry()
            else:
                self._show_empty_page()
        else:
            self.tabs.set_current_entry(self._draft_entry)
        self.view.setFocus()
        self._note("已关闭文档")

    def _on_menu_action(self, key: str) -> None:
        """菜单只发稳定 id，分发在这里；画笔/粗细另走 toolChosen/widthChosen。"""
        if key == "open_pdf":
            self._open_pdf()
        elif key == "new_draft":
            self._new_draft_entry()
        elif key == "edit_word":
            self._edit_word_document()
        elif key == "convert":
            self._open_convert_center()
        elif key == "save":
            self._save_annotations()
        elif key == "export":
            self._export_pdf()
        elif key == "close_doc":
            self._close_current_entry()
        elif key == "quit":
            self.quit_all_apps()
        elif key == "undo":
            self._undo()
        elif key == "redo":
            self._redo()
        elif key == "clear_canvas":
            self._clear_active()
        elif key == "zoom_in":
            self._zoom_by(self.ZOOM_STEP)
        elif key == "zoom_out":
            self._zoom_by(1.0 / self.ZOOM_STEP)
        elif key == "actual_size":
            self._set_pdf_zoom(1.0)
        elif key == "fit_width":
            self._fit_page_width()
        elif key == "fullscreen":
            self._toggle_fullscreen()
        elif key in ("page_normal", "page_night", "page_eye"):
            self._set_page_mode(key.removeprefix("page_"))
        elif key == "prev_page":
            self._step_page(-1)
        elif key == "next_page":
            self._step_page(1)
        elif key == "goto_page":
            self._goto_page()
        elif key == "find":
            self._show_find()
        elif key == "annotate":
            self._toggle_fullscreen_annotate()
        elif key == "screenshot":
            self._start_screenshot()
        elif key in ("toc", "thumbs"):
            self._toggle_side_panel(key)
        elif key == "shortcuts":
            dialogs.show_shortcuts(self)
        elif key == "about":
            dialogs.show_about(self)
        # pen / eraser / width_* 已由 toolChosen / widthChosen 处理，不重复分发

    def _set_page_mode(self, mode: str) -> None:
        """切换 PDF 底图阅读模式：菜单、两个视图、配置一起落地（只影响显示）。"""
        if mode not in PAGE_MODES:
            raise ValueError(f"未知阅读模式：{mode!r}（只支持 {PAGE_MODES}）")
        self.controller.settings.page_mode = mode
        self.menubar.set_page_mode(mode)
        self.pdf_view.set_page_mode(mode)
        self.thumb_panel.set_page_mode(mode)
        self.controller.save_settings()
        self._note(f"阅读模式：{PAGE_MODE_LABELS[mode]}")

    # --- 查找条 ---

    def _show_find(self) -> None:
        """唤起查找条并聚焦输入框；仅在文档模式可用。"""
        if self.mode != "document":
            self._note("文档模式下才能查找")
            return
        self.find_bar.show()
        self.find_bar.focus_input()

    def _find_shortcut(self, delta: int) -> None:
        """F3 / Shift+F3：还没有命中就先唤起查找条（有未搜查询则一并开搜）。"""
        if self.mode != "document":
            return
        if not self._find_hits:
            self._show_find()
            self.find_bar.start_search()
            return
        self._find_step(delta)

    def _toggle_side_panel(self, key: str) -> None:
        """目录 / 缩略图显隐开关：菜单点击时 Qt 已翻好勾选态，按它同步面板。

        注意：可勾选 QAction 发 triggered 时 checked 已是新状态（实测 trigger()
        与真实菜单点击皆如此），这里不能再翻一次，否则面板永远显不出来。
        """
        if self.mode != "document":
            self._note("文档模式下才能显示侧栏")
            return
        action = self.menubar.toc_action if key == "toc" else self.menubar.thumbs_action
        panel = self.toc_panel if key == "toc" else self.thumb_panel
        panel.setVisible(action.isChecked())
        self._sync_side_panel()
        if panel.isVisible():
            self._refresh_side_panels()

    def _refresh_side_panels(self) -> None:
        """侧栏按需填充：平时隐藏就不在切分屏时重建目录/缩略图。"""
        view = self.pdf_view
        if getattr(view, "kind", "pdf") != "pdf" or view.source is None:
            return
        if self.toc_panel.isVisible():
            try:
                toc = view.source.get_toc()
            except PdfError:
                toc = []
            self.toc_panel.set_entries(toc)
        if self.thumb_panel.isVisible():
            self.thumb_panel.set_source(view.source)
            self.thumb_panel.set_current(view.current_page)
        self._side_seen_sources.add(id(view.source))

    def _sync_side_panel(self) -> None:
        """任一面板勾选就显示边栏容器，全未勾选则收起（用 action 勾选态判断，
        避免子面板 isVisible() 受隐藏容器牵连而失真）。"""
        visible = self.menubar.toc_action.isChecked() or self.menubar.thumbs_action.isChecked()
        self.side_panel.setVisible(visible)

    def _queue_find(self, text: str) -> None:
        """查找条要求搜索：空串立即清屏，非空立即启动后台 worker（不再 debounce）。

        只有回车/上一个/下一个/清空输入会走到这里；打字本身不发搜索。
        """
        self._find_query = text
        self._run_find()

    def _run_find(self) -> None:
        """用当前查询启动后台 worker；空查询则取消在途搜索并清空高亮。"""
        text = (self._find_query or "").strip()
        if not text:
            self._cancel_find_worker()
            self._apply_search_result([], False)
            return
        if getattr(self.pdf_view, "kind", "pdf") != "pdf":
            self._cancel_find_worker()
            self._apply_search_result(self.pdf_view.search(text), False)
            return
        if self.pdf_view.source is None:
            self._cancel_find_worker()
            self._apply_search_result([], False)
            return
        self._cancel_find_worker()               # 先停车：旧结果绝不能盖新查询
        self._find_generation += 1
        worker = PdfSearchWorker(
            self._find_generation,
            getattr(self.pdf_view, "search_path", self.pdf_view.pdf_path), text)
        worker.progress.connect(self._on_search_progress)
        worker.finished.connect(self._on_search_finished)
        worker.cancelled.connect(self._on_search_cancelled)
        self._find_worker = worker
        self.find_bar.set_searching(0, 0)        # 总数未知：先显示「搜索中…」
        worker.start()

    def _on_search_progress(self, done: int, total: int) -> None:
        """子线程进度（已排队到主线程）：只有当前 worker 的进度才上屏。"""
        if self.sender() is not self._find_worker:
            return                               # 旧 worker 的迟到进度：丢弃
        self.find_bar.set_searching(done, total)

    def _on_search_finished(self, generation: int, hits, truncated: bool) -> None:
        """worker 完成：代号对得上才落地；随后回收线程对象（含被作废的）。"""
        worker = self.sender()
        if generation == self._find_generation:
            self._find_worker = None
            self._apply_search_result(hits, truncated)
        self._retire_search_worker(worker)

    def _on_search_cancelled(self, _generation: int) -> None:
        """被取消的 worker 只发 cancelled：回收线程对象，不碰任何查找状态。"""
        self._retire_search_worker(self.sender())

    def _cancel_find_worker(self) -> None:
        """作废当前搜索：代号 +1（在途 progress/finished 全部失效），worker 停手。"""
        self._find_generation += 1
        worker, self._find_worker = self._find_worker, None
        if worker is not None:
            worker.cancel()
            self._find_retired.append(worker)

    def _retire_search_worker(self, worker) -> None:
        """线程到达终点（finished/cancelled 已投递）：摘册并交给 Qt 回收。"""
        if worker is None:
            return
        if worker is self._find_worker:
            self._find_worker = None
        if worker in self._find_retired:
            self._find_retired.remove(worker)
        if worker.isRunning():
            # 信号是在 run 收尾前发的，极端情况下线程还差最后一步才退场
            worker.wait(100)
        if worker.isRunning():
            self._find_retired.append(worker)    # 还在跑：先别删，退出前再等
            return
        worker.deleteLater()

    def _wait_find_workers(self, timeout_ms: int = 2000) -> None:
        """退出前收口：取消搜索并等线程退场（页间检查，一页之内就会停）。"""
        self._cancel_find_worker()
        for worker in list(self._find_retired):
            if worker.isRunning():
                worker.wait(timeout_ms)

    def _find_search(self, text: str) -> None:
        """同步全文搜索；空查询或无源时清空高亮并计 0/0。

        保留给测试与"必须立刻出结果"的调用方；日常查找走 `_run_find` 的
        异步 worker，两边共用 `_apply_search_result` 落地。
        """
        text = (text or "").strip()
        if getattr(self.pdf_view, "kind", "pdf") != "pdf":
            hits = self.pdf_view.search(text) if text else []
            self._apply_search_result(hits, False)
            return
        source = self.pdf_view.source
        if source is None or not text:
            self._apply_search_result([], False)
            return
        try:
            # 多取一条判断是否被 500 封顶，既保留上限也能给用户提示
            raw = source.search_document(text, max_hits=501)
        except PdfError:
            raw = []
        truncated = len(raw) > 500
        self._apply_search_result(raw[:500], truncated)

    def _apply_search_result(self, hits, truncated: bool) -> None:
        """把一份搜索结果落地：截断标记 + 命中列表 + 显示第一条（同步/异步共用）。"""
        self._find_truncated = bool(truncated)
        self._find_hits = list(hits)
        self._find_index = 0 if self._find_hits else -1
        self._find_show_current()

    def _find_show_current(self) -> None:
        """高亮当前命中所在页的全部命中，并把页码调过去。"""
        if not self._find_hits:
            self.find_bar.set_count(0, 0)
            self.pdf_view.clear_hits()
            return
        self._find_index %= len(self._find_hits)
        page, rect = self._find_hits[self._find_index]
        same = [(p, r) for (p, r) in self._find_hits if p == page]
        current = same.index((page, rect))
        if getattr(self.pdf_view, "kind", "pdf") != "pdf":
            self.pdf_view.show_hits(0, [r for (_, r) in same], current=current)
            self.find_bar.set_count(self._find_index + 1, len(self._find_hits),
                                    truncated=self._find_truncated)
            return
        self.pdf_view.goto_page(page)
        self.pdf_view.show_hits(page, [r for (_, r) in same], current=current)
        self.find_bar.set_count(self._find_index + 1, len(self._find_hits),
                                truncated=self._find_truncated)

    def _find_step(self, delta: int) -> None:
        """上一个 / 下一个：循环不越界。"""
        if not self._find_hits:
            return
        self._find_index = (self._find_index + delta) % len(self._find_hits)
        self._find_show_current()

    def _find_close(self) -> None:
        """关查找条：取消后台搜索、清空命中与计数，把焦点还给文档视图。"""
        self._cancel_find_worker()
        self._find_hits = []
        self._find_index = -1
        self._find_truncated = False
        self.pdf_view.clear_hits()
        self.find_bar.set_count(0, 0)
        self.pdf_view.setFocus()

    # --- 最近打开 ---

    def _rebuild_recent_menu(self) -> None:
        """重建「最近打开」：顶部批量管理，条目可点；右键可删单条。"""
        menu = self.menubar.recent_menu
        menu.clear()
        paths = list(self.controller.settings.recent_files)
        manage = QAction("批量管理…", menu)
        manage.setToolTip("打开列表，批量删除最近打开记录")
        manage.triggered.connect(self._manage_recent_files)
        manage.setEnabled(bool(paths))
        menu.addAction(manage)
        menu.addSeparator()
        if not paths:
            empty = QAction("暂无最近打开", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
        for path in paths:
            target = Path(path)
            exists = target.exists()
            action = QAction(target.name, menu)
            action.setData(path)
            action.setToolTip(path if exists else f"{path}（文件不存在）")
            action.setEnabled(exists)            # 失效条目直接置灰，不等到点击才报错
            if exists:
                action.triggered.connect(
                    lambda _checked=False, p=path: self._open_recent(p))
            menu.addAction(action)
        menu.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _on_recent_context_menu(self, pos) -> None:
        """最近条目右键：只删这一条记录，不删文件。"""
        menu = self.menubar.recent_menu
        action = menu.actionAt(pos)
        path = action.data() if action is not None else None
        if not path:
            return
        context = QMenu(menu)
        remove = context.addAction("从最近打开中删除")
        remove.triggered.connect(lambda _checked=False, p=str(path): self._remove_recent(p))
        context.aboutToHide.connect(context.deleteLater)
        context.popup(menu.mapToGlobal(pos))

    def _remove_recent(self, path: str) -> None:
        settings = self.controller.settings
        settings.recent_files = remove_recent(settings.recent_files, path)
        self.controller.save_settings()
        self._rebuild_recent_menu()
        self._note(f"已从最近打开移除：{Path(path).name}")

    def _manage_recent_files(self) -> None:
        """批量管理：勾选删除或清空最近打开记录（不动磁盘文件）。"""
        settings = self.controller.settings
        paths = list(settings.recent_files)
        if not paths:
            self._note("没有最近打开记录")
            return
        dialog = RecentFilesDialog(paths, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.action == "clear":
            settings.recent_files = []
            self._note("已清空最近打开记录")
        else:
            for path in dialog.selected:
                settings.recent_files = remove_recent(settings.recent_files, path)
            self._note(f"已从最近打开移除 {len(dialog.selected)} 条记录")
        self.controller.save_settings()
        self._rebuild_recent_menu()

    def _open_recent(self, path: str) -> None:
        """从最近菜单重开：文件没了就提示，不崩溃。"""
        target = Path(path)
        if target.exists():
            self._enter_document(target)
        else:
            self._note(f"文件不存在：{path}", error=True)

    def _open_pdf(self) -> bool:
        suffixes = supported_suffixes()
        pattern = " ".join(f"*{suffix}" for suffix in suffixes)
        title = "打开 PDF" if suffixes == (".pdf",) else "打开文件"
        name_filter = ("仅 PDF (*.pdf)" if suffixes == (".pdf",)
                       else f"所有支持的文件 ({pattern})")
        path, _filter = QFileDialog.getOpenFileName(
            self, title, str(self._open_pdf_start_dir()), name_filter)
        if not path:
            return False
        return self._enter_document(Path(path))

    def _open_pdf_start_dir(self) -> Path:
        """打开对话框起点：当前 PDF 目录 → 最近打开的第一条目录 → 程序根目录。

        目录已被删除/改名时继续往后退，保证对话框永远有一个真实存在的起点。
        """
        candidates: list[Path] = []
        current = self.pdf_view.pdf_path
        if current is not None:
            candidates.append(Path(current).parent)
        for item in self.controller.settings.recent_files:
            candidates.append(Path(item).parent)
        candidates.append(app_base_dir())
        for folder in candidates:
            try:
                if folder.is_dir():
                    return folder
            except OSError:
                continue
        return app_base_dir()

    # --- 拖放打开 ---

    def _pdf_paths_from_mime(self, mime) -> list[Path]:
        """拖放数据的判定口（测试可直接喂 QMimeData）；支持后缀跟随当前产品。"""
        return local_document_paths(mime)

    def _image_paths_from_mime(self, mime) -> list[Path]:
        """草稿与 PDF 页都接受拖入图片；文本/导图阅读器不接。"""
        if self.mode == "draft":
            return local_image_paths(mime)
        if (self.mode == "document"
                and getattr(self.pdf_view, "kind", "pdf") == "pdf"):
            return local_image_paths(mime)
        return []

    def dragEnterEvent(self, ev) -> None:
        """草稿接图片，文档模式接支持的文件；其余原样拒绝。"""
        if (self._pdf_paths_from_mime(ev.mimeData())
                or self._image_paths_from_mime(ev.mimeData())):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev) -> None:
        if (self._pdf_paths_from_mime(ev.mimeData())
                or self._image_paths_from_mime(ev.mimeData())):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev) -> None:
        """草稿/PDF 页落下图片就插入；文档模式落下的第一个文件直接打开。"""
        images = self._image_paths_from_mime(ev.mimeData())
        if images:
            ev.acceptProposedAction()
            if self.mode == "document":
                if not self.pdf_view.insert_image_file(images[0]):
                    self._note(getattr(self.pdf_view, "image_error", "")
                               or "插入图片失败", error=True)
                    return
                self._apply_tool("select")
                self._note(f"已插入 {images[0].name}")
                return
            image = QImage(str(images[0]))
            self._insert_image_to_draft(image, images[0].name)
            return
        paths = self._pdf_paths_from_mime(ev.mimeData())
        if not paths:
            ev.ignore()
            return
        ev.acceptProposedAction()
        self._enter_document(paths[0])

    def _save_annotations(self) -> bool:
        """手动保存（Ctrl+S）：提交 sidecar 并清掉自动暂存。"""
        if getattr(self.pdf_view, "kind", "pdf") != "pdf":
            self._note("当前格式是只读阅读，没有批注需要保存", error=True)
            return False
        if self.pdf_view.pdf_path is None or self.pdf_view.document is None:
            self._note("没有打开的 PDF，无法保存批注", error=True)
            return False
        self.pdf_view.commit_text_edit()
        if not self._commit_sidecar():
            return False
        self._note(f"已保存批注 {datetime.now().strftime('%H:%M:%S')}")
        return True

    def _export_pdf(self) -> bool:
        """导出副本：默认 <原名>-批注.pdf，绝不覆盖原文件。"""
        view = self.pdf_view
        if getattr(view, "kind", "pdf") != "pdf":
            self._note("当前格式是只读阅读，无法导出批注 PDF", error=True)
            return False
        if view.source is None or view.document is None or view.pdf_path is None:
            self._note("没有打开的 PDF，无法导出", error=True)
            return False
        default = view.pdf_path.with_name(f"{view.pdf_path.stem}-批注.pdf")
        out, _filter = QFileDialog.getSaveFileName(self, "导出 PDF", str(default),
                                                   "PDF (*.pdf)")
        if not out:
            return False
        try:
            view.commit_text_edit()
            pages = view.document.pages
            strokes = [page.strokes for page in pages]
            notes = [page.notes for page in pages]
            texts = [page.texts for page in pages]
            images = [page.images for page in pages]
            if any(notes) or any(texts) or any(images):
                export_with_notes(view.source, strokes, notes, out,
                                  pages_texts=texts, pages_images=images,
                                  assets_path=view.pdf_path)
            else:
                export_with_strokes(view.source, strokes, out)
        except (PdfError, OSError) as exc:
            self._note(str(exc), error=True)
            return False
        self._note(f"已导出 {out}")
        if getattr(self.controller.settings, "open_export_folder", False):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out).parent)))
        return True

    def _page_count(self) -> int:
        document = self.pdf_view.document
        return len(document.pages) if document is not None else 0

    def _step_page(self, delta: int) -> None:
        """翻页：越界钳在 0..页数-1，goto_page 本身也忽略越界。"""
        if self.mode != "document":
            self._note("文档模式下才能翻页")
            return
        count = self._page_count()
        if count == 0:
            return
        index = min(count - 1, max(0, self.pdf_view.current_page + delta))
        self.pdf_view.goto_page(index)

    def _goto_page(self) -> None:
        if self.mode != "document":
            self._note("文档模式下才能翻页")
            return
        count = self._page_count()
        if count == 0:
            return
        value, ok = QInputDialog.getInt(self, "跳转到页", f"页码（1–{count}）：",
                                        self.pdf_view.current_page + 1, 1, count)
        if ok:
            self.pdf_view.goto_page(value - 1)

    def _goto_first_page(self) -> None:
        """Home：回第 1 页（文档专属快捷键，草稿模式静默）。"""
        if self.mode != "document":
            return
        self.pdf_view.goto_page(0)

    def _goto_last_page(self) -> None:
        """End：跳到末页；页数未知时什么也不做。"""
        if self.mode != "document":
            return
        count = self._page_count()
        if count:
            self.pdf_view.goto_page(count - 1)

    def _zoom_by(self, factor: float) -> None:
        if self.mode != "document":
            self._note("文档模式下才能缩放 PDF")
            return
        self.pdf_view.set_zoom(self.pdf_view.zoom * factor)

    def _set_pdf_zoom(self, zoom: float) -> None:
        if self.mode != "document":
            self._note("文档模式下才能缩放 PDF")
            return
        self.pdf_view.set_zoom(zoom)

    def _fit_page_width(self) -> None:
        """适应页宽：PDF 交给视图按页宽重算；文本阅读器等价于 100%。"""
        if self.mode != "document":
            self._note("文档模式下才能缩放")
            return
        if getattr(self.pdf_view, "kind", "pdf") != "pdf":
            self.pdf_view.fit_width()
            return
        if not self.pdf_view.document:
            self._note("文档模式下才能缩放 PDF")
            return
        # 交给视图：它会按当前页宽重算并保持 auto_fit 打开
        self.pdf_view.fit_width()

    # --- 置顶 / 状态栏 ---

    def set_always_on_top(self, on: bool) -> None:
        """置顶：Win32 原生切 TopMost，不隐藏窗口（setWindowFlag 会）。

        隐藏再显示会闪一下，并让视图在重新布局时重算适应页宽；原生路径
        只改 Z 序，几何、滚动位置与 PDF 缩放都原地不动。
        """
        on = bool(on)
        if not set_native_topmost(self, on):
            # 非 Windows/句柄不可用：退回 Qt 标志（会闪，但至少功能可用）
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
            self.show()
            self.raise_()
        self._always_on_top = on
        if self.titlebar.pin_button.isChecked() != on:
            self.titlebar.pin_button.blockSignals(True)
            self.titlebar.pin_button.setChecked(on)
            self.titlebar.pin_button.blockSignals(False)

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _toggle_fullscreen(self) -> None:
        """F11 / 视图菜单：全屏与还原；全屏期间 Esc 也是出口。"""
        if self.isFullScreen():
            self._leave_fullscreen()
            return
        self.showFullScreen()
        self._esc_shortcut.setEnabled(True)

    def _leave_fullscreen(self) -> None:
        """Esc：只在全屏时生效（非全屏时该快捷键是禁用的）。"""
        if self.isFullScreen():
            self.showNormal()
        self._esc_shortcut.setEnabled(False)

    def changeEvent(self, ev) -> None:
        if ev.type() == QEvent.WindowStateChange:
            self.titlebar.set_maximized(self.isMaximized())
            esc = getattr(self, "_esc_shortcut", None)
            if esc is not None:                 # 全屏状态也走这条兜底（最大化→全屏等）
                esc.setEnabled(self.isFullScreen())
            if self.isMinimized():
                self._save_pdf_position_now()   # 最小化后再被强杀也不丢位置
        super().changeEvent(ev)

    def hideEvent(self, ev) -> None:
        """收进托盘/隐藏窗口时先把阅读位置写盘。"""
        self._save_pdf_position_now()
        super().hideEvent(ev)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        enable_native_resize(self)
        apply_rounded_corners(self)

    def nativeEvent(self, event_type, message):
        """无边框窗口的边缘缩放：告诉 Windows 光标落在哪条边上（WM_NCHITTEST）。"""
        if _QUIT_ALL_MSG_ID and event_type == b"windows_generic_MSG":
            try:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
            except Exception:
                msg = None
            if msg is not None and msg.message == _QUIT_ALL_MSG_ID:
                if int(msg.wParam) != os.getpid():
                    QTimer.singleShot(0, self._quit_from_other_instance)
                return True, 0
        hit = native_hit_test(self, event_type, message)
        if hit is not None:
            return True, hit
        return super().nativeEvent(event_type, message)

    def _on_width_chosen(self, width: float) -> None:
        self.controller.settings.width = float(width)     # 关窗时随配置一起落盘
        self._reflect_bar(self.view.tool, float(width))

    # --- 草稿纸停靠（文档左 / 草稿右）---

    def _toggle_scratch_window(self) -> None:
        """Alt+Q / 工具栏草稿纸：已停靠就回到主窗那个分屏，否则显隐浮窗。"""
        if self._activate_scratch_dock():
            return
        self.scratch.toggle()

    def _dock_scratch(self) -> None:
        """把整块草稿纸停靠到当前文档右栏；不新建标签、不复制草稿。"""
        if self._scratch_dock is not None:
            self._activate_scratch_dock()
            return
        session = self._active_session
        if (self.mode != "document" or session is None
                or self._is_note_session(session)
                or not hasattr(session.pane, "show_note")):
            QMessageBox.information(
                self.scratch.window, "草稿纸分屏",
                "先在主窗打开一个文档（PDF / 思维导图 / Word / 文本），"
                "再点分屏把草稿纸放到右侧。")
            return
        self._close_note_split()          # 右栏只留一个（笔记卡与草稿纸互斥）
        session = self._active_session
        if session is None or not hasattr(session.pane, "show_note"):
            return
        pane = session.pane
        view = session.view
        position = view.current_position()
        prev_auto_fit = bool(getattr(view, "auto_fit", False))
        # 分屏期间冻结缩放：视口变窄时文档不会突然重排
        try:
            view.set_zoom(view.zoom)
        except (AttributeError, TypeError, ValueError):
            pass

        dock = self.scratch.window.build_dock()
        self._scratch_dock = dock
        self._scratch_dock_pane = pane
        self._scratch_dock_session = session
        self._scratch_dock_prev_auto_fit = prev_auto_fit
        self._scratch_dock_fit_slot = lambda s=session: s.view.fit_width()
        self._scratch_dock_ratio_slot = (
            lambda ratio: setattr(self, "_scratch_dock_ratio", float(ratio)))
        pane.fitRequested.connect(self._scratch_dock_fit_slot)
        pane.ratioChanged.connect(self._scratch_dock_ratio_slot)
        pane.set_note_title(session.display_name)
        pane.show_note(dock, self._scratch_dock_ratio)
        self._restore_split_position(
            view, dict(position, auto_fit=False) if position else None)
        self.scratch.hide()
        self._activate_scratch_dock()      # 浮窗一收，把主窗提到前面并聚焦草稿
        self._note("草稿纸已停靠到右侧；Alt+Q 回到这里，点「还原浮窗」可恢复浮窗")

    def _undock_scratch(self, show_window: bool = True) -> bool:
        """把草稿纸从右栏搬回浮窗；文档缩放/阅读位置按停靠前恢复。"""
        dock = self._scratch_dock
        if dock is None:
            return False
        pane = self._scratch_dock_pane
        session = self._scratch_dock_session
        prev_auto_fit = self._scratch_dock_prev_auto_fit
        view = getattr(session, "view", None)
        position = view.current_position() if view is not None else None
        self._scratch_dock = None
        self._scratch_dock_pane = None
        self._scratch_dock_session = None
        self._scratch_dock_prev_auto_fit = None
        if pane is not None:
            try:
                for signal, slot in ((pane.fitRequested,
                                      self._scratch_dock_fit_slot),
                                     (pane.ratioChanged,
                                      self._scratch_dock_ratio_slot)):
                    if slot is None:
                        continue
                    try:
                        signal.disconnect(slot)
                    except (RuntimeError, TypeError):
                        pass
                pane.hide_note()
            except RuntimeError:
                pass
        self._scratch_dock_fit_slot = None
        self._scratch_dock_ratio_slot = None
        self.scratch.window.release_dock()
        if show_window:
            self.scratch.show()
        else:
            self.scratch.hide()
        if view is not None:
            if prev_auto_fit:
                try:
                    view.fit_width()
                except RuntimeError:
                    pass
            self._restore_split_position(
                view, dict(position, auto_fit=bool(prev_auto_fit))
                if position else None)
        return True

    def _activate_scratch_dock(self) -> bool:
        """停靠中的草稿纸就是主窗的一部分：把主窗/对应标签提到前面。"""
        if self._scratch_dock is None:
            return False
        entry = self._entry_for_session(self._scratch_dock_session)
        if entry is not None and self.tabs.current_entry is not entry:
            self.tabs.set_current_entry(entry)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self._scratch_dock.focus_view()
        return True

    def _new_scratch_draft(self) -> None:
        """托盘「新建草稿」：停靠时只换右栏画布，浮窗时按原样弹出。"""
        if self._scratch_dock is not None:
            self.scratch.window.reset_document()
            self._activate_scratch_dock()
            return
        self.scratch.new_draft()

    # --- 设置对话框 ---

    def _open_settings_from_scratch(self) -> None:
        """草稿窗齿轮：对话框挂草稿窗，才不会被置顶的草稿窗压住。"""
        self._open_settings(self.scratch.window)

    def _open_settings(self, parent: QWidget | None = None) -> None:
        """齿轮 / Ctrl+,：接受后把绘制参数与热键一起落地，两个视图都跟上。"""
        if self.settings_dialog is not None:
            self.settings_dialog.deleteLater()     # 上一次的对话框已关，别越积越多
        dialog = SettingsDialog(self.controller.settings, parent or self,
                                settings_path=self.controller.settings_path)
        self.settings_dialog = dialog              # 留引用，别让 Python 回收
        # 用窗口模态而不是应用模态：设置开着时，全屏截图遮罩仍能盖在它上面
        # 被框选（Alt+A→S），否则应用模态会禁用所有其它顶层窗口。
        dialog.setWindowModality(Qt.WindowModal)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        # 阅读模式：先落字段，变了才切视图并重渲；_set_page_mode 自带一次保存，
        # 末尾的统一 save_settings() 会把其余字段一并写盘
        old_page_mode = getattr(self.controller.settings, "page_mode", "normal")
        self.controller.settings.page_mode = values.page_mode
        if values.page_mode != old_page_mode:
            self._set_page_mode(values.page_mode)
        old_hotkey = self.controller.settings.scratch_hotkey
        old_main_hotkey = self.controller.settings.main_hotkey
        old_position = self._saved_toolbar_position()

        self.view.set_color(values.color)
        self.view.set_width(values.width)
        self.view.set_clear_key(values.clear_key)
        # set_clear_key 会重建视图内建的 QShortcut，这里再关一次：清屏统一走
        # 应用级 eventFilter，避免起草稿窗/主窗双触发
        self.view.set_clear_shortcut_enabled(False)
        # 文档视图吃同一份绘制参数与清屏键
        self.pdf_view.set_color(values.color)
        self.pdf_view.set_width(values.width)
        self.view.set_ink_smoothing(values.ink_smoothing)
        self.pdf_view.set_ink_smoothing(values.ink_smoothing)
        self._apply_pressure_setting(values.pressure_enabled)
        self._apply_highlighter_color(values.highlighter_color)
        # 就地改字段：ScratchController 拿的是同一个 Settings 对象（换对象它就看不到新值）
        self.controller.settings.color = self.view.color
        self.controller.settings.width = self.view.width
        self.controller.settings.width_index = values.width_index
        self.controller.settings.clear_key = self.view.clear_key
        self.controller.settings.scratch_hotkey = values.scratch_hotkey
        self.controller.settings.main_hotkey = values.main_hotkey
        self.controller.settings.pdf_autosave = values.pdf_autosave
        self.controller.settings.ink_smoothing = values.ink_smoothing
        self.controller.settings.pressure_enabled = values.pressure_enabled
        self.controller.settings.highlighter_color = values.highlighter_color
        self.controller.settings.note_paper = values.note_paper
        self.controller.settings.close_to_tray = values.close_to_tray
        self.controller.settings.close_prompt = values.close_prompt
        self.controller.settings.open_export_folder = values.open_export_folder
        self.controller.settings.extra = dict(values.extra)
        self._configure_autosave()               # 设置面板改了开关就立即生效
        self.toolbox.reflect_width(self.view.width)
        # 设置面板只改画笔色；荧光笔色保留（工具栏在荧光笔工具下单独改）
        self.toolbox.reflect_color(self.view.color, "pen")
        self.toolbox.reflect_color(self.view.highlighter_color, "highlighter")
        self.toolbox.reflect_tool(self.view.tool)
        self.menubar.set_width_index(values.width_index)
        # 草稿窗已建出来才同步；没建的话下次创建会从同一份 settings 里读到
        self.scratch.apply_settings(
            self.view.color, self.view.width, self.view.clear_key,
            values.ink_smoothing,
            highlighter_color=values.highlighter_color,
            pressure_enabled=values.pressure_enabled)

        # 工具栏位置：没变就只记值，变了才搬布局；收起状态以工具栏真实状态为准
        position = (values.toolbar_position if values.toolbar_position in TOOLBAR_POSITIONS
                    else "top")
        self.controller.settings.toolbar_position = position
        self.controller.settings.toolbar_collapsed = self.toolbox.collapsed
        if position != old_position:
            self._apply_toolbar_position(position)

        if values.scratch_hotkey != old_hotkey and values.main_hotkey != old_main_hotkey:
            # 两个都改了可能是互换：先全注销再注册，否则旧占用会让新组合 1409
            self.hotkey.unregister()
            self.main_hotkey.unregister()
            self._register_scratch_hotkey(values.scratch_hotkey)
            self._register_main_hotkey(values.main_hotkey)
        elif values.scratch_hotkey != old_hotkey:
            self._rebind_hotkey(values.scratch_hotkey)
        elif values.main_hotkey != old_main_hotkey:
            self._rebind_main_hotkey(values.main_hotkey)
        self.controller.save_settings()
        self.refresh_status()

    def _rebind_hotkey(self, sequence: str) -> None:
        """改绑草稿纸热键：注册失败就换程序内快捷键，不能静默失效。"""
        self.hotkey.unregister()
        self._register_scratch_hotkey(sequence)

    def _register_scratch_hotkey(self, sequence: str) -> bool:
        if not self.primary_instance:
            self.toolbox.scratch_button.setToolTip(
                "草稿纸（全局热键只在第一个实例生效）")
            return False
        if self.hotkey.register(self.winId(), sequence):
            if self.fallback_shortcut is not None:
                self.fallback_shortcut.setEnabled(False)    # 原生热键已回来，别再双触发
            self.toolbox.scratch_button.setToolTip(f"草稿纸 ({sequence})")
            return True
        self._note(f"全局热键 {sequence} 不可用（{self.hotkey.error_message}）；"
                   f"已回退为程序内快捷键", error=True)
        self._note_timer.stop()          # 热键失败是持续状态，别 4 秒后消失
        self.toolbox.scratch_button.setToolTip(
            f"草稿纸（全局热键 {sequence} 不可用，仅程序内快捷键）")
        if self.fallback_shortcut is None:
            self.fallback_shortcut = QShortcut(QKeySequence(sequence), self)
            self.fallback_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self.fallback_shortcut.activated.connect(self._toggle_scratch_window)
        else:
            self.fallback_shortcut.setKey(QKeySequence(sequence))
        self.fallback_shortcut.setEnabled(True)
        return False

    def _rebind_main_hotkey(self, sequence: str) -> None:
        """改绑主窗热键：失败只提示（托盘菜单仍能呼出主窗）。"""
        self.main_hotkey.unregister()
        self._register_main_hotkey(sequence)

    def _register_main_hotkey(self, sequence: str) -> bool:
        if not self.primary_instance:
            return False
        if self.main_hotkey.register(self.winId(), sequence):
            return True
        self._note(f"主窗热键 {sequence} 不可用（{self.main_hotkey.error_message}）；"
                   f"可用托盘菜单「显示主窗」", error=True)
        self._note_timer.stop()
        return False

    def status_text(self) -> str:
        """文档模式报页码/缩放/本页笔数；草稿模式保持 v1 的当前色/粗细/缩放/笔迹。"""
        if (self.mode == "document"
                and getattr(self.pdf_view, "kind", "pdf") != "pdf"):
            return self.pdf_view.status_text()
        if self.mode == "document" and self.pdf_view.document is not None:
            pages = self.pdf_view.document.pages
            if pages:
                index = min(self.pdf_view.current_page, len(pages) - 1)
                return self.DOC_STATUS_TEMPLATE.format(
                    page=index + 1, pages=len(pages),
                    zoom=self.pdf_view.zoom * 100.0,
                    count=len(pages[index].strokes))
        view = self.view
        return self.STATUS_TEMPLATE.format(
            color=view.tool_color, width=view.width,
            zoom=view.viewport_scale() * 100.0,
            count=len(self.controller.document.strokes))

    def refresh_status(self) -> None:
        self.status_label.setText(self.status_text())
        self._refresh_save_chip()

    # --- 草稿纸 / 托盘 / 退出（设计文档 §15）---

    def _note(self, text: str, error: bool = False) -> None:
        """状态栏只显示最新一条提示（不再「；」累加），4 秒后自动清空。

        持久状态交给右侧保存角标；每条提示都重启清除表，免得旧计时器把
        后到的提示提前擦掉。
        """
        self.message_label.setStyleSheet(NOTE_ERROR_COLOR if error else "")
        self.message_label.setText(text)
        timer = getattr(self, "_note_timer", None)
        if timer is not None:
            timer.start(NOTE_CLEAR_MS)

    def _clear_note(self) -> None:
        """提示计时到点：清空文字与颜色（保存角标不受影响）。"""
        self.message_label.setText("")
        self.message_label.setStyleSheet("")

    def start_hotkey(self) -> bool:
        """按 settings.scratch_hotkey 注册全局热键；失败不静默，回退到程序内快捷键。"""
        return self._register_scratch_hotkey(self.controller.settings.scratch_hotkey)

    def start_main_hotkey(self) -> bool:
        """按 settings.main_hotkey 注册“呼出主窗”热键；失败只提示，不拦住启动。"""
        return self._register_main_hotkey(self.controller.settings.main_hotkey)

    def start_screenshot_hotkey(self) -> bool:
        """注册全局一步截图热键（仅主实例）；失败只提示，工具栏/菜单仍可用。"""
        if not self.primary_instance:
            return False
        if self.screenshot_hotkey.register(self.winId(), SCREENSHOT_HOTKEY):
            return True
        self._note(f"截图热键 {SCREENSHOT_HOTKEY} 不可用"
                   f"（{self.screenshot_hotkey.error_message}）；"
                   f"可用托盘菜单或工具栏截图", error=True)
        self._note_timer.stop()
        return False

    def start_tray(self) -> bool:
        """建托盘；系统没有托盘就只提示一句，进程与热键照常（不崩、不静默）。"""
        if self.tray is not None:
            return True
        if not QSystemTrayIcon.isSystemTrayAvailable():
            if self.hotkey.is_registered or self.main_hotkey.is_registered:
                self._note("系统托盘不可用：关主窗后进程仍在后台，全局热键仍可呼出窗口")
            else:
                self._note("系统托盘不可用，全局热键也没注册上：关主窗将直接退出")
            return False
        self.tray = TrayIcon(self)
        self.tray.showMainRequested.connect(self.restore_main)
        self.tray.newDraftRequested.connect(self._new_scratch_draft)
        self.tray.quitRequested.connect(self.quit_all_apps)
        self.tray.show()
        return True

    def restore_main(self) -> None:
        """呼出主窗：最小化才用 showNormal（还原尺寸）；隐藏的最大化窗口保持最大化。"""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        restore = self._close_suspended_restore
        if restore is not None:
            self._close_suspended_restore = None
            QTimer.singleShot(
                0, lambda r=restore: self._resume_after_close_dialog(*r))

    def quit_all_apps(self) -> None:
        """所有退出入口：先广播让其他墨页进程退出，再收口本进程。"""
        broadcast_quit_all()
        self._root_window().quit_app()

    def _root_window(self):
        """同进程多窗口时以主窗为准；副实例进程的 registry.primary 就是自己。"""
        registry = self.window_registry
        return getattr(registry, "primary", None) or self

    def _quit_from_other_instance(self) -> None:
        """收到别的实例的“退出全部”广播：每个进程只处理一次。"""
        global _QUIT_ALL_HANDLING
        if _QUIT_ALL_HANDLING:
            return
        _QUIT_ALL_HANDLING = True
        target = self._root_window()
        try:
            if getattr(target, "_quitting", False):
                return
            target.quit_app()
        finally:
            if not getattr(target, "_quitting", False):
                _QUIT_ALL_HANDLING = False

    def quit_app(self) -> None:
        """托盘「退出」：存配置、注销热键、撤掉托盘，再退进程（草稿不落盘）。"""
        # 二级窗口先收口；任一窗口取消保存就中止退出
        if self.window_registry is not None and not self.window_registry.close_all():
            return
        # 逐个会话收口：保存/放弃/取消；取消则不退出
        if not self._flush_all_sessions():
            return
        if not self._close_word_editors():
            return
        self._close_convert_center()
        self._wait_find_workers()                # 取消搜索并等线程收尾，别活过应用
        self._quitting = True                   # app.quit() 还会走 closeEvent
        self.controller.save_settings()
        self.hotkey.unregister()
        self.main_hotkey.unregister()
        self.screenshot_hotkey.unregister()
        if self.fallback_shortcut is not None:
            self.fallback_shortcut.setEnabled(False)
        self.scratch.hide()
        if self.tray is not None:
            self.tray.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # --- PDF 批注自动保存（停笔 debounce + 崩溃缓存）---

    def _on_strokes_changed(self) -> None:
        """笔迹变了：标记未提交，并交给停笔 debounce 写自动暂存。"""
        self._dirty = True
        self._refresh_title()
        self._refresh_save_chip()
        self.pdf_autosave.notify_changed()

    def _configure_autosave(self) -> None:
        """按当前配置开关自动暂存；每个会话各自一个 saver。"""
        enabled = bool(self.controller.settings.pdf_autosave)
        if (self._active_session is not None
                and not self._is_note_session(self._active_session)):
            session = self._active_session
            session.autosave.configure(
                enabled, lambda s=session: self._auto_save_cache_for(s))
            self.pdf_autosave = session.autosave
        else:
            self._idle_autosave.configure(enabled, None)
            self.pdf_autosave = self._idle_autosave

    def _auto_save_cache(self) -> None:
        """自动暂存动作（兼容旧调用）：写当前活动会话的崩溃缓存。"""
        if self._active_session is not None:
            self._auto_save_cache_for(self._active_session)
            return
        path, document = self.pdf_view.pdf_path, self.pdf_view.document
        if path is None or document is None:
            raise RuntimeError("没有打开的文档，无法自动暂存批注")
        write_cache(path, document, self._autosave_base)

    def _remember_pdf_position(self) -> None:
        """把当前文档的阅读位置记进配置（翻页/缩放/滚动/关闭时调用）。"""
        if (self._active_session is not None
                and self._is_note_session(self._active_session)):
            return
        position = self.pdf_view.current_position()
        path = self.pdf_view.pdf_path
        if position is not None and path is not None:
            remember_pdf_position(self.controller.settings, path, **position)

    def _save_pdf_position_now(self) -> None:
        """立即把阅读位置写盘；没有打开的文档就跳过。"""
        self._position_timer.stop()
        if self.pdf_view.pdf_path is None:
            return
        self._remember_pdf_position()
        self.controller.save_settings()

    def _schedule_position_save(self) -> None:
        """缩放/滚动会连续触发：停 600ms 再写，避免每滚一格都落盘。"""
        if self.pdf_view.pdf_path is not None:
            self._position_timer.start()

    def _commit_sidecar(self) -> bool:
        """把当前批注写进 sidecar，成功后清掉自动暂存。失败只提示，不抛。"""
        path, document = self.pdf_view.pdf_path, self.pdf_view.document
        if path is None or document is None:
            return True
        try:
            pdfstore.save_sidecar(path, document)
        except Exception as exc:
            self._note(f"保存批注失败：{exc}", error=True)
            return False
        clear_cache(path, self._autosave_base)
        hook = getattr(self.pdf_view, "mark_sidecar_saved", None)
        if hook is not None:
            hook()
        self.pdf_autosave.cancel()               # 别再让待定定时器写回旧缓存
        self._dirty = False
        self._saved_at = datetime.now().strftime("%H:%M:%S")
        self._refresh_title()
        self._refresh_save_chip()
        return True

    def _confirm_unsaved(self, action: str):
        """有未保存批注时问保存/放弃/取消；返回 QMessageBox 的标准按钮。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("未保存的批注")
        box.setText(f"当前 PDF 有未保存的批注。{action}")
        box.setInformativeText(
            "「保存」先写 sidecar 再继续；「放弃」丢弃本次修改；「取消」留在当前文档。")
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Save)
        return box.exec()

    def _flush_autosave(self) -> bool:
        """切换/关闭/退出前收口：待定暂存先落缓存，再按 dirty 决定提交或询问。

        返回 False 表示用户在询问里点了「取消」，调用方必须中止动作。
        """
        hook = getattr(self.pdf_view, "commit_text_edit", None)
        if callable(hook):
            hook()                      # 文字输入中直接切走/关窗：先提交再判 dirty
        if (self._active_session is not None
                and self._is_note_session(self._active_session)):
            return True                 # 笔记只在内存里，没有 sidecar/暂存
        if self.pdf_view.document is None:
            return True
        if self.pdf_autosave.pending:
            self.pdf_autosave.flush()            # 先写缓存，提交失败也不丢
        if not self._dirty:
            return True
        action = self._confirm_unsaved("切换或关闭前需要处理。")
        if action == QMessageBox.Cancel:
            return False
        if action == QMessageBox.Save:
            return self._commit_sidecar()
        # Discard：用户明确放弃本次修改，缓存也一并清掉
        path = self.pdf_view.pdf_path
        if path is not None:
            clear_cache(path, self._autosave_base)
        self._dirty = False
        self._saved_at = None
        self._refresh_title()
        self._refresh_save_chip()
        return True

    def _detach_app_filters(self) -> None:
        """窗口真正销毁前摘掉应用级过滤器，别让多个窗口的过滤器在退出时互相转发。"""
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.removeEventFilter(self)
        except RuntimeError:
            pass

    def _prepare_for_close_dialog(self):
        """关窗确认前收起 topmost 批注层/贴图，返回取消时要恢复的状态。"""
        annotate = getattr(self, "_annotate", None)
        overlay = getattr(annotate, "overlay", None)
        restore_mode = getattr(overlay, "mode", "hidden")
        if annotate is not None and restore_mode != "hidden":
            try:
                annotate.hide()
            except Exception:
                restore_mode = "hidden"
        screenshot = getattr(self, "_screenshot", None)
        if screenshot is not None:
            try:
                screenshot.suspend_topmost()
            except Exception:
                pass
        return annotate, restore_mode, screenshot

    def _ensure_interactive(self) -> None:
        """关窗确认链路的安全兜底：无条件把主窗恢复成可交互状态。

        除了重新 Enable 主窗，还会清掉可能残留在模态栈里的关窗确认框：
        它若以 ApplicationModal 身份被隐藏/延后销毁，Qt 会继续把事件拦在
        主窗之外，表现就是“能拖边框、点哪都没反应”。
        """
        app = QApplication.instance()
        if app is not None:
            modal = app.activeModalWidget()
            if modal is not None and (
                    isinstance(modal, dialogs.CloseConfirmDialog)
                    or getattr(modal, "parentWidget", lambda: None)() is self):
                try:
                    modal.setWindowModality(Qt.NonModal)
                    modal.hide()
                    modal.deleteLater()
                except (RuntimeError, AttributeError):
                    pass
        try:
            self.setEnabled(True)
        except (RuntimeError, AttributeError):
            pass
        # 释放可能残留的原生鼠标捕获（按钮/覆盖层在 mouseRelease 前被隐藏时
        # 会留下 SetCapture，之后所有鼠标事件都被这个已消失的窗口吃掉）。
        try:
            import ctypes
            ctypes.windll.user32.ReleaseCapture()
        except Exception:
            pass
        try:
            self.raise_()
            self.activateWindow()
        except (RuntimeError, AttributeError):
            pass
        try:
            if self.isVisible():
                focus = self.view if self.mode == "draft" else self.pdf_view
                focus.setFocus(Qt.OtherFocusReason)
        except (RuntimeError, AttributeError):
            pass
        try:
            QApplication.restoreOverrideCursor()
        except (RuntimeError, AttributeError):
            pass

    def _on_close_action_chosen(self, action: str) -> None:
        prompt = self._close_prompt
        self._close_prompt = None
        self._close_dialog = None
        restore = self._close_dialog_restore
        self._close_dialog_restore = None
        if prompt is not None:
            prompt.hide()
            QTimer.singleShot(0, prompt.deleteLater)
        self._close_confirm_pending = False
        if action == "quit":
            self.quit_all_apps()
            return
        if action == "tray":
            self._close_suspended_restore = restore
            self._hide_to_tray_now()
            self._ensure_interactive()
            return
        if action == "cancel":
            self._close_suspended_restore = None
            try:
                annotate = getattr(self, "_annotate", None)
                if annotate is not None:
                    annotate.hide()
            except Exception:
                pass
            try:
                screenshot = getattr(self, "_screenshot", None)
                if screenshot is not None:
                    screenshot.suspend_topmost()
            except Exception:
                pass
            self._ensure_interactive()
            QTimer.singleShot(0, self._ensure_interactive)
            QTimer.singleShot(120, self._ensure_interactive)
            return
        self._ensure_interactive()

    def _show_close_confirm(self, can_recover: bool, extra_windows: int) -> None:
        """在主窗内部盖一层关闭遮罩，不创建系统窗口、不进模态栈。"""
        if self._close_prompt is not None:
            return
        try:
            restore = self._prepare_for_close_dialog()
        except Exception as exc:
            self._close_confirm_pending = False
            self._note("关闭确认初始化失败，已跳过", error=True)
            return
        self._close_dialog_restore = restore
        prompt = dialogs.ClosePrompt(
            APP_NAME, can_minimize=can_recover, parent=self,
            extra_windows=extra_windows)
        self._close_prompt = prompt
        self._close_dialog = prompt          # 兼容旧测试/调用方的名字
        prompt.actionChosen.connect(self._on_close_action_chosen)
        prompt.setGeometry(self.rect())
        prompt.show()
        prompt.raise_()
        prompt.quit_button.setFocus(Qt.OtherFocusReason)

    def _resume_after_close_dialog(self, annotate, mode: str, screenshot) -> None:
        """关窗确认框关掉后再恢复被临时收起的批注层/贴图。"""
        if annotate is not None and mode in ("draw", "passthrough"):
            try:
                if mode == "passthrough":
                    annotate.enter_passthrough()
                else:
                    annotate.enter_draw()
            except Exception:
                pass
        if screenshot is not None:
            try:
                screenshot.resume_topmost()
            except Exception:
                pass

    def _schedule_close_confirm(self, can_recover: bool, extra_windows: int) -> None:
        """把确认框推迟到 closeEvent 返回之后；重复点关闭只弹一个。"""
        if self._close_confirm_pending:
            return
        self._close_confirm_pending = True
        QTimer.singleShot(
            0, lambda: self._run_close_confirm(can_recover, extra_windows))

    def _run_close_confirm(self, can_recover: bool, extra_windows: int) -> None:
        self._close_confirm_pending = False
        self._show_close_confirm(can_recover, extra_windows)

    def _hide_to_tray_now(self) -> None:
        """收进托盘：关掉笔记窗口/分屏并落盘，不退出进程。"""
        self._close_note_windows()
        self._close_note_split()
        self._remember_pdf_position()
        self.controller.save_settings()
        self._save_pdf_position_now()            # 关窗到托盘也算一次收口
        self.hide()
        self._show_tray_hint_once()

    def closeEvent(self, ev) -> None:
        """关主窗：先问「退出 / 最小化」，没退路时只能退出。"""
        if self._quitting:
            self._detach_app_filters()
            ev.accept()
            return
        if not self.primary:
            # 二级窗口没有托盘/热键：关之前收口自己的文档
            if not self._flush_all_sessions():
                ev.ignore()
                return
            self._close_note_windows()
            self._close_note_split()
            self._wait_find_workers()
            self.controller.save_settings()
            self._quitting = True
            if self.window_registry is not None:
                self.window_registry.unregister(self)
            self._detach_app_filters()
            ev.accept()
            return

        can_recover = (self.tray is not None or self.hotkey.is_registered
                       or self.main_hotkey.is_registered)
        action = None
        if bool(getattr(self.controller.settings, "close_prompt", True)):
            extra = len(self.window_registry.secondaries) \
                if self.window_registry is not None else 0
            # 不能在 closeEvent 里直接 exec() 模态框：Windows 上取消后窗口
            # 仍停在“关闭中”状态，点哪里都没反应。先忽略本次关闭，等事件
            # 派发结束再弹；取消就什么都不做，窗口原地留下。
            ev.ignore()
            self._schedule_close_confirm(can_recover, extra)
            return

        self._close_note_windows()
        self._close_note_split()
        if action == "tray":
            will_quit = False
        else:
            close_to_tray = getattr(self.controller.settings, "close_to_tray", True)
            will_quit = (not close_to_tray) or not can_recover
        if will_quit:
            # 真退出：赶在下面的 save 之前记位置（随后 flush 选「放弃」也已写盘）
            self._remember_pdf_position()
        self.controller.save_settings()
        if will_quit:
            # 隐藏了就再也叫不回来：没有托盘、两个热键也都没注册上，只能退出
            if not self._flush_all_sessions():   # 用户取消：窗口留下
                ev.ignore()
                return
            if not self._close_word_editors():
                ev.ignore()
                return
            self._close_convert_center()
            self._wait_find_workers()            # 搜索线程不允许活过应用
            self._quitting = True
            self.hotkey.unregister()
            self.main_hotkey.unregister()
            self.screenshot_hotkey.unregister()
            self.scratch.hide()
            self._detach_app_filters()
            app = QApplication.instance()
            if app is not None:
                app.quit()
            ev.accept()
            return
        ev.ignore()
        self._hide_to_tray_now()

    def _show_tray_hint_once(self) -> None:
        """第一次收进托盘时冒个气泡，免得用户以为程序退了（只弹一次）。"""
        if self._tray_hint_shown or self.tray is None:
            return
        self._tray_hint_shown = True
        try:
            self.tray.showMessage(
                TRAY_HINT_TITLE,
                TRAY_HINT_BODY.format(
                    scratch=self.controller.settings.scratch_hotkey,
                    main=self.controller.settings.main_hotkey),
                QSystemTrayIcon.Information, TRAY_HINT_MS)
        except Exception:                 # 托盘实现异常不该拦住关窗流程
            pass


def app_base_dir() -> Path:
    """配置/缓存根目录：源码运行=仓库根；PyInstaller 打包后=exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def apply_instance_features(window, primary_instance: bool) -> None:
    """主实例独占托盘/全局热键/全屏批注；副实例只装窗口内功能（含截图按钮）。"""
    if primary_instance:
        window.start_hotkey()
        window.start_main_hotkey()
        window.start_screenshot_hotkey()
        window.start_tray()
        annotate = install(window)
        window._annotate = annotate
        annotate.captured.connect(window._on_screenshot_captured)
        annotate.captureDone.connect(window._restore_after_screenshot)
        annotate.overlay.modeChanged.connect(window._sync_annotate_button)
    else:
        window._annotate = None
    # 截图本身不依赖全局热键：工具栏按钮/菜单在副实例仍可用
    window._screenshot = install_screenshot(window)


def _set_taskbar_app_id() -> None:
    """让 Windows 任务栏按墨页分组并使用窗口图标。

    源码版由 pythonw.exe 启动，不设显式 AppUserModelID 时 Windows 会把任务栏
    图标沿用 Python/IDLE 的；必须在建窗口前设置。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    global _QUIT_BROADCAST_ENABLED
    _QUIT_BROADCAST_ENABLED = True
    _set_taskbar_app_id()
    app = QApplication.instance() or QApplication(argv)
    # 多开：每次启动都建自己的窗口；只有抢到主实例锁的第一个进程注册
    # 托盘/全局热键/全屏批注（源码/打包共用一把锁）。
    instance_lock = single_instance.InstanceLock()
    primary_instance = instance_lock.acquire()
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(icons.app_icon())             # 任务栏/Alt-Tab 的应用图标
    app.setStyleSheet(theme.QSS)                    # 深灰苹果风（sketchpad/ui/theme.py）
    tooltips.install(app)                           # 所有控件悬浮 1 秒才出提示
    try:
        splash = show_launch_splash()               # 动画坏了也不能拦住启动
    except Exception:
        splash = None
    # 主实例关窗只隐藏（进程留驻，全局热键才有命在）；副实例关窗即退出
    app.setQuitOnLastWindowClosed(not primary_instance)
    base = app_base_dir()                           # data 跟着 exe 走（打包后也一样）
    # 配置固定写仓库根的 data/（打包后写 exe 同级的 data/）
    controller = AppController(base / DATA_DIR, persist_settings=primary_instance)
    window = MainWindow(controller, primary_instance=primary_instance)
    window._instance_lock = instance_lock if primary_instance else None
    screen = QGuiApplication.primaryScreen().availableGeometry()
    window.move(screen.center() - QPoint(window.width() // 2, window.height() // 2))
    window.show()
    apply_instance_features(window, primary_instance)
    if splash is not None:
        splash.finish()
    if not primary_instance:
        window._note("副实例：全局热键/全屏批注只在第一个实例；"
                     "文件→退出可结束全部实例")
    # 命令行/文件关联：墨页.exe xxx.pdf 直接打开（打包版也能用）
    for arg in argv[1:]:
        if not arg or arg.startswith("-"):
            continue
        candidate = Path(arg)
        if is_supported_document(candidate):
            QTimer.singleShot(0, lambda p=candidate: window._enter_document(p))
        else:
            window._note(f"忽略命令行参数（不支持的格式）：{arg}", error=True)
        break
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
