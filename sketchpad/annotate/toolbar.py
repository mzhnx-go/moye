"""纯图标工具栏：撤销 / 钢笔 / 橡皮 / 粗细 / 颜色盘 / 清空 / 穿透 / 收起。

参考截图的做法：深灰外壳、图标按钮、蓝色选中方块、圆形色块 + 当前色大圆点。
粗细做成四个由小到大的圆点（与截图左侧那排点同形），不加滑杆——笔粗只有几档
有意义，圆点比滑杆更好点、也不占地方。穿透按钮是可勾选的开关：进入穿透后整块
变亮绿、图标换成深色，再点一下回到批注（不再另开「回到批注」浮条）。

可以拖、也可以换方向：左/上端那条点阵是拖动把手（工具栏本身的空白处同样能拖），
最右/最下端那颗 swap 按钮切换横排↔竖排。位置与方向都写进 settings.json。

为什么工具栏自己就是顶层窗口：批注层进入穿透模式后整层带 WS_EX_TRANSPARENT，
不接收任何点击，挂在它下面的子控件也一起失联。工具栏必须是独立 hwnd，才能在
穿透模式下继续接点击——这也是替代旧版 PassthroughChip 的关键。

工具栏只发信号、不读画布；选中态由 reflect_* 倒映，尺寸与位置由 overlay 负责。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QBoxLayout, QButtonGroup, QColorDialog, QDialog,
                               QFrame, QInputDialog, QLabel, QSizePolicy, QSlider,
                               QToolButton, QWidget)

from sketchpad.annotate import icons, theme
from sketchpad.annotate.constants import (DEFAULT_COLOR, DEFAULT_TOGGLE_HOTKEY, MUTED,
                                  PALETTE, PALETTE_NAMES, PEN_WIDTH, PEN_WIDTH_MAX,
                                  PEN_WIDTH_MIN, PEN_WIDTHS, SHELL, SHELL_BORDER,
                                  TOOLBAR_HEIGHT)

ORIENT_HORIZONTAL = "horizontal"
ORIENT_VERTICAL = "vertical"
ORIENTATIONS = (ORIENT_HORIZONTAL, ORIENT_VERTICAL)

# 布局尺寸：40 厚时上下各留 5，正好放下 30 的按钮
BUTTON_SIZE = 30
ICON_SIZE = 18
SWATCH_SIZE = 20                 # 圆形色块直径
CURRENT_COLOR_SIZE = 24          # 当前颜色大圆点
SEPARATOR_HEIGHT = 18
SIDE_MARGIN = 6
ROW_SPACING = 2
CUSTOM_COLOR_GAP = 4             # 色块组与自定义取色之间留一点呼吸
GRIP_LENGTH = 10                 # 拖动把手占的那一条

# 粗细按钮里的圆点直径（24 逻辑坐标内，图标按 0.75 缩小显示），与 PEN_WIDTHS 一一对应。
# 上限刻意压在色块（20px）之下，粗细组才不会跟右边的色盘混成一片。
WIDTH_DOTS = (6.0, 9.0, 12.0, 15.0)
WIDTH_BUTTON_WIDTH = 26

# 自定义粗细：滑杆按 0.1 一档，数字框点击可直接输入（与主工具栏同一套口径）
SLIDER_SCALE = 10
SLIDER_LENGTH = 64
WIDTH_VALUE_WIDTH = 34
WIDTH_VALUE_HEIGHT = 22

_QWIDGET_SIZE_MAX = 16777215     # QWIDGETSIZE_MAX


class _WidthValue(QLabel):
    """粗细数字：外观像可点的输入框，左键松开后由工具栏弹数值输入。"""

    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("widthValue")
        self.setAlignment(Qt.AlignCenter)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QLabel#widthValue { background: #3a3a3c; color: #e8eaed;"
            " border: 1px solid #5a5a5e; border-radius: 4px; padding: 0 2px; }"
            "QLabel#widthValue:hover { border-color: #4dd4ac; }")

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self.rect().contains(
                ev.position().toPoint()):
            self.clicked.emit()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class _DragGrip(QWidget):
    """拖动把手：只把鼠标拖动换算成窗口移动，不参与任何按钮逻辑。

    光靠外壳那圈 8px 边距当抓手太难按，所以单独留一条点阵带。
    """

    def __init__(self, toolbar: "Toolbar") -> None:
        super().__init__(toolbar)
        self._toolbar = toolbar
        self.setObjectName("DragGrip")
        self.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("按住拖动工具栏；双击切换横排 / 竖排")

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self._toolbar.begin_drag(ev.globalPosition().toPoint())
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._toolbar.dragging:
            self._toolbar.drag_to(ev.globalPosition().toPoint())
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._toolbar.dragging:
            self._toolbar.end_drag()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        """双击把手直接换方向，不用去够那颗 swap 按钮。"""
        if ev.button() == Qt.LeftButton:
            self._toolbar.toggle_orientation()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def paintEvent(self, event) -> None:
        """三个小点：把手方向跟着排布走（横排是竖三点，竖排是横三点）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(MUTED))
        horizontal = self._toolbar.orientation == ORIENT_HORIZONTAL
        for index in (-1, 0, 1):
            if horizontal:
                painter.drawEllipse(QPoint(self.width() // 2,
                                           self.height() // 2 + index * 5), 1.6, 1.6)
            else:
                painter.drawEllipse(QPoint(self.width() // 2 + index * 5,
                                           self.height() // 2), 1.6, 1.6)
        painter.end()


class Toolbar(QWidget):
    """图标工具栏；按钮只负责发信号，状态由外部倒映。"""

    toolChosen = Signal(str)          # "pen" | "eraser"
    colorChosen = Signal(str)         # "#rrggbb"
    widthChosen = Signal(float)       # 线宽（屏幕像素）
    undoRequested = Signal()
    clearRequested = Signal()
    captureRequested = Signal()      # 框选屏幕区域 → 剪贴板
    passthroughToggled = Signal()     # 点一下：批注 <-> 穿透
    hideRequested = Signal()
    orientationChanged = Signal(str)  # "horizontal" | "vertical"
    moved = Signal(int, int)          # 拖动结束后的窗口左上角（全局坐标）

    def __init__(self, orientation: str = ORIENT_HORIZONTAL) -> None:
        # 刻意不认 parent：成为批注层的子控件就会跟着那层的点击穿透一起失效。
        # 这里只借用调用签名，窗口完全独立（overlay 用全局坐标定位）。
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.setObjectName("ToolbarRoot")
        # 逐像素透明才露出圆角外的桌面；show 时不激活，别把正在看的视频挤走
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)   # 焦点永远留给画布，B / Q / E 才不会被吃掉
        self.setWindowTitle("批注工具栏")
        self.setStyleSheet(theme.TOOLBAR_QSS)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._color = DEFAULT_COLOR
        self._tool = "pen"
        self._orientation = (orientation if orientation in ORIENTATIONS
                             else ORIENT_HORIZONTAL)
        self._items: list[QWidget] = []
        self._separators: list[QFrame] = []
        self._gaps: list[QWidget] = []
        self._drag_offset: QPoint | None = None

        self._build()
        self._apply_layout()

        self.reflect_tool("pen")
        self.reflect_color(DEFAULT_COLOR)
        self.reflect_width(PEN_WIDTH)
        self.reflect_history(False)
        self.reflect_mode("draw")

    # ---------- 构建 ----------

    def _build(self) -> None:
        """按顺序建好所有控件；真正的排布交给 _apply_layout。"""
        items = self._items

        self.grip = _DragGrip(self)
        items.append(self.grip)

        # 1. 三态互斥：穿透 / 钢笔 / 橡皮，任何时候只亮一个。
        #    穿透不是"叠加开关"，它就是三选一里的一个状态——亮着表示点击落到下层。
        self._tool_group = QButtonGroup(self)
        self.passthrough_button = self._tool_button(
            "passthroughButton", icons.through_icon(),
            f"穿透：点击落到下层。想写字就点钢笔 / 橡皮 ({DEFAULT_TOGGLE_HOTKEY})")
        self.pen_button = self._tool_button(
            "penButton", icons.pen_icon(), "钢笔 (B)")
        self.eraser_button = self._tool_button(
            "eraserButton", icons.eraser_icon(), "橡皮 (Q)：整条擦除")

        self.passthrough_button.setCheckable(True)
        self.passthrough_button.clicked.connect(
            lambda _checked=False: self.passthroughToggled.emit())
        for button, tool in ((self.pen_button, "pen"),
                             (self.eraser_button, "eraser")):
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, t=tool: self.toolChosen.emit(t))
        for button in (self.passthrough_button, self.pen_button,
                       self.eraser_button):
            self._tool_group.addButton(button)     # 互斥组：永远只有一个亮点
            items.append(button)
        items.append(self._separator())

        # 2. 粗细四档：四个由小到大的圆点，互斥；选中的那档压在绿方块上
        self._width_group = QButtonGroup(self)
        self.width_buttons: list[QToolButton] = []
        for index, (width, dot) in enumerate(zip(PEN_WIDTHS, WIDTH_DOTS)):
            button = self._width_button(index, dot, width)
            button.clicked.connect(
                lambda _checked=False, w=width: self.widthChosen.emit(float(w)))
            self._width_group.addButton(button)    # 互斥组：永远只亮一档
            self.width_buttons.append(button)
            items.append(button)

        # 自定义粗细：滑杆调 1–30，右侧数字可点开输入框直接敲值
        self.width_slider = QSlider(Qt.Horizontal, self)
        self.width_slider.setObjectName("widthSlider")
        self.width_slider.setRange(int(PEN_WIDTH_MIN * SLIDER_SCALE),
                                   int(PEN_WIDTH_MAX * SLIDER_SCALE))
        self.width_slider.setSingleStep(1)
        self.width_slider.setPageStep(4)
        self.width_slider.setFocusPolicy(Qt.NoFocus)
        self.width_slider.setToolTip(
            f"自定义粗细（{PEN_WIDTH_MIN:g}–{PEN_WIDTH_MAX:g}）")
        self.width_slider.valueChanged.connect(self._on_slider_value)
        items.append(self.width_slider)

        self.width_value = _WidthValue(self)
        self.width_value.setFixedWidth(WIDTH_VALUE_WIDTH)
        self.width_value.setMinimumHeight(WIDTH_VALUE_HEIGHT)
        self.width_value.setToolTip("当前粗细（点数字可直接输入数值）")
        self.width_value.clicked.connect(self._prompt_width)
        items.append(self.width_value)
        items.append(self._separator())

        # 5. 9 个圆形色块：与主软件同一顺序，红/蓝在前；选中时描 2px 白边
        self.color_buttons: list[QToolButton] = []
        for index, color in enumerate(PALETTE):
            button = self._color_button(index, color)
            button.clicked.connect(
                lambda _checked=False, c=color: self.colorChosen.emit(c))
            self.color_buttons.append(button)
            items.append(button)

        # 6. 自定义取色：只负责打开 QColorDialog，不在按钮上缓存选中态
        items.append(self._gap())
        self.custom_color_button = self._tool_button(
            "customColorButton", icons.palette_icon(),
            f"自定义颜色（当前 {self._color}）")
        self.custom_color_button.clicked.connect(self._pick_custom_color)
        items.append(self.custom_color_button)

        # 7. 当前颜色大圆点：纯显示，不接收鼠标和焦点
        items.append(self._gap())
        self.current_color_swatch = QFrame(self)
        self.current_color_swatch.setObjectName("currentColorSwatch")
        self.current_color_swatch.setAttribute(
            Qt.WA_TransparentForMouseEvents, True)
        self.current_color_swatch.setFocusPolicy(Qt.NoFocus)
        items.extend([self.current_color_swatch, self._separator()])

        # 9. 清空 → 撤销 → 截图 → 收起 → 换方向
        self.clear_button = self._tool_button(
            "clearButton", icons.clear_icon(), "清空 (E)")
        self.clear_button.clicked.connect(
            lambda _checked=False: self.clearRequested.emit())
        items.append(self.clear_button)

        # 撤销紧跟在清空后面：清完想反悔，手不用跑回最左边
        self.undo_button = self._tool_button(
            "undoButton", icons.undo_icon(), "撤销 (Ctrl+Z)")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(
            lambda _checked=False: self.undoRequested.emit())
        items.append(self.undo_button)

        # 截图紧跟其后：批注一半想先存一版到剪贴板，手顺势就能点到
        self.capture_button = self._tool_button(
            "captureButton", icons.capture_icon(), "截图：框选屏幕区域并复制到剪贴板")
        self.capture_button.clicked.connect(
            lambda _checked=False: self.captureRequested.emit())
        items.append(self.capture_button)

        self.hide_button = self._tool_button(
            "hideButton", icons.chevron_icon("up"), "收起（Ctrl+Alt+H）")
        self.hide_button.clicked.connect(
            lambda _checked=False: self.hideRequested.emit())
        items.append(self.hide_button)

        self.orient_button = self._tool_button(
            "orientButton", icons.orient_icon(), "切换横排 / 竖排（双击把手也行）")
        self.orient_button.clicked.connect(
            lambda _checked=False: self.toggle_orientation())
        items.append(self.orient_button)

        # 布局对象只建一次，切换方向只改 direction / 对齐 / 分隔线尺寸，
        # 这样控件引用不会变，外部（overlay、测试）持有的按钮一直有效
        self._layout = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._layout.setSpacing(ROW_SPACING)

    def _tool_button(self, name: str, icon, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(name)
        button.setIcon(icon)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.NoFocus)      # 点完工具栏，B / Q / E 仍归画布
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        return button

    def _width_button(self, index: int, dot: float, width: float) -> QToolButton:
        """粗细档位按钮：图标就是这一档的粗圆点，尺寸比普通按钮略窄。"""
        button = QToolButton(self)
        button.setObjectName(f"widthButton{index}")
        button.setIcon(icons.width_icon(dot))
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setCheckable(True)
        button.setToolTip(f"粗细 {index + 1} / {len(PEN_WIDTHS)}（{width:g}px）")
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(WIDTH_BUTTON_WIDTH, BUTTON_SIZE)
        return button

    def _color_button(self, index: int, color: str) -> QToolButton:
        """圆形色块：底色即颜色本身，选中 2px 白边（参考截图的做法）。

        样式写在按钮自己身上（而不是工具栏 QSS 里），8 个按钮才能各用各的颜色；
        选中态也把底色再写一遍，避免父级 `QToolButton:checked` 的蓝底透上来。
        """
        button = QToolButton(self)
        button.setObjectName(f"colorButton{index}")
        button.setCheckable(True)
        button.setAutoExclusive(False)         # 选中态由 reflect_color 统一倒映
        name = PALETTE_NAMES[index] if index < len(PALETTE_NAMES) else ""
        button.setToolTip(f"{index + 1} · {name} {color}".strip())
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)
        radius = SWATCH_SIZE // 2
        button.setStyleSheet(
            f"QToolButton {{ background-color: {color};"
            f" border: 1px solid rgba(255, 255, 255, 0.30);"
            f" border-radius: {radius}px; padding: 0; }}"
            "QToolButton:hover { border: 1px solid rgba(255, 255, 255, 0.75); }"
            f"QToolButton:checked {{ background-color: {color};"
            " border: 2px solid #ffffff; }"
            f"QToolButton:checked:hover {{ background-color: {color};"
            " border: 2px solid #ffffff; }"
        )
        return button

    def _separator(self) -> QFrame:
        """分组分隔线：固定 1px 厚，方向由 _apply_layout 决定。"""
        line = QFrame(self)
        line.setObjectName("Sep")
        line.setFrameShape(QFrame.NoFrame)
        self._separators.append(line)
        return line

    def _gap(self) -> QWidget:
        """色块组与自定义取色之间的呼吸条；方向变了要跟着换宽高。"""
        gap = QWidget(self)
        gap.setObjectName("Gap")
        gap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._gaps.append(gap)
        return gap

    # ---------- 排布 ----------

    @property
    def orientation(self) -> str:
        return self._orientation

    def set_orientation(self, orientation: str, *, notify: bool = True) -> None:
        """切换横排 / 竖排；非法值忽略（保持当前方向）。"""
        if orientation not in ORIENTATIONS or orientation == self._orientation:
            return
        self._orientation = orientation
        self._apply_layout()
        if notify:
            self.orientationChanged.emit(orientation)

    def toggle_orientation(self) -> None:
        self.set_orientation(ORIENT_VERTICAL
                             if self._orientation == ORIENT_HORIZONTAL
                             else ORIENT_HORIZONTAL)

    def _apply_layout(self) -> None:
        """按当前方向重排：方向、边距、对齐、分隔线/间隙尺寸、窗口固定维度。"""
        horizontal = self._orientation == ORIENT_HORIZONTAL
        layout = self._layout
        # 先清空 items 再按新对齐加回去；控件本身不销毁，信号连接与选中态都保留
        while layout.count():
            layout.takeAt(0)
        layout.setDirection(QBoxLayout.LeftToRight if horizontal
                            else QBoxLayout.TopToBottom)

        margin = max(4, (TOOLBAR_HEIGHT - BUTTON_SIZE) // 2)
        if horizontal:
            layout.setContentsMargins(SIDE_MARGIN, margin, SIDE_MARGIN, margin)
        else:
            layout.setContentsMargins(margin, SIDE_MARGIN, margin, SIDE_MARGIN)

        align = Qt.AlignVCenter if horizontal else Qt.AlignHCenter
        for widget in self._items:
            layout.addWidget(widget, 0, align)

        for separator in self._separators:
            separator.setFixedSize(*( (1, SEPARATOR_HEIGHT) if horizontal
                                     else (SEPARATOR_HEIGHT, 1) ))
        for gap in self._gaps:
            gap.setFixedSize(*((CUSTOM_COLOR_GAP, 1) if horizontal
                               else (1, CUSTOM_COLOR_GAP)))
        self.current_color_swatch.setFixedSize(
            CURRENT_COLOR_SIZE, CURRENT_COLOR_SIZE)
        self.width_slider.setOrientation(
            Qt.Horizontal if horizontal else Qt.Vertical)
        if horizontal:
            self.width_slider.setFixedSize(SLIDER_LENGTH, WIDTH_VALUE_HEIGHT)
        else:
            self.width_slider.setFixedSize(WIDTH_VALUE_HEIGHT, SLIDER_LENGTH)
        self.width_value.setFixedSize(WIDTH_VALUE_WIDTH, WIDTH_VALUE_HEIGHT)
        if horizontal:
            self.grip.setFixedSize(GRIP_LENGTH, BUTTON_SIZE)
        else:
            self.grip.setFixedSize(BUTTON_SIZE, GRIP_LENGTH)

        # 长宽都用 setFixedSize 钉死：只锁"厚度"的话，layout 会在事件循环里
        # 按 minimumSizeHint 把窗口又缩一次（竖排切回横排时实测从 749×48 掉到
        # 499×32），画面被拉成畸形、鼠标命中还算在旧表面上，整条都点不动。
        self.setMinimumSize(0, 0)
        self.setMaximumSize(_QWIDGET_SIZE_MAX, _QWIDGET_SIZE_MAX)
        layout.invalidate()
        layout.activate()
        hint = self._layout.sizeHint()
        if horizontal:
            self.setFixedSize(hint.width(), TOOLBAR_HEIGHT)
        else:
            self.setFixedSize(TOOLBAR_HEIGHT, hint.height())

    def sizeHint(self) -> QSize:
        """内容尺寸：横排取宽×44，竖排取 44×高；overlay 用它定位。"""
        hint = self._layout.sizeHint()
        if self._orientation == ORIENT_HORIZONTAL:
            return QSize(hint.width(), TOOLBAR_HEIGHT)
        return QSize(TOOLBAR_HEIGHT, hint.height())

    # ---------- 拖动 ----------

    @property
    def dragging(self) -> bool:
        return self._drag_offset is not None

    def begin_drag(self, global_pos: QPoint) -> None:
        self._drag_offset = global_pos - self.frameGeometry().topLeft()
        self.grip.setCursor(Qt.ClosedHandCursor)

    def drag_to(self, global_pos: QPoint) -> None:
        if self._drag_offset is None:
            return
        self.move(global_pos - self._drag_offset)

    def end_drag(self) -> None:
        if self._drag_offset is None:
            return
        self._drag_offset = None
        self.grip.setCursor(Qt.OpenHandCursor)
        self.moved.emit(self.x(), self.y())

    # 外壳空白处也能拖：按钮会自己吞掉按下事件，剩下的边距就落在这里
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.begin_drag(ev.globalPosition().toPoint())
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self.dragging:
            self.drag_to(ev.globalPosition().toPoint())
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self.dragging:
            self.end_drag()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    # ---------- 自定义取色 ----------

    def _pick_custom_color(self) -> None:
        """打开系统取色器；确认后把颜色交给 colorChosen，按钮本身不记状态。

        对话框挂在顶层并置顶，避免落到穿透/透明批注层后面看不见；
        关闭后的焦点归还是 overlay 的事，这里不抢。
        """
        dialog = QColorDialog(self)
        dialog.setWindowTitle("自定义颜色")
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setOption(QColorDialog.ShowAlphaChannel, False)
        dialog.setCurrentColor(theme.qcolor(self._color))
        try:
            if dialog.exec() != QDialog.Accepted:
                return
            chosen = dialog.selectedColor()
        finally:
            dialog.deleteLater()
        if chosen.isValid():
            self.colorChosen.emit(chosen.name())

    # ---------- 外部状态倒映 ----------

    def reflect_tool(self, tool: str) -> None:
        """点亮当前工具；未知工具按画笔处理，避免按钮全灭、看不出在画什么。"""
        self._tool = "eraser" if tool == "eraser" else "pen"
        target = self.eraser_button if self._tool == "eraser" else self.pen_button
        target.setChecked(True)

    def reflect_color(self, color: str) -> None:
        """色块勾选 + 当前色圆点 + 自定义取色 tooltip，一处更新三处。"""
        parsed = theme.qcolor(color)
        normalized = parsed.name() if parsed.isValid() else color
        self._color = normalized

        match = normalized.lower()
        for button, palette_color in zip(self.color_buttons, PALETTE):
            button.setChecked(palette_color.lower() == match)

        # 只用当前色重刷圆点：白边 2px，样式串里不再留上一个颜色
        self.current_color_swatch.setStyleSheet(
            f"QFrame#currentColorSwatch {{ background-color: {normalized};"
            f" border: 2px solid #ffffff;"
            f" border-radius: {CURRENT_COLOR_SIZE // 2}px; }}")
        self.custom_color_button.setToolTip(f"自定义颜色（当前 {normalized}）")

    def reflect_width(self, width: float) -> None:
        """点亮与当前线宽最接近的那一档，并同步滑杆/数字；不回声。"""
        try:
            value = float(width)
        except (TypeError, ValueError):
            value = float(PEN_WIDTH)
        value = max(PEN_WIDTH_MIN, min(PEN_WIDTH_MAX, value))
        self._check_nearest_width(value)
        self.width_slider.blockSignals(True)
        self.width_slider.setValue(int(round(value * SLIDER_SCALE)))
        self.width_slider.blockSignals(False)
        self.width_value.setText(f"{value:g}")

    def _check_nearest_width(self, width: float) -> None:
        """粗细圆点永远只亮离当前值最近的一档（自定义值也能看出大概）。"""
        nearest = min(range(len(PEN_WIDTHS)),
                      key=lambda i: abs(PEN_WIDTHS[i] - width))
        for index, button in enumerate(self.width_buttons):
            button.setChecked(index == nearest)
        self._sync_icon_colors()

    def _on_slider_value(self, raw_value: int) -> None:
        """滑杆按 0.1 一档：先更新数字与圆点，再把新线宽交给 overlay。"""
        width = raw_value / float(SLIDER_SCALE)
        self.width_value.setText(f"{width:g}")
        self._check_nearest_width(width)
        self.widthChosen.emit(width)

    def _prompt_width(self) -> None:
        """点数字直接输入线宽；取消或非法值都不动。"""
        current = self.width_slider.value() / float(SLIDER_SCALE)
        dialog = QInputDialog(self)
        dialog.setWindowTitle("自定义粗细")
        dialog.setLabelText(f"粗细（{PEN_WIDTH_MIN:g}–{PEN_WIDTH_MAX:g}）")
        dialog.setInputMode(QInputDialog.DoubleInput)
        dialog.setDoubleRange(PEN_WIDTH_MIN, PEN_WIDTH_MAX)
        dialog.setDoubleDecimals(1)
        dialog.setDoubleValue(current)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.setWindowModality(Qt.WindowModal)
        try:
            if dialog.exec() != QDialog.Accepted:
                return
            chosen = float(dialog.doubleValue())
        finally:
            dialog.deleteLater()
        self.reflect_width(chosen)
        self.widthChosen.emit(chosen)

    def reflect_history(self, can_undo: bool) -> None:
        """撤销可用性由画布历史决定，工具栏只照做。"""
        self.undo_button.setEnabled(bool(can_undo))

    def reflect_mode(self, mode: str) -> None:
        """三态互斥的唯一权威：穿透亮着 = 现在点击落到底下的软件。

        批注状态下按当前工具点亮钢笔或橡皮——穿透和画笔不可能同时亮。
        亮绿底上的图标必须换深色版，否则白图糊成一片，所以图标同步挂在这里。
        """
        if mode == "passthrough":
            self.passthrough_button.setChecked(True)
        else:
            target = (self.eraser_button if self._tool == "eraser"
                      else self.pen_button)
            target.setChecked(True)
        self._sync_icon_colors()

    def _sync_icon_colors(self) -> None:
        """按各自的选中态刷新图标配色：绿底用深色，深灰底用白色。"""
        self.pen_button.setIcon(icons.pen_icon(dark=self.pen_button.isChecked()))
        self.eraser_button.setIcon(
            icons.eraser_icon(dark=self.eraser_button.isChecked()))
        self.passthrough_button.setIcon(
            icons.through_icon(dark=self.passthrough_button.isChecked()))
        for index, button in enumerate(self.width_buttons):
            button.setIcon(icons.width_icon(WIDTH_DOTS[index],
                                            dark=button.isChecked()))

    # ---------- 外观 ----------

    def paintEvent(self, event) -> None:
        """自绘深灰圆角外壳，不依赖 QSS 给顶层半透明窗画底。

        半透明顶层窗口的根背景在离屏抓图 / 部分合成路径下会丢，而工具栏是
        穿透模式下唯一的交互入口，外观必须确定可见；按钮样式仍交给 TOOLBAR_QSS。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 内缩半像素：1px 描边才能压在像素格上，不发虚
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(theme.qcolor(SHELL_BORDER), 1))
        painter.setBrush(theme.qcolor(SHELL))
        painter.drawRoundedRect(rect, theme.TOOLBAR_RADIUS, theme.TOOLBAR_RADIUS)
        painter.end()
