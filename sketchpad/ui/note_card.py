"""笔记卡片与遮罩：卡片自带一条紧凑工具栏，遮罩负责展开/收起的浮层动画。

卡片是独立组件——按钮只调用自己的 `NoteView`，从不回调主窗口；删除/关闭/放大
三条意图各走一个信号，由接线层（任务 5）决定真正做什么。遮罩只认「宿主控件
（PDF 视图栈）+ 设置」，在宿主内铺一层半透明 scrim，卡片浮在上面。

坐标约定：`open_note(note, anchor_view_pos)` 的锚点是宿主/遮罩坐标（卡片不用
锚点记录位置，笔记在 PDF 上的锚点由任务 4 的图钉负责）。

尺寸约定：`note.w/h` 恒是**设计版面** 560x720（决定 sidecar 语义与导出笔记页的
宽高比），卡片右下角手柄拖出来的是**显示尺寸**，只在遮罩里活一辈子（`_user_size`），
绝不写回 `note`。画布 `NoteView.fit_page` 把固定版面等比塞进视口，所以卡片拖大
= 版面等比放大，笔迹坐标一个都不用改。
"""
from __future__ import annotations

from pathlib import Path

import shiboken6
from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QLineF, QPoint,
                            QPropertyAnimation, QRect, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from sketchpad.core.notes import NOTE_H, NOTE_PAPERS, NOTE_W, Note
from sketchpad.core.settings import WIDTHS, Settings
from sketchpad.ui import icons
from sketchpad.ui import theme
from sketchpad.ui.frameless import (EdgeResizeFilter, apply_rounded_corners,
                                    enable_native_resize, install_window_border,
                                    native_hit_test)
from sketchpad.ui.note_canvas import NoteView
from sketchpad.ui.toolbox import PALETTE

# 纸色与可读墨色：浅纸写深字、深纸写浅字（与画布默认笔色 #e8eaed 同一套）
INK_ON_LIGHT = "#2c2c2e"
INK_ON_DARK = "#e8eaed"
LIGHT_PAPER_THRESHOLD = 190      # QColor.lightness()：比这更亮的笔在浅纸上糊成一片
DARK_PAPER_THRESHOLD = 60        # 比这更暗的笔在深纸上看不见

CARD_MIN_W, CARD_MIN_H = 420, 540    # 卡片最小可用尺寸（再小工具行就挤满）
# 分屏模式：卡片要能住进 340px 的右栏，工具行放进横向滚动区，不再把卡片撑宽。
SPLIT_MIN_W, SPLIT_MIN_H = 340, 300
SPLIT_HINT_W, SPLIT_HINT_H = 340, 400
TOOL_SIZE = 24                        # 卡片内图标按钮的点击区
TOOL_ICON = 16                        # 图标逻辑尺寸（比主工具栏小一档）
SWATCH_SIZE = 18                      # 色块直径（卡片窄，8 个要挤进一行）
WIDTH_RADII = (2.2, 3.6, 5.0, 6.4)    # 四档粗细圆点半径（与 toolbox 的宽度按钮一致）

HEADER_MARGINS = (10, 5, 6, 5)
TOOLS_MARGINS = (6, 3, 6, 4)
TOOLS_SPACING = 2
GROUP_GAP = 4                         # 工具行里「笔 / 色 / 粗细 / 撤销」之间的空隙

OPEN_MS = 200                         # 打开动画：锚点 24x24 → 基准矩形
CLOSE_MS = 160                        # 关闭动画：当前矩形 → 中心 24x24
EXPAND_MS = 220                       # 放大/还原动画
SCRIM_ALPHA = 90                      # 遮罩黑度：够压暗 PDF，仍看得见底下的页面
BASE_RATIO = 0.9                      # 基准矩形最多占宿主 90%
LARGE_RATIO = 0.8                     # 放大矩形约宿主 80%
LARGE_FALLBACK = (0.94, 0.96)         # 窄宿主上 80% 不比基准大时才启用这一档（都 > 基准的 90%）
MAX_BASE_RATIO = 0.96                 # 手柄能拖到的显示上限：宿主 96% 内的等比框
GRIP_SIZE = 16                        # 右下角缩放手柄的透明热区边长
GRIP_INSET = 1                        # 让开卡片的 1px 描边
GRIP_MIN_CARD = 48                    # 卡片比这更小（开合动画的 24x24 种子）时不亮出手柄
DEFAULT_NOTE_TITLE = "笔记"           # 没有自定义名字时的标题（也是 `Note.name` 为空时的回退）
RENAME_HINT = "双击重命名"             # 标题上的一行提示（tooltip 尾行）
NO_PDF_HINT = "先打开 PDF 再插文件"     # 没有 PDF 时无法定位资源目录（测试会断言这句）
IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.bmp *.webp)"

CARD_MIN_DISPLAY = QSize(CARD_MIN_W, CARD_MIN_H)    # 拖拽下限（CARD_MIN 本身就合 560:720）

# 工具行滚动区：只按 objectName 命中。分屏时工具行比右栏宽，横向滚动而不是把卡片撑宽；
# 宽卡片时工具行仍铺满整行（右端撤销组照旧贴右）。
_TOOLS_SCROLL_QSS = f"""
QScrollArea#NoteToolsScroll {{
    background: {theme.NOTE_TOOLS_BG};
    border: none;
}}
QScrollArea#NoteToolsScroll > QWidget > QWidget {{
    background: {theme.NOTE_TOOLS_BG};
}}
"""

