"""快捷键一览、关于与关窗确认对话框。颜色写死在本文件，避免 theme 的全局规则罩到其它 QDialog。"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from sketchpad import __version__
from sketchpad.core.channel import APP_NAME
from sketchpad.ui import theme
from sketchpad.ui.frameless import apply_rounded_corners

_DIALOG_QSS = f"""
QDialog#ShortcutsDialog, QDialog#AboutDialog, QDialog#CloseConfirmDialog {{
    background: {theme.WINDOW_BG};
}}
QDialog#CloseConfirmDialog {{ border: 1px solid {theme.BORDER_STRONG}; }}
QLabel#CloseConfirmTitle {{ color: {theme.TEXT}; font-size: 15px; font-weight: 600; }}
QDialog#ShortcutsDialog QLabel, QDialog#AboutDialog QLabel,
QDialog#CloseConfirmDialog QLabel {{ color: {theme.TEXT}; }}
QCheckBox#CloseDontAsk {{ color: {theme.TEXT_DIM}; spacing: 7px; }}
"""

SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("画笔", "B"),
    ("荧光笔", "H"),
    ("鼠标（平移/选字/移图标）", "V"),
    ("插入笔记", "N（文档模式）"),
    ("笔记内：文字 / 图片 / 附件", "卡片工具栏（文字 T）· 分屏/独立窗口按钮"),
    ("复制选中文字", "Ctrl+C"),
    ("橡皮", "Q"),
    ("颜色 1–9", "1 … 9"),
    ("清屏 / 清空本页", "E（可在设置里改）"),
    ("撤销", "Ctrl+Z"),
    ("重做", "Ctrl+Y / Ctrl+Shift+Z"),
    ("缩放复位", "0 / Ctrl+0"),
    ("放大 / 缩小", "Ctrl+= / Ctrl+-"),
    ("适应页宽", "Ctrl+9"),
    ("平移", "鼠标工具拖空白 / Space / 中键拖动"),
    ("方向键平移", "↑ ↓ ← →"),
    ("滚轮", "滚动 / 平移"),
    ("Ctrl+滚轮", "缩放"),
    ("打开 PDF", "Ctrl+O"),
    ("新建笔记", "工具栏「新建笔记」按钮 / 文件菜单"),
    ("关闭当前标签", "Ctrl+W"),
    ("切换标签", "Ctrl+Tab / Ctrl+Shift+Tab"),
    ("编辑 Word", "Ctrl+E（文档模式，仅 .docx）"),
    ("截图", "Ctrl+1（全局；截到剪贴板）"),
    ("转换中心", "Ctrl+Shift+C（Standard）"),
    ("保存批注", "Ctrl+S"),
    ("导出 PDF", "Ctrl+Shift+E"),
    ("上一页 / 下一页", "PgUp / PgDown"),
    ("首页 / 末页", "Home / End"),
    ("跳转到页", "Ctrl+G"),
    ("查找", "Ctrl+F"),
    ("查找下一个 / 上一个", "回车 / Shift+回车 · F3 / Shift+F3"),
    ("全屏 / 退出全屏", "F11 / Esc"),
    ("设置", "Ctrl+,"),
    ("目录 / 缩略图", "菜单 导航 → 目录 / 视图 → 页面缩略图"),
    ("全屏批注（显隐/切换）", "Ctrl+Alt+D（全局）· 默认穿透"),
    ("全屏批注隐藏", "Ctrl+Alt+H（全局）"),
    ("批注工具", "Alt+1 穿透 · Alt+2 钢笔 · Alt+3 橡皮 · Alt+4 清空"),
    ("草稿纸", "Alt+Q（全局，可在设置里改）"),
    ("呼出主窗", "Alt+W（全局，可在设置里改）"),
    ("截图（区域）", "Ctrl+1（全局，仅主实例）· 相机菜单可选窗口/全屏/上一区域/取色"),
    ("截图编辑器", "Enter 复制 · Ctrl+S 保存 · Ctrl+Z/Y 撤销重做 · Esc 关闭"),
)


class _BaseDialog(QDialog):
    def __init__(self, title: str, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_DIALOG_QSS)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(18, 16, 18, 14)
        self.column.setSpacing(10)

    def add_close(self) -> QPushButton:
        button = QPushButton("关闭", self)
        button.clicked.connect(self.accept)
        self.column.addWidget(button, alignment=Qt.AlignRight)
        return button


class ShortcutsDialog(_BaseDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("快捷键一览", "ShortcutsDialog", parent)
        self.setMinimumWidth(460)
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(6)
        for row, (action, keys) in enumerate(SHORTCUTS):
            grid.addWidget(QLabel(action, self), row, 0)
            key_label = QLabel(keys, self)
            key_label.setStyleSheet(f"color: {theme.TEXT_DIM};")
            grid.addWidget(key_label, row, 1)
        self.column.addLayout(grid)
        self.close_button = self.add_close()


class AboutDialog(_BaseDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(f"关于 {APP_NAME}", "AboutDialog", parent)
        self.setMinimumWidth(420)
        for text, dim in (
            (f"{APP_NAME} v{__version__}", False),
            ("本地草稿板 + PDF 批注；草稿不保存，批注存 sidecar。", True),
            ("PDF 渲染：PyMuPDF（AGPL-3.0）；本项目以 AGPL-3.0 发布。", True),
        ):
            label = QLabel(text, self)
            label.setWordWrap(True)
            if dim:
                label.setStyleSheet(f"color: {theme.TEXT_DIM};")
            self.column.addWidget(label)
        self.close_button = self.add_close()


class _DialogTitle(QLabel):
    """无边框对话框的拖动标题：按住空白处拖动窗口。"""

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                ev.accept()
                return
        super().mousePressEvent(ev)


class CloseConfirmDialog(_BaseDialog):
    """关主窗三选一：取消 / 退出应用 / 最小化到托盘。

    取消只关闭对话框，**不会恢复全屏批注覆盖层**：那层在 draw 模式会吃掉
    所有鼠标事件，恢复它会把主窗“锁”成看着正常却点不动。Esc / Alt+F4 /
    关闭框都不执行动作，必须明确点按钮。

    **非模态**显示（``show_confirm``，绝不 ``exec()``）：真实 Windows 上
    模态框会在 OS 层 Disable 父主窗，而本框在自身 finished 槽里
    deleteLater，偶发主窗收不回 Enable、变成“只能拖边框”的假死，故改为
    非模态从根本上规避。
    """

    actionChosen = Signal(str)

    def __init__(self, app_name: str = APP_NAME, can_minimize: bool = True,
                 parent: QWidget | None = None, extra_windows: int = 0) -> None:
        super().__init__(f"关闭 {app_name} 窗口？", "CloseConfirmDialog", parent)
        # 与应用主窗同款深色外壳：不要 Windows 原生的浅色标题条
        # 置顶 + 自绘外壳：主窗被别的窗口压住时，确认框也不能躲到后面
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint)
        apply_rounded_corners(self)
        self.action = ""
        self._action_emitted = False
        self.setMinimumWidth(460)

        self.title_label = _DialogTitle(f"关闭 {app_name} 窗口？", self)
        self.title_label.setObjectName("CloseConfirmTitle")
        self.title_label.setCursor(Qt.PointingHandCursor)
        self.column.insertWidget(0, self.title_label)

        body_text = ("最小化到托盘后，后台任务会继续运行；"
                     "退出应用会关闭所有墨页实例并停止正在进行的工作。")
        if extra_windows > 0:
            body_text += (f"\n当前还有另外 {extra_windows} 个窗口开着，"
                          "选择「退出应用」会把它们全部关闭。")
        body = QLabel(body_text, self)
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        self.column.addWidget(body)
        self.body_label = body

        self.cancel_button = self._action_button(
            "取消", "CloseCancel", theme.SURFACE, theme.TEXT,
            theme.SURFACE_HOVER,
            lambda: self._choose("cancel"))
        self.quit_button = self._action_button(
            "退出应用", "CloseQuit", theme.DANGER, "#ffffff",
            theme.ACCENT_HOVER, lambda: self._choose("quit"))
        self.minimize_button = self._action_button(
            "最小化到托盘", "CloseMinimize", theme.ACCENT_SOFT, theme.TEXT,
            theme.SURFACE_PRESSED, lambda: self._choose("tray"))
        self.minimize_button.setEnabled(bool(can_minimize))
        if not can_minimize:
            self.minimize_button.setToolTip(
                "托盘与全局热键都不可用：最小化后将无法再呼出窗口")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.quit_button)
        row.addWidget(self.minimize_button)
        self.column.addLayout(row)

        (self.minimize_button if can_minimize else self.quit_button).setDefault(True)

    def _action_button(self, text: str, name: str, background: str,
                       foreground: str, hover: str, slot) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName(name)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            f"QPushButton#{name} {{ background: {background}; color: {foreground};"
            " border: none; border-radius: 6px; padding: 7px 16px; font-weight: 600; }"
            f"QPushButton#{name}:hover {{ background: {hover}; }}"
            f"QPushButton#{name}:disabled {{ background: {theme.SURFACE};"
            f" color: {theme.TEXT_MUTED}; }}")
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _emit_action(self, action: str) -> None:
        self.action = action
        if self._action_emitted:
            return
        self._action_emitted = True
        self.actionChosen.emit(action)

    def _choose(self, action: str) -> None:
        self._emit_action(action)
        self.accept()

    def reject(self) -> None:
        # 没有“取消”：Esc 不关框，避免又走取消路径
        return

    def closeEvent(self, ev) -> None:
        # Alt+F4 / 外部 close() 也不执行动作，必须点退出或最小化
        ev.ignore()

    def _force_front(self) -> None:
        self.raise_()
        self.activateWindow()

    def show_confirm(self) -> None:
        """非阻塞显示并强制到前台；动作通过 actionChosen 回传。

        非模态：不再用 Qt.ApplicationModal，避免真实 Windows 上父主窗被
        OS 层 Disable 后、对话框在自身 finished 槽里 deleteLater 导致主窗
        收不回 Enable（表现为“只能拖边框、内部全部无响应”的假死）。
        """
        parent = self.parentWidget()
        if parent is not None:
            pr = parent.geometry()
            self.adjustSize()
            self.move(pr.center() - self.rect().center())
        self.show()
        self._force_front()
        QTimer.singleShot(0, self._force_front)


def show_shortcuts(parent: QWidget | None = None) -> None:
    ShortcutsDialog(parent).exec()


def show_about(parent: QWidget | None = None) -> None:
    AboutDialog(parent).exec()


class ClosePrompt(QWidget):
    """主窗内部的关闭遮罩：不是系统窗口，取消只隐藏自己。

    顶层 QDialog 在 Windows 上会引入模态栈、前台抢占和鼠标捕获问题；
    这里用主窗子控件盖一层，按钮直接发 actionChosen，主窗始终可交互。
    """

    actionChosen = Signal(str)

    def __init__(self, app_name: str = APP_NAME, can_minimize: bool = True,
                 parent: QWidget | None = None, extra_windows: int = 0) -> None:
        super().__init__(parent)
        self.setObjectName("ClosePrompt")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.action = ""
        self.setStyleSheet(f"""
            QWidget#ClosePrompt {{ background: rgba(75, 47, 56, 145); }}
            QFrame#ClosePromptPanel {{
                background: {theme.SURFACE};
                border: 1px solid {theme.BORDER_STRONG};
                border-radius: 10px;
            }}
            QLabel#ClosePromptTitle {{
                color: {theme.TEXT}; font-size: 15px; font-weight: 600;
            }}
            QLabel#ClosePromptBody {{ color: {theme.TEXT_DIM}; font-size: 12px; }}
        """)

        panel = QFrame(self)
        panel.setObjectName("ClosePromptPanel")
        panel.setFixedWidth(460)
        title = QLabel(f"关闭 {app_name} 窗口？", panel)
        title.setObjectName("ClosePromptTitle")
        body_text = ("最小化到托盘后，后台任务会继续运行；"
                     "退出应用会关闭所有墨页实例并停止正在进行的工作。")
        if not can_minimize:
            body_text = ("这是副实例，没有托盘与全局热键；"
                         "关闭窗口会直接结束这个实例。")
        if extra_windows > 0:
            body_text += (f"\n当前还有另外 {extra_windows} 个窗口开着，"
                          "选择「退出应用」会把它们全部关闭。")
        body = QLabel(body_text, panel)
        body.setObjectName("ClosePromptBody")
        body.setWordWrap(True)

        self.cancel_button = self._button(panel, "取消", "CloseCancel",
                                          theme.SURFACE, theme.TEXT,
                                          theme.SURFACE_HOVER)
        self.quit_button = self._button(panel, "退出应用", "CloseQuit",
                                        theme.DANGER, "#ffffff",
                                        theme.ACCENT_HOVER)
        self.minimize_button = self._button(panel, "最小化到托盘", "CloseMinimize",
                                            theme.ACCENT_SOFT, theme.TEXT,
                                            theme.SURFACE_PRESSED)
        self.minimize_button.setEnabled(bool(can_minimize))
        self.cancel_button.clicked.connect(lambda: self._choose("cancel"))
        self.quit_button.clicked.connect(lambda: self._choose("quit"))
        self.minimize_button.clicked.connect(lambda: self._choose("tray"))

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.quit_button)
        row.addWidget(self.minimize_button)

        col = QVBoxLayout(panel)
        col.setContentsMargins(18, 16, 18, 14)
        col.setSpacing(10)
        col.addWidget(title)
        col.addWidget(body)
        col.addLayout(row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(panel)
        center.addStretch(1)
        outer.addLayout(center)
        outer.addStretch(1)
        self.panel = panel

    def _button(self, parent, text: str, name: str, background: str,
                foreground: str, hover: str) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName(name)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            f"QPushButton#{name} {{ background: {background}; color: {foreground};"
            " border: none; border-radius: 6px; padding: 7px 16px; font-weight: 600; }"
            f"QPushButton#{name}:hover {{ background: {hover}; }}"
            f"QPushButton#{name}:disabled {{ background: {theme.SURFACE};"
            f" color: {theme.TEXT_MUTED}; }}")
        return button

    def _choose(self, action: str) -> None:
        self.action = action
        self.hide()
        self.actionChosen.emit(action)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape:
            self._choose("cancel")
            return
        super().keyPressEvent(ev)
