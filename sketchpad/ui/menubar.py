"""v2 菜单栏（仿 Xournal++）：六项菜单、按模式启停、工具互斥组。

菜单只发稳定 id，不碰业务；接线交给 main.py。
工具项与工具栏同源：点击时除 actionTriggered 外还发 toolChosen / widthChosen。
深色主题的规则只作用于本控件（objectName "Menubar"），不改 sketchpad/ui/theme.py。
单键快捷键（B/Q/1-4/E/0）留在画布层；菜单只挂设计文档 §5 明确列出的组合键，
否则同一个按键会触发两次。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QMenu, QMenuBar

from sketchpad.ui import theme
from sketchpad.core.settings import DEFAULT_WIDTH_INDEX, PAGE_MODES, WIDTHS
from sketchpad.pdf.pagemode import PAGE_MODE_LABELS

MODES = ("draft", "document")
WIDTH_LABELS = ("细", "中", "粗", "特粗")
# 菜单文案比 PAGE_MODE_LABELS 多一句效果说明（设置对话框仍用短标签）
PAGE_MODE_MENU_LABELS = {
    "normal": PAGE_MODE_LABELS["normal"],
    "night": f"{PAGE_MODE_LABELS['night']}（反相）",
    "eye": f"{PAGE_MODE_LABELS['eye']}（羊皮纸）",
}
CLEAR_DRAFT_TEXT = "清空画布"
CLEAR_DOCUMENT_TEXT = "清空本页"

# QSS 挂在菜单栏上，QMenu 是它的子对象，弹出菜单一并吃到这些规则
MENUBAR_QSS = f"""
QMenuBar#Menubar {{
    background: transparent;
    color: {theme.TEXT};
    border: none;
    padding: 0 4px;
}}
QMenuBar#Menubar::item {{
    background: transparent;
    color: {theme.TEXT};
    padding: 3px 10px;
    border-radius: 4px;
}}
QMenuBar#Menubar::item:selected {{ background: {theme.SURFACE_HOVER}; }}
QMenuBar#Menubar::item:disabled {{ color: #6e6e73; }}
QMenuBar#Menubar QMenu {{
    background: {theme.SURFACE};
    color: {theme.TEXT};
    border: 1px solid {theme.BORDER};
    padding: 4px;
}}
QMenuBar#Menubar QMenu::item {{
    background: transparent;
    color: {theme.TEXT};
    padding: 5px 22px 5px 12px;
    border-radius: 4px;
}}
QMenuBar#Menubar QMenu::item:selected {{ background: {theme.ACCENT_SOFT}; color: {theme.TEXT}; }}
QMenuBar#Menubar QMenu::item:disabled {{ color: {theme.TEXT_MUTED}; }}
QMenuBar#Menubar QMenu::separator {{ height: 1px; background: {theme.BORDER}; margin: 4px 6px; }}
"""


class Menubar(QMenuBar):
    """六项菜单栏：草稿/文档两种模式共用，模式切换只改条目可用性。"""

    actionTriggered = Signal(str)   # 稳定 id，交给控制器分发
    toolChosen = Signal(str)        # "pen" / "highlighter" / "select" / "note" / "eraser"
    widthChosen = Signal(float)     # 四档预设的线宽

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Menubar")
        self.setStyleSheet(MENUBAR_QSS)
        self.setNativeMenuBar(False)    # 统一走自绘菜单，深色 QSS 才生效
        self.mode = "draft"
        self._build()
        self.pen_action.setChecked(True)
        self.width_actions[DEFAULT_WIDTH_INDEX].setChecked(True)
        self.set_mode("draft")

    # --- 构建 ---

    def _make(self, text: str, key: str | None = None, *,
              shortcut: str | None = None, checkable: bool = False) -> QAction:
        """建条目；给了 key 就把 triggered 转成 actionTriggered（id 固定）。"""
        action = QAction(text, self)
        action.setCheckable(checkable)
        tip = text.rstrip("…")
        if shortcut is not None:
            tip = f"{tip}（{shortcut}）"
        action.setToolTip(tip)
        action.setStatusTip(tip)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if key is not None:
            action.triggered.connect(
                lambda _checked=False, k=key: self.actionTriggered.emit(k))
        return action

    def _build(self) -> None:
        # 文件
        self.file_menu = self.addMenu("文件")
        self.open_pdf_action = self._make("打开 PDF…", "open_pdf", shortcut="Ctrl+O")
        self.new_draft_action = self._make("新建笔记", "new_draft")
        self.edit_word_action = self._make("编辑 Word…", "edit_word", shortcut="Ctrl+E")
        self.convert_action = self._make("转换中心…", "convert", shortcut="Ctrl+Shift+C")
        self.recent_menu = QMenu("最近打开", self)
        self.save_action = self._make("保存批注", "save", shortcut="Ctrl+S")
        self.export_action = self._make("导出 PDF…", "export", shortcut="Ctrl+Shift+E")
        self.close_doc_action = self._make("关闭文档", "close_doc")
        self.settings_action = self._make("设置…", "settings")
        self.settings_action.setToolTip("设置（Ctrl+,）")
        self.settings_action.setStatusTip("设置（Ctrl+,）")
        self.quit_action = self._make("退出全部实例", "quit")
        self.file_menu.addAction(self.open_pdf_action)
        self.file_menu.addAction(self.new_draft_action)
        self.file_menu.addAction(self.edit_word_action)
        self.file_menu.addAction(self.convert_action)
        self.file_menu.addMenu(self.recent_menu)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.save_action)
        self.file_menu.addAction(self.export_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.close_doc_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.settings_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.quit_action)

        # 编辑（清空只有一个入口：工具菜单；编辑菜单不再复制一份）
        self.edit_menu = self.addMenu("编辑")
        self.undo_action = self._make("撤销", "undo", shortcut="Ctrl+Z")
        self.redo_action = self._make("重做", "redo", shortcut="Ctrl+Y")
        # 重做同时接受 Ctrl+Shift+Z（与 Ctrl+Y 等价，两个视图都绑）
        self.redo_action.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.find_action = self._make("查找…", "find", shortcut="Ctrl+F")
        self.edit_menu.addAction(self.undo_action)
        self.edit_menu.addAction(self.redo_action)
        self.edit_menu.addAction(self.find_action)

        # 视图
        self.view_menu = self.addMenu("视图")
        self.thumbs_action = self._make("页面缩略图", "thumbs", checkable=True)
        self.zoom_in_action = self._make("放大", "zoom_in", shortcut="Ctrl+=")
        self.zoom_out_action = self._make("缩小", "zoom_out", shortcut="Ctrl+-")
        self.actual_size_action = self._make("实际大小", "actual_size", shortcut="Ctrl+0")
        self.fit_width_action = self._make("适应页宽", "fit_width", shortcut="Ctrl+9")
        self.fullscreen_action = self._make("全屏", "fullscreen", shortcut="F11")
        self.view_menu.addAction(self.thumbs_action)
        self.view_menu.addSeparator()
        for action in (self.zoom_in_action, self.zoom_out_action,
                       self.actual_size_action, self.fit_width_action):
            self.view_menu.addAction(action)
        self.view_menu.addSeparator()
        # 阅读模式：只改 PDF 底图，笔迹颜色不变；草稿模式整组禁用
        self.page_mode_menu = QMenu("阅读模式", self)
        self._page_mode_group = QActionGroup(self)
        self._page_mode_group.setExclusive(True)
        self.page_mode_actions: dict[str, QAction] = {}
        for mode in PAGE_MODES:
            action = self._make(PAGE_MODE_MENU_LABELS[mode], f"page_{mode}",
                                checkable=True)
            self._page_mode_group.addAction(action)
            self.page_mode_menu.addAction(action)
            self.page_mode_actions[mode] = action
        self.page_mode_actions["normal"].setChecked(True)
        self.view_menu.addMenu(self.page_mode_menu)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.fullscreen_action)

        # 导航
        self.navigate_menu = self.addMenu("导航")
        # 目录是文档导航入口，放在导航菜单最前（与页面缩略图分开）
        self.toc_action = self._make("目录", "toc", checkable=True)
        self.prev_page_action = self._make("上一页", "prev_page", shortcut="PgUp")
        self.next_page_action = self._make("下一页", "next_page", shortcut="PgDown")
        self.goto_page_action = self._make("跳转到页…", "goto_page", shortcut="Ctrl+G")
        self.navigate_menu.addAction(self.toc_action)
        self.navigate_menu.addSeparator()
        self.navigate_menu.addAction(self.prev_page_action)
        self.navigate_menu.addAction(self.next_page_action)
        self.navigate_menu.addSeparator()
        self.navigate_menu.addAction(self.goto_page_action)

        # 工具
        self.tools_menu = self.addMenu("工具")
        self._tool_group = QActionGroup(self)
        self.pen_action = self._make("画笔", None, checkable=True)
        self.highlighter_action = self._make("荧光笔", None, shortcut="H",
                                             checkable=True)
        self.select_action = self._make("鼠标（平移/选字）", None, checkable=True)
        self.note_action = self._make("插入笔记", None, checkable=True)
        self.text_action = self._make("文字", None, shortcut="T", checkable=True)
        self.eraser_action = self._make("橡皮", None, checkable=True)
        self.select_action.setShortcut(QKeySequence("V"))
        self.note_action.setShortcut(QKeySequence("N"))
        self.pen_action.triggered.connect(lambda _checked=False: self._choose_tool("pen"))
        self.highlighter_action.triggered.connect(
            lambda _checked=False: self._choose_tool("highlighter"))
        self.select_action.triggered.connect(
            lambda _checked=False: self._choose_tool("select"))
        self.note_action.triggered.connect(
            lambda _checked=False: self._choose_tool("note"))
        self.text_action.triggered.connect(
            lambda _checked=False: self._choose_tool("text"))
        self.eraser_action.triggered.connect(lambda _checked=False: self._choose_tool("eraser"))
        for action in (self.pen_action, self.highlighter_action,
                       self.select_action, self.note_action, self.text_action,
                       self.eraser_action):
            self._tool_group.addAction(action)
            self.tools_menu.addAction(action)

        self.tools_menu.addSeparator()
        self.fullscreen_annotate_action = self._make(
            "全屏批注", "annotate", shortcut="Ctrl+Alt+D", checkable=True)
        self.screenshot_action = self._make("截图（Ctrl+1）", "screenshot")
        self.tools_menu.addAction(self.fullscreen_annotate_action)
        self.tools_menu.addAction(self.screenshot_action)

        self.tools_menu.addSeparator()
        self._width_group = QActionGroup(self)
        self.width_actions: list[QAction] = []
        for index, (label, width) in enumerate(zip(WIDTH_LABELS, WIDTHS)):
            action = self._make(label, None, checkable=True)
            action.setStatusTip(f"{width:g} 像素")
            action.triggered.connect(
                lambda _checked=False, i=index: self._choose_width(i))
            self._width_group.addAction(action)
            self.width_actions.append(action)
            self.tools_menu.addAction(action)

        self.tools_menu.addSeparator()
        # 唯一清空入口：文案按模式切换（草稿清整块 / 文档清当前页）
        self.clear_canvas_action = self._make(CLEAR_DRAFT_TEXT, "clear_canvas")
        self.tools_menu.addAction(self.clear_canvas_action)

        # 帮助
        self.help_menu = self.addMenu("帮助")
        self.shortcuts_action = self._make("快捷键一览", "shortcuts")
        self.about_action = self._make("关于", "about")
        self.help_menu.addAction(self.shortcuts_action)
        self.help_menu.addAction(self.about_action)

    # --- 工具联动 ---

    def _choose_tool(self, tool: str) -> None:
        """菜单点工具：先发 id，再发工具名。"""
        self.actionTriggered.emit(tool)
        self.toolChosen.emit(tool)

    def _choose_width(self, index: int) -> None:
        """菜单点粗细：稳定 id + 具体线宽。"""
        self.actionTriggered.emit(f"width_{index}")
        self.widthChosen.emit(float(WIDTHS[index]))

    def set_tool(self, tool: str) -> None:
        """外部（工具栏/键盘）改工具后同步勾选；只 setChecked，不发信号。"""
        actions = {"pen": self.pen_action, "highlighter": self.highlighter_action,
                   "eraser": self.eraser_action, "select": self.select_action,
                   "note": self.note_action, "text": self.text_action}
        if tool not in actions:
            raise ValueError(
                f"未知工具：{tool!r}（只支持 pen / highlighter / select / note / text / eraser）")
        actions[tool].setChecked(True)

    def set_width_index(self, index: int) -> None:
        """外部改粗细后同步勾选；只 setChecked，不发信号。"""
        if not 0 <= index < len(self.width_actions):
            raise ValueError(f"粗细档位越界：{index}（0–{len(self.width_actions) - 1}）")
        self.width_actions[index].setChecked(True)

    def set_page_mode(self, mode: str) -> None:
        """外部（设置对话框/启动恢复）改阅读模式后同步勾选；不发信号。"""
        if mode not in PAGE_MODES:
            raise ValueError(f"未知阅读模式：{mode!r}（只支持 {PAGE_MODES}）")
        self.page_mode_actions[mode].setChecked(True)

    # --- 模式 ---

    def set_mode(self, mode: str) -> None:
        """切换模式：草稿模式禁用文档专属条目（含缩放）与导航菜单。"""
        if mode not in MODES:
            raise ValueError(f"未知模式：{mode!r}（只支持 {MODES}）")
        self.mode = mode
        document = mode == "document"
        for action in (self.save_action, self.export_action, self.close_doc_action,
                       self.edit_word_action,
                       self.prev_page_action, self.next_page_action,
                       self.goto_page_action, self.fit_width_action,
                       self.zoom_in_action, self.zoom_out_action,
                       self.actual_size_action,
                       self.find_action, self.toc_action, self.thumbs_action,
                       self.select_action, self.note_action):
            action.setEnabled(document)
        # 清空只剩这一个条目：文案跟模式走，草稿清整块、文档清当前页
        self.clear_canvas_action.setText(
            CLEAR_DOCUMENT_TEXT if document else CLEAR_DRAFT_TEXT)
        self.open_pdf_action.setEnabled(True)       # 文档模式下允许另开一份
        self.navigate_menu.setEnabled(document)
        self.page_mode_menu.setEnabled(document)    # 阅读模式只服务 PDF 底图
        self.text_action.setEnabled(not document)   # 文字工具只在草稿/笔记画布
        self.set_word_editable(False)

    def set_word_editable(self, editable: bool) -> None:
        """只有当前文档是可编辑的 Word 时才点亮「编辑 Word…」。"""
        self.edit_word_action.setEnabled(bool(editable) and self.mode == "document")

    def set_convert_available(self, available: bool) -> None:
        """Lite 没有转换中心：整条隐藏；Standard 常亮。"""
        self.convert_action.setVisible(bool(available))
        self.convert_action.setEnabled(bool(available))

    def set_viewer_kind(self, kind: str) -> None:
        """非 PDF 阅读器只保留查找/目录/缩放：批注与翻页条目不可用。"""
        pdf = kind == "pdf"
        document = self.mode == "document"
        for action in (self.save_action, self.export_action, self.thumbs_action,
                       self.prev_page_action, self.next_page_action,
                       self.goto_page_action, self.note_action):
            action.setEnabled(document and pdf)
        self.select_action.setEnabled(document)      # 文本/导图也可用鼠标选字/拖动
        self.page_mode_menu.setEnabled(document and pdf)

    def set_open_label(self, text: str, tooltip: str | None = None) -> None:
        self.open_pdf_action.setText(text)
        if tooltip is not None:
            self.open_pdf_action.setStatusTip(tooltip)