# 独立笔记窗口：无边框 + 自绘 28px 标题行（与主窗 36px 标题栏同一套配色，只是矮一档）。
NOTE_WINDOW_TITLE_HEIGHT = 28
NOTE_WINDOW_MIN_W, NOTE_WINDOW_MIN_H = 260, 300    # 窗口缩放下限（用户要求能更小）
NOTE_WINDOW_BUTTON_W, NOTE_WINDOW_BUTTON_H = 28, 22
NOTE_WINDOW_QSS = f"""
QWidget#NoteWindow {{
    background: {theme.WINDOW_BG};
}}
QWidget#NoteWindowTitleBar {{
    background: {theme.TOOLBAR_BG};
    border-bottom: 1px solid {theme.BORDER};
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}}
QWidget#NoteWindowTitleBar QLabel#NoteWindowTitle {{
    color: {theme.TEXT_DIM};
    font-size: 12px;
    font-weight: 600;
}}
QToolButton#NoteWindowButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0;
}}
QToolButton#NoteWindowButton:hover {{ background: {theme.SURFACE_HOVER}; }}
QToolButton#NoteWindowButton:pressed {{ background: {theme.SURFACE_PRESSED}; }}
QToolButton#NoteWindowButton[close="true"]:hover {{ background: #e81123; }}
"""


def display_width(size: QSize) -> float:
    """把显示尺寸折成等比宽：取两轴里更小的一边，保证结果两轴都不超出 size。"""
    return min(float(size.width()), float(size.height()) * NOTE_W / NOTE_H)


def snap_display_size(width: float) -> QSize:
    """由宽算出显示尺寸——版面纵横比锁死后，宽是唯一的自由度。"""
    w = max(1, int(round(float(width))))
    return QSize(w, max(1, int(round(w * NOTE_H / NOTE_W))))


def clamp_display_size(size: QSize, min_size: QSize | None = None,
                       max_size: QSize | None = None) -> QSize:
    """把**显示尺寸**钳进 [min_size, max_size]，纵横比恒等于版面 560:720。

    拖不出任意形状：先把 size 折成等比宽（两轴取更受限的一边），再上下限夹，
    最后由宽反算高。设计版面 `note.w/h` 全程不参与，所以导出比例不会被动歪。
    """
    width = display_width(size)
    if max_size is not None:
        width = min(width, display_width(max_size))
    if min_size is not None:
        width = max(width, display_width(min_size))
    return snap_display_size(width)


def note_title(note: Note, display_name: str = "") -> str:
    """一张笔记的显示名：显式 `display_name` → `note.name` → 「笔记」。

    `Note.name` 由另一个任务在加，可能还没落地；这里一律 `getattr` 兜底，
    绝不 import 不存在的名字、也不假设字段一定在。
    """
    for candidate in (display_name, getattr(note, "name", "")):
        text = str(candidate or "").strip()
        if text:
            return text
    return DEFAULT_NOTE_TITLE


