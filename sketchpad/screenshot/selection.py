"""选区几何：8 向手柄、缩放、移动、钳制、微调。

全部在“场景坐标”（冻结截图的物理像素）里算，不依赖任何窗口；
手柄命中/光标由 overlay 把逻辑像素换算成场景单位再调用这里。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt

HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
CORNER_HANDLES = ("nw", "ne", "se", "sw")
MIN_SIZE = 8.0


@dataclass
class SelectionModel:
    bounds: QRectF
    rect: QRectF | None = None
    min_size: float = MIN_SIZE

    # ---------- 基本读写 ----------

    def has_selection(self) -> bool:
        return self.rect is not None and not self.rect.isEmpty()

    def set_rect(self, rect: QRectF | None) -> bool:
        """设置选区；太小/为空返回 False（保持原选区）。"""
        if rect is None:
            self.rect = None
            return True
        normalized = QRectF(rect).normalized()
        clipped = normalized.intersected(self.bounds)
        if clipped.width() < self.min_size or clipped.height() < self.min_size:
            return False
        self.rect = clipped
        return True

    def clear(self) -> None:
        self.rect = None

    def current(self) -> QRectF | None:
        return QRectF(self.rect) if self.rect is not None else None

    # ---------- 手柄 ----------

    def handle_points(self) -> dict[str, QPointF]:
        if self.rect is None:
            return {}
        r = self.rect
        cx, cy = r.center().x(), r.center().y()
        return {
            "nw": QPointF(r.left(), r.top()),
            "n": QPointF(cx, r.top()),
            "ne": QPointF(r.right(), r.top()),
            "e": QPointF(r.right(), cy),
            "se": QPointF(r.right(), r.bottom()),
            "s": QPointF(cx, r.bottom()),
            "sw": QPointF(r.left(), r.bottom()),
            "w": QPointF(r.left(), cy),
        }

    def hit_handle(self, point: QPointF, radius: float) -> str | None:
        """最近的手柄，且距离不超过 radius（场景单位）。"""
        if self.rect is None:
            return None
        best: str | None = None
        best_distance = float("inf")
        for name, handle in self.handle_points().items():
            distance = ((handle.x() - point.x()) ** 2
                        + (handle.y() - point.y()) ** 2) ** 0.5
            if distance <= radius and distance < best_distance:
                best, best_distance = name, distance
        return best

    def resize(self, handle: str, point: QPointF) -> QRectF | None:
        """按手柄拖到 point；不翻转、不越过对边、钳在 bounds 内。"""
        if self.rect is None:
            return None
        r = QRectF(self.rect)
        left, right = r.left(), r.right()
        top, bottom = r.top(), r.bottom()
        x = min(max(point.x(), self.bounds.left()), self.bounds.right())
        y = min(max(point.y(), self.bounds.top()), self.bounds.bottom())

        if "w" in handle:
            left = min(max(x, self.bounds.left()), right - self.min_size)
        if "e" in handle:
            right = max(min(x, self.bounds.right()), left + self.min_size)
        if "n" in handle:
            top = min(max(y, self.bounds.top()), bottom - self.min_size)
        if "s" in handle:
            bottom = max(min(y, self.bounds.bottom()), top + self.min_size)

        self.rect = QRectF(QPointF(left, top), QPointF(right, bottom))
        return QRectF(self.rect)

    # ---------- 移动 ----------

    def move_by(self, dx: float, dy: float) -> QRectF | None:
        if self.rect is None:
            return None
        r = QRectF(self.rect)
        dx = max(self.bounds.left() - r.left(),
                 min(dx, self.bounds.right() - r.right()))
        dy = max(self.bounds.top() - r.top(),
                 min(dy, self.bounds.bottom() - r.bottom()))
        r.translate(dx, dy)
        self.rect = r
        return QRectF(r)

    def nudge(self, dx: float, dy: float) -> QRectF | None:
        return self.move_by(dx, dy)

    def contains(self, point: QPointF) -> bool:
        return self.rect is not None and self.rect.contains(point)

    # ---------- 光标 ----------

    @staticmethod
    def cursor_for_handle(handle: str | None) -> Qt.CursorShape:
        if handle in ("nw", "se"):
            return Qt.CursorShape.SizeFDiagCursor
        if handle in ("ne", "sw"):
            return Qt.CursorShape.SizeBDiagCursor
        if handle in ("n", "s"):
            return Qt.CursorShape.SizeVerCursor
        if handle in ("e", "w"):
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.ArrowCursor
