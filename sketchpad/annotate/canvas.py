"""全屏透明批注画布：只有钢笔与橡皮，颜色和线宽由工具栏给。

为什么双缓冲：透明层若每帧重绘所有笔迹，1080p 连写时会掉帧。已固化的
笔迹画进一张与控件同尺寸的 QImage，`paintEvent` 只贴这张图 + 重绘制中的
一笔 + 笔尖圆点；拖动时再按脏矩形裁剪，实际重绘面积只有笔尖附近一小块。

为什么只认左键按下：GAOMON 数位板悬空靠近时也会发 mouseMove，若"收到
move 就画"会画出鬼线。拾笔只认左键，悬停只移动圆点。

坐标口径：`document` 里的点就是控件坐标（= 主屏屏幕坐标），没有缩放/平移。
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from sketchpad.annotate.constants import (DEFAULT_COLOR, PALETTE, PEN_WIDTH,
                                  PEN_WIDTH_MAX as MAX_PEN_WIDTH,
                                  PEN_WIDTH_MIN as MIN_PEN_WIDTH)
from sketchpad.annotate.core.commands import AddStroke, ClearAll, EraseStrokes
from sketchpad.annotate.core.history import History
from sketchpad.annotate.core.smoothing import (INK_SMOOTHING_LEVELS,
                                               smooth_points)
from sketchpad.annotate.core.strokes import (Document, MAX_POINTS_PER_STROKE,
                                     MIN_POINT_DIST, Stroke, stroke_distance,
                                     thin)

TOOLS = ("pen", "eraser")
SMOOTHING = "low"
ERASER_TOLERANCE = 12.0        # 橡皮命中容差（屏幕像素）
ERASER_RING_RADIUS = 14.0      # 橡皮光标圆环半径（放大后更醒目）
ERASER_RING_COLOR = "#ff453a"  # 橡皮轮廓：红色
ERASER_HALO_COLOR = "#ffffff"  # 轮廓外白色光晕，深色/花背景也能看见
CURSOR_SMALL_RADIUS = 3.0      # 书写/移动时的小圆点（直径 6，不挡落笔）
CURSOR_LARGE_RADIUS = 10.0     # 空闲时的大圆点（直径 20，容易看见）
CURSOR_IDLE_MS = 3000          # 停笔后 3 秒才恢复大光标（用户要求）
CURSOR_ALPHA = 204             # 笔尖圆点 80% 不透明：看得见又不遮住落点
CACHE_MARGIN = 2.0             # 缓存局部重绘时多清 2px，盖住抗锯齿边缘

Point = tuple[float, float]


def _stroke_path(points: list[Point]) -> QPainterPath:
    """逐点直连；单点用 0.001 长的线段 + 圆头画成实心圆点（Qt 不画零点线段）。"""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0][0], points[0][1])
    if len(points) == 1:
        path.lineTo(points[0][0] + 0.001, points[0][1])
        return path
    for x, y in points[1:]:
        path.lineTo(x, y)
    return path


def _stroke_rect(stroke: Stroke) -> QRectF:
    """笔迹的屏幕包围盒（含半线宽），橡皮粗筛与缓存局部重绘共用。"""
    points = stroke.points
    if not points:
        return QRectF()
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    pad = max(float(stroke.width), 1.0) / 2.0 + CACHE_MARGIN
    return QRectF(min(xs) - pad, min(ys) - pad,
                  max(xs) - min(xs) + 2.0 * pad, max(ys) - min(ys) + 2.0 * pad)


class Canvas(QWidget):
    """透明批注层。尺寸由 overlay 决定（全屏），这里只负责画和响应输入。"""

    changed = Signal()
    escapePressed = Signal()
    saveRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = Document()
        self.history = History()
        self.tool = "pen"
        self.color = DEFAULT_COLOR
        # 线宽用 pen_width 这个名字：叫 width 会遮蔽 QWidget.width()，布局会崩
        self.pen_width = float(PEN_WIDTH)
        # 全屏批注沿用主设置的平滑档位；之前这里写死 low，设置成超高也不生效
        self.ink_smoothing = SMOOTHING
        # 落笔瞬间锁定的颜色 / 线宽：画到一半换色换粗，不影响这一笔
        self._stroke_color = DEFAULT_COLOR
        self._stroke_width = float(PEN_WIDTH)

        # 透明窗口关键设置：不刷系统底色，只画笔迹
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)       # 悬停也要收到 move，圆点才跟手
        self.setCursor(Qt.BlankCursor)    # 画布内不显示系统箭头
        self.setFocusPolicy(Qt.StrongFocus)

        # 双缓冲：与控件同尺寸（按 DPR 放大）的已固化笔迹层
        self._cache: QImage | None = None

        self._drawing = False
        self._points: list[Point] = []

        self._erasing = False
        self._erased: list[Stroke] = []                    # 本次划过已命中的
        self._candidates: list[tuple[Stroke, QRectF]] = []  # 按下时取一次快照
        self._hidden: set[int] = set()                     # id(stroke)，缓存里跳过

        self._cursor: QPointF | None = None
        self._cursor_large = True
        self._cursor_idle_timer = QTimer(self)
        self._cursor_idle_timer.setSingleShot(True)
        self._cursor_idle_timer.setInterval(CURSOR_IDLE_MS)
        self._cursor_idle_timer.timeout.connect(self._on_cursor_idle)

    # --- 对外状态 ---

    def set_tool(self, tool: str) -> None:
        if tool not in TOOLS or tool == self.tool:
            return
        old_rect = self._cursor_rect(self._cursor) if self._cursor is not None else None
        self.tool = tool
        if old_rect is not None and self._cursor is not None:
            self.update(old_rect.toAlignedRect())
            self.update(self._cursor_rect(self._cursor).toAlignedRect())
        self.changed.emit()

    def set_color(self, color: str) -> None:
        """换笔色；非法值保持原色，避免坏配置把笔迹画成透明。"""
        if not color or color == self.color:
            return
        if not QColor(color).isValid():
            return
        self.color = color
        if self.tool == "pen" and self._cursor is not None:
            self.update(self._cursor_rect(self._cursor).toAlignedRect())
        self.changed.emit()

    def set_pen_width(self, value: float) -> None:
        """换线宽（屏幕像素）；超出上下限的值夹住，坏配置也画不出离谱的笔。"""
        try:
            width = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(width):
            return
        width = max(MIN_PEN_WIDTH, min(MAX_PEN_WIDTH, width))
        if abs(width - self.pen_width) < 1e-6:
            return
        # 旧光标按旧半径算包围盒，改完再按新半径算一次，两次都重绘
        old_rect = self._cursor_rect(self._cursor) if self._cursor is not None else None
        self.pen_width = width
        if old_rect is not None and self._cursor is not None:
            self.update(old_rect.toAlignedRect())
            self.update(self._cursor_rect(self._cursor).toAlignedRect())
        self.changed.emit()

    def set_ink_smoothing(self, level: str) -> None:
        """同步主设置里的抬笔平滑档位；未知值回低档。"""
        self.ink_smoothing = (level if level in INK_SMOOTHING_LEVELS
                              else SMOOTHING)

    def has_ink(self) -> bool:
        return bool(self.document.strokes)

    def abort_gesture(self) -> None:
        """外部兜底：鼠标早松开了，但手势还挂着（捕获丢了 / release 事件丢了）。

        按正常抬笔收尾，不丢已在画的那一笔，也不留半截状态。
        """
        if self._drawing:
            end = QPointF(*self._points[-1]) if self._points else QPointF()
            self._finish_stroke(end)
        elif self._erasing:
            self._finish_erase()
        self._release_grab()

    def _release_grab(self) -> None:
        """只在真的握着鼠标时释放，避免 Qt 打 "No mouse grab" 警告。"""
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
    # --- 撤销 / 清屏 ---

    def undo(self) -> None:
        if not self.history.can_undo:
            return
        # 撤销会打断进行中的手势；橡皮还没提交的"隐藏"必须先恢复
        self._reset_gesture()
        self.history.pop_undo()
        self._invalidate_cache()
        self.changed.emit()

    def clear_all(self) -> None:
        """清屏 = 一个 ClearAll 撤销单位；本来就空则不入栈（免得撤销键假装可用）。"""
        self._reset_gesture()
        if not self.document.strokes:
            self.update()
            return
        cmd = ClearAll(self.document)
        cmd.apply()
        self.history.push(cmd)
        self._invalidate_cache()
        self.changed.emit()

    def render_png(self, path: str | Path) -> bool:
        """导出只有笔迹的透明 PNG；笔迹为空时也导出全透明图（保底留存方式）。"""
        dpr = self._dpr()
        widget_width, widget_height = self._logical_size()
        width = int(round(widget_width * dpr))
        height = int(round(widget_height * dpr))
        if width <= 0 or height <= 0:
            return False
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for stroke in self.document.strokes:
            if id(stroke) not in self._hidden:
                self._draw_stroke(painter, stroke)
        painter.end()
        try:
            return bool(image.save(str(path), "PNG"))
        except (TypeError, ValueError):
            return False

    # --- Qt 事件 ---

    def _logical_size(self) -> tuple[float, float]:
        size = self.size()
        return float(size.width()), float(size.height())

    def resizeEvent(self, ev) -> None:
        # 缓存尺寸必须跟控件一致；resize 时重建，下一次 paintEvent 重绘全部笔迹
        self._cache = None
        super().resizeEvent(ev)

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 逐像素透明的分层窗口按 alpha 做命中测试：alpha=0 的像素会把鼠标放过去。
        # 铺一层 alpha=1 的黑（视觉上不可察觉），否则 draw 模式点空白处也会穿透，
        # 第一笔永远落不下来。
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        self._ensure_cache()
        if self._cache is not None and not self._cache.isNull():
            painter.drawImage(0, 0, self._cache)
        if self._drawing and len(self._points) >= 1:
            self._draw_preview(painter)
        self._draw_cursor(painter)

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            super().mousePressEvent(ev)
            return
        self.setFocus(Qt.MouseFocusReason)   # 点一下画布，键盘快捷键就该归画布
        pos = ev.position()
        self._cursor = pos
        self._mark_cursor_active()
        if self.tool == "eraser":
            self._begin_erase(pos)
        else:
            self._begin_stroke(pos)
        self.update(self._cursor_rect(pos).toAlignedRect())
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        old = self._cursor
        # 悬停移动不改圆点大小、也不重置计时：数位板悬停会连续发 move，
        # 若在这里 _mark_cursor_active，计时器永远被重置，空闲大点就永远不出现。
        self._cursor = pos
        # 圆点跟着走：旧位置和新位置都要重画（悬停不落笔，这就是全部动作）
        if old is not None:
            self.update(self._cursor_rect(old).toAlignedRect())
        self.update(self._cursor_rect(pos).toAlignedRect())

        pressed = bool(ev.buttons() & Qt.LeftButton)
        if self._drawing and pressed:
            last = QPointF(*self._points[-1])
            if self._append(pos):
                self.update(self._segment_rect(last, pos).toAlignedRect())
        elif self._erasing and pressed:
            # 命中立即从缓存里抹掉：不能等抬笔才"整条消失"
            self._erase_at(pos)
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            super().mouseReleaseEvent(ev)
            return
        pos = ev.position()
        self._cursor = pos
        if self._drawing:
            self._finish_stroke(pos)
        elif self._erasing:
            self._finish_erase()
        self._mark_cursor_active()           # 抬笔后重新等 3s 再放大
        # 显式放掉隐式捕获：不依赖 Qt 内部时机，数位板下 release 偶尔会迟到
        self._release_grab()
        self.update(self._cursor_rect(pos).toAlignedRect())
        ev.accept()

    def leaveEvent(self, ev) -> None:
        # 鼠标离开画布（去点工具栏）时收起圆点；手势进行中则保持
        if self._cursor is not None and not (self._drawing or self._erasing):
            self.update(self._cursor_rect(self._cursor).toAlignedRect())
            self._cursor = None
            self._cursor_idle_timer.stop()
        super().leaveEvent(ev)

    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        mods = ev.modifiers()
        if mods & Qt.ControlModifier:
            if key == Qt.Key_Z:
                self.undo()
            elif key == Qt.Key_S:
                self.saveRequested.emit()
            else:
                ev.ignore()           # Ctrl+Y 等已删除的组合不再有动作
                return
            ev.accept()
            return
        if mods & (Qt.AltModifier | Qt.MetaModifier):
            ev.ignore()
            return
        if key == Qt.Key_Escape:
            self.escapePressed.emit()
        elif Qt.Key_1 <= key <= Qt.Key_6:
            self.set_color(PALETTE[key - Qt.Key_1])
        elif key == Qt.Key_B:
            self.set_tool("pen")
        elif key == Qt.Key_Q:
            self.set_tool("eraser")
        elif key == Qt.Key_E:
            self.clear_all()
        else:
            ev.ignore()
            return
        ev.accept()

    # --- 落笔 ---

    def _begin_stroke(self, pos: QPointF) -> None:
        self._reset_gesture()             # 上一次抬手丢失时别把橡皮状态带进来
        self._ensure_cache()
        self._drawing = True
        self._stroke_color = self.color
        self._stroke_width = self.pen_width
        self._points = [(pos.x(), pos.y())]

    def _append(self, pos: QPointF) -> bool:
        """收集原始点；超出上限就停手（抬笔仍正常成条）。"""
        if not self._points or len(self._points) >= MAX_POINTS_PER_STROKE:
            return False
        point = (pos.x(), pos.y())
        if point == self._points[-1]:
            return False
        self._points.append(point)
        return True

    def _finish_stroke(self, pos: QPointF) -> None:
        points = self._points
        self._points = []
        self._drawing = False
        if points and len(points) < MAX_POINTS_PER_STROKE:
            end = (pos.x(), pos.y())
            if end != points[-1]:
                points.append(end)
        points = thin(points, MIN_POINT_DIST)
        points = smooth_points(points, self.ink_smoothing)
        if not points:
            self.update()
            return

        stroke = Stroke(points, self._stroke_color, self._stroke_width, "pen")
        # 先确保缓存存在（缓存缺失时 _ensure_cache 会重绘整张文档），
        # 再 AddStroke，避免同一笔在缓存里画两遍
        self._ensure_cache()
        cmd = AddStroke(self.document, stroke)
        cmd.apply()
        self.history.push(cmd)

        if self._cache is not None:
            painter = QPainter(self._cache)
            painter.setRenderHint(QPainter.Antialiasing, True)
            self._draw_stroke(painter, stroke)
            painter.end()
        # 固化后只重绘这一笔的包围盒，不清整屏
        self.update(_stroke_rect(stroke).toAlignedRect())
        self.changed.emit()

    # --- 橡皮（整条擦除）---

    def _begin_erase(self, pos: QPointF) -> None:
        self._reset_gesture()
        self._ensure_cache()
        self._erasing = True
        # 候选只在按下时快照一次：拖动过程不遍历文档里的新笔迹，也不重建图元
        self._candidates = [(s, _stroke_rect(s)) for s in self.document.strokes]
        self._erase_at(pos)

    def _erase_at(self, pos: QPointF) -> None:
        point = (pos.x(), pos.y())
        remaining: list[tuple[Stroke, QRectF]] = []
        for stroke, rect in self._candidates:
            # 先用包围盒粗筛（O(1)），命中才逐段算距离——快速划动不卡
            if not rect.adjusted(-ERASER_TOLERANCE, -ERASER_TOLERANCE,
                                 ERASER_TOLERANCE, ERASER_TOLERANCE).contains(
                                     QPointF(point[0], point[1])):
                remaining.append((stroke, rect))
                continue
            if stroke_distance(stroke, point) <= ERASER_TOLERANCE:
                self._erased.append(stroke)
                self._hidden.add(id(stroke))
                self._erase_region_from_cache(rect)
            else:
                remaining.append((stroke, rect))
        self._candidates = remaining

    def _finish_erase(self) -> None:
        hits = self._erased
        self._erased = []
        self._candidates = []
        self._hidden.clear()
        self._erasing = False
        if not hits:
            return                    # 什么都没擦到：不入栈，不留空撤销步
        # 一次划过删掉的所有条 = 1 个撤销单位
        cmd = EraseStrokes(self.document, hits)
        cmd.apply()
        self.history.push(cmd)
        self.changed.emit()

    def _erase_region_from_cache(self, rect: QRectF) -> None:
        """把命中笔迹的包围盒从缓存里清掉，再补画相交的其它笔迹。

        只处理这一小块而不是重建整张缓存：连续擦多条时也能保持跟手。
        """
        cache = self._cache
        if cache is None or cache.isNull():
            self.update(rect.toAlignedRect())
            return
        region = rect.adjusted(-CACHE_MARGIN, -CACHE_MARGIN,
                               CACHE_MARGIN, CACHE_MARGIN)
        widget_width, widget_height = self._logical_size()
        region = region.intersected(QRectF(0.0, 0.0, widget_width, widget_height))
        if region.isEmpty():
            return
        painter = QPainter(cache)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(region, Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(region)
        for stroke in self.document.strokes:
            if id(stroke) in self._hidden:
                continue
            if _stroke_rect(stroke).intersects(region):
                self._draw_stroke(painter, stroke)
        painter.end()
        self.update(region.toAlignedRect())

    # --- 绘制 ---

    def _dpr(self) -> float:
        dpr = float(self.devicePixelRatioF())
        if not math.isfinite(dpr) or dpr <= 0.0:
            return 1.0
        return dpr

    def _ensure_cache(self) -> None:
        """缓存缺失、尺寸或 DPR 变化时重建，并重绘 document 全部笔迹。"""
        dpr = self._dpr()
        widget_width, widget_height = self._logical_size()
        width = max(1, int(round(widget_width * dpr)))
        height = max(1, int(round(widget_height * dpr)))
        cache = self._cache
        if (cache is not None and cache.width() == width
                and cache.height() == height
                and abs(cache.devicePixelRatio() - dpr) < 1e-4):
            return
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)       # QPainter 会按 DPR 自动放大逻辑坐标
        image.fill(Qt.transparent)
        if widget_width > 0 and widget_height > 0:
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, True)
            for stroke in self.document.strokes:
                if id(stroke) not in self._hidden:
                    self._draw_stroke(painter, stroke)
            painter.end()
        self._cache = image

    def _invalidate_cache(self) -> None:
        self._cache = None
        self.update()

    def _draw_stroke(self, painter: QPainter, stroke: Stroke) -> None:
        """固定宽度钢笔：圆头圆角，颜色来自笔迹自身。"""
        if not stroke.points:
            return
        color = QColor(stroke.color)
        if not color.isValid():
            color = QColor(DEFAULT_COLOR)
        color.setAlpha(255)
        pen = QPen(color)
        pen.setWidthF(max(float(stroke.width), 1.0))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(_stroke_path(stroke.points))
        painter.restore()

    def _draw_preview(self, painter: QPainter) -> None:
        """正在画的一笔：颜色和线宽都用落笔时锁定的值。"""
        color = QColor(self._stroke_color)
        pen = QPen(color)
        pen.setWidthF(float(self._stroke_width))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(_stroke_path(self._points))
        painter.restore()

    def _draw_cursor(self, painter: QPainter) -> None:
        if self._cursor is None:
            return
        radius = self._cursor_radius()
        painter.save()
        if self.tool == "eraser":
            halo = QPen(QColor(ERASER_HALO_COLOR))
            halo.setWidthF(4.0)
            painter.setPen(halo)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self._cursor, radius, radius)
            ring = QPen(QColor(ERASER_RING_COLOR))
            ring.setWidthF(2.0)
            painter.setPen(ring)
        else:
            color = QColor(self._stroke_color if self._drawing else self.color)
            color.setAlpha(CURSOR_ALPHA)     # 80%：看得见落点，又不遮住底下
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
        painter.drawEllipse(self._cursor, radius, radius)
        painter.restore()

    # --- 几何小工具 ---

    def _cursor_radius(self) -> float:
        if self.tool == "eraser":
            return ERASER_RING_RADIUS
        if self._drawing:
            return CURSOR_SMALL_RADIUS
        return CURSOR_LARGE_RADIUS if self._cursor_large else CURSOR_SMALL_RADIUS

    def _mark_cursor_active(self) -> None:
        """鼠标在动/正在写：切小圆点，并重新开始空闲计时。"""
        self._set_cursor_large(False)
        self._cursor_idle_timer.start()

    def _on_cursor_idle(self) -> None:
        if self._drawing or self._erasing:
            self._cursor_idle_timer.start()   # 手势没结束就继续等
            return
        self._set_cursor_large(True)

    def _set_cursor_large(self, large: bool) -> None:
        if self._cursor_large == large:
            return
        self._cursor_large = large
        if self._cursor is None:
            return
        # 用最大半径刷一次，旧的大圆点/小圆点都能清干净
        radius = max(CURSOR_LARGE_RADIUS, CURSOR_SMALL_RADIUS,
                     ERASER_RING_RADIUS) + CACHE_MARGIN
        point = self._cursor
        self.update(QRectF(point.x() - radius, point.y() - radius,
                           radius * 2.0, radius * 2.0).toAlignedRect())

    def _cursor_rect(self, pos: QPointF) -> QRectF:
        radius = self._cursor_radius() + CACHE_MARGIN
        return QRectF(pos.x() - radius, pos.y() - radius,
                      radius * 2.0, radius * 2.0)

    def _segment_rect(self, start: QPointF, end: QPointF) -> QRectF:
        """拖动时只重绘"上一段"这条窄带，外加半线宽与抗锯齿余量。"""
        pad = float(self._stroke_width) / 2.0 + CACHE_MARGIN
        left = min(start.x(), end.x()) - pad
        top = min(start.y(), end.y()) - pad
        return QRectF(left, top,
                      abs(end.x() - start.x()) + 2.0 * pad,
                      abs(end.y() - start.y()) + 2.0 * pad)

    def _reset_gesture(self) -> None:
        """丢弃进行中的手势（撤销/清屏/新落笔前调用）；隐藏的笔迹回缓存。"""
        had_hidden = bool(self._hidden)
        self._drawing = False
        self._points = []
        self._erasing = False
        self._erased = []
        self._candidates = []
        self._hidden.clear()
        if had_hidden:
            self._invalidate_cache()
