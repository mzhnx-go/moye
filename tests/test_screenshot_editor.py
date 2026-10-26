"""截图编辑器：工具、撤销、裁剪、导出、OCR 注入。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QGraphicsItem

from sketchpad.screenshot import editor as editor_module
from sketchpad.screenshot.constants import TOOL_RECT, TOOL_SELECT
from sketchpad.screenshot.editor import ScreenshotEditor
from sketchpad.screenshot.items import ShapeItem, StrokeItem

_ALIVE: list[ScreenshotEditor] = []


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _cleanup(qapp):
    yield
    while _ALIVE:
        editor = _ALIVE.pop()
        try:
            editor.close()
            editor.deleteLater()
        except RuntimeError:
            pass
    qapp.processEvents()


def make_editor(width: int = 200, height: int = 120, **kwargs) -> ScreenshotEditor:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    editor = ScreenshotEditor(image, **kwargs)
    _ALIVE.append(editor)
    return editor


def make_marked_editor() -> ScreenshotEditor:
    image = QImage(100, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    for x in range(40, 50):
        for y in range(40, 50):
            image.setPixelColor(x, y, QColor("#000000"))
    editor = ScreenshotEditor(image)
    _ALIVE.append(editor)
    return editor


def test_场景尺寸就是截图物理像素():
    editor = make_editor()
    assert editor.scene.sceneRect().size() == QRectF(0, 0, 200, 120).size()
    output = editor.render_output()
    assert (output.width(), output.height()) == (200, 120)
    assert QColor(output.pixel(10, 10)) == QColor("#ffffff")


def test_序号自增且撤销重做():
    editor = make_editor()
    first = editor.place_number(QPointF(20, 20))
    second = editor.place_number(QPointF(60, 60))
    assert (first.number, second.number) == (1, 2)
    numbers = [item for item in editor.scene.annotation_items()
               if hasattr(item, "number")]
    assert len(numbers) == 2
    assert editor.undo() is True
    assert len(editor.scene.annotation_items()) == 1
    assert editor.redo() is True
    assert len(editor.scene.annotation_items()) == 2


def test_文字提交后保留空文字自动移除():
    editor = make_editor()
    item = editor.place_text(QPointF(10, 10))
    item.setPlainText("你好")
    editor.commit_text()
    assert item.scene() is editor.scene
    assert item.toPlainText() == "你好"

    empty = editor.place_text(QPointF(30, 30))
    editor.commit_text()
    assert empty.scene() is None


def test_形状和画笔加入场景并可撤销():
    editor = make_editor()
    shape = ShapeItem(TOOL_RECT, QPointF(5, 5), QPointF(60, 40), "#ff0000", 3)
    editor.scene.addItem(shape)
    editor.commit_item(shape)
    stroke = StrokeItem([QPointF(1, 1), QPointF(30, 30)], "#00ff00", 3)
    editor.scene.addItem(stroke)
    editor.commit_item(stroke)
    assert len(editor.scene.annotation_items()) == 2
    editor.undo()
    assert stroke.scene() is None
    editor.undo()
    assert shape.scene() is None
    editor.redo()
    assert shape.scene() is editor.scene


def test_马赛克真正改变导出像素():
    editor = make_marked_editor()
    before = editor.render_output().copy(30, 30, 40, 40)
    from sketchpad.screenshot.constants import TOOL_MOSAIC
    editor.apply_region_effect(TOOL_MOSAIC, QRectF(30, 30, 40, 40))
    output = editor.render_output()
    assert output.copy(30, 30, 40, 40) != before
    # 区域外不受影响
    assert QColor(output.pixel(5, 5)) == QColor("#ffffff")


def test_模糊生成图元并保留底图尺寸():
    editor = make_marked_editor()
    from sketchpad.screenshot.constants import TOOL_BLUR
    editor.apply_region_effect(TOOL_BLUR, QRectF(30, 30, 40, 40))
    assert len(editor.scene.annotation_items()) == 1
    assert editor.scene.sceneRect().width() == 100


def test_裁剪与撤销恢复尺寸和图元位置():
    editor = make_editor()
    number = editor.place_number(QPointF(30, 30))
    assert editor.apply_crop(QRectF(20, 10, 100, 80)) is True
    assert editor.scene.sceneRect().width() == 100
    assert editor.scene.sceneRect().height() == 80
    assert number.pos() == QPointF(-4, 6)      # (30,30)-半径14-(20,10)
    assert editor.undo() is True
    assert editor.scene.sceneRect().width() == 200
    assert editor.scene.sceneRect().height() == 120
    assert number.pos() == QPointF(16, 16)
    assert editor.redo() is True
    assert editor.scene.sceneRect().width() == 100


def test_切换工具改变画布交互方式():
    editor = make_editor()
    editor.place_number(QPointF(20, 20))
    editor.set_tool(TOOL_RECT)
    assert editor.view.dragMode().name == "NoDrag"
    item = editor.scene.annotation_items()[0]
    assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton
    editor.set_tool(TOOL_SELECT)
    assert editor.view.dragMode().name == "RubberBandDrag"
    assert item.acceptedMouseButtons() == Qt.MouseButton.LeftButton


def test_选中图元可改颜色和粗细():
    editor = make_editor()
    shape = ShapeItem(TOOL_RECT, QPointF(5, 5), QPointF(60, 40), "#ff0000", 3)
    editor.scene.addItem(shape)
    editor.commit_item(shape)
    shape.setSelected(True)
    editor.set_color("#00ff00")
    editor.set_width(10)
    assert shape.color.name() == "#00ff00"
    assert shape.width == 10


def test_选中文字可改颜色和字号():
    editor = make_editor()
    item = editor.place_text(QPointF(10, 10))
    item.setPlainText("文字")
    editor.commit_text()
    item.setSelected(True)
    editor.set_color("#00ff00")
    editor.set_width(10)
    assert item.defaultTextColor().name() == "#00ff00"
    assert item.font().pixelSize() > 22


def test_复制写剪贴板并只结束一次():
    editor = make_editor()
    copied = QSignalSpy(editor.copied)
    finished = QSignalSpy(editor.finished)
    closed = QSignalSpy(editor.closed)
    assert editor.copy_to_clipboard() is True
    assert copied.count() == 1
    assert finished.count() == 1
    assert closed.count() == 1
    image = QApplication.clipboard().image()
    assert (image.width(), image.height()) == (200, 120)


def test_保存PNG发出saved信号(tmp_path):
    editor = make_editor()
    saved = QSignalSpy(editor.saved)
    target = tmp_path / "shot.png"
    assert editor.save_to_file(str(target)) is True
    assert target.exists()
    assert saved.count() == 1
    assert QImage(str(target)).width() == 200


def test_贴图发出pinned信号():
    editor = make_editor()
    pinned = QSignalSpy(editor.pinned)
    assert editor.pin_to_screen() is True
    assert pinned.count() == 1


def test_OCR使用注入函数并显示结果(monkeypatch):
    editor = make_editor(ocr_enabled=True, ocr_func=lambda image: "识别出的文字")
    captured: list[str] = []

    class FakeDialog:
        def __init__(self, text, parent=None):
            captured.append(text)

        def exec(self):
            return 1

    monkeypatch.setattr(editor_module, "OCRResultDialog", FakeDialog)
    assert editor.run_ocr() is True
    assert captured == ["识别出的文字"]


def test_双击空白复制():
    editor = make_editor()
    copied = QSignalSpy(editor.copied)
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    event = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(10, 10),
                        QPointF(10, 10), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    editor.view.mouseDoubleClickEvent(event)
    assert copied.count() == 1


def test_Esc关闭不发复制信号():
    editor = make_editor()
    copied = QSignalSpy(editor.copied)
    finished = QSignalSpy(editor.finished)
    editor.cancel()
    assert copied.count() == 0
    assert finished.count() == 1
