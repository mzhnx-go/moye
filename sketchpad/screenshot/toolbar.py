"""截图工具栏：白底黑图标的单行工具条。

图标全部来自 `screenshot/icons.py`（QPainter 现画），不再用汉字拼接；
悬停显示“名称 + 快捷键 + 行为”。选中态用浅蓝底 + 蓝边，保持白底可读。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel,
                               QToolButton, QWidget)
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from sketchpad.screenshot import icons
from sketchpad.screenshot.constants import (ANNOTATION_COLORS, DEFAULT_COLOR,
                                            DEFAULT_WIDTH, TOOL_ARROW,
                                            TOOL_BLUR, TOOL_CROP, TOOL_ELLIPSE,
                                            TOOL_MARKER, TOOL_MOSAIC,
                                            TOOL_NUMBER, TOOL_PEN, TOOL_RECT,
                                            TOOL_SELECT, TOOL_TEXT, WIDTHS)

_TOOLS = (
    (TOOL_SELECT, "指针 (V)", "选择/移动标注；拖选区空白移动选区"),
    (TOOL_RECT, "矩形 (R)", "拖拽画矩形"),
    (TOOL_ELLIPSE, "椭圆 (O)", "拖拽画椭圆"),
    (TOOL_ARROW, "箭头 (A)", "拖拽画箭头"),
    (TOOL_PEN, "画笔 (P)", "自由画笔"),
    (TOOL_MARKER, "马克笔 (M)", "半透明马克笔"),
    (TOOL_TEXT, "文字 (T)", "点击输入；Ctrl+Enter 结束，Esc 结束编辑不关遮罩"),
    (TOOL_NUMBER, "序号 (N)", "点击生成 1、2、3…"),
    (TOOL_MOSAIC, "马赛克 (G)", "框选区域打马赛克"),
    (TOOL_BLUR, "模糊 (B)", "框选区域模糊"),
    (TOOL_CROP, "裁剪 (C)", "把当前选区变成新底图（可撤销）"),
)

_WIDTH_LABELS = {3: "细 3px", 6: "中 6px", 10: "粗 10px"}
_WIDTH_DOTS = {3: 6.0, 6: 10.0, 10: 14.0}

BAR_QSS = """
QWidget#EditorBar {
    background: #ffffff;
    border: 1px solid #c7c9cf;
    border-radius: 8px;
}
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 2px;
}
QToolButton:hover { background: #f2f3f5; }
QToolButton:pressed { background: #e7eaf0; }
QToolButton:checked { background: #dbeafe; border: 1px solid #0a84ff; }
QToolButton:disabled { background: transparent; }
QFrame#BarSep { background: #d9dbe0; max-width: 1px; }
QLabel { color: #1c1c1e; font-size: 12px; }
QLabel#OcrBusy { color: #0a84ff; }
QToolTip {
    background: #ffffff;
    color: #1c1c1e;
    border: 1px solid #c7c9cf;
    border-radius: 5px;
    padding: 5px 8px;
    font-size: 12px;
}
"""

ICON = QSize(18, 18)
BUTTON = 28


class EditorToolbar(QWidget):
    toolChanged = Signal(str)
    undoRequested = Signal()
    redoRequested = Signal()
    copyRequested = Signal()
    saveRequested = Signal()
    pinRequested = Signal()
    ocrRequested = Signal()
    closeRequested = Signal()
    colorChanged = Signal(str)
    widthChanged = Signal(int)

    def __init__(self, ocr_enabled: bool = False, parent=None,
                 allow_drag: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("EditorBar")
        self.setStyleSheet(BAR_QSS)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._allow_drag = bool(allow_drag)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons: dict[str, QToolButton] = {}
        for tool, label, tip in _TOOLS:
            button = self._icon_button(icons.tool_icon(tool), label, tip,
                                       checkable=True)
            button.setObjectName(f"tool_{tool}")
            button.clicked.connect(
                lambda _checked=False, t=tool: self.toolChanged.emit(t))
            self._tool_group.addButton(button)
            self._tool_buttons[tool] = button
            layout.addWidget(button)
        self._tool_buttons[TOOL_SELECT].setChecked(True)
        layout.addWidget(self._sep())

        self._color_group = QButtonGroup(self)
        self._color_group.setExclusive(True)
        self._color_buttons: dict[str, QToolButton] = {}
        for name, color in ANNOTATION_COLORS:
            button = self._icon_button(icons.swatch_icon(color), f"{name}色",
                                       f"{name}色（{color.upper()}）",
                                       checkable=True)
            button.setObjectName(f"color_{color.lstrip('#')}")
            button.clicked.connect(
                lambda _checked=False, c=color: self.colorChanged.emit(c))
            self._color_group.addButton(button)
            self._color_buttons[color] = button
            layout.addWidget(button)
        self._color_buttons[DEFAULT_COLOR].setChecked(True)
        layout.addWidget(self._sep())

        self._width_group = QButtonGroup(self)
        self._width_group.setExclusive(True)
        self._width_buttons: dict[int, QToolButton] = {}
        for width in WIDTHS:
            button = self._icon_button(
                icons.dot_icon(_WIDTH_DOTS.get(width, 10.0)),
                f"粗细 {width}px", _WIDTH_LABELS.get(width, f"粗细 {width}px"),
                checkable=True)
            button.setObjectName(f"width_{width}")
            button.clicked.connect(
                lambda _checked=False, w=width: self.widthChanged.emit(w))
            self._width_group.addButton(button)
            self._width_buttons[width] = button
            layout.addWidget(button)
        self._width_buttons[DEFAULT_WIDTH].setChecked(True)
        layout.addWidget(self._sep())

        self.undo_button = self._action(
            icons.undo_icon(), "撤销 (Ctrl+Z)", "撤销上一步", self.undoRequested,
            "undoButton")
        self.redo_button = self._action(
            icons.redo_icon(), "重做 (Ctrl+Y)", "重做", self.redoRequested,
            "redoButton")
        layout.addWidget(self._sep())
        self._action(icons.copy_icon(), "复制 (Enter)", "复制选区+标注到剪贴板",
                     self.copyRequested, "copyButton")
        self._action(icons.save_icon(), "保存 (Ctrl+S)", "另存为 PNG/JPG/WebP",
                     self.saveRequested, "saveButton")
        self._action(icons.pin_icon(), "贴图", "钉在屏幕上（可移动/缩放/旋转）",
                     self.pinRequested, "pinButton")
        self.ocr_button = self._action(
            icons.ocr_icon(), "OCR", "识别图中文字（仅标准版）",
            self.ocrRequested, "ocrButton")
        self.ocr_button.setVisible(bool(ocr_enabled))
        layout.addStretch(1)
        self.status_label = QLabel("", self)
        self.status_label.setObjectName("OcrBusy")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)
        self.size_label = QLabel("", self)
        self.size_label.setToolTip("截图尺寸（像素）")
        layout.addWidget(self.size_label)
        self._action(icons.close_icon(), "关闭 (Esc)", "放弃本次截图并关闭",
                     self.closeRequested, "closeButton")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)
        self.set_undo_enabled(False)
        self.set_redo_enabled(False)

    # ---------- 控件工厂 ----------

    def _icon_button(self, icon: QIcon, label: str, tip: str,
                     checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icon)
        button.setIconSize(ICON)
        button.setFixedSize(BUTTON, BUTTON)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setToolTip(f"{label}\n{tip}")
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAccessibleName(label)
        return button

    def _action(self, icon: QIcon, label: str, tip: str,
                signal, object_name: str) -> QToolButton:
        button = self._icon_button(icon, label, tip)
        button.setObjectName(object_name)
        button.clicked.connect(lambda _checked=False: signal.emit())
        self.layout().addWidget(button)
        return button

    def _sep(self) -> QFrame:
        line = QFrame(self)
        line.setObjectName("BarSep")
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        return line

    # ---------- 状态 ----------

    def set_tool(self, tool: str) -> None:
        button = self._tool_buttons.get(tool)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def set_color(self, color: str) -> None:
        button = self._color_buttons.get(color)
        if button is not None:
            button.setChecked(True)

    def set_width(self, width: int) -> None:
        button = self._width_buttons.get(width)
        if button is not None:
            button.setChecked(True)

    def set_undo_enabled(self, enabled: bool) -> None:
        self.undo_button.setEnabled(bool(enabled))

    def set_redo_enabled(self, enabled: bool) -> None:
        self.redo_button.setEnabled(bool(enabled))

    def set_size_text(self, text: str) -> None:
        self.size_label.setText(text)

    def set_ocr_enabled(self, enabled: bool) -> None:
        self.ocr_button.setVisible(bool(enabled))

    def set_ocr_busy(self, busy: bool) -> None:
        self.ocr_button.setEnabled(not busy)
        self.status_label.setText("识别中…" if busy else "")
        self.status_label.setVisible(bool(busy))
        self.adjustSize()

    def mousePressEvent(self, event) -> None:
        """空白处按住可拖窗口（截图遮罩里由 allow_drag=False 关闭）。"""
        if event.button() == Qt.MouseButton.LeftButton and self._allow_drag:
            window = self.window()
            handle = window.windowHandle() if window is not None else None
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)
