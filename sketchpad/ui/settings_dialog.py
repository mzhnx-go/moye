"""设置对话框：绘制参数 + 三条可改绑快捷键 + 固定键位表。

捕获键盘时自己吃掉 Esc/Enter：QDialog 默认会在 Esc 上 reject、在 Enter 上点默认按钮，
所以捕获态既不调 super().keyPressEvent，也在 event() 里拦 ShortcutOverride（QShortcut 先于
keyPressEvent，PROGRESS §5）。捕获按键名/修饰键的规范化直接复用 Toolbox 的实现，避免两份。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPalette
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSlider, QTabWidget,
                               QVBoxLayout, QWidget)

from sketchpad.ui import icons, theme
from sketchpad.services.hotkey import parse_hotkey
from sketchpad.core.settings import (PAGE_MODES, TOOLBAR_POSITIONS, WIDTHS, Settings,
                                     is_reserved_key)
from sketchpad.pdf.pagemode import PAGE_MODE_LABELS
from sketchpad.ui.toolbox import (CAPTURE_TEXT, CAPTURE_TIMEOUT_MS, HINT_CAPTURE,
                         PALETTE, SLIDER_SCALE, WIDTH_MAX, WIDTH_MIN,
                         _MODIFIER_KEYS, _MODIFIER_NAMES, _canonical_key,
                         _event_key_text, _modifiers_text, _split_modifiers)

HINT_RESERVED = "这个键已被占用"
HINT_INVALID = "无效热键：需要修饰键 + 一个按键（F1–F24 可单独使用）"
HINT_TIMEOUT = "捕获超时，未修改"
FIXED_BINDINGS = ("B 画笔", "H 荧光笔", "V 鼠标（平移/选字/移图标）", "N 插入笔记（文档模式）",
                  "Q 橡皮", "1–4 粗细",
                  "Ctrl+C 复制选中文字", "Ctrl+Z/Y 撤销/重做",
                  "Ctrl+Shift+Z 重做", "0 / Ctrl+0 缩放复位",
                  "Ctrl+= / Ctrl+- 缩放", "Ctrl+9 适应页宽",
                  "Space/中键 平移", "方向键 平移",
                  "PgUp/PgDn 翻页", "Home/End 首末页",
                  "Ctrl+F 查找", "F3/Shift+F3 查找命中",
                  "F11 全屏（Esc 退出）", "E 默认清屏")

_CLEAR, _HOTKEY, _MAIN_HOTKEY = "clear", "hotkey", "main_hotkey"
HINT_DUPLICATE_SCRATCH = "不能与草稿纸热键相同"
HINT_DUPLICATE_MAIN = "不能与主窗热键相同"


def _same_sequence(left: str, right: str) -> bool:
    """按 Qt 规范名比较两个组合键，忽略大小写/修饰键写法差异。"""
    return (QKeySequence(left).toString().lower()
            == QKeySequence(right).toString().lower())


class SettingsDialog(QDialog):
    """设置窗口：values() 返回新 Settings，绝不改动传进来的对象。"""

    def __init__(self, settings: Settings, parent: QWidget | None = None,
                 settings_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        # QDialog 子类同样不吃 QSS 背景（PROGRESS §5）
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle("设置")
        self.setMinimumSize(560, 500)
        self._color = settings.color
        self._width_index = settings.width_index
        self._clear_key = settings.clear_key
        self._hotkey = settings.scratch_hotkey
        self._main_hotkey = settings.main_hotkey
        self._pdf_autosave = settings.pdf_autosave
        # 工具栏位置/收起：位置在对话框里改；收起状态原样带回（点「还原默认」才清掉）
        self._toolbar_position = (settings.toolbar_position
                                  if settings.toolbar_position in TOOLBAR_POSITIONS else "top")
        self._toolbar_collapsed = bool(settings.toolbar_collapsed)
        # 阅读模式：只影响 PDF 底图显示，笔迹与导出不变
        self._page_mode = getattr(settings, "page_mode", "normal")
        self._ink_smoothing = getattr(settings, "ink_smoothing", "low")
        self._pressure_enabled = getattr(settings, "pressure_enabled", True)
        self._highlighter_color = getattr(settings, "highlighter_color", "#d4a017")
        self._note_paper = getattr(settings, "note_paper", "dark")
        self._close_to_tray = getattr(settings, "close_to_tray", True)
        self._close_prompt = getattr(settings, "close_prompt", True)
        self._open_export_folder = getattr(settings, "open_export_folder", False)
        self._extra = dict(getattr(settings, "extra", {}) or {})
        self._settings_path = Path(settings_path) if settings_path else None
        self._capturing: str | None = None

        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setInterval(CAPTURE_TIMEOUT_MS)
        self._capture_timer.timeout.connect(self._on_capture_timeout)

        self._build()
        self._reflect_color()
        self._reflect_highlighter_color()
        self._set_width(settings.width)
        if self._width_value() in WIDTHS:          # 粗细正好落在预设上：索引也归位
            self._width_index = WIDTHS.index(self._width_value())
        self.hint_label.setText("")

    # --- 布局 ---

    def _build(self) -> None:
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 10, 14, 12)
        column.setSpacing(8)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("SettingsTabs")
        self.tabs.setDocumentMode(True)
        sections = (
            ("绘制", self._build_draw_box(), "颜色、粗细、压感与平滑"),
            ("界面", self._build_ui_box(), "工具栏位置、阅读模式、笔记与关闭行为"),
            ("快捷键", self._build_key_box(), "三条可改绑热键 + 固定键位"),
            ("文件", self._build_pdf_row(), "自动暂存与导出后打开文件夹"),
            ("高级", self._build_advanced_box(), "配置文件位置与扩展说明"),
        )
        for title, widget, tip in sections:
            # QTabWidget 的页默认吃系统白底，深色主题下白字会“消失”；
            # 给每页显式铺深色底，标签页切换后不再闪白。
            widget.setObjectName("SettingsPage")
            widget.setAutoFillBackground(True)
            palette = widget.palette()
            palette.setColor(QPalette.Window, QColor(theme.SURFACE))
            palette.setColor(QPalette.Base, QColor(theme.SURFACE))
            widget.setPalette(palette)
            self.tabs.addTab(widget, title)
            self.tabs.setTabToolTip(self.tabs.count() - 1, tip)
        column.addWidget(self.tabs, 1)
        column.addLayout(self._build_buttons())

    def _build_draw_box(self) -> QGroupBox:
        box = QGroupBox("绘制", self)
        grid = QGridLayout(box)
        grid.setSpacing(8)

        swatches = QHBoxLayout()
        swatches.setSpacing(4)
        self.color_buttons: list[QPushButton] = []
        for color in PALETTE:
            button = QPushButton(box)
            button.setCheckable(True)
            button.setFixedSize(20, 20)
            button.setToolTip(color)
            button.setProperty("color", color)
            # 与工具栏同款圆形色块：选中时白色描边
            button.setStyleSheet(
                f"QPushButton {{background-color: {color}; border: 1px solid {theme.BORDER_STRONG};"
                f" border-radius: 10px; padding: 0; min-height: 0;}}"
                f"QPushButton:checked {{border: 2px solid {theme.ACCENT};}}")
            button.clicked.connect(lambda _=False, c=color: self._pick_color(c))
            self.color_buttons.append(button)
            swatches.addWidget(button)
        self.custom_color_button = QPushButton(icons.palette_icon(), "取色器", box)
        self.custom_color_button.setToolTip("打开系统取色器")
        self.custom_color_button.clicked.connect(self._pick_custom_color)
        swatches.addWidget(self.custom_color_button)
        swatches.addStretch(1)
        grid.addLayout(swatches, 0, 0)

        self.size_slider = QSlider(Qt.Horizontal, box)
        self.size_slider.setRange(int(WIDTH_MIN * SLIDER_SCALE),
                                  int(WIDTH_MAX * SLIDER_SCALE))
        self.size_slider.setSingleStep(1)
        self.size_slider.setPageStep(4)
        self.size_slider.setToolTip(f"粗细（{WIDTH_MIN:g}–{WIDTH_MAX:g}，0.1 一档）")
        self.size_slider.valueChanged.connect(self._on_slider_value)
        self.size_label = QLabel(box)
        self.size_label.setFixedWidth(36)
        self.size_label.setToolTip("当前粗细（像素）")
        grid.addWidget(QLabel("粗细", box), 1, 0)
        grid.addWidget(self.size_slider, 1, 1)
        grid.addWidget(self.size_label, 1, 2)

        grid.addWidget(QLabel("笔迹平滑", box), 2, 0)
        self.ink_smoothing_combo = QComboBox(box)
        for level, label in (("off", "关"), ("low", "低"), ("medium", "中"),
                             ("high", "高"), ("ultra", "超高")):
            self.ink_smoothing_combo.addItem(label, level)
        self.ink_smoothing_combo.setCurrentIndex(
            max(0, self.ink_smoothing_combo.findData(self._ink_smoothing)))
        self.ink_smoothing_combo.setToolTip(
            "抬笔后去抖并锁定拐角；低/中只挪点、直线段直连，高/超高再做曲线圆角消除颗粒感")
        grid.addWidget(self.ink_smoothing_combo, 2, 1, 1, 2)

        self.pressure_check = QCheckBox("压感（数位板）", box)
        self.pressure_check.setChecked(self._pressure_enabled)
        self.pressure_check.setToolTip(
            "只影响钢笔：轻画线细、用力接近当前粗细；荧光笔与橡皮保持恒定宽度；鼠标没有压力值")
        grid.addWidget(self.pressure_check, 3, 0, 1, 3)

        grid.addWidget(QLabel("荧光笔颜色", box), 4, 0)
        self.highlighter_color_button = QPushButton(box)
        self.highlighter_color_button.setToolTip("点开系统取色器；只改荧光笔，不影响画笔色")
        self.highlighter_color_button.clicked.connect(self._pick_highlighter_color)
        grid.addWidget(self.highlighter_color_button, 4, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        return box

    def _build_ui_box(self) -> QGroupBox:
        """界面：工具栏放顶部横排还是右侧竖排（v2.9）。"""
        box = QGroupBox("界面", self)
        grid = QGridLayout(box)
        grid.setSpacing(8)

        grid.addWidget(QLabel("工具栏位置", box), 0, 0)
        self.toolbar_position_combo = QComboBox(box)
        self.toolbar_position_combo.addItem("顶部", "top")
        self.toolbar_position_combo.addItem("右侧", "right")
        self.toolbar_position_combo.setCurrentIndex(
            max(0, self.toolbar_position_combo.findData(self._toolbar_position)))
        self.toolbar_position_combo.setToolTip("顶部=横向工具栏；右侧=竖排侧栏")
        grid.addWidget(self.toolbar_position_combo, 0, 1)

        grid.addWidget(QLabel("阅读模式", box), 1, 0)
        self.page_mode_combo = QComboBox(box)
        for mode in PAGE_MODES:
            self.page_mode_combo.addItem(PAGE_MODE_LABELS[mode], mode)
        self.page_mode_combo.setCurrentIndex(
            max(0, self.page_mode_combo.findData(self._page_mode)))
        self.page_mode_combo.setToolTip("夜间/护眼只反相 PDF 底图，笔迹颜色不变")
        grid.addWidget(self.page_mode_combo, 1, 1)

        grid.addWidget(QLabel("新建笔记纸张", box), 2, 0)
        self.note_paper_combo = QComboBox(box)
        self.note_paper_combo.addItem("深色", "dark")
        self.note_paper_combo.addItem("浅色", "light")
        self.note_paper_combo.setCurrentIndex(
            max(0, self.note_paper_combo.findData(self._note_paper)))
        self.note_paper_combo.setToolTip("只影响之后新建的笔记；已有笔记保持自己的纸色")
        grid.addWidget(self.note_paper_combo, 2, 1)

        grid.addWidget(QLabel("关闭主窗时", box), 3, 0)
        self.close_behavior_combo = QComboBox(box)
        self.close_behavior_combo.addItem("先询问（取消 / 退出 / 最小化）", "ask")
        self.close_behavior_combo.addItem("直接最小化到托盘", "tray")
        self.close_behavior_combo.addItem("直接退出程序", "quit")
        mode = "ask" if self._close_prompt else (
            "tray" if self._close_to_tray else "quit")
        self.close_behavior_combo.setCurrentIndex(
            max(0, self.close_behavior_combo.findData(mode)))
        self.close_behavior_combo.setToolTip(
            "先询问 = 每次关窗都弹「取消 / 退出应用 / 最小化到托盘」；"
            "托盘与热键都不可用时，最小化会改用直接退出")
        grid.addWidget(self.close_behavior_combo, 3, 1)
        grid.setColumnStretch(1, 1)
        return box

    def _build_key_box(self) -> QGroupBox:
        box = QGroupBox("快捷键", self)
        grid = QGridLayout(box)
        grid.setSpacing(8)

        grid.addWidget(QLabel("清屏键", box), 0, 0)
        self.clear_key_edit = self._read_only_field(self._clear_key, box)
        self.clear_key_button = QPushButton("修改", box)
        self.clear_key_button.setToolTip("重新绑定清屏键")
        self.clear_key_button.setAutoDefault(False)
        self.clear_key_button.clicked.connect(lambda: self._start_capture(_CLEAR))
        grid.addWidget(self.clear_key_edit, 0, 1)
        grid.addWidget(self.clear_key_button, 0, 2)

        grid.addWidget(QLabel("草稿纸热键", box), 1, 0)
        self.hotkey_edit = self._read_only_field(self._hotkey, box)
        self.hotkey_button = QPushButton("修改", box)
        self.hotkey_button.setToolTip("重新绑定草稿纸全局热键")
        self.hotkey_button.setAutoDefault(False)
        self.hotkey_button.clicked.connect(lambda: self._start_capture(_HOTKEY))
        grid.addWidget(self.hotkey_edit, 1, 1)
        grid.addWidget(self.hotkey_button, 1, 2)

        grid.addWidget(QLabel("主窗热键", box), 2, 0)
        self.main_hotkey_edit = self._read_only_field(self._main_hotkey, box)
        self.main_hotkey_button = QPushButton("修改", box)
        self.main_hotkey_button.setToolTip("重新绑定呼出主窗全局热键")
        self.main_hotkey_button.setAutoDefault(False)
        self.main_hotkey_button.clicked.connect(
            lambda: self._start_capture(_MAIN_HOTKEY))
        grid.addWidget(self.main_hotkey_edit, 2, 1)
        grid.addWidget(self.main_hotkey_button, 2, 2)

        self.hint_label = QLabel("", box)
        self.hint_label.setObjectName("SettingsHint")
        grid.addWidget(self.hint_label, 3, 0, 1, 3)

        heading = QLabel("固定键位", box)
        heading.setObjectName("FixedBinding")
        grid.addWidget(heading, 4, 0, 1, 3)
        self.fixed_labels: list[QLabel] = []
        for index, text in enumerate(FIXED_BINDINGS):
            label = QLabel(text, box)
            label.setObjectName("FixedBinding")
            self.fixed_labels.append(label)
            grid.addWidget(label, 5 + index // 2, index % 2)
        grid.setColumnStretch(1, 1)
        return box

    def _build_pdf_row(self) -> QWidget:
        """PDF 文件行为：自动暂存 + 导出后打开文件夹。"""
        box = QGroupBox("文件", self)
        column = QVBoxLayout(box)
        column.setSpacing(6)
        self.pdf_autosave_check = QCheckBox("PDF 批注自动保存（停笔 2 秒）", box)
        self.pdf_autosave_check.setChecked(self._pdf_autosave)
        self.pdf_autosave_check.setToolTip("停笔 2 秒后把批注写进 sidecar 文件")
        column.addWidget(self.pdf_autosave_check)
        self.open_export_folder_check = QCheckBox("导出 PDF 后打开所在文件夹", box)
        self.open_export_folder_check.setChecked(self._open_export_folder)
        self.open_export_folder_check.setToolTip(
            "导出成功后在资源管理器里定位到输出文件")
        column.addWidget(self.open_export_folder_check)
        return box

    def _build_advanced_box(self) -> QGroupBox:
        """高级 / 扩展：配置路径 + 打开目录 + 未知字段保留说明。"""
        box = QGroupBox("高级 / 扩展", self)
        column = QVBoxLayout(box)
        column.setSpacing(6)
        path_text = (str(self._settings_path) if self._settings_path
                     else "（配置尚未落盘）")
        self.settings_path_label = QLabel(path_text, box)
        self.settings_path_label.setObjectName("SettingsHint")
        self.settings_path_label.setWordWrap(True)
        self.settings_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        column.addWidget(self.settings_path_label)
        row = QHBoxLayout()
        self.open_config_dir_button = QPushButton("打开配置文件夹", box)
        self.open_config_dir_button.setToolTip("在资源管理器中打开 settings.json 所在目录")
        self.open_config_dir_button.setAutoDefault(False)
        self.open_config_dir_button.setEnabled(self._settings_path is not None)
        self.open_config_dir_button.clicked.connect(self._open_config_dir)
        row.addWidget(self.open_config_dir_button)
        row.addStretch(1)
        column.addLayout(row)
        hint = QLabel("可直接编辑 settings.json。程序不认识的字段会原样保留，方便自定义/二次开发。", box)
        hint.setObjectName("SettingsHint")
        hint.setWordWrap(True)
        column.addWidget(hint)
        return box

    def _open_config_dir(self) -> None:
        if self._settings_path is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._settings_path.parent)))

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.reset_button = QPushButton("还原默认", self)
        self.reset_button.setToolTip("把所有设置恢复为默认值（保存后生效）")
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setToolTip("放弃本次修改")
        self.save_button = QPushButton("保存", self)
        self.save_button.setToolTip("保存设置并关闭")
        for button in (self.reset_button, self.cancel_button, self.save_button):
            button.setAutoDefault(False)
        self.save_button.setDefault(True)        # 捕获态会在 keyPressEvent 里拦住 Enter
        self.reset_button.clicked.connect(self._reset_defaults)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)
        row.addWidget(self.reset_button)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.save_button)
        return row

    def _read_only_field(self, text: str, parent: QWidget) -> QLineEdit:
        """只读显示当前绑定；键盘输入一律走「修改」的捕获流程。"""
        edit = QLineEdit(text, parent)
        edit.setReadOnly(True)
        edit.setFocusPolicy(Qt.NoFocus)
        edit.setMinimumWidth(150)
        return edit

    # --- 取值 ---

    def _width_value(self) -> float:
        return self.size_slider.value() / SLIDER_SCALE

    def _set_width(self, width: float) -> None:
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(int(round(float(width) * SLIDER_SCALE)))
        self.size_slider.blockSignals(False)
        self.size_label.setText(f"{self._width_value():g}")

    def values(self) -> Settings:
        """编辑结果；width_index 跟住滑杆命中的预设，否则沿用原配置。"""
        close_mode = self.close_behavior_combo.currentData()
        return Settings(clear_key=self._clear_key, color=self._color,
                        width_index=self._width_index,
                        scratch_hotkey=self._hotkey, width=self._width_value(),
                        pdf_autosave=self.pdf_autosave_check.isChecked(),
                        toolbar_position=self.toolbar_position_combo.currentData(),
                        toolbar_collapsed=self._toolbar_collapsed,
                        main_hotkey=self._main_hotkey,
                        page_mode=self.page_mode_combo.currentData(),
                        ink_smoothing=self.ink_smoothing_combo.currentData(),
                        pressure_enabled=self.pressure_check.isChecked(),
                        highlighter_color=self._highlighter_color,
                        note_paper=self.note_paper_combo.currentData(),
                        close_to_tray=close_mode in ("ask", "tray"),
                        close_prompt=close_mode == "ask",
                        open_export_folder=self.open_export_folder_check.isChecked(),
                        extra=dict(self._extra))

    def _pick_highlighter_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._highlighter_color), self,
                                       "选择荧光笔颜色")
        if chosen.isValid():
            self._highlighter_color = chosen.name()
            self._reflect_highlighter_color()

    def _reflect_highlighter_color(self) -> None:
        color = QColor(self._highlighter_color)
        text = "#202124" if color.lightness() > 140 else "#ffffff"
        self.highlighter_color_button.setText(self._highlighter_color)
        self.highlighter_color_button.setStyleSheet(
            f"QPushButton {{background-color: {self._highlighter_color};"
            f" color: {text}; border: 1px solid rgba(255,255,255,0.35);"
            " border-radius: 5px; padding: 4px 10px;}")

    # --- 颜色 ---

    def _pick_color(self, color: str) -> None:
        self._color = color
        self._reflect_color()

    def _reflect_color(self) -> None:
        for button in self.color_buttons:
            checked = str(button.property("color")).lower() == self._color.lower()
            button.setChecked(checked)

    def _pick_custom_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "选择画笔颜色")
        if chosen.isValid():
            self._pick_color(chosen.name())

    def _on_slider_value(self, value: int) -> None:
        width = value / SLIDER_SCALE
        self.size_label.setText(f"{width:g}")
        if width in WIDTHS:
            self._width_index = WIDTHS.index(width)

    def _reset_defaults(self) -> None:
        """只把界面改回默认，不立即应用（点「保存」才落盘）。"""
        if self._capturing is not None:
            self._end_capture(None, "")
        default = Settings()
        self._color = default.color
        self._width_index = default.width_index
        self._clear_key = default.clear_key
        self._hotkey = default.scratch_hotkey
        self._main_hotkey = default.main_hotkey
        self._pdf_autosave = default.pdf_autosave
        self._toolbar_collapsed = default.toolbar_collapsed
        self._reflect_color()
        self._set_width(default.width)
        self.clear_key_edit.setText(default.clear_key)
        self.hotkey_edit.setText(default.scratch_hotkey)
        self.main_hotkey_edit.setText(default.main_hotkey)
        self.pdf_autosave_check.setChecked(default.pdf_autosave)
        self.toolbar_position_combo.setCurrentIndex(
            max(0, self.toolbar_position_combo.findData(default.toolbar_position)))
        self.page_mode_combo.setCurrentIndex(
            max(0, self.page_mode_combo.findData(default.page_mode)))
        self.ink_smoothing_combo.setCurrentIndex(
            max(0, self.ink_smoothing_combo.findData(default.ink_smoothing)))
        self.pressure_check.setChecked(default.pressure_enabled)
        self._highlighter_color = default.highlighter_color
        self._reflect_highlighter_color()
        self.note_paper_combo.setCurrentIndex(
            max(0, self.note_paper_combo.findData(default.note_paper)))
        default_mode = ("ask" if default.close_prompt
                        else ("tray" if default.close_to_tray else "quit"))
        self.close_behavior_combo.setCurrentIndex(
            max(0, self.close_behavior_combo.findData(default_mode)))
        self.open_export_folder_check.setChecked(default.open_export_folder)
        self.hint_label.setText("")

    # --- 快捷键捕获 ---

    def is_capturing(self) -> bool:
        return self._capturing is not None

    def _button_for(self, target: str) -> QPushButton:
        if target == _CLEAR:
            return self.clear_key_button
        if target == _MAIN_HOTKEY:
            return self.main_hotkey_button
        return self.hotkey_button

    def _start_capture(self, target: str) -> None:
        if self._capturing is not None:
            self._end_capture(None, "")
        self._capturing = target
        self._button_for(target).setText(CAPTURE_TEXT)
        self.hint_label.setText(HINT_CAPTURE)
        self._capture_timer.start()
        self.grabKeyboard()

    def _on_capture_timeout(self) -> None:
        if self._capturing is not None:
            self._end_capture(None, HINT_TIMEOUT)

    def _cancel_capture(self, hint: str = "") -> None:
        self._end_capture(None, hint)

    def _end_capture(self, sequence: str | None, hint: str) -> None:
        """停表、放键盘、按钮复原；sequence 非空才算收下（三条收尾路都走这里）。"""
        target = self._capturing
        self._capture_timer.stop()
        self.releaseKeyboard()
        self._capturing = None
        self.hint_label.setText(hint)
        if target is None:
            return
        self._button_for(target).setText("修改")
        if sequence is None:
            return
        if target == _CLEAR:
            self._clear_key = sequence
            self.clear_key_edit.setText(sequence)
        elif target == _MAIN_HOTKEY:
            self._main_hotkey = sequence
            self.main_hotkey_edit.setText(sequence)
        else:
            self._hotkey = sequence
            self.hotkey_edit.setText(sequence)

    def _handle_capture_key(self, modifiers: str, key: str) -> None:
        """Esc 取消；保留键/非法热键只提示、留在捕获态；其余成规范序列。"""
        if self._capturing is None:
            return
        name = key.strip()
        if name and name.lower() in _MODIFIER_NAMES:
            return                                  # 单独的修饰键不算按键
        if name.lower() in ("esc", "escape"):
            self._cancel_capture()
            return
        parts = _split_modifiers(modifiers)
        if not name and not parts:
            return
        parts = parts + ([_canonical_key(name)] if name else [""])
        sequence = "+".join(parts)

        if self._capturing == _CLEAR:
            reason = self._clear_key_reason(sequence)
            if reason:
                self.hint_label.setText(reason)
                return
        else:
            try:
                parse_hotkey(sequence)
            except ValueError:
                self.hint_label.setText(HINT_INVALID)
                return
            duplicate = self._duplicate_hotkey_hint(sequence, self._capturing)
            if duplicate:
                self.hint_label.setText(duplicate)
                return
        self._end_capture(sequence, "")

    def _duplicate_hotkey_hint(self, sequence: str, target: str) -> str:
        """两条全局热键不能相同；相同的组合在 Win32 里第二条必然注册失败。"""
        other = self._hotkey if target == _MAIN_HOTKEY else self._main_hotkey
        if _same_sequence(sequence, other):
            return HINT_DUPLICATE_SCRATCH if target == _MAIN_HOTKEY else HINT_DUPLICATE_MAIN
        return ""

    @staticmethod
    def _clear_key_reason(sequence: str) -> str:
        """清屏键的拒收理由；空串表示可用（黑名单与配置加载共用同一份判定）。"""
        if is_reserved_key(sequence):
            return HINT_RESERVED
        if not QKeySequence(sequence).toString():
            return HINT_INVALID
        return ""

    # --- 捕获态的按键边界 ---

    def event(self, ev) -> bool:
        # 构造期 Qt 就会调 event()，此时 _capturing 还没赋值
        if getattr(self, "_capturing", None) is not None and ev.type() == QEvent.ShortcutOverride:
            ev.accept()                 # QShortcut 比 keyPressEvent 先到，不能让它抢键
            return True
        return super().event(ev)

    def keyPressEvent(self, ev) -> None:
        """捕获态自己吃所有按键：不交给 QDialog，Esc/Enter 都不会收窗。"""
        if self._capturing is not None:
            if ev.key() not in _MODIFIER_KEYS:
                self._handle_capture_key(_modifiers_text(ev.modifiers()),
                                         _event_key_text(ev))
            ev.accept()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev) -> None:
        if self._capturing is not None:
            self._end_capture(None, "")     # 不放键盘的话进程里再没有按键
        super().closeEvent(ev)
