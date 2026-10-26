"""截图：工具栏按钮、全局热键配置、截图落到笔记画布。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import main
from sketchpad.annotate.config import Config
from sketchpad.annotate.constants import DEFAULT_CAPTURE_HOTKEY

_ALIVE: list = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path):
    w = main.MainWindow(main.AppController(tmp_path / "cfg"))
    w._confirm_unsaved = lambda action: QMessageBox.Discard
    w.show()
    QTest.qWaitForWindowExposed(w)
    _ALIVE.append(w)
    yield w
    w.scratch.hide()
    w._quitting = True
    w.close()


class FakeAnnotate:
    def __init__(self):
        self.calls = 0

    def start_capture(self):
        self.calls += 1


def test_capture_button_and_hotkey_default(win):
    assert DEFAULT_CAPTURE_HOTKEY == "Alt+A+S"
    assert win.toolbox.capture_button.toolTip().startswith("截图")
    assert win.toolbox.capture_button.objectName() == "CaptureButton"
    assert win.toolbox.capture_button.isHidden() is False
    fake = FakeAnnotate()
    win._annotate = fake
    assert win._start_screenshot() is True
    assert fake.calls == 1


def test_captured_image_goes_to_clipboard_only(win):
    image = QImage(120, 90, QImage.Format_ARGB32)
    image.fill(0xFF3366CC)
    win._on_screenshot_captured(image)
    assert win.view.image_count == 0
    assert QGuiApplication.clipboard().image().size().width() == 120
    assert "剪贴板" in win.message_label.text()


class _FakeOverlay:
    def __init__(self):
        self.mode = "hidden"
        self.calls = 0

    def toggle_mode(self):
        self.calls += 1


class _FakeAnnotateWithOverlay:
    def __init__(self):
        self.overlay = _FakeOverlay()
        self.opened = 0
        self.closed = 0

    def enter_draw(self):
        self.overlay.mode = "draw"
        self.opened += 1

    def enter_passthrough(self):
        self.overlay.mode = "passthrough"
        self.opened += 1

    def hide(self):
        self.overlay.mode = "hidden"
        self.closed += 1


def test_toolbar_fullscreen_annotate_button(win):
    assert win.toolbox.annotate_button.toolTip().startswith("全屏批注")
    fake = _FakeAnnotateWithOverlay()
    win._annotate = fake
    assert win._toggle_fullscreen_annotate() is True
    assert fake.opened == 1
    assert win._toggle_fullscreen_annotate() is True
    assert fake.closed == 1
    win.toolbox.annotate_button.setChecked(True)
    assert fake.opened == 2
    win.toolbox.annotate_button.setChecked(False)
    assert fake.closed == 2


class FakeScreenshot:
    def __init__(self):
        self.starts: list[str] = []
        self.opened: list[tuple] = []
        self.history_shown = 0

    def start(self, mode="region"):
        self.starts.append(mode)
        return True

    def open_editor(self, image, source="region"):
        self.opened.append((image, source))

    def show_history(self):
        self.history_shown += 1


def test_截图控制器优先于旧批注路径(win):
    fake = FakeScreenshot()
    win._screenshot = fake
    assert win._start_screenshot("window") is True
    assert fake.starts == ["window"]


def test_截图后打开编辑器且仍复制剪贴板(win):
    fake = FakeScreenshot()
    win._screenshot = fake
    image = QImage(50, 40, QImage.Format_ARGB32)
    image.fill(0xFF3366CC)
    win._on_screenshot_captured(image)
    assert fake.opened and fake.opened[0][1] == "region"
    assert QGuiApplication.clipboard().image().width() == 50
    assert "可继续标注" in win.message_label.text()


def test_相机下拉列出四种模式和历史(win):
    from PySide6.QtTest import QSignalSpy
    spy = QSignalSpy(win.toolbox.captureModeRequested)
    actions = {action.text(): action
               for action in win.toolbox.capture_button.menu().actions()}
    expected = [("区域截图", "region"), ("窗口截图", "window"),
                ("全屏截图", "fullscreen"), ("上一区域", "last"),
                ("屏幕取色", "color"), ("截图历史…", "history")]
    for label, _mode in expected:
        actions[label].trigger()
    assert [spy.at(index)[0] for index in range(spy.count())] == [
        mode for _label, mode in expected]


def test_历史菜单打开截图历史(win):
    fake = FakeScreenshot()
    win._screenshot = fake
    win._on_capture_mode("history")
    assert fake.history_shown == 1


class _CloseOverlay:
    def __init__(self, mode="draw"):
        self.mode = mode


class _CloseAnnotate:
    def __init__(self, mode="draw"):
        self.overlay = _CloseOverlay(mode)
        self.hides = 0
        self.draws = 0
        self.passes = 0

    def hide(self):
        self.hides += 1
        self.overlay.mode = "hidden"

    def enter_draw(self):
        self.draws += 1
        self.overlay.mode = "draw"

    def enter_passthrough(self):
        self.passes += 1
        self.overlay.mode = "passthrough"


def _choose_close(win, action, monkeypatch):
    """走真实的异步关窗回调；quit 时替换掉真正退出进程的步骤。"""
    win._close_dialog_restore = win._prepare_for_close_dialog()
    if action == "quit":
        monkeypatch.setattr(win, "quit_app", lambda: None)
    win._on_close_action_chosen(action)
    QTest.qWait(20)


class _CloseScreenshot:
    def __init__(self):
        self.suspends = 0
        self.resumes = 0

    def suspend_topmost(self):
        self.suspends += 1

    def resume_topmost(self):
        self.resumes += 1


def test_关窗时先收起批注层_选择托盘后从托盘恢复(win, monkeypatch, qapp):
    fake = _CloseAnnotate("draw")
    win._annotate = fake
    shot = _CloseScreenshot()
    win._screenshot = shot
    _choose_close(win, "tray", monkeypatch)
    assert fake.hides == 1
    assert shot.suspends == 1
    assert win.isVisible() is False

    win.restore_main()
    qapp.processEvents()
    assert fake.draws == 1
    assert fake.overlay.mode == "draw"
    assert shot.resumes == 1


def test_关窗时穿透模式在托盘恢复后还原(win, monkeypatch, qapp):
    fake = _CloseAnnotate("passthrough")
    win._annotate = fake
    _choose_close(win, "tray", monkeypatch)
    assert fake.hides == 1

    win.restore_main()
    qapp.processEvents()
    assert fake.passes == 1


def test_关窗选择退出时不恢复批注层(win, monkeypatch):
    fake = _CloseAnnotate("draw")
    win._annotate = fake
    shot = _CloseScreenshot()
    win._screenshot = shot
    _choose_close(win, "quit", monkeypatch)
    assert fake.hides == 1
    assert shot.suspends == 1
    assert fake.draws == 0
    assert shot.resumes == 0
