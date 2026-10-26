"""画布视图：QGraphicsView + 一条笔迹一个 PathItem。视图层，依赖 Qt。

坐标换算一律走 sketchpad/core/camera.py 的纯函数，视图不自己写缩放算术；Qt 的变换与
camera.world_to_screen 必须逐值对上（tests/test_canvas_offscreen.py 第 1 条）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QKeySequence, QMouseEvent, QPainter,
                           QPainterPath, QPen, QPixmap, QShortcut, QTabletEvent,
                           QTransform)
from PySide6.QtWidgets import (QFrame, QGraphicsPathItem, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsTextItem, QGraphicsView)

from sketchpad.core import camera
from sketchpad.core.commands import AddStroke, AddText, ClearAll, EraseStrokes
from sketchpad.core.history import History
from sketchpad.core.smoothing import smooth_points
from sketchpad.core.wintab import debug_log, pen_pressure, reset_pressure
from sketchpad.core.settings import (DEFAULT_CLEAR_KEY, DEFAULT_COLOR, HIGHLIGHTER_COLOR,
                                     INK_SMOOTHING_LEVELS, RESERVED_KEYS, WIDTHS, Settings)
from sketchpad.ui.toolbox import PALETTE
from sketchpad.core.strokes import (HIGHLIGHTER_OPACITY, HIGHLIGHTER_WIDTH,
                         MAX_POINTS_PER_STROKE, Document, Point, Stroke, TextBlock,
                         Viewport, smooth_with_pressures, stroke_distance, thin,
                         thin_with_pressures)
from sketchpad.ui.cursor import (CURSOR_RADIUS_PX, CURSOR_RESTORE_MS,
                                 ERASER_HALO_COLOR, ERASER_RADIUS_PX,
                                 ERASER_RING_COLOR, WRITING_CURSOR_RADIUS_PX)
from sketchpad.ui.inkitem import InkItem, draw_ink_stroke

CANVAS_BG = "#202124"   # 深灰底色（参考 Lorien）；笔色默认取浅色保证对比（设计文档 R1/§7）
MIN_STROKE_POINT_DIST = 0.1   # 抬笔抽稀阈值（世界像素）：远小于 1 屏幕像素，保留手写细节


class _ScreenPoint(tuple):
    """屏幕点：(x, y) 浮点元组，另带 x()/y()。

    Qt 6 的 QGraphicsView.mapFromScene(QPointF) 返回整数 QPoint，亚像素被截断；
    相机约定 screen = world * scale + origin 需要浮点结果，故由视图覆写该方法。
    元组继承让 pytest.approx 能直接比较（QPointF 与 approx 互相判不等）。
    """

    __slots__ = ()

    def x(self) -> float:
        return self[0]

    def y(self) -> float:
        return self[1]


def _stroke_path(points: list[Point]) -> QPainterPath:
    """逐点直连的折线路径；单点笔迹用零点线段 + 圆头画成实心圆点。

    采样点已是真实手写轨迹，直连折线最忠实；圆头/圆角由笔尖负责。
    抬笔后的插值平滑（Catmull-Rom）实测字形不如直连、且明显更卡，已回退。
    """
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0][0], points[0][1])
    if len(points) == 1:
        # Qt 不画零点线段；挪 0.001 世界像素，圆头 + 圆角即实心圆点（设计文档 §9）
        path.lineTo(points[0][0] + 0.001, points[0][1])
        return path
    for x, y in points[1:]:
        path.lineTo(x, y)
    return path


def _make_pen(color: str, width: float, scale: float, opacity: float = 1.0) -> QPen:
    """线宽按世界单位；width*scale 不足 1 屏幕像素时钳到 1（设计文档 §7）。

    `opacity<1` 用 QColor 的 alpha 实现（荧光笔 0.35，不遮住底下的字）；
    默认 1.0，既有调用方行为逐值不变。
    """
    pen_color = QColor(color)
    if opacity < 1.0:
        pen_color.setAlpha(round(255 * max(0.0, opacity)))
    pen = QPen(pen_color)
    pen.setWidthF(max(width, 1.0 / scale))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _stroke_pen(stroke: Stroke, scale: float) -> QPen:
    """按笔迹自己的工具取笔：荧光笔半透明、线宽照存；其它工具完全不透明。"""
    opacity = HIGHLIGHTER_OPACITY if stroke.tool == "highlighter" else 1.0
    return _make_pen(stroke.color, stroke.width, scale, opacity)


class SketchScene(QGraphicsScene):
    """模型 + item 投影。item.data(0) 存对应的 Stroke，撤销/重建时能找回。"""

    def __init__(self, document: Document, parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self._scale = document.viewport.scale
        # 草稿背景图：独立于笔迹模型，撤销/清屏笔迹时保留题目截图
        self.images: list[tuple[QImage, QRectF]] = []
        self._image_items: list[QGraphicsPixmapItem] = []
        self.setBackgroundBrush(QColor(CANVAS_BG))
        for block in document.texts:
            self.add_text_block(block)
        for stroke in document.strokes:
            self.add_stroke(stroke)

    def item_for(self, stroke: Stroke) -> QGraphicsPathItem | None:
        for item in self.items():
            if item.data(0) is stroke:
                return item
        return None

    def add_stroke(self, stroke: Stroke) -> QGraphicsPathItem:
        # InkItem 仍是 QGraphicsPathItem：无压感走描边，有压感在 paint 里画变宽 ribbon
        item = InkItem(stroke, self._scale)
        self.addItem(item)
        return item

    def remove_stroke(self, stroke: Stroke) -> None:
        item = self.item_for(stroke)
        if item is not None:
            self.removeItem(item)

    def add_image(self, image: QImage, rect: QRectF) -> QGraphicsPixmapItem | None:
        if image is None or image.isNull() or rect.isEmpty():
            return None
        self.images.append((QImage(image), QRectF(rect)))
        return self._project_image(self.images[-1][0], self.images[-1][1])

    def _project_image(self, image: QImage, rect: QRectF) -> QGraphicsPixmapItem:
        item = QGraphicsPixmapItem(QPixmap.fromImage(image))
        item.setTransform(QTransform.fromScale(
            rect.width() / max(1, image.width()),
            rect.height() / max(1, image.height())))
        item.setPos(rect.topLeft())
        item.setZValue(-100)                 # 永远在笔迹下面
        item.setAcceptedMouseButtons(Qt.NoButton)   # 落笔穿透，不抢画布事件
        self.addItem(item)
        self._image_items.append(item)
        return item

    def clear_images(self) -> None:
        for item in list(self._image_items):
            try:
                self.removeItem(item)
            except RuntimeError:
                pass
        self._image_items.clear()
        self.images.clear()

    def add_text_block(self, block: TextBlock) -> QGraphicsTextItem:
        """把模型里的一段文字投影成只读 item（编辑走视图的临时编辑态）。"""
        item = QGraphicsTextItem(block.text)
        item.setDefaultTextColor(QColor(block.color))
        item.setFont(QFont(self._text_font_family(), int(block.size)))
        item.setPos(QPointF(*block.pos))
        item.setData(0, block)
        item.setAcceptedMouseButtons(Qt.NoButton)   # 点击穿透：编辑由视图统一接管
        self.addItem(item)
        return item

    @staticmethod
    def _text_font_family() -> str:
        return "Microsoft YaHei UI"

    def apply_scale(self, scale: float) -> None:
        """缩放变了就重算各 item 的世界线宽（保证缩小后仍看得见）。"""
        self._scale = scale
        for item in self.items():
            stroke = item.data(0)
            if isinstance(stroke, Stroke):
                item.setPen(_stroke_pen(stroke, scale))

    def rebind(self) -> None:
        """按 document.strokes 全量重建 item。

        ClearAll.unapply、EraseStrokes.unapply 只改模型，投影必须整体对齐；
        模型是唯一真相，所以这里不尝试增量修补。
        """
        self.clear()
        self._image_items.clear()
        for image, rect in self.images:
            self._project_image(image, rect)
        for block in self.document.texts:
            self.add_text_block(block)
        for stroke in self.document.strokes:
            self.add_stroke(stroke)


class _NotifyingHistory(History):
    """撤销/重做后回调视图重建 item；栈里放的仍是 commands.py 的原命令对象。"""

    def __init__(self, limit: int, on_change) -> None:
        super().__init__(limit)
        self._on_change = on_change

    def pop_undo(self):
        cmd = super().pop_undo()
        if cmd is not None:
            self._on_change()
        return cmd

    def pop_redo(self):
        cmd = super().pop_redo()
        if cmd is not None:
            self._on_change()
        return cmd


class SketchView(QGraphicsView):
    """画布：滚轮平移/缩放、落笔、橡皮、清屏。"""

    changed = Signal()

    def __init__(self, scene: SketchScene, settings: Settings | None = None) -> None:
        super().__init__(scene)
        # 属性名沿用计划的 scene；QGraphicsView.scene() 只在 C++ 内部使用
        self.scene = scene
        self.document = scene.document
        self.tool = "pen"
        self.color = DEFAULT_COLOR
        self.highlighter_color = HIGHLIGHTER_COLOR
        self.width = WIDTHS[1]
        self.clear_key = DEFAULT_CLEAR_KEY
        self.ink_smoothing = "low"
        self.history = _NotifyingHistory(100, self._after_history_change)
        self._drawing = False
        self._points: list[Point] = []
        self._erasing = False
        self._erased: list[Stroke] = []
        self._erased_items: list[QGraphicsPathItem] = []
        self._candidates: list[tuple[Stroke, QGraphicsPathItem]] = []
        self._pen_scale: float | None = None
        self._clear_shortcut: QShortcut | None = None
        self._panning = False
        self._pan_last: QPointF | None = None
        self._space_down = False
        self._text_editing: QGraphicsTextItem | None = None   # T 工具的临时编辑态
        self._cursor_pos: QPointF | None = None
        # 落笔时收紧笔尖圆点；停笔 3 秒才恢复大光标（方便定位又不挡字）
        self._cursor_compact = False
        self._cursor_restore_timer = QTimer(self)
        self._cursor_restore_timer.setSingleShot(True)
        self._cursor_restore_timer.setInterval(CURSOR_RESTORE_MS)
        self._cursor_restore_timer.timeout.connect(self._restore_cursor_size)
        # 压感：仅钢笔；_pressures 与 _points 等长，鼠标输入保持空列表
        self.pressure_enabled = (getattr(settings, "pressure_enabled", True)
                                 if settings is not None else True)
        self._pressures: list[float] = []
        self._tablet_stroke = False

        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setFocusPolicy(Qt.StrongFocus)
        # 悬停（无按键）也要收到 mouseMove，否则笔尖圆点不跟随（R8）
        self.viewport().setMouseTracking(True)
        # 先在画布内显示普通箭头；收到第一次鼠标移动后再切成笔尖圆点（R8）
        self.viewport().setCursor(Qt.ArrowCursor)

        if settings is None:
            self.set_clear_key(DEFAULT_CLEAR_KEY)
        else:
            self.set_color(settings.color)
            self.set_highlighter_color(
                getattr(settings, "highlighter_color", HIGHLIGHTER_COLOR))
            self.set_width(WIDTHS[max(0, min(len(WIDTHS) - 1, settings.width_index))])
            self.set_clear_key(settings.clear_key)
            self.set_ink_smoothing(getattr(settings, "ink_smoothing", "low"))
        self._apply_view()

    # --- 对外状态 ---

    @property
    def tool_color(self) -> str:
        """当前工具落笔用的颜色：荧光笔用它自己那份，其余用画笔色。"""
        return self.highlighter_color if self.tool == "highlighter" else self.color

    def set_highlighter_color(self, color: str) -> None:
        """换荧光笔颜色；不影响画笔色（两份颜色分开存）。"""
        if color == self.highlighter_color:
            return
        self.highlighter_color = color
        if self.tool == "highlighter" and self._cursor_pos is not None:
            self.viewport().update()
        self.changed.emit()

    def viewport_scale(self) -> float:
        return self.document.viewport.scale

    def viewport_origin(self) -> Point:
        return tuple(self.document.viewport.origin)

    def screen_to_world(self, p: QPointF) -> Point:
        return camera.screen_to_world(self.document.viewport, (p.x(), p.y()))

    def pan_screen(self, dx: float, dy: float) -> None:
        if not self._viewport_alive():
            return                      # 窗口尺寸 0：跳过变换计算（设计文档 §9）
        self.document.viewport = camera.pan_by(self.document.viewport, dx, dy)
        self._apply_view()
        self.changed.emit()

    def zoom_at_screen(self, sx: float, sy: float, factor: float) -> None:
        if not self._viewport_alive():
            return
        self.document.viewport = camera.zoom_at(self.document.viewport, (sx, sy), factor)
        self._apply_view()
        self.changed.emit()

    def _base_cursor(self):
        """各工具的静止光标：select=手形、text=I 形、画笔类=笔尖圆点。

        画笔类只有在鼠标真正进过画布（``_cursor_pos`` 已记录）后才隐藏系统
        指针；刚打开窗口、还没收到第一次 move 时先显示普通箭头，避免“鼠标消失”。
        """
        if self.tool == "select":
            return Qt.OpenHandCursor
        if self.tool == "text":
            return Qt.IBeamCursor
        if self.tool in ("pen", "eraser", "highlighter"):
            return (Qt.ArrowCursor if self._cursor_pos is None
                    else Qt.BlankCursor)
        return Qt.BlankCursor

    def set_tool(self, tool: str) -> None:
        """切工具：变了才广播 changed，工具栏高亮与状态栏靠它跟上（键盘 B/Q）。"""
        if tool == self.tool:
            return
        if self.tool == "text" or tool == "text":
            self._commit_text_edit()
        self.tool = tool
        self._cursor_restore_timer.stop()
        self._cursor_compact = False
        if not self._panning:
            self.viewport().setCursor(self._base_cursor())
        self.changed.emit()

    def set_color(self, color: str) -> None:
        """换色：变了才广播，状态栏的当前色不必等下一条笔迹。"""
        if color == self.color:
            return
        self.color = color
        self.changed.emit()

    def set_pressure_enabled(self, enabled: bool) -> None:
        """压感开关只影响下一次落笔；进行中的笔迹保持原样。"""
        self.pressure_enabled = bool(enabled)

    def set_ink_smoothing(self, level: str) -> None:
        """抬笔平滑强度；未知值回低强度，off 时保留原始折线。"""
        self.ink_smoothing = level if level in INK_SMOOTHING_LEVELS else "low"

    def set_width(self, width: float) -> None:
        """换粗细：变了才广播；键盘 1-4 也要把工具栏第 N 档点亮。"""
        width = float(width)
        if width != self.width:
            self.width = width
            self.changed.emit()
        if self._cursor_pos is not None:
            self.viewport().update()

    def dot_radius_px(self) -> float:
        """笔尖圆点的屏幕像素半径：所有画笔粗细统一 6px（12px 直径）。

        实际落墨宽度仍按当前粗细/压感走；光标只负责“看得见笔尖”。
        橡皮保留更大的 12px 半径圆环。
        """
        if self.tool == "eraser":
            return ERASER_RADIUS_PX
        if self.tool == "pen" and (self._drawing or self._cursor_compact):
            return WRITING_CURSOR_RADIUS_PX
        return CURSOR_RADIUS_PX

    def _compact_cursor_for_writing(self) -> None:
        if self.tool != "pen":
            return
        self._cursor_restore_timer.stop()
        if not self._cursor_compact:
            self._cursor_compact = True
            self.viewport().update()

    def _schedule_cursor_restore(self) -> None:
        if self.tool != "pen":
            return
        self._cursor_restore_timer.start()

    def _restore_cursor_size(self) -> None:
        if self._cursor_compact:
            self._cursor_compact = False
            self.viewport().update()

    def reset_zoom(self) -> None:
        """`0`：缩放到 100%，并把笔迹包围盒中心放到视口中心（设计文档 §6.1）。"""
        vw, vh = self.viewport().width(), self.viewport().height()
        if vw <= 0 or vh <= 0:
            return
        xs = [p[0] for s in self.document.strokes for p in s.points]
        ys = [p[1] for s in self.document.strokes for p in s.points]
        cx = (min(xs) + max(xs)) / 2.0 if xs else 0.0
        cy = (min(ys) + max(ys)) / 2.0 if ys else 0.0
        self.document.viewport = Viewport(1.0, (vw / 2.0 - cx, vh / 2.0 - cy))
        self._apply_view()
        self.changed.emit()

    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        if key == Qt.Key_Escape and self._text_editing is not None:
            self._cancel_text_edit()
            ev.accept()
            return
        if self._text_editing is not None:
            # 文字编辑中所有键（包括 B/Q/1-9）都归 QGraphicsTextItem；
            # 否则一敲 B 就被视图切成画笔，只留下第一个字符（2026-10-24 报错）
            super().keyPressEvent(ev)
            return
        if key == Qt.Key_Space and not ev.isAutoRepeat():
            self._space_down = True
            self.viewport().setCursor(Qt.OpenHandCursor)
            ev.accept()
            return
        if key == Qt.Key_0:
            self.reset_zoom()
            ev.accept()
            return
        if key == Qt.Key_B:
            self.set_tool("pen")
            ev.accept()
            return
        if key == Qt.Key_Q:
            self.set_tool("eraser")
            ev.accept()
            return
        if Qt.Key_1 <= key <= Qt.Key_9:
            index = key - Qt.Key_1
            if index < len(PALETTE):
                self.set_color(PALETTE[index])   # 1–9 = 色板第 1–9 色
                ev.accept()
                return
        if key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            # 方向键与滚轮同方向：Down = 内容向上（往文档下方看），一格 10% 视口
            w = self.viewport().width() * camera.WHEEL_PAN_RATIO
            h = self.viewport().height() * camera.WHEEL_PAN_RATIO
            dx = -w if key == Qt.Key_Right else (w if key == Qt.Key_Left else 0.0)
            dy = -h if key == Qt.Key_Down else (h if key == Qt.Key_Up else 0.0)
            self.pan_screen(dx, dy)
            ev.accept()
            return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Space and not ev.isAutoRepeat():
            self._space_down = False
            self.viewport().setCursor(Qt.BlankCursor)
            ev.accept()
            return
        super().keyReleaseEvent(ev)

    def enterEvent(self, ev) -> None:
        if not self._panning:
            self.viewport().setCursor(self._base_cursor())
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._cursor_pos = None                  # 移出画布：不画圆点，恢复箭头
        if not self._panning:
            self.viewport().setCursor(Qt.ArrowCursor)
        self.viewport().update()
        super().leaveEvent(ev)

    def set_clear_key(self, key: str) -> None:
        """重建清屏快捷键；挂在视图上，焦点在子控件里时不触发（设计文档 §6.1）。"""
        key = (key or DEFAULT_CLEAR_KEY).strip() or DEFAULT_CLEAR_KEY
        if key.lower() in {k.lower() for k in RESERVED_KEYS}:
            key = DEFAULT_CLEAR_KEY    # 与平移/复位/工具键冲突：清屏键优先，这些组合直接拒收
        sequence = QKeySequence(key)
        if sequence.isEmpty():
            key = DEFAULT_CLEAR_KEY
            sequence = QKeySequence(key)
        old = self._clear_shortcut
        if old is not None:
            old.setEnabled(False)
            old.setParent(None)        # 立刻从 findChildren 里消失
            old.deleteLater()
        shortcut = QShortcut(sequence, self)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.clear_all)
        self.clear_key = key
        self._clear_shortcut = shortcut

    def set_clear_shortcut_enabled(self, enabled: bool) -> None:
        """捕获新清屏键期间临时停用当前清屏键：否则它先触发，把画布清空。"""
        if self._clear_shortcut is not None:
            self._clear_shortcut.setEnabled(enabled)

    def insert_image(self, image: QImage, scene_pos: QPointF | None = None) -> bool:
        """把截图/图片作为背景放进草稿；只缩小不放大，居中放进当前视口。"""
        if image is None or image.isNull():
            return False
        viewport = self.viewport()
        scale = max(1e-6, float(self.document.viewport.scale))
        max_w = max(80.0, viewport.width() * 0.85 / scale)
        max_h = max(80.0, viewport.height() * 0.85 / scale)
        ratio = min(1.0, max_w / image.width(), max_h / image.height())
        width, height = image.width() * ratio, image.height() * ratio
        if scene_pos is None:
            scene_pos = self.mapToScene(viewport.rect().center())
        rect = QRectF(scene_pos.x() - width / 2.0, scene_pos.y() - height / 2.0,
                      width, height)
        if self.scene.add_image(image, rect) is None:
            return False
        self.changed.emit()
        # 把视口挪到图片中心：粘贴发生在窗口刚恢复/画布滚动过时，图片也一定可见
        self.centerOn(scene_pos)
        return True

    def clear_images(self) -> None:
        self.scene.clear_images()
        self.changed.emit()

    @property
    def image_count(self) -> int:
        return len(self.scene.images)

    def clear_all(self) -> None:
        self._drawing = False
        self._points = []
        self._erasing = False
        self._erased = []
        self._erased_items = []
        self._candidates = []
        cmd = ClearAll(self.document)
        cmd.apply()
        self.history.push(cmd)
        self.scene.rebind()
        self.changed.emit()

    def after_history_change(self) -> None:
        """撤销/重做后同步投影；History 子类已自动调用，留给接线层兜底。"""
        self._after_history_change()

    # --- 变换 ---

    def _viewport_alive(self) -> bool:
        return self.viewport().width() > 0 and self.viewport().height() > 0

    def _after_history_change(self) -> None:
        # 撤销/重做会打断进行中的手势：否则橡皮在 rebind 后会把同一批笔迹再删一次，
        # 甚至压入一个空命令把 redo 分支清掉
        self._drawing = False
        self._points = []
        self._erasing = False
        self._erased = []
        self._erased_items = []
        self._candidates = []
        self._panning = False
        self._pan_last = None
        self.scene.rebind()
        self.changed.emit()

    def _apply_view(self) -> None:
        vp = self.document.viewport
        if vp.scale != self._pen_scale:
            self._pen_scale = vp.scale
            self.scene.apply_scale(vp.scale)
        self.setTransform(QTransform().translate(vp.origin[0], vp.origin[1])
                          .scale(vp.scale, vp.scale))
        self._sync_scene_rect()

    def _sync_scene_rect(self) -> None:
        """让场景矩形正好等于视口对应的世界矩形：滚动偏移恒为 0，屏幕 = 世界*scale+origin。"""
        if not self._viewport_alive():
            return
        vp = self.document.viewport
        inv = 1.0 / vp.scale
        self.setSceneRect(QRectF(-vp.origin[0] * inv, -vp.origin[1] * inv,
                                 self.viewport().width() * inv,
                                 self.viewport().height() * inv))

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if getattr(self, "document", None) is not None and self._viewport_alive():
            self._sync_scene_rect()

    def mapFromScene(self, point) -> _ScreenPoint:
        """返回亚像素屏幕坐标；Qt6 原生只给整数 QPoint，会破坏 1e-6 不变量。"""
        mapped = self.viewportTransform().map(QPointF(point))
        return _ScreenPoint((mapped.x(), mapped.y()))

    # --- 输入 ---

    def wheelEvent(self, ev) -> None:
        steps = ev.angleDelta().y() / 120.0
        if steps == 0.0:
            pixel = ev.pixelDelta()
            if not pixel.isNull():     # 触控板：按像素位移直接平移
                self.pan_screen(float(pixel.x()), float(pixel.y()))
                ev.accept()
                return
            ev.ignore()
            return
        if ev.modifiers() & Qt.ControlModifier:
            self.zoom_at_screen(ev.position().x(), ev.position().y(),
                                camera.WHEEL_ZOOM_STEP ** steps)
        elif ev.modifiers() & Qt.ShiftModifier:
            # 标准滚动方向：滚轮向上（正 delta）= 内容向下/向右移
            self.pan_screen(steps * self.viewport().width() * camera.WHEEL_PAN_RATIO, 0.0)
        else:
            self.pan_screen(0.0, steps * self.viewport().height() * camera.WHEEL_PAN_RATIO)
        ev.accept()

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        # 文字工具：先提交上一段，再在落点开新编辑（或双击命中已有块再编辑）
        if self.tool == "text" and ev.button() == Qt.LeftButton:
            self._commit_text_edit()
            self._begin_text_edit(QPointF(self.mapToScene(ev.position().toPoint())))
            ev.accept()
            return
        # 平移：中键拖动、按住空格 + 左键，或鼠标工具直接左键拖（2026-10-24）
        if (ev.button() == Qt.MiddleButton or self.tool == "select"
                or (ev.button() == Qt.LeftButton and self._space_down)):
            self._panning = True
            self._pan_last = ev.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            ev.accept()
            return
        # 起笔只认左键按下；悬停（无按键 move）永远不会走到这里
        if ev.button() == Qt.LeftButton and self.tool in ("pen", "eraser", "highlighter"):
            self._begin(ev.position(), self._mouse_pressure())
            # 橡皮按下的第一下命中也得立刻重绘：不能等第一个 move 才看到笔迹消失
            self.viewport().update()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        self._cursor_pos = ev.position()          # 悬停也更新，圆点跟着走（R8）
        if (self.tool in ("pen", "eraser", "highlighter") and not self._panning
                and self.viewport().cursor().shape() != Qt.BlankCursor):
            self.viewport().setCursor(Qt.BlankCursor)
        if self._panning and self._pan_last is not None:
            delta = ev.position() - self._pan_last
            self._pan_last = ev.position()
            self.pan_screen(delta.x(), delta.y())
            ev.accept()
            return
        if (ev.buttons() & Qt.LeftButton) and (self._drawing or self._erasing):
            self._advance(ev.position(), self._mouse_pressure())
            self.viewport().update()   # 光标圆点/圆环跟着走；橡皮分支原来漏了这句
            ev.accept()
            return
        self.viewport().update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:
        if self._panning and ev.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._panning = False
            self._pan_last = None
            self.viewport().setCursor(
                Qt.OpenHandCursor if (self._space_down or self.tool == "select")
                else self._base_cursor())
            # 画到一半被中键平移打断：左键抬起时仍要收笔，否则预览会一直挂在屏上
            if ev.button() == Qt.LeftButton and (self._drawing or self._erasing):
                self._end(ev.position(), self._mouse_pressure())
                reset_pressure()
            ev.accept()
            return
        if ev.button() == Qt.LeftButton and (self._drawing or self._erasing):
            self._end(ev.position(), self._mouse_pressure())
            reset_pressure()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def tabletEvent(self, ev: QTabletEvent) -> None:
        """数位板：钢笔采压力，荧光笔/橡皮恒定宽度；鼠标路径完全不变。"""
        kind = ev.type()
        if self._space_down or self._panning:
            ev.ignore()                 # 平移手势优先：空格/中键期间笔不落笔
            return
        pressure = self._tablet_pressure(ev)
        if kind == QEvent.TabletPress and (ev.buttons() & Qt.LeftButton):
            self._begin(ev.position(), pressure)
            self.viewport().update()
            ev.accept()
            return
        if (kind == QEvent.TabletMove and (ev.buttons() & Qt.LeftButton)
                and (self._drawing or self._erasing)):
            self._advance(ev.position(), pressure)
            self.viewport().update()
            ev.accept()
            return
        if kind == QEvent.TabletRelease and (self._drawing or self._erasing):
            self._end(ev.position(), pressure)
            ev.accept()
            return
        ev.ignore()                     # 悬空靠近（Proximity）不落笔

    def _tablet_pressure(self, ev: QTabletEvent) -> float | None:
        """只在钢笔 + 开关打开时返回压力；其它工具返回 None = 恒定宽度。"""
        if not self.pressure_enabled or self.tool != "pen":
            return None
        value = float(ev.pressure())
        if not value > 0.0:
            value = 0.05                # 某些笔按下第一帧报 0，别让线细到看不见
        return min(1.0, max(0.0, value))

    def _mouse_pressure(self) -> float | None:
        """鼠标路径下的压力：钢笔 + 开关打开时，直连 WinTab 取笔压。

        Qt6 在部分国产板上把笔识别成普通鼠标，收不到 QTabletEvent（探针实测
        0 条），所以实际落笔走的是这里；压力改从驱动挂出的 WinTab 服务直取。
        取不到就是 None = 恒定宽度，与没有压感时的旧行为一致。
        """
        debug_log(f"canvas._mouse_pressure tool={self.tool} "
                  f"enabled={self.pressure_enabled}")
        if not self.pressure_enabled or self.tool != "pen":
            return None
        try:
            value = pen_pressure(self._wintab_hwnd())
        except Exception as exc:
            debug_log(f"  pen_pressure 异常: {exc!r}")
            value = None
        debug_log(f"  -> pen_pressure={value}")
        if value is None:
            return None
        if not value > 0.0:
            value = 0.05
        return min(1.0, max(0.0, value))

    def _wintab_hwnd(self) -> int:
        """WinTab context 绑顶层窗口；不给画布强建原生 HWND。

        每次都重新取 winId()：Qt 在换屏 / DPI 变化 / 部分重绘时会重建原生窗口，
        winId() 随之改变；若缓存旧 HWND，context 会一直绑在失效句柄上。
        """
        try:
            return int(self.window().winId())
        except Exception:
            return 0

    def drawForeground(self, painter: QPainter, rect) -> None:
        """未固化笔迹的实时预览 + 笔尖圆点（设计文档 §7 / R8）。"""
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._drawing and self._points:
            width = HIGHLIGHTER_WIDTH if self.tool == "highlighter" else self.width
            if self._pressures and len(self._pressures) == len(self._points):
                preview = Stroke(list(self._points), self.tool_color, width,
                                 self.tool, list(self._pressures))
                draw_ink_stroke(painter, preview)
            else:
                opacity = HIGHLIGHTER_OPACITY if self.tool == "highlighter" else 1.0
                painter.setPen(_make_pen(self.tool_color, width,
                                         self.document.viewport.scale, opacity))
                painter.drawPath(_stroke_path(self._points))
        if (self._cursor_pos is not None and self._viewport_alive()
                and self.tool in ("pen", "eraser", "highlighter")):
            scale = self.document.viewport.scale
            inv, ok = self.viewportTransform().inverted()
            center = inv.map(self._cursor_pos) if ok else QPointF(0.0, 0.0)
            radius = self.dot_radius_px() / scale
            if self.tool == "eraser":
                # 橡皮：白色光晕 + 红色轮廓；白页/深色页/浅色笔迹上都不丢目标
                halo = QPen(QColor(ERASER_HALO_COLOR))
                halo.setWidthF(4.0 / scale)
                painter.setPen(halo)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(center, radius, radius)
                ring = QPen(QColor(ERASER_RING_COLOR))
                ring.setWidthF(2.0 / scale)
                painter.setPen(ring)
            else:
                fill = QColor(self.tool_color)
                fill.setAlpha(204)               # 80%：不遮住落点
                painter.setPen(Qt.NoPen)
                painter.setBrush(fill)
            painter.drawEllipse(center, radius, radius)
        painter.restore()

    # --- 文字工具（T）：落点输入 / 提交 / 取消 ---

    TEXT_FONT_SIZE = 16.0      # 世界像素；随画布缩放，与笔迹同一坐标系

    def _begin_text_edit(self, scene_pos: QPointF) -> None:
        """在落点放一个可编辑的临时 item；提交/取消才进模型。"""
        item = QGraphicsTextItem()
        self.scene.addItem(item)             # 所有权交给场景，引用自己持有
        item.setPos(scene_pos)
        item.setDefaultTextColor(QColor(self.color))
        item.setFont(QFont(self.scene._text_font_family(), int(self.TEXT_FONT_SIZE)))
        item.setPlainText("")
        item.setTextInteractionFlags(Qt.TextEditorInteraction)
        item.setZValue(10)                   # 文字浮在笔迹上面（笔迹 Z=0）
        item.setFocus(Qt.MouseFocusReason)
        item_document = item.document()
        item_document.setDocumentMargin(0.0)
        self._text_editing = item

    def _commit_text_edit(self) -> None:
        """编辑收尾：非空 → AddText 进模型与撤销栈；空 → 直接丢弃。"""
        item, self._text_editing = self._text_editing, None
        if item is None:
            return
        try:
            text = item.toPlainText().strip("\n\r")
            if not text:
                self.scene.removeItem(item)
                return
            block = TextBlock((item.pos().x(), item.pos().y()), text,
                              self.color, self.TEXT_FONT_SIZE)
            command = AddText(self.document, block)
            command.apply()
            self.history.push(command)
            self.scene.removeItem(item)     # 投影换模型版（颜色/字体统一）
            self.scene.add_text_block(block)
            self.changed.emit()
        except RuntimeError:
            pass

    def _cancel_text_edit(self) -> None:
        """Esc：丢弃编辑中的内容。"""
        item, self._text_editing = self._text_editing, None
        if item is not None:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass

    # --- 落笔 / 橡皮 ---

    def _world(self, pos: QPointF) -> Point:
        return camera.screen_to_world(self.document.viewport, (pos.x(), pos.y()))

    def _begin(self, pos: QPointF, pressure: float | None = None) -> None:
        if self._erased:
            # 上次擦除没收尾（抬笔事件丢失）：先把隐藏的 item 恢复，否则这些笔迹会
            # 永久停在"看不到、还在文档里、没有撤销记录"的状态
            self._erased = []
            self._erased_items = []
            self._candidates = []
            self._erasing = False
            self.scene.rebind()
        if self.tool == "eraser":
            self._erasing = True
            # 候选只在按下时取一次：拖动过程里不再遍历整个文档，也不重建任何 item
            self._candidates = [(item.data(0), item) for item in self.scene.items()
                                if isinstance(item.data(0), Stroke)]
            self._erased_items = []
            self._erase_at(pos)
            return
        self._compact_cursor_for_writing()
        self._drawing = True
        self._points = [self._world(pos)]
        self._tablet_stroke = pressure is not None
        self._pressures = [pressure] if pressure is not None else []
        self.viewport().update()

    def _advance(self, pos: QPointF, pressure: float | None = None) -> None:
        if self._drawing:
            self._append(self._world(pos), pressure)
        elif self._erasing:
            self._erase_at(pos)

    def _end(self, pos: QPointF, pressure: float | None = None) -> None:
        if self._drawing:
            self._finish_stroke(pos, pressure)
        else:
            self._finish_erase()

    def _append(self, p: Point, pressure: float | None = None) -> None:
        if len(self._points) >= MAX_POINTS_PER_STROKE:
            return                      # 到上限停手，抬笔仍正常成条（设计文档 §5）
        if self._points and p == self._points[-1]:
            return
        self._points.append(p)
        if self._tablet_stroke:
            self._pressures.append(pressure if pressure is not None else 0.5)
        self.viewport().update()

    def _finish_stroke(self, pos: QPointF, pressure: float | None = None) -> None:
        points = self._points
        pressures = self._pressures or None
        self._points = []
        self._pressures = []
        self._drawing = False
        self._schedule_cursor_restore()
        if len(points) < MAX_POINTS_PER_STROKE:
            end = self._world(pos)
            if end != points[-1]:
                points.append(end)
                if pressures is not None:
                    pressures.append(pressure if pressure is not None else 0.5)
        if pressures is not None and len(pressures) == len(points):
            points, pressures = thin_with_pressures(
                points, pressures, MIN_STROKE_POINT_DIST)
            points, pressures = smooth_with_pressures(
                points, pressures, self.ink_smoothing)
        else:
            pressures = None
            points = thin(points, MIN_STROKE_POINT_DIST)
            points = smooth_points(points, self.ink_smoothing)
        if points:
            width = HIGHLIGHTER_WIDTH if self.tool == "highlighter" else self.width
            stroke = Stroke(points, self.tool_color, width, self.tool, pressures)
            cmd = AddStroke(self.document, stroke)
            cmd.apply()
            self.history.push(cmd)
            self.scene.add_stroke(stroke)
            self.changed.emit()
        self.viewport().update()

    def _erase_at(self, pos: QPointF) -> None:
        tol = max(self.width, 6.0) / 2.0       # 命中半径（设计文档 §6.1）
        world = self._world(pos)
        point = QPointF(*world)
        remaining: list[tuple[Stroke, QGraphicsPathItem]] = []
        for stroke, item in self._candidates:
            # 先用 item 的世界包围盒粗筛（O(1)），命中才逐点算距离——拖动不再卡
            if (item.sceneBoundingRect().adjusted(-tol, -tol, tol, tol).contains(point)
                    and stroke_distance(stroke, world) <= tol):
                self._erased.append(stroke)
                self._erased_items.append(item)
                item.setVisible(False)
            else:
                remaining.append((stroke, item))
        self._candidates = remaining

    def _finish_erase(self) -> None:
        hits = self._erased
        items = self._erased_items
        self._erased = []
        self._erased_items = []
        self._candidates = []
        self._erasing = False
        if not hits:
            return                      # 一次划过删掉的所有条 = 1 个撤销单位
        cmd = EraseStrokes(self.document, hits)
        cmd.apply()
        self.history.push(cmd)
        # 增量删除命中的 item：不 rebind，画布上其它笔迹的 item 一个都不重建
        for item in items:
            self.scene.removeItem(item)
        self.changed.emit()
