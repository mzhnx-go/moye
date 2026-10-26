"""主窗统一顶栏：标题、菜单、工具栏共用一块平面，不做玻璃/阴影/浮层。

只负责容器与材质；标题栏/菜单/工具栏仍由各自类处理行为与信号。
横向工具栏模式下三行都在这里；右侧竖栏模式只留标题与菜单（工具栏由
MainWindow 搬回 body）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

HOUSE_MARGINS = (0, 0, 0, 0)
PANEL_MARGINS = (6, 0, 6, 2)
ROW_SPACING = 2


class TopChrome(QWidget):
    """透明外壳 + 一块与窗口同色的平面；rows 是标题/菜单/工具栏的家。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopChrome")
        self.setFocusPolicy(Qt.NoFocus)

        self.panel = QFrame(self)
        self.panel.setObjectName("TopChromePanel")
        self.panel.setAttribute(Qt.WA_StyledBackground, True)
        self.panel.setFocusPolicy(Qt.NoFocus)

        self.rows = QVBoxLayout(self.panel)
        self.rows.setContentsMargins(*PANEL_MARGINS)
        self.rows.setSpacing(ROW_SPACING)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*HOUSE_MARGINS)
        outer.setSpacing(0)
        outer.addWidget(self.panel)

    def add_row(self, widget: QWidget) -> None:
        self.rows.addWidget(widget)

    def remove_row(self, widget: QWidget) -> None:
        self.rows.removeWidget(widget)

    def has_row(self, widget: QWidget) -> bool:
        return self.rows.indexOf(widget) >= 0
