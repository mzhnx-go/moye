"""兼容入口：真正的小程序在 sketchpad/app.py。

保留这个文件是为了 `python main.py`、run.bat 与既有测试/工具不用改调用方式。
"""
import sys

if getattr(sys, "frozen", False):
    # 打包版没有控制台：先装异常钩子，再导入 UI，闪退才有日志和提示框
    from sketchpad.startup import install_exception_hooks

    install_exception_hooks()

from sketchpad.app import MIN_H, MIN_W, AppController, MainWindow, main

__all__ = ["MIN_H", "MIN_W", "AppController", "MainWindow", "main"]

if __name__ == "__main__":
    from sketchpad.convert import install as install_convert
    from sketchpad.reader import install as install_readers
    from sketchpad.word import install as install_word

    install_readers()
    install_word()
    install_convert()
    raise SystemExit(main())
