"""选区几何：手柄缩放、移动、钳制、最小尺寸、方向键。"""
from PySide6.QtCore import QPointF, QRectF, Qt

from sketchpad.screenshot.selection import HANDLES, SelectionModel

BOUNDS = QRectF(0, 0, 400, 300)


def make(rect=None) -> SelectionModel:
    selection = SelectionModel(bounds=BOUNDS)
    if rect is not None:
        assert selection.set_rect(rect)
    return selection


def test_设置选区会归一化并钳在屏幕内():
    selection = make(QRectF(300, 200, -200, -100))
    assert selection.rect == QRectF(100, 100, 200, 100)


def test_太小或越界返回False且保留原选区():
    selection = make(QRectF(10, 10, 100, 100))
    assert selection.set_rect(QRectF(20, 20, 3, 3)) is False
    assert selection.rect == QRectF(10, 10, 100, 100)
    assert selection.set_rect(QRectF(500, 500, 50, 50)) is False


def test_八个手柄位置():
    selection = make(QRectF(100, 50, 200, 100))
    points = selection.handle_points()
    assert set(points) == set(HANDLES)
    assert points["nw"] == QPointF(100, 50)
    assert points["se"] == QPointF(300, 150)
    assert points["n"] == QPointF(200, 50)
    assert points["w"] == QPointF(100, 100)


def test_手柄命中有半径限制且取最近():
    selection = make(QRectF(100, 50, 200, 100))
    assert selection.hit_handle(QPointF(102, 52), 8) == "nw"
    assert selection.hit_handle(QPointF(300, 150), 8) == "se"
    assert selection.hit_handle(QPointF(200, 50), 8) == "n"
    assert selection.hit_handle(QPointF(150, 80), 8) is None
    assert selection.hit_handle(QPointF(200, 150), 8) == "s"


def test_四角缩放():
    selection = make(QRectF(100, 50, 200, 100))
    assert selection.resize("nw", QPointF(80, 40)) == QRectF(80, 40, 220, 110)
    assert selection.resize("se", QPointF(330, 180)) == QRectF(80, 40, 250, 140)
    assert selection.resize("ne", QPointF(360, 20)) == QRectF(80, 20, 280, 160)
    assert selection.resize("sw", QPointF(60, 220)) == QRectF(60, 20, 300, 200)


def test_四边缩放只改一边():
    selection = make(QRectF(100, 50, 200, 100))
    assert selection.resize("n", QPointF(150, 20)) == QRectF(100, 20, 200, 130)
    assert selection.resize("s", QPointF(200, 220)) == QRectF(100, 20, 200, 200)
    assert selection.resize("w", QPointF(40, 100)) == QRectF(40, 20, 260, 200)
    assert selection.resize("e", QPointF(380, 100)) == QRectF(40, 20, 340, 200)


def test_缩放到最小尺寸不会翻转():
    right_edge = make(QRectF(100, 50, 200, 100))
    rect = right_edge.resize("e", QPointF(90, 100))
    assert rect.width() == right_edge.min_size
    assert rect.left() == 100

    left_edge = make(QRectF(100, 50, 200, 100))
    rect = left_edge.resize("w", QPointF(350, 100))
    assert rect.width() == left_edge.min_size
    assert rect.right() == 300


def test_缩放钳在屏幕内():
    selection = make(QRectF(100, 50, 200, 100))
    assert selection.resize("nw", QPointF(-50, -50)) == QRectF(0, 0, 300, 150)
    assert selection.resize("se", QPointF(500, 400)) == QRectF(0, 0, 400, 300)


def test_移动整体且不出屏幕():
    selection = make(QRectF(100, 50, 200, 100))
    assert selection.move_by(30, 40) == QRectF(130, 90, 200, 100)
    assert selection.move_by(500, 500) == QRectF(200, 200, 200, 100)
    assert selection.move_by(-500, -500) == QRectF(0, 0, 200, 100)


def test_方向键微调与contains():
    selection = make(QRectF(100, 50, 200, 100))
    assert selection.nudge(1, -1) == QRectF(101, 49, 200, 100)
    assert selection.contains(QPointF(150, 100)) is True
    assert selection.contains(QPointF(20, 20)) is False


def test_光标映射():
    assert SelectionModel.cursor_for_handle("nw") == Qt.CursorShape.SizeFDiagCursor
    assert SelectionModel.cursor_for_handle("se") == Qt.CursorShape.SizeFDiagCursor
    assert SelectionModel.cursor_for_handle("ne") == Qt.CursorShape.SizeBDiagCursor
    assert SelectionModel.cursor_for_handle("n") == Qt.CursorShape.SizeVerCursor
    assert SelectionModel.cursor_for_handle("w") == Qt.CursorShape.SizeHorCursor
    assert SelectionModel.cursor_for_handle(None) == Qt.CursorShape.ArrowCursor


def test_清空():
    selection = make(QRectF(10, 10, 100, 100))
    selection.clear()
    assert selection.has_selection() is False
    assert selection.current() is None
