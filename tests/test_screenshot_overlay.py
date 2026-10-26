"""in-place 遮罩：松手保留选区、手柄缩放、直接标注、按选区导出。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from sketchpad.screenshot.constants import (TOOL_CROP, TOOL_MARKER,
                                            TOOL_MOSAIC, TOOL_NUMBER, TOOL_RECT,
                                            TOOL_SELECT, TOOL_TEXT)
from sketchpad.screenshot.items import NumberItem, TextItem
from sketchpad.screenshot.overlay import ScreenshotOverlay

_ALIVE: list[ScreenshotOverlay] = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _cleanup(qapp):
    yield
    while _ALIVE:
        overlay = _ALIVE.pop()
        try:
            overlay.cancel()
            overlay.deleteLater()
        except RuntimeError:
            pass
    qapp.processEvents()


def make_overlay(width: int = 400, height: int = 300,
                 marked: bool = False, **kwargs) -> ScreenshotOverlay:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    if marked:
        for x in range(80, 120):
            for y in range(80, 120):
                image.setPixelColor(x, y, QColor("#000000"))
    overlay = ScreenshotOverlay(image, QRect(0, 0, width, height), **kwargs)
    overlay.show()
    QApplication.processEvents()
    _ALIVE.append(overlay)
    return overlay


def mouse(kind, x: float, y: float) -> QMouseEvent:
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def drag(overlay: ScreenshotOverlay, x1, y1, x2, y2) -> None:
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, x1, y1))
    overlay.mouseMoveEvent(mouse(QEvent.Type.MouseMove, x2, y2))
    overlay.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, x2, y2))


def select(overlay: ScreenshotOverlay, x1=50, y1=40, x2=250, y2=200) -> None:
    drag(overlay, x1, y1, x2, y2)


def test_拖选后保留选区并显示工具栏且不复制():
    overlay = make_overlay()
    copied = QSignalSpy(overlay.copied)
    select(overlay)
    assert overlay.selection_rect() == QRectF(50, 40, 200, 160)
    assert overlay.toolbar.isVisible() is True
    assert copied.count() == 0
    assert overlay.isVisible() is True


def test_拖角手柄缩放选区():
    overlay = make_overlay()
    select(overlay)
    drag(overlay, 250, 200, 300, 260)
    assert overlay.selection_rect() == QRectF(50, 40, 250, 220)


def test_拖边手柄只改一个方向():
    overlay = make_overlay()
    select(overlay)
    drag(overlay, 250, 120, 330, 120)     # 右边中点
    assert overlay.selection_rect() == QRectF(50, 40, 280, 160)


def test_指针拖空白移动选区且标注不跟着动():
    overlay = make_overlay()
    select(overlay)
    overlay.set_tool(TOOL_RECT)
    drag(overlay, 60, 60, 150, 120)
    item = overlay.session.scene.annotation_items()[0]
    start = QPointF(item.pos())
    overlay.set_tool(TOOL_SELECT)
    drag(overlay, 200, 170, 230, 200)     # 选区内部空白
    assert overlay.selection_rect() == QRectF(80, 70, 200, 160)
    assert item.pos() == start


def test_画矩形可撤销重做():
    overlay = make_overlay()
    select(overlay)
    overlay.set_tool(TOOL_RECT)
    drag(overlay, 60, 60, 150, 120)
    assert len(overlay.session.scene.annotation_items()) == 1
    assert overlay.session.undo() is True
    assert len(overlay.session.scene.annotation_items()) == 0
    assert overlay.session.redo() is True
    assert len(overlay.session.scene.annotation_items()) == 1


def test_文字工具在选区内落字():
    overlay = make_overlay()
    select(overlay)
    overlay.set_tool(TOOL_TEXT)
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 100, 100))
    item = overlay.session.scene.focusItem()
    assert isinstance(item, TextItem)
    item.setPlainText("你好")
    overlay._commit_text()
    assert item.scene() is overlay.session.scene


def test_文字编辑中工具快捷键不生效():
    overlay = make_overlay()
    select(overlay)
    overlay.set_tool(TOOL_TEXT)
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 100, 100))
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_R,
                      Qt.KeyboardModifier.NoModifier, "r")
    overlay.keyPressEvent(event)
    assert overlay.session.tool == TOOL_TEXT


def test_序号工具自增():
    overlay = make_overlay()
    select(overlay)
    overlay.set_tool(TOOL_NUMBER)
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 100, 100))
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 150, 130))
    numbers = sorted(item.number for item in overlay.session.scene.annotation_items()
                     if isinstance(item, NumberItem))
    assert numbers == [1, 2]


def test_马赛克只导出选区且改变像素():
    overlay = make_overlay(marked=True)
    select(overlay, 50, 50, 200, 200)
    before = overlay.session.render_region(overlay.selection_rect())
    overlay.set_tool(TOOL_MOSAIC)
    drag(overlay, 70, 70, 140, 140)
    after = overlay.session.render_region(overlay.selection_rect())
    assert after != before


def test_复制只导出选区且不含压暗层(qapp):
    overlay = make_overlay()
    select(overlay, 50, 50, 150, 130)
    assert overlay.copy_to_clipboard() is True
    image = QApplication.clipboard().image()
    assert (image.width(), image.height()) == (100, 80)
    assert QColor(image.pixel(5, 5)) == QColor("#ffffff")


def test_Enter复制并关闭():
    overlay = make_overlay()
    select(overlay)
    copied = QSignalSpy(overlay.copied)
    closed = QSignalSpy(overlay.closed)
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                    Qt.KeyboardModifier.NoModifier))
    assert copied.count() == 1
    assert closed.count() == 1
    assert overlay.isVisible() is False


def test_Esc取消不复制():
    overlay = make_overlay()
    select(overlay)
    copied = QSignalSpy(overlay.copied)
    closed = QSignalSpy(overlay.closed)
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                    Qt.KeyboardModifier.NoModifier))
    assert copied.count() == 0
    assert closed.count() == 1


def test_工具栏下方放不下时翻到上方():
    overlay = make_overlay()
    select(overlay, 50, 200, 200, 290)
    top = overlay.mapFromScene(overlay.selection_rect().topLeft()).y()
    assert overlay.toolbar.isVisible() is True
    assert overlay.toolbar.y() < top


def test_保存PNG并关闭(tmp_path):
    overlay = make_overlay()
    select(overlay)
    target = tmp_path / "shot.png"
    saved = QSignalSpy(overlay.saved)
    assert overlay.save_to_file(str(target)) is True
    assert target.exists()
    assert saved.count() == 1
    assert overlay.isVisible() is False


def test_复制包含标注内容():
    overlay = make_overlay()
    select(overlay, 50, 50, 150, 130)
    overlay.set_tool(TOOL_RECT)
    drag(overlay, 60, 60, 140, 120)
    overlay.set_tool(TOOL_SELECT)
    assert overlay.copy_to_clipboard() is True
    image = QApplication.clipboard().image()
    assert image.size().width() == 100 and image.size().height() == 80
    # 红框落在导出图里（找任意一个红色像素）
    found = any(QColor(image.pixel(x, y)).red() > 200
                and QColor(image.pixel(x, y)).green() < 100
                for x in range(0, image.width(), 5)
                for y in range(0, image.height(), 5))
    assert found is True


def test_OCR使用注入函数且遮罩保持打开(monkeypatch):
    overlay = make_overlay(ocr_enabled=True, ocr_func=lambda image: "识别文字")
    select(overlay)
    captured: list[str] = []

    class FakeDialog:
        def __init__(self, text, parent=None):
            captured.append(text)

        def exec(self):
            return 1

    monkeypatch.setattr("sketchpad.screenshot.overlay.OCRResultDialog", FakeDialog)
    assert overlay.run_ocr() is True
    from PySide6.QtTest import QTest
    for _ in range(20):
        QTest.qWait(50)
        if captured:
            break
    assert captured == ["识别文字"]
    assert overlay.isVisible() is True


def test_OCR在后台线程不阻塞UI(monkeypatch):
    import time

    def slow_ocr(image):
        time.sleep(0.35)
        return "慢结果"

    overlay = make_overlay(ocr_enabled=True, ocr_func=slow_ocr)
    select(overlay)
    captured: list[str] = []

    class FakeDialog:
        def __init__(self, text, parent=None):
            captured.append(text)

        def exec(self):
            return 1

    monkeypatch.setattr("sketchpad.screenshot.overlay.OCRResultDialog", FakeDialog)
    started = time.monotonic()
    assert overlay.run_ocr() is True
    assert time.monotonic() - started < 0.2      # UI 线程只做渲染和启动
    assert overlay.ocr_busy() is True
    from PySide6.QtTest import QTest
    for _ in range(30):
        QTest.qWait(50)
        if captured:
            break
    assert captured == ["慢结果"]
    assert overlay.ocr_busy() is False
    assert overlay.isVisible() is True


def test_工具栏按钮全部图标化且都有悬浮提示():
    overlay = make_overlay()
    bar = overlay.toolbar
    buttons = list(bar._tool_buttons.values())
    buttons += list(bar._color_buttons.values())
    buttons += list(bar._width_buttons.values())
    buttons += [bar.undo_button, bar.redo_button, bar.ocr_button]
    for button in buttons:
        assert button.icon().isNull() is False
        assert button.text() == ""
        assert button.toolTip().strip() != ""
        assert "\n" in button.toolTip()       # 名称 + 行为
    assert "#ffffff" in bar.styleSheet()


def test_取色模式悬停并复制(qapp):
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    image.setPixelColor(10, 10, QColor("#ff0000"))
    overlay = ScreenshotOverlay(image, QRect(0, 0, 100, 100), color_mode=True)
    overlay.show()
    QApplication.processEvents()
    _ALIVE.append(overlay)
    overlay._hover_scene = QPointF(10, 10)
    assert overlay.hover_hex() == "#FF0000"
    picked = QSignalSpy(overlay.colorPicked)
    assert overlay.copy_hover_color() is True
    assert picked.count() == 1
    assert QApplication.clipboard().text() == "#FF0000"
    QApplication.processEvents()


def test_裁剪把选区内容变成新底图并可撤销():
    overlay = make_overlay()
    select(overlay, 50, 50, 250, 200)
    overlay.set_tool(TOOL_CROP)
    drag(overlay, 70, 70, 170, 170)
    assert overlay.session.scene.sceneRect() == QRectF(70, 70, 100, 100)
    assert overlay.selection_rect() == QRectF(70, 70, 100, 100)
    assert overlay.undo() is True
    assert overlay.session.scene.sceneRect() == QRectF(0, 0, 400, 300)
    assert overlay.selection.bounds == QRectF(0, 0, 400, 300)


def test_工具栏按钮切换工具():
    overlay = make_overlay()
    overlay.toolbar._tool_buttons[TOOL_MARKER].click()
    assert overlay.session.tool == TOOL_MARKER


def test_初始选区让全屏模式直接显示手柄和工具栏():
    image = QImage(120, 80, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    overlay = ScreenshotOverlay(image, QRect(0, 0, 120, 80),
                                initial_rect=QRectF(0, 0, 120, 80))
    overlay.show()
    QApplication.processEvents()
    _ALIVE.append(overlay)
    assert overlay.selection_rect() == QRectF(0, 0, 120, 80)
    assert overlay.toolbar.isVisible() is True
