"""全屏批注层：PageFlow 的子功能。

原来是一个独立小工具（`E:\\批注`），有自己的单实例锁、托盘和全局热键；并入
PageFlow 之后这些都由主程序提供，本包只保留批注层本身：

- `controller.py`：`AnnotateController`（挂载点 + 热键路由）+ `install()`（一行接线）
- `overlay.py` / `canvas.py` / `toolbar.py`：全屏透明批注层、画布、图标工具栏
- `core/`：笔迹模型、撤销历史、抽稀（纯逻辑，无 Qt 依赖）
- `config.py`：`data/annotate.json`，刻意不并入 `sketchpad/core/settings.py`

用法（`sketchpad/app.py` 的 `main()` 里两行）::

    from sketchpad.annotate import install
    ...
    window.start_tray()
    window._annotate = install(window)
"""
from __future__ import annotations

from sketchpad.annotate.config import Config
from sketchpad.annotate.controller import (AnnotateController,
                                           foreground_belongs_to_us)


def install(window) -> AnnotateController:
    """把批注层挂到已建好的 MainWindow 上；必须在 `start_tray()` 之后调用。

    选在托盘建好之后，是为了托盘菜单项可以在这里动态追加——不必去改
    `sketchpad/ui/scratch.py`（该文件有协作者在改）。
    """
    controller = AnnotateController(window)
    tray = getattr(window, "tray", None)
    menu = tray.contextMenu() if tray is not None else None
    if menu is not None:
        action = menu.addAction("全屏批注")
        action.triggered.connect(controller.enter_passthrough)
        menu.addSeparator()
    return controller


__all__ = ["AnnotateController", "Config", "foreground_belongs_to_us", "install"]
