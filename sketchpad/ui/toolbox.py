"""工具栏：常用色板（含深黄）+ 取色器 + 4 档粗细 + 画笔/荧光笔/鼠标/插入笔记/橡皮
+ 清屏 + 草稿纸 + 保存。

清屏按钮只发 clearRequested，由接线层按当前模式决定清哪里；草稿纸按钮只发
scratchRequested。清屏键的改绑只在设置面板里做（设计文档 §6.3）。"""
from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QColorDialog, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton,
                               QSizePolicy, QSlider, QSpacerItem, QToolButton,
                               QVBoxLayout, QWidget)

from sketchpad.ui import icons, theme
from sketchpad.core.settings import WIDTHS, Settings

PALETTE = ["#ff453a", "#2980b9", "#f1c40f", "#e8eaed", "#d35400",
           "#d4a017", "#27ae60", "#8e44ad", "#7f8c8d"]
WIDTH_LABELS = ("细", "中", "粗", "特粗")
WIDTH_MIN, WIDTH_MAX = 0.5, 10.0          # 自定义粗细范围（细端留到 0.5，方便调细）
SLIDER_SCALE = 10                         # 滑杆按 0.1 一档
ICON_SIZE = 18                            # 工具图标逻辑尺寸（紧凑工具栏，20 会显高）
HIT_TARGET = 28                           # 图标按钮的最小点击区（仍比 18px 图标大一圈）
ROW_SPACING = 2                           # 横向行内控件间距
COLUMN_SPACING = 4                        # 纵向右侧栏的列间距
GROUP_GAP = 7                             # 横排组间留白（替代竖线）：QBoxLayout 只加一次行距 2，有效 9px
COLUMN_GROUP_GAP = 6                      # 竖排组间留白：QBoxLayout 只加一次列距 4，有效 10px
SLIDER_LENGTH = 120                       # 滑杆长边（横排 120 宽 / 竖排 120 高）
VERTICAL_SLIDER_WIDTH = 20                # 纵向滑杆的宽度（窄列里只能这么宽）
SIZE_LABEL_WIDTH = 34                     # 粗细数字的固定宽度（看起来像可点的输入框）
CURRENT_COLOR_SIZE = 22                   # 当前颜色指示器直径
COLLAPSE_SIZE = 20                        # 折叠/展开箭头按钮（收起后只剩它）
COLLAPSED_BAND = COLLAPSE_SIZE + 2        # 收起后的窄条厚度：横排压高度、竖排压宽度
_MAX_SIZE = 16777215                      # QWIDGETSIZE_MAX：解除固定尺寸时用
# 设置对话框的键位捕获复用这三个常量与下面的按键文本规范化函数
CAPTURE_TIMEOUT_MS = 3000
CAPTURE_TEXT = "请按键…"
HINT_CAPTURE = "按 Esc 取消"

_MODIFIER_ORDER = ("Ctrl", "Shift", "Alt", "Meta")
_MODIFIER_ALIASES = {"ctrl": "Ctrl", "control": "Ctrl", "shift": "Shift",
                     "alt": "Alt", "meta": "Meta", "win": "Meta", "cmd": "Meta"}
_MODIFIER_NAMES = frozenset(_MODIFIER_ALIASES) | {"altgr", "super", "hyper"}
_KEY_ALIASES = {"Del": "Delete"}          # QKeySequence 的规范名 → 配置里用的名字
_MODIFIER_KEYS = (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
                  Qt.Key_AltGr, Qt.Key_Super_L, Qt.Key_Super_R,
                  Qt.Key_Hyper_L, Qt.Key_Hyper_R)


def _modifiers_text(modifiers) -> str:
    """Qt 修饰键标志 → "Ctrl+Shift" 形式（顺序固定）。"""
    parts = []
    if modifiers & Qt.ControlModifier:
        parts.append("Ctrl")
    if modifiers & Qt.ShiftModifier:
        parts.append("Shift")
    if modifiers & Qt.AltModifier:
        parts.append("Alt")
    if modifiers & Qt.MetaModifier:
        parts.append("Meta")
    return "+".join(parts)


def _split_modifiers(text: str) -> list[str]:
    """把 "Shift+Ctrl" 之类的输入规范化成固定顺序，丢掉不认识的词。"""
    found = set()
    for token in text.replace(",", "+").split("+"):
        name = _MODIFIER_ALIASES.get(token.strip().lower())
        if name:
            found.add(name)
    return [name for name in _MODIFIER_ORDER if name in found]