class NoteCard(QWidget):
    """一张笔记卡：标题行 + 工具行 + 笔记画布，右下角带一个可拖的缩放手柄。

    `contentChanged` 由画布 `changed` 转发（落笔/橡皮/撤销/换色/富内容都会发），接线层
    据此做自动暂存；其它信号是纯意图，卡片自己不关不删不挪位置。

    `resizeRequested` 也是纯意图：手柄只算出「想要多大的**显示尺寸**」，几何由遮
    罩负责摆，卡片自己从不 setGeometry（也绝不改 `note.w/h`）。

    `splitRequested` / `windowRequested` 对应卡头的「分屏 / 独立窗口」按钮：卡片
    只表达意图，真正的摆放由接线层决定。卡片里**没有**任何「插笔记」入口——笔记
    不能套笔记。
    """

    contentChanged = Signal()
    toggleExpandRequested = Signal()
    splitRequested = Signal()           # 卡头「分屏阅读」：只发意图
    windowRequested = Signal()          # 卡头「在独立窗口打开」：只发意图
    deleteRequested = Signal()
    closeRequested = Signal()
    resizeRequested = Signal(QSize)
    renameRequested = Signal()          # 双击标题：只表达「想改名」，改名本身归接线层

    def __init__(self, note: Note, settings: Settings, parent: QWidget | None = None,
                 display_name: str = "", pdf_path: Path | None = None) -> None:
        super().__init__(parent)
        self.note = note
        self.settings = settings
        # 富内容素材（图片/附件）都在 `<pdf>.sketch-assets/`：没有 PDF 就无处可放
        self.pdf_path = Path(pdf_path) if pdf_path is not None else None
        self._expanded = False
        self._max_display: QSize | None = None    # 显示上限：遮罩按宿主给，None = 不设限
        self._drag_delta_w = 0.0                  # 按下时「当前宽 - 手算宽」的补偿
        self._last_drag_size: QSize | None = None
        self._resize_enabled = True               # 独立窗口里由系统边框缩放，手柄要收起来
        self._display_name = note_title(note, display_name)
        self._split_mode = False                  # 分屏右栏：340px 也能住，工具行横向滚动
        self._syncing_tools = False

        self.setObjectName("NoteCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        # 不用 setMinimumSize：那会把浮层动画的 24x24 起始矩形直接钳回最小尺寸
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.view = NoteView(note, settings, self, pdf_path=self.pdf_path)
        self._build_header()
        self._build_tools()
        self._build_layout()
        self._build_grip()

        self.view.changed.connect(self._on_view_changed)
        # 名字只落在卡片的标题/提示上（模型的 Note.name 归接线层写），这里绝不动 note
        self.set_display_name(self._display_name)
        # 还原笔记自己的纸色（坏值在这里归一成 dark）；顺手保证墨色可读
        self.set_paper(note.paper)
        self._reflect_tools()
        QTimer.singleShot(0, self._sync_tools_scroll)

    # --- 尺寸 ---

    def minimumSizeHint(self) -> QSize:
        if self._split_mode:
            return QSize(SPLIT_MIN_W, SPLIT_MIN_H)
        return QSize(CARD_MIN_W, CARD_MIN_H)

    def sizeHint(self) -> QSize:
        if self._split_mode:
            return QSize(SPLIT_HINT_W, SPLIT_HINT_H)
        return QSize(int(NOTE_W), int(NOTE_H))

    def set_split_mode(self, on: bool) -> None:
        """分屏右栏模式：放宽/收起尺寸提示，工具行宽度交给滚动区处理。

        默认行为（遮罩/独立窗口）一点不动：关掉时仍是最小 420x540、建议 560x720。
        """
        on = bool(on)
        if on == self._split_mode:
            return
        self._split_mode = on
        self.updateGeometry()
        QTimer.singleShot(0, self._sync_tools_scroll)

    # --- 构建 ---

    def _header_button(self, icon, tooltip: str) -> QToolButton:
        # 尺寸/图标大小/焦点策略都与工具行按钮一致，_icon_button 已经设全
        return self._icon_button(self.header, icon, tooltip)

    def _icon_button(self, parent: QWidget, icon, tooltip: str) -> QToolButton:
        button = QToolButton(parent)
        button.setIcon(icon)
        button.setIconSize(QSize(TOOL_ICON, TOOL_ICON))
        button.setFixedSize(TOOL_SIZE, TOOL_SIZE)
        button.setToolTip(tooltip)
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _build_header(self) -> None:
        self.header = QWidget(self)
        self.header.setObjectName("NoteHeader")
        self.header.setAttribute(Qt.WA_StyledBackground, True)

        self.title_label = QLabel(DEFAULT_NOTE_TITLE, self.header)
        self.title_label.setObjectName("NoteTitle")
        self.title_label.setToolTip(f"笔记 {self.note.id}")
        self.title_label.setFocusPolicy(Qt.NoFocus)
        self.title_label.setCursor(Qt.PointingHandCursor)
        # 双击只认标题这一小块：eventFilter 比子类化/全局鼠标事件都窄，卡片本体不受影响
        self.title_label.installEventFilter(self)

        self.paper_button = self._header_button(
            icons.paper_icon(self.note.paper == "dark"), "切换纸张深浅")
        self.paper_button.clicked.connect(self._toggle_paper)

        self.split_button = self._header_button(icons.split_icon(), "分屏阅读")
        self.split_button.clicked.connect(self.splitRequested.emit)

        self.window_button = self._header_button(icons.popout_icon(), "在独立窗口打开")
        self.window_button.clicked.connect(self.windowRequested.emit)

        self.expand_button = self._header_button(icons.maximize_icon(), "放大卡片")
        self.expand_button.clicked.connect(self.toggleExpandRequested.emit)

        self.delete_button = self._header_button(icons.delete_icon(), "删除笔记")
        self.delete_button.clicked.connect(self._request_delete)

        self.close_button = self._header_button(icons.close_icon(), "关闭笔记")
        self.close_button.clicked.connect(self.closeRequested.emit)

        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(*HEADER_MARGINS)
        self.header_layout.setSpacing(2)
        self.header_layout.addWidget(self.title_label)
        self.header_layout.addStretch(1)
        for button in (self.paper_button, self.split_button, self.window_button,
                       self.expand_button, self.delete_button, self.close_button):
            self.header_layout.addWidget(button)

    def _build_tools(self) -> None:
        self.tools = QWidget(self)
        self.tools.setObjectName("NoteTools")
        self.tools.setAttribute(Qt.WA_StyledBackground, True)

        self._tool_group = QButtonGroup(self)
        self.pen_button = self._icon_button(self.tools, icons.pen_icon(), "画笔 (B)")
        self.eraser_button = self._icon_button(self.tools, icons.eraser_icon(), "橡皮 (Q)")
        for button, tool in ((self.pen_button, "pen"), (self.eraser_button, "eraser")):
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, t=tool: self._activate_tool(t))
            self._tool_group.addButton(button)

        # 文字是第三个「工具」，但按钮可再点一次取消，所以不塞进互斥组：
        # 选中态统一由 `_reflect_tools` 反映 view.text_mode。
        self.text_button = self._icon_button(self.tools, icons.text_icon(), "文字 (T)")
        self.text_button.setCheckable(True)
        self.text_button.clicked.connect(self._toggle_text_mode)

        self.image_button = self._icon_button(self.tools, icons.image_icon(), "插入图片")
        self.image_button.clicked.connect(self._insert_image)
        self.attach_button = self._icon_button(self.tools, icons.attach_icon(), "插入附件")
        self.attach_button.clicked.connect(self._insert_attachment)

        self._color_group = QButtonGroup(self)
        self.color_buttons: list[QPushButton] = []
        for color in PALETTE:
            button = QPushButton(self.tools)
            button.setCheckable(True)
            button.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)
            button.setToolTip(color)
            button.setProperty("color", color)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{background-color: {color};"
                f" border: 1px solid {theme.BORDER_STRONG};"
                f" border-radius: {SWATCH_SIZE // 2}px; padding: 0; min-height: 0;}}"
                f"QPushButton:checked {{border: 2px solid {theme.ACCENT};}}")
            button.clicked.connect(lambda _=False, c=color: self.view.set_color(c))
            self._color_group.addButton(button)
            self.color_buttons.append(button)

        self._width_group = QButtonGroup(self)
        self.width_buttons: list[QToolButton] = []
        for width, radius in zip(WIDTHS, WIDTH_RADII):
            button = self._icon_button(self.tools, icons.width_icon(radius),
                                       f"粗细 {width:g}")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, w=width: self.view.set_width(w))
            self._width_group.addButton(button)
            self.width_buttons.append(button)

        self.undo_button = self._icon_button(self.tools, icons.undo_icon(), "撤销 (Ctrl+Z)")
        self.redo_button = self._icon_button(self.tools, icons.redo_icon(), "重做 (Ctrl+Y)")
        self.clear_button = self._icon_button(self.tools, icons.clear_icon(), "清空本卡")
        self.undo_button.clicked.connect(self._undo)
        self.redo_button.clicked.connect(self._redo)
        self.clear_button.clicked.connect(self.view.clear_all)

        self.tools_layout = QHBoxLayout(self.tools)
        self.tools_layout.setContentsMargins(*TOOLS_MARGINS)
        self.tools_layout.setSpacing(TOOLS_SPACING)
        self.tools_layout.addWidget(self.pen_button)
        self.tools_layout.addWidget(self.eraser_button)
        self.tools_layout.addWidget(self.text_button)
        self.tools_layout.addWidget(self.image_button)
        self.tools_layout.addWidget(self.attach_button)
        self.tools_layout.addSpacing(GROUP_GAP)
        for button in self.color_buttons:
            self.tools_layout.addWidget(button)
        self.tools_layout.addSpacing(GROUP_GAP)
        for button in self.width_buttons:
            self.tools_layout.addWidget(button)
        self.tools_layout.addStretch(1)
        self.tools_layout.addWidget(self.undo_button)
        self.tools_layout.addWidget(self.redo_button)
        self.tools_layout.addWidget(self.clear_button)

    def _build_layout(self) -> None:
        # 工具行放进一个不换行的横向滚动区：分屏右栏只有 340px 时按钮不被挤扁，
        # 宽卡片时仍按视口宽度铺满（撤销组保持贴右，和以前一样）。
        self.tools_scroll = QScrollArea(self)
        self.tools_scroll.setObjectName("NoteToolsScroll")
        self.tools_scroll.setFrameShape(QFrame.NoFrame)
        self.tools_scroll.setWidgetResizable(False)
        self.tools_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tools_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tools_scroll.setFocusPolicy(Qt.NoFocus)
        self.tools_scroll.setStyleSheet(_TOOLS_SCROLL_QSS)
        palette = self.tools_scroll.viewport().palette()
        background = QColor(theme.NOTE_TOOLS_BG)
        palette.setColor(QPalette.Window, background)
        palette.setColor(QPalette.Base, background)
        self.tools_scroll.viewport().setAutoFillBackground(True)
        self.tools_scroll.viewport().setPalette(palette)
        self.tools_scroll.setWidget(self.tools)
        self.tools_scroll.setFixedHeight(self.tools.sizeHint().height())
        self.tools_scroll.horizontalScrollBar().rangeChanged.connect(
            self._on_tools_range_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)     # 让出 1px 边框，子控件不压住描边
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.tools_scroll)
        layout.addWidget(self.view, 1)

    def _on_tools_range_changed(self, _minimum: int, _maximum: int) -> None:
        self._sync_tools_scroll()

    def _sync_tools_scroll(self) -> None:
        """让工具行在宽卡片上铺满、在窄卡片上保持原宽并出现横向滚动条。"""
        if self._syncing_tools or not hasattr(self, "tools_scroll"):
            return
        # 遮罩收起动画会先销毁卡片子控件；排队中的 rangeChanged 此时还会进来
        if not shiboken6.isValid(self.tools) or not shiboken6.isValid(self.tools_scroll):
            return
        self._syncing_tools = True
        try:
            hint = self.tools.sizeHint()
            target_w = max(hint.width(), self.tools_scroll.viewport().width())
            if (self.tools.width() != target_w
                    or self.tools.height() != hint.height()):
                self.tools.resize(target_w, hint.height())
            bar = self.tools_scroll.horizontalScrollBar()
            extra = bar.sizeHint().height() if bar.maximum() > bar.minimum() else 0
            height = hint.height() + extra
            if (self.tools_scroll.minimumHeight() != height
                    or self.tools_scroll.maximumHeight() != height):
                self.tools_scroll.setFixedHeight(height)
        finally:
            self._syncing_tools = False

    def _build_grip(self) -> None:
        """右下角的缩放热区：盖在画布上面（那 16x16 画布本来会吃掉鼠标事件）。

        只有这一小块抢事件，工具行的按钮各行其道；卡片本体的 mousePressEvent 仍空着，
        遮罩的「点卡片外关闭」不受影响。
        """
        self.grip = _SizeGrip(self)
        self._place_grip()

    # --- 标题 ---

    @property
    def display_name(self) -> str:
        return self._display_name

    def _toggle_paper_from_title(self) -> None:
        self.card._toggle_paper()
        self.titlebar.set_paper_dark(self.card.note.paper == "dark")

    def set_display_name(self, name: str) -> None:
        """更新标题与提示（`name` 为空时回退 `note.name`，再回退「笔记」）。

        只改控件文本，不写 `note.name`：名字的落盘/图钉标签由接线层统一处理，
        这样遮罩卡片和独立窗口同时开着也不会互相打脸。
        """
        display = note_title(self.note, name)
        self._display_name = display
        self.title_label.setText(display)
        self.title_label.setToolTip(
            f"笔记 {self.note.id} · {display}\n{RENAME_HINT}")

    def eventFilter(self, obj, ev) -> bool:
        """标题双击 → `renameRequested`（只装在这一个 QLabel 上，不动全局鼠标事件）。"""
        if obj is self.title_label and ev.type() == QEvent.MouseButtonDblClick:
            if ev.button() == Qt.LeftButton:
                ev.accept()
                self.renameRequested.emit()
                return True
        return super().eventFilter(obj, ev)

    # --- 缩放手柄 ---

    def _grip_rect(self) -> QRect:
        """手柄矩形（卡片坐标），钉在右下角、让开 1px 描边。"""
        return QRect(self.width() - GRIP_SIZE - GRIP_INSET,
                     self.height() - GRIP_SIZE - GRIP_INSET, GRIP_SIZE, GRIP_SIZE)

    def sizeHitTest(self, point: QPoint) -> bool:
        """卡片坐标下的某点是否落在缩放手柄热区上（停用手柄后恒为 False）。"""
        return self._resize_enabled and self._grip_rect().contains(
            QPoint(int(point.x()), int(point.y())))

    def set_resize_enabled(self, on: bool) -> None:
        """手柄开关：独立窗口里关掉（系统边框才是缩放入口），遮罩里开着。

        关掉时不只是隐藏——`sizeHitTest` 也一并返回 False，热区不再吃事件。
        """
        self._resize_enabled = bool(on)
        self.grip.setEnabled(self._resize_enabled)
        self._place_grip()

    def resize_enabled(self) -> bool:
        return self._resize_enabled

    def _place_grip(self) -> None:
        self.grip.setGeometry(self._grip_rect())
        # 开合动画的 24x24 种子矩形里亮出手柄只会糊成一团小三角，等够大再出现
        self.grip.setVisible(self._resize_enabled
                             and self.width() >= GRIP_MIN_CARD
                             and self.height() >= GRIP_MIN_CARD)
        if self.grip.isVisible():
            self.grip.raise_()        # 必须压过 NoteView，否则鼠标事件全落到画布上

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if hasattr(self, "grip"):
            self._place_grip()        # 卡片被遮罩摆到新尺寸后，手柄跟着搬到新角落
        self._sync_tools_scroll()

    def set_resize_bounds(self, max_size: QSize | None) -> None:
        """遮罩按宿主给的显示上限（等比框）；None 表示只有 CARD_MIN 下限、不设顶。"""
        self._max_display = max_size

    def resize_bounds(self) -> QSize | None:
        return self._max_display

    def grip_ink(self) -> QColor:
        """斜角提示线的颜色：跟纸色走，浅纸画深线、深纸画浅线。"""
        return QColor(INK_ON_DARK if self.note.paper == "dark" else INK_ON_LIGHT)

    # --- 手柄拖拽：只算尺寸，不碰几何 ---

    def _raw_display_width(self, global_pos: QPoint) -> float:
        """手对应的等比宽：卡片在遮罩里居中，所以「手到中心的两倍」就是新尺寸。

        两轴各折回宽（高乘 560/720），取更大的那一轴——按住 45° 往外拖时版面是
        竖的，取大者才跟得上手；取小者会让卡片永远落后于光标。超出上限由钳制负责。
        """
        origin = self.mapToGlobal(QPoint(0, 0))
        cx = origin.x() + self.width() / 2.0
        cy = origin.y() + self.height() / 2.0
        from_x = 2.0 * (global_pos.x() - cx)
        from_y = 2.0 * (global_pos.y() - cy) * NOTE_W / NOTE_H
        return max(1.0, max(from_x, from_y))

    def display_size_for(self, global_pos: QPoint) -> QSize:
        """某全局位置拖到的目标显示尺寸：锁版面纵横比，再夹 CARD_MIN 与遮罩上限。"""
        width = self._raw_display_width(global_pos) + self._drag_delta_w
        wanted = snap_display_size(width)
        return clamp_display_size(wanted, CARD_MIN_DISPLAY, self._max_display)

    def begin_size_drag(self, global_pos: QPoint) -> None:
        """按下：记下「当前宽 - 手算宽」，第一步才不会凭空缩掉热区那 16px。"""
        self._drag_delta_w = float(self.width()) - self._raw_display_width(global_pos)
        self._last_drag_size = None

    def update_size_drag(self, global_pos: QPoint) -> None:
        """拖动中：只在尺寸真的变了时发信号，遮罩收到后立刻改几何（不走动画）。"""
        size = self.display_size_for(global_pos)
        if size != self._last_drag_size:
            self._last_drag_size = size
            self.resizeRequested.emit(size)

    def finish_size_drag(self, global_pos: QPoint) -> None:
        """松手定稿：最后再发一次，终值就是松手那一刻拖到的尺寸。"""
        size = self.display_size_for(global_pos)
        self._drag_delta_w = 0.0
        self._last_drag_size = None
        self.resizeRequested.emit(size)

    # --- 外部状态 ---

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, on: bool) -> None:
        """放大状态由遮罩驱动：这里只换图标/提示，不碰几何。"""
        on = bool(on)
        if on == self._expanded:
            self._reflect_tools()
            return
        self._expanded = on
        self._reflect_tools()

    def set_paper(self, paper: str) -> None:
        """切纸色并把不可读的墨色换成对应纸面的默认色。"""
        paper = paper if paper in NOTE_PAPERS else "dark"
        self.note.paper = paper
        self.view.set_paper(paper)
        color = QColor(self.view.color)
        if paper == "light" and color.lightness() > LIGHT_PAPER_THRESHOLD:
            self.view.set_color(INK_ON_LIGHT)
        elif paper == "dark" and color.lightness() < DARK_PAPER_THRESHOLD:
            self.view.set_color(INK_ON_DARK)
        self._reflect_tools()

    # --- 内部动作 ---

    def _activate_tool(self, tool: str) -> None:
        """画笔/橡皮：顺手退出文字模式（三者互斥）。"""
        if self.view.text_mode:
            self.view.set_text_mode(False)
        self.view.set_tool(tool)
        self._reflect_tools()

    def _toggle_text_mode(self) -> None:
        """文字工具：点一次进（画布单击即放字），再点或落笔后自动退出。"""
        self.view.set_text_mode(not self.view.text_mode)
        if self.view.text_mode:
            self.view.setFocus(Qt.OtherFocusReason)
        self._reflect_tools()

    def _require_pdf(self) -> bool:
        """没有 PDF 就没有 `<pdf>.sketch-assets/`，插入文件无处存放。"""
        if self.pdf_path is not None:
            return True
        QMessageBox.warning(self, "插入文件", NO_PDF_HINT)
        return False

    def _insert_image(self) -> None:
        if not self._require_pdf():
            return
        path, _ = QFileDialog.getOpenFileName(self, "插入图片", "", IMAGE_FILTER)
        if not path:
            return
        if self.view.add_image_file(Path(path)) is None:
            QMessageBox.warning(self, "插入图片", "图片读取失败，没有插入。")

    def _insert_attachment(self) -> None:
        if not self._require_pdf():
            return
        path, _ = QFileDialog.getOpenFileName(self, "插入附件", "", "")
        if not path:
            return
        if self.view.add_attachment_file(Path(path)) is None:
            QMessageBox.warning(self, "插入附件", "附件复制失败，没有插入。")

    def _toggle_paper(self) -> None:
        self.set_paper("light" if self.note.paper == "dark" else "dark")

    def _request_delete(self) -> None:
        answer = QMessageBox.question(
            self, "删除笔记", f"确定要删除笔记 {self.note.id} 吗？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.deleteRequested.emit()

    def _undo(self) -> None:
        self.view.history.pop_undo()
        self._reflect_tools()

    def _redo(self) -> None:
        self.view.history.pop_redo()
        self._reflect_tools()

    def _on_view_changed(self) -> None:
        self._reflect_tools()
        self.contentChanged.emit()

    def _sync_checks(self, buttons: list[QAbstractButton], active) -> None:
        """互斥组里批量改选中态：先关互斥才能把「无匹配」的一整组全部取消。"""
        group = buttons[0].group() if buttons else None
        if group is not None:
            group.setExclusive(False)
        for button in buttons:
            button.setChecked(bool(active(button)))
        if group is not None:
            group.setExclusive(True)

    def _reflect_tools(self) -> None:
        """把视图/卡片状态反映到按钮；不再反过来发点击信号。"""
        if not hasattr(self, "pen_button"):
            return
        text_mode = self.view.text_mode
        self._sync_checks(
            [self.pen_button, self.eraser_button],
            lambda b: (not text_mode)
            and self.view.tool == ("pen" if b is self.pen_button else "eraser"))
        self.text_button.setChecked(text_mode)

        current = self.view.color.lower()
        self._sync_checks(self.color_buttons,
                          lambda b: b.property("color").lower() == current)
        self._sync_checks(
            self.width_buttons,
            lambda b, ws=tuple(WIDTHS): abs(self.view.width - ws[self.width_buttons.index(b)]) < 1e-6)

        dark = self.note.paper == "dark"
        self.paper_button.setIcon(icons.paper_icon(dark))
        self.paper_button.setToolTip(
            "当前纸张：深色（点击切浅色）" if dark else "当前纸张：浅色（点击切深色）")
        self.expand_button.setIcon(
            icons.restore_icon() if self._expanded else icons.maximize_icon())
        self.expand_button.setToolTip("还原卡片" if self._expanded else "放大卡片")
        self.undo_button.setEnabled(self.view.history.can_undo)
        self.redo_button.setEnabled(self.view.history.can_redo)


class _SizeGrip(QWidget):
    """右下角缩放手柄：透明热区 + QPainter 画的两笔斜角提示线，不用任何图标资源。

    自己不算尺寸也不摆几何——按下/移动/松开全部转成 `NoteCard` 的三个方法，由卡片
    发 `resizeRequested`。按下后 Qt 的隐式鼠标抓取会把后续移动都送回本控件，所以
    拖出手柄、甚至拖出遮罩都还能跟手。
    """

    def __init__(self, card: NoteCard) -> None:
        super().__init__(card)
        self.card = card
        self.setObjectName("NoteResizeGrip")
        self.setCursor(Qt.SizeFDiagCursor)
        self.setToolTip("按住拖动缩放卡片（版面比例锁定 560:720）")
        self._dragging = False

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = self.card.grip_ink()
        color.setAlpha(255 if self._dragging else 190)
        pen = QPen(color)
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        right = float(self.width() - 3)
        bottom = float(self.height() - 3)
        for step in (float(GRIP_SIZE - 6), float(GRIP_SIZE - 10)):
            painter.drawLine(QLineF(right, bottom - step, right - step, bottom))
        painter.end()

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            super().mousePressEvent(ev)       # 右键/中键继续冒泡，别拦住遮罩的右键
            return
        self._dragging = True
        self.card.begin_size_drag(ev.globalPosition().toPoint())
        ev.accept()
        self.update()

    def mouseMoveEvent(self, ev) -> None:
        if not self._dragging:
            super().mouseMoveEvent(ev)        # 没有 setMouseTracking：平时不该收到移动
            return
        self.card.update_size_drag(ev.globalPosition().toPoint())
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if not self._dragging or ev.button() != Qt.LeftButton:
            super().mouseReleaseEvent(ev)
            return
        self._dragging = False
        self.card.finish_size_drag(ev.globalPosition().toPoint())
        ev.accept()
        self.update()


class NoteWindowTitleBar(QWidget):
    """独立笔记窗口的 28px 自绘标题行：左标题，右最小化/最大化/关闭。

    拖动交给 `QWindow.startSystemMove()`，双击走最大化/还原；只有标题文字和空白
    处算拖动区，三个按钮（及其子控件）的点击一律不触发拖动或双击最大化。
    """

    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()
    splitRequested = Signal()       # 切主窗内分屏（原卡片头按钮随 header 收起，挪到这里）
    paperRequested = Signal()       # 切换纸张深浅
    renameRequested = Signal()
    deleteRequested = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NoteWindowTitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(NOTE_WINDOW_TITLE_HEIGHT)
        self.setFocusPolicy(Qt.NoFocus)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 4, 0)
        row.setSpacing(2)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("NoteWindowTitle")
        self.title_label.setFocusPolicy(Qt.NoFocus)
        row.addWidget(self.title_label, 0, Qt.AlignVCenter)
        row.addStretch(1)

        self.min_button = self._button(icons.minimize_icon(), "最小化")
        self.max_button = self._button(icons.maximize_icon(), "最大化")
        self.close_button = self._button(icons.close_icon(), "关闭")
        self.close_button.setProperty("close", True)
        self._buttons = (self.min_button, self.max_button, self.close_button)
        for button in self._buttons:
            row.addWidget(button, 0, Qt.AlignVCenter)

        # 卡片头整行收起后，这些动作必须在窗口标题行有入口
        self.split_button = self._button(icons.split_icon(), "切回主窗内分屏")
        self.paper_button = self._button(icons.paper_icon(True), "切换纸张深浅")
        self.rename_button = self._button(icons.text_icon(), "重命名笔记")
        self.delete_button = self._button(icons.delete_icon(), "删除笔记")
        self._buttons = (self.split_button, self.paper_button, self.rename_button,
                         self.delete_button, *self._buttons)
        for index, button in enumerate(
                (self.split_button, self.paper_button,
                 self.rename_button, self.delete_button)):
            row.insertWidget(1 + index, button, 0, Qt.AlignVCenter)

        self.min_button.clicked.connect(self.minimizeRequested.emit)
        self.max_button.clicked.connect(self.maximizeRequested.emit)
        self.close_button.clicked.connect(self.closeRequested.emit)
        self.split_button.clicked.connect(self.splitRequested.emit)
        self.paper_button.clicked.connect(self.paperRequested.emit)
        self.rename_button.clicked.connect(self.renameRequested.emit)
        self.delete_button.clicked.connect(self.deleteRequested.emit)
        self.title_label.installEventFilter(self)

    def _button(self, icon, tip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("NoteWindowButton")
        button.setIcon(icon)
        button.setIconSize(QSize(12, 12))
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(NOTE_WINDOW_BUTTON_W, NOTE_WINDOW_BUTTON_H)
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        return button

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def set_paper_dark(self, dark: bool) -> None:
        self.paper_button.setIcon(icons.paper_icon(dark))

    def eventFilter(self, obj, ev):
        if (obj is self.title_label and ev.type() == QEvent.MouseButtonDblClick
                and ev.button() == Qt.LeftButton):
            self.renameRequested.emit()
            ev.accept()
            return True
        return super().eventFilter(obj, ev)

    def set_maximized(self, on: bool) -> None:
        """最大化后图标换成「还原」，提示同步。"""
        self.max_button.setIcon(icons.restore_icon() if on else icons.maximize_icon())
        self.max_button.setToolTip("还原" if on else "最大化")

    def _is_window_drag_area(self, pos: QPoint) -> bool:
        """标题行坐标里的 pos 是否属于窗口拖动区；按钮不算。"""
        child = self.childAt(pos)
        while child is not None and child is not self:
            if child in self._buttons:
                return False
            child = child.parentWidget()
        return True

    def mousePressEvent(self, ev) -> None:
        pos = self.mapFromGlobal(ev.globalPosition().toPoint())
        if ev.button() == Qt.LeftButton and self._is_window_drag_area(pos):
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        pos = self.mapFromGlobal(ev.globalPosition().toPoint())
        if ev.button() == Qt.LeftButton and self._is_window_drag_area(pos):
            self.maximizeRequested.emit()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)


