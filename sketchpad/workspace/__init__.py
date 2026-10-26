"""多文档工作区：标签栏容器 + 双文档分屏容器。

两个组件都只依赖 Qt，会话对象走鸭子类型（``.pane`` / ``.tab_title``），
不反向依赖主窗，方便被两套产品壳复用。
"""
from __future__ import annotations

from sketchpad.workspace.split import SplitWorkspace
from sketchpad.workspace.tabs import TabEntry, WorkspaceTabs

__all__ = ["TabEntry", "WorkspaceTabs", "SplitWorkspace"]