def _canonical_key(name: str) -> str:
    """按键名尽量走 QKeySequence 的规范写法；单个字母一律大写。"""
    name = name.strip()
    if len(name) == 1:
        return name.upper()
    canonical = QKeySequence.fromString(name).toString()
    if not canonical:
        return name
    return _KEY_ALIASES.get(canonical, canonical)


def _event_key_text(ev) -> str:
    """QKeyEvent → 按键名：能打印的字符直接用，其余走 QKeySequence 的规范名。"""
    text = ev.text()
    if len(text) == 1 and text.isprintable() and not text.isspace():
        return text.upper()
    canonical = QKeySequence(ev.key()).toString()
    return _KEY_ALIASES.get(canonical, canonical)


class Toolbox(QWidget):
    """一行工具栏；选了什么只发信号，动作由 main.py 接线。"""

    colorPicked = Signal(str)
    widthChosen = Signal(float)
    toolChosen = Signal(str)
    clearRequested = Signal()
    scratchRequested = Signal()
    undoRequested = Signal()
    redoRequested = Signal()
    editRequested = Signal()                 # 编辑当前 Word（仅 .docx 显示）
    imageRequested = Signal()                # 草稿插入截图/图片
    findRequested = Signal()
    captureRequested = Signal()               # 框选屏幕截图
    captureModeRequested = Signal(str)        # region/window/fullscreen/last/color/history
    newNoteRequested = Signal()               # 新建独立笔记标签
    annotateToggled = Signal(bool)           # 开启/关闭全屏批注层                 # 查找（仅 PDF 文档模式显示）
    splitRequested = Signal()                # 与另一标签分屏（文档模式显示）
    splitDocRequested = Signal()             # 草稿纸浮窗：与文档分屏（文档左 / 草稿右）
    settingsRequested = Signal()             # 设置对话框（原标题栏齿轮）
    collapsedChanged = Signal(bool)           # 工具栏收起/展开（外层据此调高度）

    def __init__(self, settings: Settings | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toolbar")              # QSS 里靠这个 id 画底部分隔线
        self.setProperty("dock", "top")        # QSS：顶排透明，右排独立玻璃卡
        # QWidget 子类默认不吃 QSS 的 background，必须显式打开，否则工具栏仍是系统白底
        self.setAttribute(Qt.WA_StyledBackground, True)
        cfg = settings or Settings()
        # 画笔与荧光笔各记各的颜色：切工具时色板跟着切，互不覆盖
        self._colors = {"pen": cfg.color,
                        "highlighter": getattr(cfg, "highlighter_color", cfg.color)}
        self._tool = "pen"
        self._color = self._colors["pen"]
        self._collapsed = False
        self._vertical = False                 # 顶部横排 / 右侧竖排
        self._select_enabled = True            # 鼠标工具（平移/选字）只在文档模式出现
        self._note_enabled = True              # 插入笔记只在文档模式出现
        self._annotations_enabled = True       # 非 PDF 阅读器整组隐藏批注控件
        # 与控件当前状态对齐：首次展开时不要多调一次 setVisible(True)，
        # 否则样式表在 polish 时把 setFixedSize 的最小高度改回 min-height。
        self._annotation_visible_state: bool | None = (
            True if not self._collapsed else None)
        self._scratch_visible_state: bool | None = (
            True if not self._collapsed else None)
        self._word_edit_enabled = False
        self._draft_images_enabled = False
        self._new_note_enabled = True
        self._capture_enabled = True
        self._annotate_enabled = True

        self.color_buttons: list[QPushButton] = []
        self.width_buttons: list[QPushButton] = []
        self._group_gaps: list[QSpacerItem] = []
        self._create_controls()                 # 控件与信号只建一次，换方向只重排
        self._rebuild_layout()
        self._reflect_color(cfg.color)
        self.reflect_width(cfg.width)
        # 按钮不抢焦点：否则点过一次工具栏，画布的 B/Q/1-4/方向键就收不到了
        for button in self.findChildren(QAbstractButton):
            button.setFocusPolicy(Qt.NoFocus)

    # --- 布局 ---

    def _create_controls(self) -> None:
        """建全部控件并接好信号；布局方向变化时只重建 QBoxLayout，不重建控件。"""
        # 最左边的折叠键：收起后其它控件隐藏，只留这只箭头负责再展开
        self.collapse_button = QToolButton(self)
        self.collapse_button.setObjectName("CollapseToolbar")
        self.collapse_button.setIcon(icons.chevron_icon("up"))
        self.collapse_button.setIconSize(QSize(14, 14))
        self.collapse_button.setFixedSize(COLLAPSE_SIZE, COLLAPSE_SIZE)
        self.collapse_button.setToolTip("隐藏工具栏")
        self.collapse_button.setFocusPolicy(Qt.NoFocus)
        self.collapse_button.setCursor(Qt.PointingHandCursor)
        self.collapse_button.clicked.connect(self.toggle_collapsed)

        self._tool_group = QButtonGroup(self)
        self.undo_button = self._tool_button(icons.undo_icon(), "撤销 (Ctrl+Z)")
        self.redo_button = self._tool_button(icons.redo_icon(), "重做 (Ctrl+Y)")
        self.undo_button.clicked.connect(self.undoRequested.emit)
        self.redo_button.clicked.connect(self.redoRequested.emit)

        self.pen_button = self._tool_button(icons.pen_icon(), "画笔 (B)")
        self.highlighter_button = self._tool_button(
            icons.highlighter_icon(), "荧光笔 (H；拖过文字按选区高亮，空白处手绘)")
        self.select_button = self._tool_button(
            icons.pointer_icon(),
            "鼠标 (V)：拖空白平移 · 拖文字选字 · 拖图标移动 · 点 × 删除")
        self.note_button = self._tool_button(icons.note_icon(), "插入笔记 (N)")
        self.text_button = self._tool_button(
            icons.text_icon(), "文字 (T)：点击画布输入，回车外点击提交，Esc 取消")
        self.eraser_button = self._tool_button(icons.eraser_icon(), "橡皮 (Q)")
        self.text_button.setVisible(False)     # 文字工具：默认草稿才显示
        self._text_enabled = False
        for button, tool in ((self.pen_button, "pen"),
                             (self.highlighter_button, "highlighter"),
                             (self.select_button, "select"),
                             (self.note_button, "note"),
                             (self.text_button, "text"),
                             (self.eraser_button, "eraser")):
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, t=tool: self.toolChosen.emit(t))
            self._tool_group.addButton(button)
        self.pen_button.setChecked(True)

        self._width_group = QButtonGroup(self)
        for index, (width, label, radius) in enumerate(
                zip(WIDTHS, WIDTH_LABELS, (2.2, 3.6, 5.0, 6.4))):
            button = self._tool_button(icons.width_icon(radius), f"{label}（{width:g} 像素）")
            button.setText(label)                   # 只供读屏/测试，图标模式下不显示
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, w=width: self._pick_width(w))
            self._width_group.addButton(button, index)
            self.width_buttons.append(button)

        self.size_slider = QSlider(Qt.Horizontal, self)
        self.size_slider.setRange(int(WIDTH_MIN * SLIDER_SCALE), int(WIDTH_MAX * SLIDER_SCALE))
        self.size_slider.setSingleStep(1)
        self.size_slider.setPageStep(4)
        self.size_slider.setFocusPolicy(Qt.NoFocus)
        self.size_slider.setToolTip(f"自定义粗细（{WIDTH_MIN:g}–{WIDTH_MAX:g}）")
        self.size_slider.valueChanged.connect(self._on_slider_value)
        self.size_label = QLabel(self)
        self.size_label.setObjectName("SizeValue")
        self.size_label.setFixedWidth(SIZE_LABEL_WIDTH)
        self.size_label.setMinimumHeight(20)
        self.size_label.setAlignment(Qt.AlignCenter)
        self.size_label.setAttribute(Qt.WA_Hover, True)
        self.size_label.setStyleSheet(
            f"QLabel#SizeValue {{ background: {theme.SURFACE}; color: {theme.TEXT};"
            f" border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; padding: 1px 3px; }}"
            f"QLabel#SizeValue:hover {{ border-color: {theme.ACCENT_BORDER}; }}")
        self.size_label.setToolTip("当前粗细（点数字可直接输入数值）")
        self.size_label.setCursor(Qt.PointingHandCursor)
        self.size_label.installEventFilter(self)   # 点数字 → 弹输入框

        for color in PALETTE:
            button = QPushButton(self)
            button.setCheckable(True)
            button.setFixedSize(theme.SWATCH, theme.SWATCH)
            button.setToolTip(color)
            button.setProperty("color", color)
            # 圆形色块：半径 = 直径/2；选中时白色描边（macOS 取色器的样子）
            button.setStyleSheet(
                f"QPushButton {{background-color: {color}; border: 1px solid {theme.BORDER_STRONG};"
                f" border-radius: {theme.SWATCH // 2}px; padding: 0; min-height: 0;}}"
                f"QPushButton:checked {{border: 2px solid {theme.ACCENT};}}")
            button.clicked.connect(lambda _=False, c=color: self._pick_color(c))
            self.color_buttons.append(button)

        self.custom_color_button = self._tool_button(icons.palette_icon(), "自定义颜色")
        self.custom_color_button.clicked.connect(self._pick_custom_color)
        # 当前颜色指示器：纯显示（不接收鼠标、不发信号），取色器选完后一眼能看到是什么色
        self.current_color_swatch = QFrame(self)
        self.current_color_swatch.setObjectName("CurrentColor")
        self.current_color_swatch.setFixedSize(CURRENT_COLOR_SIZE, CURRENT_COLOR_SIZE)
        self.current_color_swatch.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.current_color_swatch.setFocusPolicy(Qt.NoFocus)

        self.clear_button = self._tool_button(icons.clear_icon(), "清屏 / 清空本页 (E)")
        self.clear_button.clicked.connect(lambda _checked=False: self.clearRequested.emit())
        self.find_button = self._tool_button(icons.search_icon(), "查找 (Ctrl+F)")
        self.find_button.clicked.connect(
            lambda _checked=False: self.findRequested.emit())
        self.find_button.setVisible(False)          # 仅能搜的文档（PDF）里显示
        self._find_enabled = False
        self.new_note_button = self._tool_button(
            icons.new_note_icon(), "新建笔记")
        self.new_note_button.clicked.connect(
            lambda _checked=False: self.newNoteRequested.emit())
        self.annotate_button = self._tool_button(
            icons.annotate_icon(), "全屏批注 (Ctrl+Alt+D；Alt+1 穿透 / Alt+2 钢笔)")
        self.annotate_button.setObjectName("AnnotateButton")
        self.annotate_button.setCheckable(True)
        self.annotate_button.toggled.connect(self.annotateToggled.emit)
        self.capture_button = self._tool_button(
            icons.capture_icon(), "截图 (Ctrl+1 全局)：框选后可标注、贴图、OCR")
        self.capture_button.setObjectName("CaptureButton")
        self.capture_button.clicked.connect(
            lambda _checked=False: self.captureRequested.emit())
        capture_menu = QMenu(self.capture_button)
        for label, mode in (("区域截图", "region"), ("窗口截图", "window"),
                            ("全屏截图", "fullscreen"), ("上一区域", "last"),
                            ("屏幕取色", "color")):
            action = capture_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, m=mode: self.captureModeRequested.emit(m))
        capture_menu.addSeparator()
        history = capture_menu.addAction("截图历史…")
        history.triggered.connect(
            lambda _checked=False: self.captureModeRequested.emit("history"))
        self.capture_button.setMenu(capture_menu)
        self.capture_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.scratch_button = self._tool_button(icons.scratch_icon(), "草稿纸 (Alt+Q)")
        self.scratch_button.clicked.connect(
            lambda _checked=False: self.scratchRequested.emit())
        # 分屏：原标题栏标签条上的按钮，统一收到工具栏（2026-10-23）
        self.split_button = self._tool_button(icons.split_icon(), "与另一标签分屏")
        self.split_button.clicked.connect(
            lambda _checked=False: self.splitRequested.emit())
        self.split_button.setVisible(False)
        self._split_available = False        # 文档模式才出现
        self._split_ready = False            # 有另一个可配对的标签才可点
        # 草稿纸浮窗专用：与文档分屏（左文档、右草稿画布）
        self.split_doc_button = self._tool_button(
            icons.split_icon(), "与文档分屏（文档左 / 草稿右）")
        self.split_doc_button.clicked.connect(
            lambda _checked=False: self.splitDocRequested.emit())
        self.split_doc_button.setVisible(False)
        self._split_doc_available = False
        # 设置：原标题栏齿轮
        self.settings_button = self._tool_button(icons.gear_icon(), "设置 (Ctrl+,)")
        self.settings_button.clicked.connect(
            lambda _checked=False: self.settingsRequested.emit())
        self.word_button = self._tool_button(icons.text_icon(), "编辑 Word (Ctrl+E)")
        self.word_button.clicked.connect(
            lambda _checked=False: self.editRequested.emit())
        self.word_button.setVisible(False)
        self.image_button = self._tool_button(icons.image_icon(), "插入截图/图片 (Ctrl+V)")
        self.image_button.clicked.connect(
            lambda _checked=False: self.imageRequested.emit())
        self.image_button.setVisible(False)

    def _rebuild_layout(self) -> None:
        """按当前方向重建布局：横向一行 / 纵向右侧一列，控件与信号原样复用。"""
        old = self.layout()
        if old is not None:
            while old.count():                  # 只摘下位置，控件仍归 self 所有
                old.takeAt(0)
            shiboken6.delete(old)               # 布局壳要显式销毁，否则 setLayout 会警告
        box = QVBoxLayout(self) if self._vertical else QHBoxLayout(self)
        if self._vertical:
            box.setContentsMargins(1, 6, 1, 6)  # 窄列：左右贴边，上下留白
            box.setSpacing(COLUMN_SPACING)
        else:
            box.setContentsMargins(6, 1, 6, 1)
            box.setSpacing(ROW_SPACING)

        blocks = (
            (self.collapse_button,),
            (self.undo_button, self.redo_button),
            (self.pen_button, self.highlighter_button,
             self.select_button, self.note_button, self.text_button,
             self.eraser_button, self.annotate_button),
            tuple(self.width_buttons),
            (self.size_slider, self.size_label),
            (*self.color_buttons, self.custom_color_button, self.current_color_swatch),
            (self.word_button, self.image_button, self.new_note_button,
             self.capture_button, self.find_button, self.clear_button,
             self.scratch_button, self.split_button, self.split_doc_button,
             self.settings_button),
        )
        self._group_gaps: list[QSpacerItem] = []
        # QBoxLayout 只在 spacer 一侧算一次 spacing（不是两侧各一次），所以有效组间
        # 留白 = spacing + 这里的 spacer 尺寸：横排 2 + 7 = 9px，竖排 4 + 6 = 10px。
        for index, block in enumerate(blocks):
            if index:
                if self._vertical:
                    gap = QSpacerItem(1, COLUMN_GROUP_GAP,
                                      QSizePolicy.Minimum, QSizePolicy.Fixed)
                else:
                    gap = QSpacerItem(GROUP_GAP, 1,
                                      QSizePolicy.Fixed, QSizePolicy.Minimum)
                box.addItem(gap)
                self._group_gaps.append(gap)
            for widget in block:
                if self._vertical:
                    box.addWidget(widget, 0, Qt.AlignHCenter)   # 窄列里居中才不歪
                else:
                    box.addWidget(widget)
        box.addStretch(1)

        self._shape_slider()
        # 收起时要隐藏的控件（折叠键自己除外）；stretch 不是 widget，天然忽略
        self._collapsible_widgets: list[QWidget] = []
        for index in range(box.count()):
            widget = box.itemAt(index).widget()
            if (widget is not None and widget is not self.collapse_button
                    and widget is not self.word_button
                    and widget is not self.image_button
                    and widget is not self.find_button
                    and widget is not self.split_button
                    and widget is not self.split_doc_button
                    and widget is not self.text_button):
                self._collapsible_widgets.append(widget)
        self._apply_visibility()

    def set_annotations_enabled(self, enabled: bool) -> None:
        """非 PDF 阅读器（TXT/Markdown）没有批注语义：整组隐藏画笔、粗细、颜色与清空。"""
        self._annotations_enabled = bool(enabled)
        self._apply_visibility()

    def set_select_enabled(self, enabled: bool) -> None:
        """鼠标工具（平移/选字/拖图标）只在文档模式出现（草稿画布没有 PDF 可平移）。"""
        self._select_enabled = bool(enabled)
        self._apply_select_visibility()

    def set_note_enabled(self, enabled: bool) -> None:
        """插入笔记只在文档模式出现（草稿画布/草稿窗没有 PDF 页可落图标）。"""
        self._note_enabled = bool(enabled)
        self._apply_select_visibility()

    def set_text_enabled(self, enabled: bool) -> None:
        """文字工具：草稿/笔记画布与 PDF/Word 页面批注都可用。"""
        self._text_enabled = bool(enabled)
        self._apply_select_visibility()

    def set_word_edit_enabled(self, enabled: bool) -> None:
        """只有可编辑的 Word 文档才显示「编辑」按钮。"""
        self._word_edit_enabled = bool(enabled)
        self._apply_visibility()

    def set_annotate_active(self, active: bool) -> None:
        """外部（批注层模式变化）同步按钮的中性选中态，不回声。"""
        button = self.annotate_button
        if button.isChecked() == bool(active):
            return
        button.blockSignals(True)
        button.setChecked(bool(active))
        button.blockSignals(False)

    def set_annotate_enabled(self, enabled: bool) -> None:
        """二级窗口/草稿浮窗没有全屏批注控制器，不显示入口。"""
        self._annotate_enabled = bool(enabled)
        self._apply_visibility()

    def set_capture_enabled(self, enabled: bool) -> None:
        """草稿浮窗等精简工具栏可以不要截图按钮。"""
        self._capture_enabled = bool(enabled)
        self._apply_visibility()

    def set_new_note_enabled(self, enabled: bool) -> None:
        """只有主窗口能新建笔记（二级窗口没有草稿工作区）。"""
        self._new_note_enabled = bool(enabled)
        self._apply_visibility()

    def set_draft_images_enabled(self, enabled: bool) -> None:
        """插入截图只在草稿模式有意义。"""
        self._draft_images_enabled = bool(enabled)
        self._apply_visibility()

    def set_find_enabled(self, enabled: bool) -> None:
        """查找按钮只在能全文搜索的文档（PDF）里出现。"""
        self._find_enabled = bool(enabled)
        self._apply_visibility()

    def set_split_available(self, available: bool) -> None:
        """分屏只在文档模式出现（草稿标签暂不参与分屏）。"""
        self._split_available = bool(available)
        self._apply_split_visibility()

    def set_split_enabled(self, ready: bool) -> None:
        """有另一个可配对的标签时才可点；没有就灰着（比直接消失好找）。"""
        self._split_ready = bool(ready)
        self.split_button.setEnabled(self._split_available and self._split_ready)

    def _apply_split_visibility(self) -> None:
        self.split_button.setVisible(self._split_available and not self._collapsed)
        self.split_button.setEnabled(self._split_available and self._split_ready)

    def set_split_doc_available(self, available: bool) -> None:
        """草稿纸浮窗工具栏：是否显示「与文档分屏」按钮（主窗口不显示）。"""
        self._split_doc_available = bool(available)
        self._apply_split_doc_visibility()

    def _apply_split_doc_visibility(self) -> None:
        visible = self._split_doc_available and not self._collapsed
        self.split_doc_button.setVisible(visible)
        self.split_doc_button.setEnabled(visible)

    def _annotation_widgets(self) -> list[QWidget]:
        return [self.undo_button, self.redo_button,
                self.pen_button, self.highlighter_button, self.eraser_button,
                *self.width_buttons, self.size_slider, self.size_label,
                *self.color_buttons, self.custom_color_button,
                self.current_color_swatch, self.clear_button]

    def _apply_visibility(self) -> None:
        visible = self._annotations_enabled and not self._collapsed
        gaps_visible = self._annotations_enabled and not self._collapsed
        for gap in self._group_gaps:
            if self._vertical:
                gap.changeSize(1, COLUMN_GROUP_GAP if gaps_visible else 0,
                               QSizePolicy.Minimum, QSizePolicy.Fixed)
            else:
                gap.changeSize(GROUP_GAP if gaps_visible else 0, 1,
                               QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.layout().invalidate()
        if self._annotation_visible_state is not visible:
            for widget in self._annotation_widgets():
                widget.setVisible(visible)
            self._annotation_visible_state = visible
        scratch = not self._collapsed
        if self._scratch_visible_state is not scratch:
            self.scratch_button.setVisible(scratch)
            self._scratch_visible_state = scratch
        self.word_button.setVisible(self._word_edit_enabled and not self._collapsed)
        self.image_button.setVisible(
            self._draft_images_enabled and not self._collapsed)
        self.find_button.setVisible(self._find_enabled and not self._collapsed)
        self.capture_button.setVisible(
            self._capture_enabled and not self._collapsed)
        self.annotate_button.setVisible(
            self._annotate_enabled and not self._collapsed)
        self.new_note_button.setVisible(
            self._new_note_enabled and not self._collapsed)
        self.settings_button.setVisible(not self._collapsed)   # 全局按钮，收起时一起藏
        self._apply_select_visibility()
        self._apply_split_visibility()
        self._apply_split_doc_visibility()

    def _apply_select_visibility(self) -> None:
        """鼠标工具对文本/导图阅读器也保留（原生选字/拖动画布），笔记仍只给 PDF。"""
        expanded = not self._collapsed
        self.select_button.setVisible(self._select_enabled and expanded)
        self.note_button.setVisible(
            self._note_enabled and self._annotations_enabled and expanded)
        self.text_button.setVisible(self._text_enabled and expanded)

    def _shape_slider(self) -> None:
        """横排 120px 宽；竖排 20px 宽 × 120px 高，数字在窄列里居中。"""
        slider = self.size_slider
        if self._vertical:
            slider.setOrientation(Qt.Vertical)
            slider.setFixedWidth(VERTICAL_SLIDER_WIDTH)
            slider.setFixedHeight(SLIDER_LENGTH)
            self.size_label.setAlignment(Qt.AlignCenter)
        else:
            slider.setOrientation(Qt.Horizontal)
            slider.setFixedWidth(SLIDER_LENGTH)
            slider.setMinimumHeight(0)
            slider.setMaximumHeight(_MAX_SIZE)
            self.size_label.setAlignment(Qt.AlignCenter)

    @property
    def vertical(self) -> bool:
        return self._vertical

    def set_dock(self, dock: str) -> None:
        """QSS 用：顶部并进顶栏（透明），右侧独立成玻璃卡。"""
        if dock not in ("top", "right"):
            raise ValueError(f"未知工具栏位置：{dock!r}（只支持 top / right）")
        if self.property("dock") == dock:
            return
        self.setProperty("dock", dock)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_vertical(self, on: bool) -> None:
        """切成右侧竖排（True）或顶部横排（False）；收起态保持对应方向的窄条。"""
        on = bool(on)
        if on == self._vertical:
            return
        self._vertical = on
        self._rebuild_layout()
        self._apply_collapse_visuals()
        self.updateGeometry()

    # --- 折叠 ---

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, on: bool) -> None:
        """收起时隐藏除折叠键外的所有控件：横排压成 22px 高、竖排压成 22px 宽。"""
        on = bool(on)
        if on == self._collapsed:
            return
        self._collapsed = on
        self._apply_visibility()
        self._apply_collapse_visuals()
        self.updateGeometry()
        self.collapsedChanged.emit(on)

    def _chevron_direction(self) -> str:
        """横排收起朝下/展开朝上；右侧竖排收起朝右/展开朝左。"""
        if self._vertical:
            return "left" if self._collapsed else "right"
        return "down" if self._collapsed else "up"

    def _apply_collapse_visuals(self) -> None:
        """按当前方向重画收起态：箭头、提示、以及唯一那条固定尺寸。

        先无条件解除两个方向的固定尺寸，再按方向只压一条，换方向时才不会
        把上一种方向的 22px 限制带过去。
        """
        self.collapse_button.setIcon(icons.chevron_icon(self._chevron_direction()))
        self.collapse_button.setToolTip("显示工具栏" if self._collapsed else "隐藏工具栏")
        self.setMinimumWidth(0)
        self.setMaximumWidth(_MAX_SIZE)
        self.setMinimumHeight(0)
        self.setMaximumHeight(_MAX_SIZE)
        if self._collapsed:
            if self._vertical:
                self.setFixedWidth(COLLAPSED_BAND)
            else:
                self.setFixedHeight(COLLAPSED_BAND)

    def _tool_button(self, icon: QIcon, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icon)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setMinimumSize(HIT_TARGET, HIT_TARGET)   # 只放图标，但点击区别太小
        button.setToolTip(tooltip)
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        return button

    # --- 颜色与工具 ---

    def _color_slot(self, tool: str | None = None) -> str:
        """当前该改哪一份颜色：荧光笔单独一份，其余工具（含橡皮/选择）都改画笔色。"""
        active = self._tool if tool is None else tool
        return "highlighter" if active == "highlighter" else "pen"

    def _pick_color(self, color: str) -> None:
        """色块点击：写进当前工具的颜色槽，只留一个选中，再广播。"""
        self._colors[self._color_slot()] = color
        self._reflect_color(color)
        self.colorPicked.emit(color)

    def reflect_color(self, color: str, tool: str | None = None) -> None:
        """外部同步某个工具的配色；只有它是当前工具时才更新可见色板。"""
        slot = self._color_slot(tool)
        self._colors[slot] = color
        if slot == self._color_slot():
            self._reflect_color(color)

    def _reflect_color(self, color: str) -> None:
        """把当前色反映到色块选中态 + 取色器提示 + 当前色指示器（都不发信号）。"""
        self._color = color
        for button in self.color_buttons:
            button.setChecked(button.property("color").lower() == color.lower())
        self.custom_color_button.setToolTip(f"自定义颜色（当前 {color}）")
        self.current_color_swatch.setStyleSheet(
            f"QFrame#CurrentColor {{background-color: {color};"
            f" border: 2px solid {theme.ACCENT};"
            f" border-radius: {CURRENT_COLOR_SIZE // 2}px;}}")
        self.current_color_swatch.setToolTip(f"当前颜色 {color}")

    def _pick_custom_color(self) -> None:
        """取色器：取消对话框就不改当前色。"""
        title = ("选择荧光笔颜色" if self._color_slot() == "highlighter"
                 else "选择画笔颜色")
        chosen = QColorDialog.getColor(QColor(self._color), self, title)
        if chosen.isValid():
            self._pick_color(chosen.name())

    # --- 外部状态反射（键盘改了工具/粗细，这里只同步选中态）---

    def reflect_tool(self, tool: str) -> None:
        """把视图当前工具反映到按钮；同时把色板切到该工具记住的颜色。"""
        self._tool = tool
        if tool == "select":
            button = self.select_button
        elif tool == "note":
            button = self.note_button
        elif tool == "text":
            button = self.text_button
        elif tool == "eraser":
            button = self.eraser_button
        elif tool == "highlighter":
            button = self.highlighter_button
        else:
            button = self.pen_button
        button.setChecked(True)
        self._reflect_color(self._colors[self._color_slot()])

    def eventFilter(self, obj, event) -> bool:
        """粗细数字可点：左键松开弹输入框，直接敲数值。"""
        if (obj is self.size_label
                and event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton):
            self._prompt_width()
            return True
        return super().eventFilter(obj, event)

    def _prompt_width(self) -> None:
        """弹一个小输入框改粗细；取消或非法值都不动。"""
        current = self.size_slider.value() / float(SLIDER_SCALE)
        value, ok = QInputDialog.getDouble(
            self, "自定义粗细",
            f"粗细（{WIDTH_MIN:g}–{WIDTH_MAX:g}）",
            current, WIDTH_MIN, WIDTH_MAX, 1)
        if ok:
            self._pick_width(value)

    def _pick_width(self, width: float) -> None:
        """点预设：生效并把滑杆同步过去（reflect 里会屏蔽信号，不回声）。"""
        self.reflect_width(width)
        self.widthChosen.emit(float(width))

    def _on_slider_value(self, value: int) -> None:
        width = value / SLIDER_SCALE
        self.size_label.setText(f"{width:g}")
        self._sync_preset_checks(width)
        self.widthChosen.emit(width)

    def _sync_preset_checks(self, width: float) -> None:
        """把四档预设的勾选对齐到 width；互斥组必须先关互斥才能全部取消。"""
        hits = [candidate == width for candidate in WIDTHS]
        self._width_group.setExclusive(False)
        for button, hit in zip(self.width_buttons, hits):
            button.setChecked(hit)
        self._width_group.setExclusive(True)

    def reflect_width(self, width: float) -> None:
        """把当前粗细反映到预设按钮 + 滑杆 + 数字；不回声（滑块信号被屏蔽）。"""
        width = max(WIDTH_MIN, min(WIDTH_MAX, float(width)))
        self._sync_preset_checks(width)
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(int(round(width * SLIDER_SCALE)))
        self.size_slider.blockSignals(False)
        self.size_label.setText(f"{width:g}")