class NoteWindow(QWidget):
    """把一张笔记放进**主窗之外**的独立窗口：自绘标题行 + 无边框外壳。

    与主窗同一套外壳：`Qt.FramelessWindowHint`、28px 深色自绘标题行（拖动 /
    双击最大化 / 最小化 / 关闭）、1px 圆角外框（`install_window_border`）和
    应用级边缘缩放过滤器（`EdgeResizeFilter`）。窗口里只有一张 `NoteCard`
    （标题行以下铺满），卡片右下角手柄被关掉——窗口边缘才是缩放入口；卡片头
    里重复的关闭键也隐藏，关窗统一由窗口标题行负责。

    引用归属：**用 `WA_DeleteOnClose`**——`close()` 走完 `closeEvent` 后 Qt 用
    `deleteLater` 收尾。接线层（`AppController._note_windows`）仍持有 dict 做「同一张
    笔记只开一个窗口」的查找，在 `closed` 里摘掉即可；此时对象一定还活着、`sender()`
    也认得出是谁关的（真正的删除排在下一轮事件循环，不会悬空）。

    `Esc` 不关窗：QWidget 窗口默认就不吃 Esc，唯一的键处理在 `NoteView` 自己手里
    （B/Q/1-4/Ctrl+Z），文本输入框和其它控件完全不受影响。
    """

    contentChanged = Signal()
    renameRequested = Signal()
    closed = Signal()
    deleteRequested = Signal()      # 卡片删除按钮的意图（窗口自己不删笔记）
    splitRequested = Signal()       # 窗口里也能切回主窗分屏
    windowRequested = Signal()      # 已经在窗口里：仍然透传，免得接线层拿不到

    def __init__(self, note: Note, settings: Settings, display_name: str = "",
                 parent: QWidget | None = None,
                 pdf_path: Path | None = None) -> None:
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self.note = note
        self.settings = settings
        self.pdf_path = Path(pdf_path) if pdf_path is not None else None
        self._closed_emitted = False

        self.setObjectName("NoteWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)   # 不透明底色，不要半透明背景
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setStyleSheet(NOTE_WINDOW_QSS)
        title = f"笔记 · {note_title(note, display_name)}"
        self.setWindowTitle(title)

        self.titlebar = NoteWindowTitleBar(title, self)
        self.titlebar.minimizeRequested.connect(self._minimize)
        self.titlebar.maximizeRequested.connect(self._toggle_maximized)
        self.titlebar.closeRequested.connect(self.close)
        self.titlebar.splitRequested.connect(self.splitRequested.emit)
        self.titlebar.paperRequested.connect(self._toggle_paper_from_title)
        self.titlebar.renameRequested.connect(self.renameRequested.emit)
        self.titlebar.deleteRequested.connect(self.deleteRequested.emit)

        self.card = NoteCard(note, settings, self, display_name=display_name,
                             pdf_path=self.pdf_path)
        self.card.set_resize_enabled(False)      # 窗口边缘负责缩放，卡片手柄让位
        # 窗口标题行已经有「笔记 · 名称」，卡片再顶一行就是标题套标题：整行收起
        self.card.header.hide()
        self.card.expand_button.hide()           # 窗口本身可最大化，卡片“放大”会失效
        self.card.window_button.hide()           # 已经在独立窗口里，不再需要“开窗口”
        self.card.close_button.hide()            # 关窗由窗口标题行负责，别重复一个 ×

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)     # 标题行以下卡片铺满，不留边距
        layout.setSpacing(0)
        layout.addWidget(self.titlebar)
        layout.addWidget(self.card, 1)

        self.card.contentChanged.connect(self.contentChanged)
        self.card.renameRequested.connect(self.renameRequested)
        self.card.deleteRequested.connect(self.deleteRequested)
        self.card.closeRequested.connect(self.close)   # 隐藏的卡片 × 仍保持原语义
        self.card.splitRequested.connect(self.splitRequested)
        self.card.windowRequested.connect(self.windowRequested)

        # 最小尺寸：2026-10-24 用户要求能缩得更小——不再钳到卡片的最小可用尺寸
        # （工具行小屏下会挤，但内容照常显示）；卡片自身的 hint 在窗口里让位
        self.card.setMinimumSize(0, 0)
        self.setMinimumSize(NOTE_WINDOW_MIN_W, NOTE_WINDOW_MIN_H)
        self.resize(640, 820)

        # 边缘缩放 / 1px 外框：与主窗、草稿窗共用同一套外壳
        self._edge_resize_filter = EdgeResizeFilter.install(self)
        self._window_border = install_window_border(self)

    # --- 名字 ---

    def _toggle_paper_from_title(self) -> None:
        self.card._toggle_paper()
        self.titlebar.set_paper_dark(self.card.note.paper == "dark")

    def set_display_name(self, name: str) -> None:
        """窗口标题、自绘标题行和卡片标题一起换（空名回退 `note.name` → 「笔记」）。"""
        display = note_title(self.note, name)
        self.card.set_display_name(display)
        self.titlebar.set_title(f"笔记 · {display}")
        self.setWindowTitle(f"笔记 · {display}")

    # --- 最大化 / 还原 ---

    def _minimize(self) -> None:
        """标题行最小化按钮：走 `showMinimized`（留一层便于测试替换）。"""
        self.showMinimized()

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.titlebar.set_maximized(self.isMaximized())

    def changeEvent(self, ev) -> None:
        """系统改动窗口状态（含双击标题栏之外的最大化）时同步按钮图标。"""
        if ev.type() == QEvent.WindowStateChange and hasattr(self, "titlebar"):
            self.titlebar.set_maximized(self.isMaximized())
        super().changeEvent(ev)

    # --- 无边框外壳 ---

    def showEvent(self, ev) -> None:
        """补 WS_THICKFRAME（只靠 WM_NCHITTEST 不够）并给 Windows 11 加圆角。"""
        super().showEvent(ev)
        enable_native_resize(self)
        apply_rounded_corners(self)

    def nativeEvent(self, event_type, message):
        """边缘缩放光标：与主窗、草稿窗共用同一套 WM_NCHITTEST 判定。"""
        hit = native_hit_test(self, event_type, message)
        if hit is not None:
            return True, hit
        return super().nativeEvent(event_type, message)

    # --- 关闭 ---

    def closeEvent(self, ev) -> None:
        """`closed` 只在真正关窗时发一次（重复 close 不再发）。"""
        if not self._closed_emitted:
            self._closed_emitted = True
            self.closed.emit()
        super().closeEvent(ev)

