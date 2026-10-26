"""主窗底部状态行：消息、保存角标、页面/缩放/笔迹，直接铺在窗口底色上。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy


class _ShrinkableLabel(QLabel):
    """单行提示：宽度可以被压到 0，文字超出时由胶囊裁切，不挤占持久文字。"""

    def minimumSizeHint(self) -> QSize:
        # QLabel 默认拿整段文字宽度当最小宽度；布局空间不够时会连 Fixed
        # 的标签一起压，所以这里把最小宽度让出去。
        return QSize(0, super().minimumSizeHint().height())


class StatusPill(QFrame):
    """状态栏里的一行文字；名字保留给 app.py 现有接线，不再画胶囊/边框。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 2, 10, 2)
        row.setSpacing(6)

        # 持久文字（保存角标、页面/缩放/笔迹）不许被临时提示挤掉：
        # Fixed 让它们在布局里保持 sizeHint，多余/不足的宽度都由消息承担。
        self.message = _ShrinkableLabel("", self)
        self.message.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.message.setMinimumWidth(0)
        self.save = QLabel("", self)
        self.save.setObjectName("SaveStatus")
        self.save.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.status = QLabel(self)
        self.status.setObjectName("StatusValue")
        self.status.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.save.hide()

        row.addWidget(self.message, 1)
        row.addWidget(self.save, 0)
        row.addWidget(self.status, 0)
