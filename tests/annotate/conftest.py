"""测试公共配置。

必须在导入任何 PySide6 模块之前把平台插件固定为 offscreen，
否则在没有显示器的环境里会尝试创建原生窗口。
"""
import os

# setdefault：外部若已显式指定平台（例如真机手测）则尊重外部设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from sketchpad.annotate import install
from sketchpad.services.hotkey import GlobalHotkey


class _FakeAppController:
    """只要 PageFlow AppController 上那个被批注用到的成员。"""

    def __init__(self, settings_path):
        self.settings_path = settings_path


class _FakeWindow(QWidget):
    """只实现批注侧真正用到的 MainWindow 接口，避免把整个 PageFlow 拖进测试。"""

    def __init__(self, tmp_path):
        super().__init__()
        self.controller = _FakeAppController(tmp_path / "data" / "settings.json")
        self.tray = QSystemTrayIcon(self)
        self.tray.setContextMenu(QMenu(self))
        self.notes: list[str] = []

    def _note(self, text: str) -> None:
        self.notes.append(text)


@pytest.fixture
def window(qapp, tmp_path):
    """假的 PageFlow 主窗；用完记得让 controller.shutdown() 再 deleteLater。"""
    return _FakeWindow(tmp_path)


@pytest.fixture
def fake_register(monkeypatch):
    """拦掉真实 RegisterHotKey（会占系统键）：默认注册失败，记录调用序列。"""
    calls = []

    def register(self, hwnd, sequence):
        calls.append(sequence)
        return False

    monkeypatch.setattr(GlobalHotkey, "register", register)
    return calls


@pytest.fixture
def controller_factory(monkeypatch, fake_register):
    """造一个装到假主窗上的 AnnotateController；注册结果可现场改。"""
    made = []

    def make(window, registered: bool = False):
        if registered:
            monkeypatch.setattr(GlobalHotkey, "register",
                                lambda self, hwnd, seq: True)
        # 走 install()：把"加托盘菜单项"这条真实路径也一起覆盖掉
        controller = install(window)
        made.append(controller)
        return controller

    yield make
    for controller in made:
        controller.shutdown()


@pytest.fixture(scope="session")
def qapp():
    """整个测试会话共用一个 QApplication（Qt 不允许创建第二个）。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _ensure_qapp(qapp):
    """控件测试不能没有 QApplication：单独跑某个文件时也要先建好。"""
    yield


@pytest.fixture
def qt_events(qapp):
    """测试结束后冲刷 Qt 事件队列，避免延迟信号串到下一个用例。"""
    yield
    qapp.processEvents()
