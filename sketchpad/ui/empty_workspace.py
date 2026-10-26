"""空白工作区：最后一个标签关掉后显示，而不是强行留一个草稿页。

只发两个意图信号，建草稿/开文件仍由主窗决定。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from sketchpad.ui import icons, theme


class EmptyWorkspace(QWidget):
    newDraftRequested = Signal()
    openRequested = Signal()

    def __init__(self, parent: QWidget | None = None, *,
                 allow_draft: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyWorkspace")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QWidget#EmptyWorkspace {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FFF4F8, stop:1 #FFE3EE); }}
            QFrame#EmptyWorkspaceCard {{ background: rgba(255, 252, 253, 0.50); border: 1px solid rgba(236, 171, 195, 0.60); border-radius: 18px; }}
            QLabel#EmptyWorkspaceTitle {{
                color: {theme.TEXT};
                font-size: 15px;
                font-weight: 600;
            }}
            QLabel#EmptyWorkspaceHint {{ color: {theme.TEXT_DIM}; font-size: 12px; }}
            QPushButton#EmptyWorkspaceAction {{
                background: {theme.SURFACE};
                color: {theme.TEXT};
                border: 1px solid {theme.BORDER_STRONG};
                border-radius: 8px;
                padding: 7px 18px;
                font-size: 13px;
            }}
            QPushButton#EmptyWorkspaceAction:hover {{ background: {theme.SURFACE_HOVER}; }}
            QPushButton#EmptyWorkspaceAction:pressed {{ background: {theme.SURFACE_PRESSED}; }}
            QPushButton#EmptyWorkspacePrimary {{
                background: {theme.ACCENT_SOFT};
                color: {theme.ACCENT};
                border: 1px solid {theme.ACCENT_BORDER};
            }}
            QPushButton#EmptyWorkspacePrimary:hover {{ background: {theme.SURFACE_PRESSED}; }}
        """)

        avatar = QLabel(self)
        avatar.setObjectName("EmptyWorkspaceAvatar")
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(64, 64)
        avatar.setPixmap(icons.app_icon().pixmap(64, 64))
        avatar.setToolTip("墨页")

        title = QLabel("没有打开的文档", self)
        title.setObjectName("EmptyWorkspaceTitle")
        title.setAlignment(Qt.AlignCenter)
        hint = QLabel("打开一个文件，或从其他窗口拖一个标签进来", self)
        hint.setObjectName("EmptyWorkspaceHint")
        hint.setAlignment(Qt.AlignCenter)

        card = QFrame(self)
        card.setObjectName("EmptyWorkspaceCard")
        card.setMaximumWidth(360)
        column = QVBoxLayout(card)
        column.setContentsMargins(28, 26, 28, 24)
        column.setSpacing(10)
        column.addWidget(avatar, 0, Qt.AlignHCenter)
        column.addWidget(title)
        column.addWidget(hint)
        if allow_draft:
            draft_button = QPushButton("新建笔记", self)
            draft_button.setObjectName("EmptyWorkspaceAction")
            draft_button.setCursor(Qt.PointingHandCursor)
            draft_button.setToolTip("新建笔记")
            draft_button.clicked.connect(self.newDraftRequested.emit)
            column.addWidget(draft_button, 0, Qt.AlignHCenter)
        open_button = QPushButton("打开文件", self)
        open_button.setObjectName("EmptyWorkspacePrimary")
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.setToolTip("打开文件 (Ctrl+O)")
        open_button.clicked.connect(self.openRequested.emit)
        column.addWidget(open_button, 0, Qt.AlignHCenter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        outer.addWidget(card, 0, Qt.AlignHCenter)
        outer.addStretch(1)
