"""批注层的 PageFlow 侧接线：热键路由、托盘菜单、退出收尾。

不注册真实全局热键（会占系统键），`fake_register` / `controller_factory`
把 `GlobalHotkey.register` 换成假的，只验证"注册失败不崩、注册成功会接信号"。
"""
from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QAction

from sketchpad.annotate import controller as controller_module
from sketchpad.annotate import install


def test_foreground_belongs_to_us返回布尔():
    assert isinstance(controller_module.foreground_belongs_to_us(), bool)


def test_install给托盘菜单加全屏批注(window, controller_factory):
    controller = controller_factory(window)
    labels = [a.text() for a in window.tray.contextMenu().findChildren(QAction)]
    assert "全屏批注" in labels
    assert controller.overlay.toolbar.capture_button.objectName() == "captureButton"


def test_install后配置跟着PageFlow数据目录走(window, controller_factory, tmp_path):
    controller = controller_factory(window)
    expected = tmp_path / "data" / "annotate.json"
    assert controller.config.toolbar_orientation == "horizontal"
    assert controller.config.storage_dir == tmp_path / "data" / "annotations"
    assert controller.config == type(controller.config).load(expected)


def test_热键注册失败只提示不崩(window, fake_register, controller_factory):
    controller = controller_factory(window)
    assert fake_register == ["Ctrl+Alt+D", "Ctrl+Alt+H", "Alt+1", "Alt+2",
                             "Alt+3", "Alt+4", "Alt+A"]
    assert any("不可用" in text for text in window.notes)
    assert controller.overlay.mode == "hidden"


def test_前台是别的程序时切批注层(window, monkeypatch, controller_factory):
    monkeypatch.setattr(controller_module, "foreground_belongs_to_us",
                        lambda: False)
    controller = controller_factory(window, registered=True)
    controller.on_toggle()
    assert controller.overlay.mode == "passthrough"   # 打开默认穿透


def test_前台是PageFlow时优先激活主窗(window, monkeypatch, controller_factory):
    monkeypatch.setattr(controller_module, "foreground_belongs_to_us",
                        lambda: True)
    raised: list[str] = []
    monkeypatch.setattr(window, "show", lambda: raised.append("show"))
    monkeypatch.setattr(window, "raise_", lambda: raised.append("raise"))
    monkeypatch.setattr(window, "activateWindow", lambda: raised.append("activate"))
    controller = controller_factory(window, registered=True)
    controller.on_toggle()
    # 主窗被调到前台，批注层不动（不抢焦点、不弹出来）
    assert raised == ["show", "raise", "activate"]
    assert controller.overlay.mode == "hidden"


def test_隐藏键显隐批注层(window, controller_factory):
    controller = controller_factory(window, registered=True)
    controller.on_hide()
    assert controller.overlay.mode == "passthrough"
    controller.on_hide()
    assert controller.overlay.mode == "hidden"


def test_shutdown可重复调用且收掉覆盖层(window, controller_factory):
    controller = controller_factory(window, registered=True)
    controller.enter_draw()
    controller.shutdown()
    controller.shutdown()                 # 第二次不能抛
    assert controller.overlay.mode == "hidden"


def test_截图按钮在撤销之后(window, controller_factory):
    controller = controller_factory(window)
    order = [w.objectName() for w in controller.overlay.toolbar._items]
    assert order.index("clearButton") < order.index("undoButton")
    assert order.index("undoButton") < order.index("captureButton")
    assert order.index("captureButton") < order.index("hideButton")


def test_截图先把批注层收掉再抓屏(window, monkeypatch, controller_factory):
    """抓屏前必须把整层藏掉，否则工具栏会一起进截图。"""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QGuiApplication

    monkeypatch.setattr(QGuiApplication, "screenAt", staticmethod(lambda pos: None))

    class _FakeScreen(QObject):
        """只实现控制器 / overlay 用到的屏幕接口。"""
        geometryChanged = Signal()

        def __init__(self):
            super().__init__()
            self.grabbed = 0

        def geometry(self):
            return QRect(0, 0, 800, 600)

        def availableGeometry(self):
            return QRect(0, 0, 800, 600)

        def grabWindow(self, *_args):
            self.grabbed += 1
            from PySide6.QtGui import QImage, QPixmap
            image = QImage(800, 600, QImage.Format_ARGB32)
            image.fill(Qt.blue)
            return QPixmap.fromImage(image)

    screen = _FakeScreen()
    monkeypatch.setattr(QGuiApplication, "primaryScreen",
                        staticmethod(lambda: screen))
    controller = controller_factory(window, registered=True)
    controller.enter_draw()

    controller.start_capture()
    assert controller.overlay.mode == "hidden"       # 抓屏前先收
    assert screen.grabbed == 0                        # 还在等那一帧

    controller._grab_and_show()                       # 手动跳过 80ms 延时
    assert screen.grabbed == 1


def test_有截图控制器时截图入口委托给新遮罩(window, controller_factory):
    controller = controller_factory(window)

    class FakeScreenshot:
        def __init__(self):
            self.starts = []

        def start(self, mode="region"):
            self.starts.append(mode)
            return True

    window._screenshot = FakeScreenshot()
    controller.start_capture()
    assert window._screenshot.starts == ["region"]


def test_Alt1_Alt2_Alt3_Alt4_switch_annotation_modes(window, controller_factory):
    controller = controller_factory(window, registered=True)
    controller.overlay.enter_draw_mode()
    controller.on_pen()
    assert controller.overlay.mode == "draw"
    assert controller.overlay.canvas.tool == "pen"
    controller.on_eraser()
    assert controller.overlay.canvas.tool == "eraser"
    controller.on_passthrough()
    assert controller.overlay.mode == "passthrough"
    controller.on_clear()
    assert controller.overlay.canvas.document.strokes == []
