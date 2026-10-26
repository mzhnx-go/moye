"""PDF 文档视图：连续纵向页面，位图底图 + 笔迹位图缓存 + 尾部矢量笔迹。场景单位 = PDF 点。

笔迹点一律存**页面局部坐标（PDF 点，显示/页框坐标：旋转页就是 `page.rect`
那一套）**：缩放只动视图变换，不动数据，导出与 sidecar 因此与缩放无关
（设计文档 §4.2；旋转口径见 PROGRESS §4.1，导出时才用 `derotation_matrix`
换回未旋转坐标）。底图按当前缩放渲染，视口外的页可以暂时没有位图——滚动到位
再补（§4.3）。打开即按视口宽铺满当前页；搜索高亮是页面上的矩形项，只显示、
不接收鼠标。

**跨页拖笔（v2.5）**：一条笔迹只属于它起笔的那一页。绘制过程中页外的点一律丢弃
（页边留 `PAGE_TOLERANCE` 容差），抬笔时把终点钳回起始页矩形；橡皮同样只作用于
起始页，页外的移动直接忽略。因此预览、提交的笔迹、导出裁剪范围永远在同一页内。

**笔迹渲染分两层**（v2.2 性能，实测见 docs/verification/perf-pdf-2026-10-20.md）：
- 尾部 STROKE_TAIL 条最新笔迹保持矢量项：落笔预览、橡皮实时反馈、撤销粒度都在这一层；
- 更早的笔迹烘进每页一张的透明位图缓存（当前缩放渲染一次，之后每帧只是一张 blit）。
实测 500 条 × 100 点：500 个矢量项 123ms/帧；改成位图缓存后 1.6ms/帧。设计文档 §7
原定的"每 200 条合并成一个 QGraphicsPathItem"实测反而更慢（1658ms——一条 5 万段
的巨路径会拖垮光栅化且失去逐项裁剪），故改用位图缓存，验收线不变。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import (QBuffer, QByteArray, QEvent, QIODevice, QLineF,
                            QPoint, QPointF, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QImage,
                           QPainter, QPainterPath, QPen, QPixmap,
                           QTabletEvent, QTextCharFormat, QTextCursor, QTransform)
from PySide6.QtWidgets import (QFrame, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsTextItem,
                               QGraphicsView, QInputDialog, QMenu)

from sketchpad.core import noteassets, pdfstore
from sketchpad.core.camera import WHEEL_ZOOM_STEP
from sketchpad.core.history import History
from sketchpad.core.pdfcommands import (AddStrokeToPage, ClearPage,
                                        EraseStrokesFromPage, ReplaceMany,
                                        ReplacePageItems)
from sketchpad.pdf.document import PdfError, PdfSource
from sketchpad.pdf.pagemode import PAGE_MODES, apply_page_mode
from sketchpad.pdf import richtext
from sketchpad.core.pdfstore import Page, PdfDocument
from sketchpad.core.notes import (Note, NoteImage, TextBlock, clamp_note_anchor,
                                  note_display_name)
from sketchpad.core.settings import HIGHLIGHTER_COLOR, INK_SMOOTHING_LEVELS, WIDTHS, Settings
from sketchpad.ui.toolbox import PALETTE
from sketchpad.core.smoothing import smooth_points
from sketchpad.core.wintab import debug_log, pen_pressure, reset_pressure
from sketchpad.core.strokes import (HIGHLIGHTER_OPACITY, HIGHLIGHTER_WIDTH,
                                    MAX_POINTS_PER_STROKE, TEXT_HIGHLIGHT, Point, Stroke,
                                    has_pressure, is_highlight, smooth_with_pressures,
                                    stroke_distance, text_highlight_rects, thin,
                                    thin_with_pressures)
from sketchpad.ui.cursor import (CURSOR_RADIUS_PX, CURSOR_RESTORE_MS,
                                 ERASER_HALO_COLOR, ERASER_RADIUS_PX,
                                 ERASER_RING_COLOR, WRITING_CURSOR_RADIUS_PX)
from sketchpad.ui.inkitem import InkItem, draw_ink_stroke
from sketchpad.ui.note_pin import NotePin

PAGE_GAP = 8.0                  # 页间纵向间隙（PDF 点）
MIN_ZOOM = 0.1
MAX_ZOOM = 8.0
RENDER_MARGIN = 200.0           # 视口外多渲一圈（PDF 点），滚动时少一次白页
RENDER_DELAY_MS = 40            # 缩放/滚动重渲的去抖间隔
STROKE_TAIL = 16                # 尾部保留的矢量笔迹条数（其余烘进位图缓存）
CACHE_MAX_ZOOM = 4.0            # 笔迹位图缓存的渲染倍率上限：A4 4× ≈ 32MB、8× ≈ 128MB；
                                # 更高倍率把 4× 缓存放大显示（略软），不再分配更大图
MAX_RENDER_SCALE = 8.0          # 页面底图实际像素倍率上限（zoom×DPR 后再封顶，防 200% 屏+8× 爆内存）
SUPERSAMPLE = 2.0               # 低倍率下的超采样倍数：先按 2× 渲染再平滑缩回，字更实
SUPERSAMPLE_MAX_ZOOM = 3.5      # 缩放 ≤ 350% 都开超采样（更高倍率本身像素已够密）
SYNC_RENDER_MAX_SCALE = 8.0     # 缩放后同步重渲的上限：覆盖全部超采样区间，不为清晰度妥协
KEEP_PAGE_RANGE = 1             # 回收时额外保留 current_page ± N 页（翻页/滚动回来不闪白）
PAGE_TOLERANCE = 1.0            # 跨页判定容差（PDF 点）：页边 1pt 内的点仍算本页
MIN_STROKE_POINT_DIST = 0.1     # 抬笔抽稀阈值（PDF 点）：远小于 1 屏幕像素，几乎不丢采样点
DRAG_THRESHOLD_PX = 4.0         # 视口像素：按下后移动超过它才算"拖动"（图钉移动判定）
UNITS_CACHE_PAGES = 8           # 字符表按页缓存的上限（悬停/选字都会问它）
VIEW_BG = "#202124"             # 与草稿画布同一深灰底
HIT_FILL = QColor(255, 214, 0, 70)        # 搜索命中：半透明黄
HIT_CURRENT_FILL = QColor(255, 152, 0, 170)   # 当前命中：更浓的橙黄
HIT_Z = 0.5                     # 高亮压在页面底图之上，不接收鼠标
SELECT_FILL = QColor(51, 144, 255, 90)    # 选中文字：半透明蓝（与搜索黄区分）
SELECT_Z = 0.6                  # 选择高亮压住搜索命中，仍不接收鼠标
STROKE_Z_HIGHLIGHTER = 1.0      # 荧光笔矢量项：压在页面/缓存（z=0、0.5、0.6）之上
STROKE_Z_PEN = 2.0              # 画笔矢量项：压在荧光笔之上，保证墨迹不被盖住
IMAGE_Z = 0.7                   # 页面图片：压在底图/缓存上、文字与笔迹下面
TEXT_Z = 0.8                    # 页面文字：压在图片上、笔迹下面
TEXT_EDIT_Z = 3.0               # 正在输入的文字：临时浮到笔迹上面
SELECT_BOX_Z = 20.0             # 页面对象选中框（不参与导出）
SELECT_HANDLE_Z = 21.0
SELECT_COLOR = QColor("#0a84ff")
HANDLE_SIZE = 8.0               # 手柄屏幕像素（ItemIgnoresTransformations）
HANDLE_MODES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
TEXT_FONT_FAMILY = "Microsoft YaHei UI"
DEFAULT_PAGE_TEXT_SIZE = 16.0
TEXT_SIZE_PRESETS = (12, 16, 20, 24, 32)
TEXT_FORMAT_KEYS = {
    Qt.Key_B: "bold",
    Qt.Key_I: "italic",
    Qt.Key_U: "underline",
}
SELECT_UNIT = tuple[str, float, float, float, float, int, int]


def _join_words(words: list[SELECT_UNIT]) -> str:
    """把按阅读顺序选中的字符拼回文本：英文词内不插空格，跨词一个空格，跨行换行。

    选择单位是**单个字符**（中文才能一个字一个字选）；英文词间的空格不返回，
    这里用行高的一小部分做阈值把词间空隙补回空格，两种语言都不用词典判断。
    """
    parts: list[str] = []
    previous = None
    for word in words:
        text = word[0]
        if not text:
            continue
        if previous is not None:
            _, px0, py0, px1, py1, pblock, pline = previous
            _, x0, y0, _x1, _y1, block, line = word
            if (block, line) != (pblock, pline):
                parts.append("\n")
            else:
                height = max(1.0, min(py1 - py0, _y1 - y0))
                if x0 - px1 > 0.1 * height:
                    parts.append(" ")
        parts.append(text)
        previous = word
    return "".join(parts)


def _stroke_path(points: list[Point]) -> QPainterPath:
    """逐点直连的折线路径；单点笔迹用零点线段 + 圆头画成实心圆点。

    与草稿画布同一口径：采样点已是真实手写轨迹，直连折线最忠实。
    抬笔后的插值平滑（Catmull-Rom）实测字形不如直连、且明显更卡，已回退。
    """
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0][0], points[0][1])
    if len(points) == 1:
        # Qt 不画零点线段；挪 0.001 点，圆头 + 圆角即实心圆点
        path.lineTo(points[0][0] + 0.001, points[0][1])
        return path
    for x, y in points[1:]:
        path.lineTo(x, y)
    return path


def _make_pen(color: str, width: float, zoom: float, opacity: float = 1.0) -> QPen:
    """线宽按 PDF 点；width*zoom 不足 1 屏幕像素时钳到 1（沿用画布规则）。

    `opacity<1` 用 QColor 的 alpha（荧光笔 0.35）；默认 1.0 让既有调用方不变。
    """
    pen_color = QColor(color)
    if opacity < 1.0:
        pen_color.setAlpha(round(255 * max(0.0, opacity)))
    pen = QPen(pen_color)
    pen.setWidthF(max(float(width), 1.0 / zoom))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


# 工具栏像素宽 → PDF 点的固定折算基准：页面铺满 900 逻辑像素宽时，
# 屏幕粗细 = 工具栏数字 × PEN_WIDTH_SCALE。不能再用“当前视口宽”，否则同一支笔
# 在宽/窄窗口落笔会存成不同的点宽，之后一起看就粗细不一（2026-10-25 用户实测）。
PEN_WIDTH_REFERENCE_PX = 900.0
# 工具栏数字 → PDF 点的整体倍率：用户实测同一数字 3 在页面上仍偏粗
# （2026-10-25「3 都很大」），PDF 笔宽统一减半；草稿/批注层不受影响。
PEN_WIDTH_SCALE = 0.5


def pen_points_for(page_width: float, width: float,
                   reference_px: float = PEN_WIDTH_REFERENCE_PX) -> float:
    """工具栏像素宽换算成 PDF 点：基准是固定页宽，不随窗口/缩放变化。"""
    page_width = float(page_width or 0.0)
    reference_px = float(reference_px or 0.0)
    if page_width <= 0.0 or reference_px <= 0.0:
        return max(0.1, float(width))
    return max(0.1, float(width) / (reference_px / page_width))


def _stroke_pen(stroke: Stroke, zoom: float,
                scale_for_min_width: float | None = None) -> QPen:
    """按笔迹自己的工具取笔：荧光笔半透明、线宽照存；其它工具完全不透明。

    `zoom` 是视图缩放（1 屏幕像素 = 1/zoom 点）；`scale_for_min_width` 供位图缓存
    传"物理像素倍率"（缓存倍率 / DPR），保证缓存里细线也至少 1 物理像素。
    """
    opacity = HIGHLIGHTER_OPACITY if stroke.tool == "highlighter" else 1.0
    scale = zoom if scale_for_min_width is None else scale_for_min_width
    return _make_pen(stroke.color, stroke.width, scale, opacity)


def _highlights_first(strokes: list[Stroke]) -> list[Stroke]:
    """荧光笔/选字高亮在前、画笔在后（稳定排序，不改模型列表本身）。

    半透明高亮必须压在墨迹底下，否则导出/缓存重渲时会盖住上面的字和笔迹。
    """
    return sorted(strokes, key=lambda s: 0 if is_highlight(s) else 1)


def _highlight_brush(stroke: Stroke) -> QColor:
    color = QColor(stroke.color)
    color.setAlpha(round(255 * HIGHLIGHTER_OPACITY))
    return color


def _stroke_shape_path(stroke: Stroke) -> QPainterPath:
    """选字荧光：每个矩形一条子路径；普通笔迹仍走逐点折线。"""
    if stroke.tool != TEXT_HIGHLIGHT:
        return _stroke_path(stroke.points)
    path = QPainterPath()
    for x0, y0, x1, y1 in text_highlight_rects(stroke):
        path.addRect(QRectF(min(x0, x1), min(y0, y1),
                            abs(x1 - x0), abs(y1 - y0)))
    return path


def _paint_cached_stroke(painter: QPainter, stroke: Stroke, scale: float) -> None:
    """按笔迹类型落笔：选字荧光是填充矩形，压感是 ribbon，其余是描边路径。"""
    if stroke.tool == TEXT_HIGHLIGHT:
        painter.setPen(Qt.NoPen)
        painter.setBrush(_highlight_brush(stroke))
        painter.drawPath(_stroke_shape_path(stroke))
        return
    if has_pressure(stroke):
        draw_ink_stroke(painter, stroke, 1.0)
        return
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_stroke_pen(stroke, scale))
    painter.drawPath(_stroke_path(stroke.points))


@dataclass
class _PageLayer:
    """一页的投影：底图项 + 笔迹位图缓存 + 尾部矢量笔迹项。

    x/y 是页面左上角在场景里的位置（PDF 点）。cache_strokes 是已烘进
    cache_item 的笔迹；stroke_items 是尾部未烘笔迹的矢量项（带 data(0)=Stroke）。
    """

    index: int
    x: float
    y: float
    width: float
    height: float
    pixmap_item: QGraphicsPixmapItem
    cache_item: QGraphicsPixmapItem | None = None      # 已固化笔迹的位图缓存
    cache_strokes: list[Stroke] = field(default_factory=list)
    cache_zoom: float | None = None                    # 缓存渲染时的视图缩放
    stroke_items: list[QGraphicsPathItem] = field(default_factory=list)
    text_items: list[QGraphicsTextItem] = field(default_factory=list)
    image_items: list = field(default_factory=list)
    rendered_zoom: float | None = None
    rendered_ratio: float | None = None   # 位图 DPR（超采样时 = 渲染倍率 / 缩放）


class _PageHistory(History):
    """撤销/重做后回调视图，把命令自己带着的页码交给它只重绑那一页。"""

    def __init__(self, limit: int, on_change) -> None:
        super().__init__(limit)
        self._on_change = on_change

    def pop_undo(self):
        cmd = super().pop_undo()
        if cmd is not None:
            self._on_change(cmd)
        return cmd

    def pop_redo(self):
        cmd = super().pop_redo()
        if cmd is not None:
            self._on_change(cmd)
        return cmd


class PdfView(QGraphicsView):
    """文档模式视图：逐页批注、翻页、光标锚定缩放。自己不落盘，document 交给调用方。

    **鼠标工具（内部 id 仍是 `select`，键位 V）**一个工具干四件事：
    拖空白/页外 = 平移视图（只改滚动条，绝不改缩放）；拖文字 = 选字（Ctrl+C 复制）；
    拖图钉 = 移动笔记锚点；点图钉右上角 × = 请求删除（松手仍在热区内才算数）。
    图钉在**任何工具**下都优先吃掉按下：不超阈值 = 点击开卡，超了 = 挪锚点。
    """

    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    changed = Signal()
    # 仅笔迹数据变化时发（区别于 changed：缩放/切工具也发 changed，但不发这条）
    strokesChanged = Signal()
    selectionCopied = Signal(int)      # 复制选中文字成功：字符数（给状态栏提示）
    objectCopied = Signal(str)         # 复制页面图片/文字对象成功：提示文案
    # note 工具落点：页号 + 页面局部坐标（PDF 点）；由主窗建 Note
    notePlacementRequested = Signal(int, float, float)
    # 点了某页的图钉（按下松手没超过拖动阈值）：页号 + note id；由主窗打开卡片
    noteRequested = Signal(int, str)
    # 按在图钉右上角 × 上并在同一热区里松手：删除请求，确认框由主窗弹
    noteDeleteRequested = Signal(int, str)
    # 拖动图钉改过锚点：页号（Note.x/y 已写回，图钉已重排），由主窗标脏
    notesChanged = Signal(int)
    # 右键图钉：页号 + note id + 全局屏幕坐标（菜单由主窗弹，视图自己不弹）
    noteContextRequested = Signal(int, str, QPoint)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.document: PdfDocument | None = None
        self._source: PdfSource | None = None
        self._path: Path | None = None
        self._anchor_path: Path | None = None
        self._layers: list[_PageLayer] = []
        self._zoom = 1.0
        self._auto_fit = True           # 打开即跟随视口宽度，手动缩放才关
        self._fitting = False           # fit 内部改视口会触发 Resize，防重入
        self._hit_items: list[QGraphicsRectItem] = []
        # 笔记图钉：键 (页号, note id)；close_document 场景清空后同步清掉
        self._note_pins: dict[tuple[int, str], NotePin] = {}
        self._current_page = 0
        # 页面文字编辑：临时 QGraphicsTextItem，提交才进模型（与草稿文字工具同规则）
        self._text_editing: QGraphicsTextItem | None = None
        self._text_editing_page: int | None = None
        self._text_editing_original: TextBlock | None = None
        self._text_editing_item: QGraphicsTextItem | None = None
        self._text_editing_color: str | None = None
        self._text_editing_size: float = DEFAULT_PAGE_TEXT_SIZE
        self._text_editing_bold = False
        self._text_editing_italic = False
        self._text_editing_underline = False
        self.image_error = ""           # 最近一次插图失败的原因（给状态栏用）
        # 页面对象（图片/文字）选择框与拖动/缩放状态
        # 选择集合 = [(页号, "image"|"text", 对象 id)]；单选时 _sel_* 同步为它，
        # 多选时 _sel_* 为 None（不显示手柄，批量拖/删/微调走 _selected_objects）。
        self._selection: list[tuple[int, str, str]] = []
        self._sel_page: int | None = None
        self._sel_kind: str | None = None       # "image" / "text"
        self._sel_id: str | None = None
        self._sel_box: QGraphicsRectItem | None = None
        self._sel_handles: list[QGraphicsRectItem] = []
        self._obj_drag: dict | None = None
        # Ctrl+左键拖空白：虚线框选（场景坐标矩形，只在 drawForeground 里画）
        self._marquee: dict | None = None

        self.tool = "pen"
        self.color = settings.color
        self.highlighter_color = getattr(settings, "highlighter_color",
                                         HIGHLIGHTER_COLOR)
        self.width = float(settings.width)
        # 底图阅读模式（正常/夜间/护眼）；设置里持久化，打开时沿用
        self.page_mode = getattr(settings, "page_mode", "normal")
        self.ink_smoothing = getattr(settings, "ink_smoothing", "low")
        self.history = _PageHistory(100, self._after_history_change)

        self._drawing = False
        self._drawing_page: int | None = None
        self._points: list[Point] = []
        self._preview_path: QPainterPath | None = None   # 无压感笔迹的增量预览路径
        self._erasing = False
        self._erase_page: int | None = None
        self._candidates: list[tuple[Stroke, QGraphicsPathItem | None, QRectF]] = []
        self._erased: list[tuple[Stroke, QGraphicsPathItem | None]] = []
        # 拖动中已命中、但还在模型里的缓存笔迹：重渲缓存位图时临时跳过，松手才落命令
        self._erased_cache_ids: set[int] = set()
        # 压感：仅钢笔；_pressures 与 _points 等长，鼠标输入保持空列表
        self.pressure_enabled = getattr(settings, "pressure_enabled", True)
        self._pressures: list[float] = []
        self._tablet_stroke = False

        # 文字选择：按页缓存**字符**表（旋转页已换成显示坐标），选择只落在起选页
        self._units_cache: dict[int, list[SELECT_UNIT]] = {}
        self._select_page: int | None = None
        self._select_anchor: int | None = None
        self._selection_words: list[SELECT_UNIT] = []
        self._selection_items: list[QGraphicsRectItem] = []
        # 选区用途：copy=蓝色选字复制；highlight=荧光笔选字，松手落成高亮批注
        self._selection_purpose = "copy"
        self._selection_fill = QColor(SELECT_FILL)
        # 鼠标工具（select）的手势状态：平移视图 / 拖图钉 / 按 × 请求删除
        self._panning = False
        self._pan_last: QPointF | None = None       # 上一次移动的视口坐标
        self._pin_press: NotePin | None = None      # 按住的图钉（未超过阈值 = 点击）
        self._pin_press_pos = QPointF()             # 按下的视口坐标（算拖动阈值）
        self._pin_offset = QPointF()                # 图钉原点与按下点的场景差：拖动不跳位
        self._pin_dragging = False
        self._close_press: NotePin | None = None    # 按在 × 上的图钉，松手仍在热区才删
        self._hover_pin: NotePin | None = None      # 悬停中的图钉（× 高亮跟着它）
        # 笔尖圆点（与草稿画布同一套 R8 交互）：光标位置存视口坐标，移出视图清空
        self._cursor_pos: QPointF | None = None
        # 落笔时收紧笔尖圆点；停笔 3 秒才恢复大光标（方便定位又不挡字）
        self._cursor_compact = False
        self._cursor_restore_timer = QTimer(self)
        self._cursor_restore_timer.setSingleShot(True)
        self._cursor_restore_timer.setInterval(CURSOR_RESTORE_MS)
        self._cursor_restore_timer.timeout.connect(self._restore_cursor_size)

        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        # 超采样位图缩回页面矩形时走平滑插值，否则 2×→1× 的最近邻采样会出锯齿
        self.setRenderHints(self.renderHints() | QPainter.SmoothPixmapTransform)
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setBackgroundBrush(QColor(VIEW_BG))
        # 悬停（无按键）也要收到 mouseMove；悬停绝不能落下笔迹
        self.viewport().setMouseTracking(True)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(RENDER_DELAY_MS)
        self._render_timer.timeout.connect(self._render_visible)
        self._screen_connected = False

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(self._on_scroll)
        self.setTransform(QTransform.fromScale(self._zoom, self._zoom))
        self._update_cursor()

    # --- 对外状态 ---

    @property
    def pdf_path(self) -> Path | None:
        """sidecar / 标题 / 最近打开用的锚点；Word 指向原 docx，不是缓存 PDF。"""
        return self._anchor_path or self._path

    @property
    def search_path(self) -> Path | None:
        """后台查找要打开的真实 PDF；Word 是缓存 PDF。"""
        return self._path

    @property
    def source(self) -> PdfSource | None:
        return self._source

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def auto_fit(self) -> bool:
        """是否自动适应页宽：打开为真，任何手动缩放为假，fit_width 重新打开。"""
        return self._auto_fit

    def fit_width(self) -> None:
        """按当前页宽铺满视口；无页或视口无宽度时只打开自适应开关。

        缩放后把视口中心钉回原来的场景点：换缩放会挪动滚动条的像素位置，
        不钉的话中心会飘到相邻页，下一次 fit 就用错页宽（来回跳）。
        """
        self._auto_fit = True
        if self._fitting or not self._layers:
            return
        viewport_width = self.viewport().width()
        visible = self._visible_scene_rect()
        if viewport_width <= 0 or visible.isEmpty():
            return
        anchor = visible.center()
        index = self.current_page
        width = (self._layers[index].width
                 if 0 <= index < len(self._layers)
                 else max(layer.width for layer in self._layers))
        if width <= 0:
            return
        self._fitting = True
        try:
            self._apply_zoom(viewport_width / width)
            self.centerOn(anchor)
        finally:
            self._fitting = False

    def set_zoom(self, zoom: float) -> None:
        """用户显式缩放（钳 0.1–8.0）；同时关掉自动适应页宽。"""
        self._auto_fit = False
        self._apply_zoom(zoom)

    def _apply_zoom(self, zoom: float) -> bool:
        """只改视图变换，笔迹数据不回算；fit_width 走这里，不碰自适应开关。"""
        zoom = min(MAX_ZOOM, max(MIN_ZOOM, float(zoom)))
        if zoom == self._zoom:
            return False
        self._zoom = zoom
        self.setTransform(QTransform.fromScale(zoom, zoom))
        self._update_pen_widths()
        self._update_pixmap_transforms()
        if not self._fitting:
            # 显式缩放：立刻按新倍率重渲当前视口内的页，否则会有一帧旧位图被拉伸的发糊
            self._render_sharp_now()
        self.zoomChanged.emit(zoom)
        self.changed.emit()
        self._schedule_render()
        return True

    def _render_sharp_now(self) -> None:
        """缩放后同步重渲视口内的页（含笔迹缓存），避免"缩放瞬间发糊"。

        只处理视口内、且倍率不超过 `SYNC_RENDER_MAX_SCALE` 的页：A4 6× 约 100ms，
        再高就交给去抖计时器，免得滚轮连拨时界面卡住。自适应页宽（`_fitting`）
        在拖动窗口期间会连续改缩放，此处跳过同步渲染。
        """
        if self._source is None or self.document is None or not self._layers:
            return
        scale = self._page_render_scale()
        ratio = self._page_render_ratio()
        if scale > SYNC_RENDER_MAX_SCALE:
            return
        visible = self._visible_scene_rect()
        if visible.isEmpty():
            return
        for layer in self._layers:
            if not visible.intersects(_rect_of(layer)):
                continue
            if layer.rendered_zoom != scale or layer.rendered_ratio != ratio:
                try:
                    image = apply_page_mode(
                        self._source.render(layer.index, scale), self.page_mode)
                except PdfError:
                    continue                      # 关文档/页码失效：留旧位图，不崩
                layer.pixmap_item.setPixmap(self._page_pixmap(image, ratio))
                self._fit_pixmap(layer)
                layer.rendered_zoom = scale
                layer.rendered_ratio = ratio
            if layer.cache_zoom != self._cache_zoom() and layer.cache_strokes:
                self._render_stroke_cache(layer)

    def zoom_at(self, screen_pos: QPointF, factor: float) -> None:
        """以光标为锚缩放：屏幕位置 screen_pos 下的场景点在缩放前后不动。"""
        self._auto_fit = False
        pos = QPointF(screen_pos)
        before = self.mapToScene(pos)
        new_zoom = min(MAX_ZOOM, max(MIN_ZOOM, self._zoom * float(factor)))
        if new_zoom == self._zoom:
            return
        self.set_zoom(new_zoom)
        after = self.mapToScene(pos)
        # 平移量按场景单位补：视线里那一点原地不动
        self.translate(after.x() - before.x(), after.y() - before.y())
        self._update_current_page()
        self._schedule_render()

    @property
    def current_page(self) -> int:
        """视口中心所在页；掉在页间空隙时取离得最近的一页。"""
        if not self._layers:
            return 0
        center = self._visible_scene_rect().center()
        index = self._page_at(center)
        if index is not None:
            return index
        return min(self._layers, key=lambda layer: (
            (center.x() - _rect_of(layer).center().x()) ** 2
            + (center.y() - _rect_of(layer).center().y()) ** 2)).index

    def goto_page(self, index: int) -> None:
        """把某页居中；页码越界直接忽略（UI 不该崩）。"""
        if not 0 <= index < len(self._layers):
            return
        layer = self._layers[index]
        self.centerOn(QPointF(layer.x + layer.width / 2.0,
                              layer.y + layer.height / 2.0))
        self._update_current_page()
        self._schedule_render()

    def current_position(self) -> dict | None:
        """当前阅读位置：页码 / 视口中心在页内的纵向比例 / 缩放（0..1，供记忆）。"""
        if self.document is None or not self._layers:
            return None
        center = self._visible_scene_rect().center()
        layer = self._layers[self.current_page]
        rect = _rect_of(layer)
        offset = (center.y() - rect.top()) / rect.height() if rect.height() > 0 else 0.0
        return {"page": layer.index,
                "offset": min(1.0, max(0.0, offset)),
                "zoom": self._zoom,
                "auto_fit": self._auto_fit}

    def restore_position(self, page, offset, zoom, auto_fit) -> None:
        """恢复阅读位置；坏值/越界一律钳到有效范围，没有页时什么也不做。

        必须在视图显示（视口尺寸定下来）之后调用：fit_width/centerOn 都依赖
        当前视口大小。打开时仍走 open() 的 fit_width + scroll_to_top 作默认。
        """
        if not self._layers:
            return
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            offset = 0.0
        if not math.isfinite(offset):
            offset = 0.0
        offset = min(1.0, max(0.0, offset))
        try:
            page = int(page)
        except (TypeError, ValueError, OverflowError):
            page = 0
        page = min(max(page, 0), len(self._layers) - 1)
        if auto_fit:
            self.fit_width()
        else:
            try:
                zoom = float(zoom)
            except (TypeError, ValueError):
                zoom = 1.0
            if not math.isfinite(zoom):
                zoom = 1.0
            self.set_zoom(zoom)
        layer = self._layers[page]
        rect = _rect_of(layer)
        self.centerOn(QPointF(layer.x + layer.width / 2.0,
                              rect.top() + offset * rect.height()))
        self._update_current_page()
        self._schedule_render()

    def set_tool(self, tool: str) -> None:
        if tool not in ("pen", "eraser", "select", "highlighter", "note",
                        "text") or tool == self.tool:
            return
        if self._text_editing is not None:
            self._commit_text_edit()
        if tool != "select":
            self._clear_object_selection()
        self.tool = tool
        self._cursor_restore_timer.stop()
        self._cursor_compact = False
        if tool != "select":
            self.clear_selection()
        else:
            self._update_selection_overlay()
        self._update_cursor()
        self.changed.emit()

    def _update_cursor(self) -> None:
        """工具基准光标：鼠标工具=可平移的手形；note 给十字；画笔/橡皮隐藏系统光标。

        只看"这个工具是什么"，指针下是什么由 `_hover_cursor` 现算（文字 I 形、
        图钉手形、拖动中闭手/SizeAll），所以拖动结束/换工具后由这里收回基准值。
        """
        if self._panning:
            self.viewport().setCursor(Qt.ClosedHandCursor)
        elif self._pin_dragging:
            self.viewport().setCursor(Qt.SizeAllCursor)
        elif self.tool == "select":
            self.viewport().setCursor(Qt.OpenHandCursor)
        elif self.tool == "note":
            self.viewport().setCursor(Qt.CrossCursor)
        elif self.tool == "text":
            self.viewport().setCursor(Qt.IBeamCursor)
        else:
            # 还没收到第一次鼠标移动时先显示箭头，避免一打开就没有鼠标
            self.viewport().setCursor(
                Qt.ArrowCursor if self._cursor_pos is None else Qt.BlankCursor)

    # --- 文字选择 / 复制 ---

    def _units(self, index: int) -> list[SELECT_UNIT]:
        """取一页字符表（首次访问才问 PDF）；PDF 已关/越界时给空表，不抛。

        只留最近 `UNITS_CACHE_PAGES` 页：鼠标工具的悬停光标也要问字符表，
        不设上限的话沿长文档滚一遍就会把每页的几千个字符框全留在内存里。
        """
        cached = self._units_cache.get(index)
        if cached is not None:
            return cached
        if self._source is None:
            return []
        try:
            units = self._source.page_chars(index)
        except PdfError:
            units = []
        self._units_cache[index] = units
        while len(self._units_cache) > UNITS_CACHE_PAGES:
            self._units_cache.pop(next(iter(self._units_cache)))   # dict 保序：丢最早那页
        return units

    @staticmethod
    def _unit_index_at(units: list[SELECT_UNIT], point: Point,
                       slack_ratio: float = 0.0) -> int | None:
        """点落在哪个字符上；`slack_ratio>0` 时允许落在字符外一小段距离内。

        按下起选用 0.6 倍字高做容差（点到字间空隙也能选中最近的字符，点页面
        大片空白仍以 None 返回）；拖动延伸时不设阈值，拖到行尾/词间也能继续扩选。
        """
        x, y = point
        best: int | None = None
        best_dist = float("inf")
        for index, unit in enumerate(units):
            _, x0, y0, x1, y1, _block, _line = unit
            if x0 <= x <= x1 and y0 <= y <= y1:
                return index
            if slack_ratio <= 0.0:
                continue
            dx = max(x0 - x, 0.0, x - x1)
            dy = max(y0 - y, 0.0, y - y1)
            dist = dx * dx + dy * dy
            if dist < best_dist:
                best, best_dist = index, dist
        if best is None:
            return None
        _, _x0, y0, _x1, y1, _block, _line = units[best]
        height = max(1.0, y1 - y0)
        return best if best_dist <= (slack_ratio * height) ** 2 else None

    def _select_to(self, index: int, local: Point) -> None:
        """把选区从起选字符延伸到 local 所在的字符（同一页内）。"""
        if index != self._select_page:
            return
        units = self._units(index)
        current = self._unit_index_at(units, local)
        if current is None or self._select_anchor is None:
            return
        low, high = sorted((self._select_anchor, current))
        self._selection_words = units[low:high + 1]
        self._paint_selection(index)

    def _selection_runs(self) -> list[tuple[float, float, float, float]]:
        """把选中字符按行合并成连续矩形（页面局部坐标）。

        逐字符建矩形在整页选中时会有上千个；同一行相邻字符并成一段，
        既减少项数，视觉上也和普通阅读器的连续选区一致。
        """
        runs: list[list[float]] = []          # [x0, y0, x1, y1, block, line]
        for _text, x0, y0, x1, y1, block, line in self._selection_words:
            height = max(1.0, y1 - y0)
            if runs:
                run = runs[-1]
                gap = x0 - run[2]
                if (run[4] == block and run[5] == line
                        and abs(y0 - run[1]) <= 0.5 * height
                        and -0.5 * height <= gap <= 0.8 * height):
                    run[2] = max(run[2], x1)
                    run[1] = min(run[1], y0)
                    run[3] = max(run[3], y1)
                    continue
            runs.append([x0, y0, x1, y1, float(block), float(line)])
        return [(x0, y0, x1, y1) for x0, y0, x1, y1, _block, _line in runs]

    def _paint_selection(self, index: int) -> None:
        """按选中字符重画高亮块（页面局部坐标 + 页面在场景里的偏移）。"""
        self._remove_selection_items()
        if not 0 <= index < len(self._layers):
            return
        layer = self._layers[index]
        for x0, y0, x1, y1 in self._selection_runs():
            item = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
            item.setPos(layer.x, layer.y)
            item.setBrush(QColor(self._selection_fill))
            item.setPen(Qt.NoPen)
            item.setAcceptedMouseButtons(Qt.NoButton)
            item.setZValue(SELECT_Z)
            self._scene.addItem(item)
            self._selection_items.append(item)
        self.viewport().update()

    def _remove_selection_items(self) -> None:
        for item in self._selection_items:
            self._scene.removeItem(item)
        self._selection_items.clear()

    def clear_selection(self) -> None:
        """取消选择（换工具/点击空白/关闭文档）；不发任何信号。"""
        self._select_page = None
        self._select_anchor = None
        self._selection_words = []
        self._selection_purpose = "copy"
        self._selection_fill = QColor(SELECT_FILL)
        self._remove_selection_items()
        self.viewport().update()

    def selected_text(self) -> str:
        """当前选中的文字（未选中为空串）。"""
        return _join_words(self._selection_words)

    def copy_selection(self) -> int:
        """把选中的页面对象/文字放进剪贴板；返回字符数（图片返回 1）。

        多选暂不支持复制到剪贴板：只给状态栏提示并返回 1（表示"这次按键已处理"），
        避免宿主再把它当成普通文本复制。
        """
        if len(self._selection) > 1:
            self.objectCopied.emit("多选暂不支持复制")
            return 1
        if self._copy_selected_object():
            return 1
        text = self.selected_text()
        if not text:
            return 0
        QGuiApplication.clipboard().setText(text)
        self.selectionCopied.emit(len(text))
        return len(text)

    # --- 搜索高亮 ---

    def show_hits(self, page_index: int, rects: list[tuple[float, float, float, float]],
                  current: int = 0) -> None:
        """在 page_index 页画命中高亮（页面局部点，x0/y0/x1/y1）；替换上一批。

        只画给这一页；current 那一条用更浓的颜色。高亮不接收鼠标，
        落笔照常穿过它进页面。
        """
        self.clear_hits()
        if not 0 <= page_index < len(self._layers):
            return
        layer = self._layers[page_index]
        for index, (x0, y0, x1, y1) in enumerate(rects):
            rect = QRectF(float(x0), float(y0), float(x1) - float(x0),
                          float(y1) - float(y0)).normalized()
            item = QGraphicsRectItem(rect)
            item.setPos(layer.x, layer.y)
            fill = HIT_CURRENT_FILL if index == current else HIT_FILL
            item.setBrush(QColor(fill))
            item.setPen(Qt.NoPen)
            item.setAcceptedMouseButtons(Qt.NoButton)
            item.setZValue(HIT_Z)
            self._scene.addItem(item)
            self._hit_items.append(item)
        self.viewport().update()

    def clear_hits(self) -> None:
        """去掉所有搜索高亮。"""
        for item in self._hit_items:
            self._scene.removeItem(item)
        self._hit_items.clear()
        self.viewport().update()

    def set_color(self, color: str) -> None:
        if color == self.color:
            return
        self.color = color
        if self._text_editing is not None:
            self._text_editing_color = color
            self._text_editing.setDefaultTextColor(QColor(color))
        self.viewport().update()             # 笔尖圆点跟着换色
        self.changed.emit()

    def set_pressure_enabled(self, enabled: bool) -> None:
        """压感开关只影响下一次落笔；进行中的笔迹保持原样。"""
        self.pressure_enabled = bool(enabled)

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

    def set_ink_smoothing(self, level: str) -> None:
        """抬笔平滑强度；未知值回低强度，off 时保留原始折线。"""
        self.ink_smoothing = level if level in INK_SMOOTHING_LEVELS else "low"

    def set_page_mode(self, mode: str) -> None:
        """切换底图阅读模式；已渲染的页作废，视口内的立即按新模式重渲。"""
        if mode not in PAGE_MODES:
            raise ValueError(f"未知阅读模式：{mode!r}（只支持 {PAGE_MODES}）")
        if mode == self.page_mode:
            return
        self.page_mode = mode
        for layer in self._layers:
            layer.rendered_zoom = None
            layer.rendered_ratio = None
        self._render_visible()

    def set_width(self, width: float) -> None:
        width = float(width)
        if width == self.width:
            return
        self.width = width
        self.changed.emit()

    def mapToScene(self, point) -> QPointF:
        """单点版本给浮点结果；Qt6 原生只收 QPoint，亚像素会被截断。

        QRect/QPolygon 等其它重载原样转交父类（视图内部只用单点）。
        """
        if isinstance(point, QPointF):
            inv, ok = self.viewportTransform().inverted()
            return inv.map(point) if ok else QPointF(point)
        return super().mapToScene(point)

    # --- 打开 / 关闭 ---

    def open(self, path: Path, *, ignore_sidecar: bool = False,
             document_override=None, anchor_path: Path | None = None) -> None:
        """打开 PDF 并配对 sidecar；打不开抛 PdfError，旧文档保持原样。

        `ignore_sidecar=True`：跳过 sidecar（坏文件已被上层备份）。
        `document_override`：用崩溃恢复的缓存文档替代 sidecar（页数必须一致）。
        `anchor_path`：sidecar/标题/最近打开用这份路径（Word 传原 docx，
        `path` 则是它的缓存 PDF）。
        """
        path = Path(path)
        anchor = Path(anchor_path) if anchor_path is not None else path
        src = PdfSource(path)                    # 失败在这里抛，不动现有文档
        try:
            if document_override is not None:
                if len(document_override.pages) != src.page_count:
                    raise pdfstore.PdfPageCountMismatch(
                        f"自动暂存页数不符：批注 {len(document_override.pages)} 页，"
                        f"PDF {src.page_count} 页")
                doc = document_override
            elif ignore_sidecar:
                doc = None
            else:
                doc = pdfstore.load_sidecar(anchor, src.page_count)
        except Exception:
            src.close()                          # sidecar/缓存失败时别泄漏文件句柄
            raise
        if doc is None:
            doc = PdfDocument([
                Page(width, height, 0, [])
                for width, height in (src.page_size(i) for i in range(src.page_count))
            ])
        self.close_document()
        self._source = src
        self._path = path
        self._anchor_path = anchor if anchor != path else None
        self.document = doc
        self.history = _PageHistory(100, self._after_history_change)
        self._zoom = 1.0
        self.setTransform(QTransform.fromScale(self._zoom, self._zoom))
        self._build_layers()
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self._current_page = 0
        self._render_visible()
        self._update_current_page()
        self.fit_width()                  # 末步：按视口宽定缩放（打开即铺满）
        # fit_width 会保住缩放前的视口中心（换缩放后滚动条像素位置会漂），
        # 高页因此停在页中间。打开动作明确要求"从页首看起"，这里把两条
        # 滚动条归零；后续缩放/改变窗口尺寸仍走 fit_width 的原有逻辑。
        self.scroll_to_top()
        self.zoomChanged.emit(self._zoom)
        self.changed.emit()

    def scroll_to_top(self) -> None:
        """把视口钉到页首（场景左上角）。打开后、以及视图从隐藏变可见后调用。

        视图藏在 QStackedWidget 里时视口尺寸不是最终值，显示那一刻会触发一次
        自适应重算——fit_width 保住旧中心，页面又会被推到中间。调用方在显示
        之后补这一下即可，fit_width 本身的行为不变。
        """
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self._update_current_page()

    def close_document(self) -> None:
        """关掉 PDF 并清空场景；数据不再可访问（视图不落盘）。"""
        self._render_timer.stop()
        self._reset_gesture()
        self._clear_object_selection()
        self._cancel_text_edit()
        self._layers.clear()
        self._hit_items.clear()
        self._units_cache.clear()
        self._select_page = None
        self._select_anchor = None
        self._selection_words = []
        self._selection_items.clear()
        self._note_pins.clear()           # 场景清空会删掉图钉项，字典同步
        self._hover_pin = None            # 悬停高亮指向的也是即将失效的图钉包装
        self._scene.clear()
        self._sel_box = None              # 选择框/手柄随场景一起销毁
        self._sel_handles = []
        self._scene.setSceneRect(QRectF())
        if self._source is not None:
            self._source.close()
        self._source = None
        self._path = None
        self._anchor_path = None
        self.document = None
        self._current_page = 0
        self._auto_fit = True             # 下一个文档重新适应页宽

    # --- 笔记图钉 ---

    def add_note(self, page_index: int, note: Note) -> None:
        """把一张笔记挂到页面上，并重建该页图钉。"""
        if self.document is None or not 0 <= page_index < len(self.document.pages):
            return
        self.document.pages[page_index].notes.append(note)
        self.rebuild_note_pins(page_index)

    def remove_note(self, page_index: int, note_id: str) -> None:
        """按 id 摘掉一张笔记，并重编该页图钉序号。"""
        if self.document is None or not 0 <= page_index < len(self.document.pages):
            return
        page = self.document.pages[page_index]
        page.notes = [note for note in page.notes if note.id != note_id]
        self.rebuild_note_pins(page_index)

    def rebuild_note_pins(self, page_index: int) -> None:
        """删掉该页旧图钉，按 notes 顺序重建（number 从 1 起）。

        图钉带 `ItemIgnoresTransformations`，缩放/滚动都不需要重建；只有笔记
        集合或页面布局变化时才调用。不存在的页只清旧项，不抛。
        """
        for key in [key for key in self._note_pins if key[0] == page_index]:
            pin = self._note_pins.pop(key)
            if pin is self._hover_pin:
                self._hover_pin = None          # 旧包装对象要离开场景，别再引用它
            if pin.scene() is self._scene:
                self._scene.removeItem(pin)
        if (self.document is None
                or not 0 <= page_index < len(self.document.pages)
                or not 0 <= page_index < len(self._layers)):
            return
        page = self.document.pages[page_index]
        layer = self._layers[page_index]
        for number, note in enumerate(page.notes, 1):
            # 标签直接拿 display name：自定义名优先、空名回退「笔记 N」（与卡片标题同规则）
            pin = NotePin(note.id, page_index, number,
                          name=note_display_name(note, number))
            pin.setPos(layer.x + note.x, layer.y + note.y)
            self._scene.addItem(pin)
            self._note_pins[(page_index, note.id)] = pin

    def note_anchor_view_pos(self, page_index: int, note_id: str) -> QPoint | None:
        """图钉锚点对应的视口坐标（卡片打开动画的锚点）；图钉不存在返回 None。"""
        pin = self._note_pins.get((page_index, note_id))
        if pin is None:
            return None
        pos = self.mapFromScene(pin.scenePos())
        return QPoint(round(pos.x()), round(pos.y()))

    # --- 场景构建 ---

    def _build_layers(self) -> None:
        sizes = [self._source.page_size(i) for i in range(self._source.page_count)]
        total_w = max((w for w, _ in sizes), default=0.0)
        y = 0.0
        for index, (width, height) in enumerate(sizes):
            x = (total_w - width) / 2.0
            item = QGraphicsPixmapItem()
            item.setData(0, index)
            item.setPos(x, y)
            item.setTransformationMode(Qt.SmoothTransformation)
            self._scene.addItem(item)
            # 笔迹位图缓存：紧随底图入场景（z 相同，按插入顺序压在底图上、尾部位图下）
            cache = QGraphicsPixmapItem()
            cache.setPos(x, y)
            cache.setAcceptedMouseButtons(Qt.NoButton)
            self._scene.addItem(cache)
            page = self.document.pages[index]
            page.width, page.height = width, height     # 版面以 PDF 实际页面尺寸为准
            # r 记真实旋转角：写进 sidecar 后，下一次打开/导出都能知道这页转过
            page.rotation = self._source.rotation(index)
            layer = _PageLayer(index, x, y, width, height, item, cache)
            for stroke in page.strokes:
                layer.stroke_items.append(self._add_stroke_item(layer, stroke))
            self._rebind_page_rich(layer)
            self._layers.append(layer)
            self.rebuild_note_pins(index)     # sidecar 里已有的笔记：打开即出图钉
            y += height + PAGE_GAP
        height = max(0.0, y - PAGE_GAP)
        self._scene.setSceneRect(QRectF(0.0, 0.0, total_w, height))

    def _add_stroke_item(self, layer: _PageLayer, stroke: Stroke) -> QGraphicsPathItem:
        item = InkItem(stroke, self._zoom,
                       HIGHLIGHTER_OPACITY if is_highlight(stroke) else None)
        if stroke.tool == TEXT_HIGHLIGHT:
            item.setPath(_stroke_shape_path(stroke))   # 每个矩形一条子路径
            item.setPen(Qt.NoPen)
            item.setBrush(_highlight_brush(stroke))
        else:
            item.setPen(_stroke_pen(stroke, self._zoom))
        # 荧光笔/选字高亮在各 z 图层里最低（但仍高于页面与缓存），画笔压在其上
        item.setZValue(STROKE_Z_HIGHLIGHTER if is_highlight(stroke)
                       else STROKE_Z_PEN)
        item.setPos(layer.x, layer.y)
        self._scene.addItem(item)
        return item

    # --- 页面级文字 / 图片（sidecar v4）---

    @staticmethod
    def _text_font(size: float) -> QFont:
        font = QFont(TEXT_FONT_FAMILY)
        font.setPixelSize(max(1, int(round(float(size)))))
        return font

    @staticmethod
    def _apply_text_item_font(item: QGraphicsTextItem, font: QFont) -> None:
        """整段套字体：setFont 只改默认字体，已有文字还要用 char format 覆盖。"""
        item.setFont(font)
        char_format = QTextCharFormat()
        char_format.setFont(font)
        cursor = item.textCursor()
        cursor.select(QTextCursor.Document)
        cursor.mergeCharFormat(char_format)

    def _rebind_page_rich(self, layer: _PageLayer) -> None:
        """按模型重建一页的文字/图片项；笔迹缓存与富内容互不相干。"""
        for item in layer.text_items:
            self._scene.removeItem(item)
        for item in layer.image_items:
            self._scene.removeItem(item)
        layer.text_items = []
        layer.image_items = []
        page = self.document.pages[layer.index]
        for block in page.texts:
            layer.text_items.append(self._add_text_item(layer, block))
        for image in page.images:
            layer.image_items.append(self._add_image_item(layer, image))

    def _add_text_item(self, layer: _PageLayer, block: TextBlock) -> QGraphicsTextItem:
        item = QGraphicsTextItem(block.text)
        item.document().setDocumentMargin(0)      # 与导出的文字起点对齐
        item.setDefaultTextColor(QColor(block.color))
        self._apply_text_item_font(item, richtext.block_font(block))
        item.setTextWidth(max(1.0, float(block.w)))
        item.setPos(layer.x + float(block.x), layer.y + float(block.y))
        item.setData(0, block)
        item.setAcceptedMouseButtons(Qt.NoButton)
        item.setZValue(TEXT_Z)
        self._scene.addItem(item)
        return item

    def _add_image_item(self, layer: _PageLayer, image: NoteImage):
        """图片按页面局部矩形铺开；文件缺失画灰框占位（右键仍可删）。"""
        path = None
        if self.pdf_path is not None:
            try:
                path = noteassets.asset_path(self.pdf_path, image.asset)
            except ValueError:
                path = None
        pixmap = QPixmap(str(path)) if path is not None and path.is_file() else QPixmap()
        if pixmap.isNull():
            item = QGraphicsRectItem(0.0, 0.0, float(image.w), float(image.h))
            item.setBrush(QColor(120, 120, 120, 35))
            item.setPen(QPen(QColor(160, 160, 160, 140), 1.0, Qt.DashLine))
            item.setToolTip("图片文件缺失；右键可删除")
        else:
            item = QGraphicsPixmapItem(pixmap)
            item.setTransformationMode(Qt.SmoothTransformation)
            size = pixmap.deviceIndependentSize()
            if size.width() > 0 and size.height() > 0:
                item.setTransform(QTransform.fromScale(
                    float(image.w) / size.width(), float(image.h) / size.height()))
        item.setPos(layer.x + float(image.x), layer.y + float(image.y))
        item.setData(0, image)
        item.setAcceptedMouseButtons(Qt.NoButton)
        item.setZValue(IMAGE_Z)
        self._scene.addItem(item)
        return item

    # --- 页面文字编辑 ---

    def is_text_editing(self) -> bool:
        return self._text_editing is not None

    def commit_text_edit(self) -> None:
        """外部收口（保存/导出/切文档）前把正在输入的文字落进模型。"""
        self._commit_text_edit()

    def _text_block_at_scene(self, index: int, scene_point: QPointF):
        if index is None or not 0 <= index < len(self._layers):
            return None
        for item in self._layers[index].text_items:
            if item.boundingRect().translated(item.pos()).contains(scene_point):
                block = item.data(0)
                if isinstance(block, TextBlock):
                    return block
        return None

    def _text_item_for(self, index: int, block: TextBlock):
        for item in self._layers[index].text_items:
            if item.data(0) is block:
                return item
        return None

    def _begin_text_edit(self, page_index: int, existing: TextBlock | None,
                         scene_point: QPointF) -> None:
        """点页面上的文字=编辑它；点空白=在落点新建一块（提交才进模型）。"""
        if self.document is None or not 0 <= page_index < len(self._layers):
            return
        self._cancel_text_edit()
        self._clear_object_selection()
        layer = self._layers[page_index]
        page = self.document.pages[page_index]
        if existing is not None:
            block = existing
        else:
            x = min(max(0.0, scene_point.x() - layer.x), max(0.0, page.width - 40.0))
            y = min(max(0.0, scene_point.y() - layer.y), max(0.0, page.height - 24.0))
            width = max(40.0, min(320.0, page.width - x - 12.0))
            block = TextBlock(uuid.uuid4().hex[:12], x, y, width, "",
                              DEFAULT_PAGE_TEXT_SIZE, self.color)
        editor = QGraphicsTextItem()
        editor.document().setDocumentMargin(0)
        editor.setPlainText(block.text)
        editor.setDefaultTextColor(QColor(block.color))
        self._apply_text_item_font(
            editor, richtext.text_font(
                block.size, getattr(block, "bold", False),
                getattr(block, "italic", False),
                getattr(block, "underline", False)))
        editor.setTextWidth(max(1.0, float(block.w)))
        editor.setPos(layer.x + float(block.x), layer.y + float(block.y))
        editor.setTextInteractionFlags(Qt.TextEditorInteraction)
        editor.setZValue(TEXT_EDIT_Z)
        self._scene.addItem(editor)
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        editor.setTextCursor(cursor)
        editor.setFocus(Qt.MouseFocusReason)

        rendered = self._text_item_for(page_index, block) if existing is not None else None
        if rendered is not None:
            rendered.setVisible(False)
        self._text_editing = editor
        self._text_editing_page = page_index
        self._text_editing_original = existing
        self._text_editing_item = rendered
        self._text_editing_color = block.color
        self._text_editing_size = float(block.size)
        self._text_editing_bold = bool(getattr(block, "bold", False))
        self._text_editing_italic = bool(getattr(block, "italic", False))
        self._text_editing_underline = bool(getattr(block, "underline", False))

    def _commit_text_edit(self) -> None:
        editor, self._text_editing = self._text_editing, None
        if editor is None:
            return
        page_index = self._text_editing_page
        original = self._text_editing_original
        rendered = self._text_editing_item
        color = self._text_editing_color
        bold = self._text_editing_bold
        italic = self._text_editing_italic
        underline = self._text_editing_underline
        self._text_editing_page = None
        self._text_editing_original = None
        self._text_editing_item = None
        self._text_editing_color = None
        self._text_editing_size = DEFAULT_PAGE_TEXT_SIZE
        self._text_editing_bold = False
        self._text_editing_italic = False
        self._text_editing_underline = False
        text = editor.toPlainText().strip()
        layer = (self._layers[page_index]
                 if page_index is not None and 0 <= page_index < len(self._layers)
                 else None)
        editor_pos = editor.pos()
        width = editor.textWidth()
        try:
            self._scene.removeItem(editor)
        except RuntimeError:
            pass
        if rendered is not None:
            try:
                rendered.setVisible(True)
            except RuntimeError:
                pass
        if self.document is None or layer is None:
            return
        page = self.document.pages[page_index]
        before = list(page.texts)
        after = list(before)
        target_id = original.id if original is not None else None
        if not text:
            if original is None:
                return                      # 新块没输入：直接丢弃
            after = [item for item in before if item.id != original.id]
            target_id = None
        elif original is None:
            written = TextBlock(uuid.uuid4().hex[:12],
                                editor_pos.x() - layer.x,
                                editor_pos.y() - layer.y,
                                width, text, DEFAULT_PAGE_TEXT_SIZE,
                                color or self.color, bold, italic, underline)
            after.append(written)
            target_id = written.id
        else:
            updated = replace(original, text=text, color=color or original.color,
                              bold=bold, italic=italic, underline=underline)
            after = [updated if item.id == original.id else item
                     for item in before]
        if after == before:
            return
        cmd = ReplacePageItems(self.document, page_index, "texts", before, after)
        cmd.apply()
        self.history.push(cmd)
        self._rebind_page(page_index)
        target = next((item for item in after if item.id == target_id), None) \
            if target_id is not None else None
        if target is not None:
            self._select_object(page_index, "text", target)
        else:
            self._clear_object_selection()
        self.changed.emit()
        self.strokesChanged.emit()

    def _cancel_text_edit(self) -> None:
        editor, self._text_editing = self._text_editing, None
        if editor is None:
            return
        rendered = self._text_editing_item
        self._text_editing_page = None
        self._text_editing_original = None
        self._text_editing_item = None
        self._text_editing_color = None
        self._text_editing_size = DEFAULT_PAGE_TEXT_SIZE
        self._text_editing_bold = False
        self._text_editing_italic = False
        self._text_editing_underline = False
        try:
            self._scene.removeItem(editor)
        except RuntimeError:
            pass
        if rendered is not None:
            try:
                rendered.setVisible(True)
            except RuntimeError:
                pass

    def _apply_editor_font(self) -> None:
        """编辑器整段换字体，保留光标/选区（Ctrl+B/I/U 实时可见）。"""
        editor = self._text_editing
        if editor is None:
            return
        font = richtext.text_font(
            self._text_editing_size, self._text_editing_bold,
            self._text_editing_italic, self._text_editing_underline)
        char_format = QTextCharFormat()
        char_format.setFont(font)
        cursor = editor.textCursor()
        anchor, position = cursor.anchor(), cursor.position()
        cursor.select(QTextCursor.Document)
        cursor.mergeCharFormat(char_format)
        cursor.setPosition(anchor)
        cursor.setPosition(position, QTextCursor.KeepAnchor)
        editor.setTextCursor(cursor)

    def _toggle_editor_format(self, key: int) -> bool:
        field = TEXT_FORMAT_KEYS.get(key)
        if field is None or self._text_editing is None:
            return False
        setattr(self, f"_text_editing_{field}",
                not getattr(self, f"_text_editing_{field}"))
        self._apply_editor_font()
        return True

    # --- 页面图片 ---

    def insert_image(self, image: QImage, name: str = "图片") -> bool:
        """把图片放进当前页（可见区域中心），资源写进 assets，可撤销。"""
        self.image_error = ""
        if image.isNull():
            self.image_error = "剪贴板/文件里的图片读不出来"
            return False
        if (self.document is None or self._source is None
                or self.pdf_path is None or not self._layers):
            self.image_error = "当前文档不可写"
            return False
        page_index = min(max(0, self.current_page), len(self._layers) - 1)
        layer = self._layers[page_index]
        page = self.document.pages[page_index]
        clone = QImage(image)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        saved = clone.save(buffer, "PNG")
        buffer.close()
        if not saved:
            self.image_error = "图片编码失败"
            return False
        try:
            asset = noteassets.store_png(self.pdf_path, bytes(data))
        except OSError as exc:
            self.image_error = f"无法写入图片资源：{exc}"
            return False

        width = max(1.0, page.width) * 0.45
        height = width * clone.height() / max(1, clone.width())
        max_height = max(1.0, page.height) * 0.8
        if height > max_height:
            width *= max_height / height
            height = max_height
        scene_center = None
        if self._cursor_pos is not None:
            candidate = self.mapToScene(self._cursor_pos)
            if (layer.x <= candidate.x() <= layer.x + layer.width
                    and layer.y <= candidate.y() <= layer.y + layer.height):
                scene_center = candidate
        if scene_center is None:
            scene_center = self.mapToScene(self.viewport().rect().center())
        if (layer.x <= scene_center.x() <= layer.x + layer.width
                and layer.y <= scene_center.y() <= layer.y + layer.height):
            x = scene_center.x() - layer.x - width / 2.0
            y = scene_center.y() - layer.y - height / 2.0
        else:
            x = (page.width - width) / 2.0
            y = (page.height - height) / 2.0
        x = min(max(8.0, x), max(8.0, page.width - width - 8.0))
        y = min(max(8.0, y), max(8.0, page.height - height - 8.0))
        item = NoteImage(uuid.uuid4().hex[:12], x, y, width, height, asset)
        before = list(page.images)
        cmd = ReplacePageItems(self.document, page_index, "images",
                               before, before + [item])
        cmd.apply()
        self.history.push(cmd)
        self._rebind_page(page_index)
        self._select_object(page_index, "image", item)
        self.changed.emit()
        self.strokesChanged.emit()
        return True

    def insert_image_file(self, path) -> bool:
        image = QImage(str(path))
        if image.isNull():
            return False
        return self.insert_image(image, Path(path).name)

    def _image_at_scene(self, scene_point: QPointF):
        for layer in self._layers:
            for item in layer.image_items:
                rect = item.mapRectToScene(
                    QRectF(0.0, 0.0, item.boundingRect().width(),
                           item.boundingRect().height()))
                if rect.contains(scene_point):
                    image = item.data(0)
                    if isinstance(image, NoteImage):
                        return layer.index, image
        return None, None

    def _delete_image(self, page_index: int, image: NoteImage) -> None:
        if self.document is None:
            return
        page = self.document.pages[page_index]
        before = list(page.images)
        after = [item for item in before if item.id != image.id]
        if after == before:
            return
        cmd = ReplacePageItems(self.document, page_index, "images", before, after)
        cmd.apply()
        self.history.push(cmd)
        self._rebind_page(page_index)
        self.changed.emit()
        self.strokesChanged.emit()

    def _delete_text(self, page_index: int, block: TextBlock) -> None:
        if self.document is None:
            return
        page = self.document.pages[page_index]
        before = list(page.texts)
        after = [item for item in before if item.id != block.id]
        if after == before:
            return
        cmd = ReplacePageItems(self.document, page_index, "texts", before, after)
        cmd.apply()
        self.history.push(cmd)
        self._rebind_page(page_index)
        self.changed.emit()
        self.strokesChanged.emit()

    def _change_text_size(self, keys) -> None:
        """字号：单选弹输入框，多选按同一字号批量改；都保留粗/斜/下划线。"""
        blocks = [obj for key in keys
                  if isinstance(obj := self._object_for_key(key), TextBlock)]
        if not blocks:
            return
        current = int(round(blocks[0].size))
        size, ok = QInputDialog.getInt(
            self, "文字字号", "字号（pt）：", current, 6, 96, 1)
        if not ok:
            return
        self._set_text_size(keys, float(size))

    def _set_text_size(self, keys, size: float) -> bool:
        updates = []
        for key in keys:
            block = self._object_for_key(key)
            if isinstance(block, TextBlock):
                updates.append((key, replace(block, size=float(size))))
        return self._replace_objects(updates)

    def _set_text_format(self, keys, field: str, value: bool) -> bool:
        """把粗/斜/下划线写进所有选中文字；图片和未知对象忽略，一条撤销。"""
        if field not in ("bold", "italic", "underline"):
            raise ValueError(f"未知文字格式：{field!r}")
        updates = []
        for key in keys:
            block = self._object_for_key(key)
            if isinstance(block, TextBlock):
                updates.append((key, replace(block, **{field: bool(value)})))
        return self._replace_objects(updates)

    def _toggle_text_format(self, keys, key: int) -> bool:
        """Ctrl+B/I/U：全部已开则关，否则全部打开（混选按 OneNote 习惯）。"""
        field = TEXT_FORMAT_KEYS.get(key)
        if field is None:
            return False
        blocks = [obj for key in keys
                  if isinstance(obj := self._object_for_key(key), TextBlock)]
        if not blocks:
            return False
        value = not all(getattr(block, field, False) for block in blocks)
        return self._set_text_format(keys, field, value)

    def _text_keys(self) -> list[tuple[int, str, str]]:
        return [key for key, obj in self._selected_objects()
                if isinstance(obj, TextBlock)]

    def _text_format_state(self, keys) -> dict[str, bool]:
        """右键菜单勾选态：全部文字都开才勾上。"""
        blocks = [obj for key in keys
                  if isinstance(obj := self._object_for_key(key), TextBlock)]
        return {
            field: bool(blocks) and all(getattr(block, field, False)
                                        for block in blocks)
            for field in ("bold", "italic", "underline")
        }

    # --- 页面对象选择 / 移动 / 缩放（图片、文字）---

    def _selection_object(self):
        """单选时返回那个对象；多选时不返回（手柄/单对象操作只服务单选）。"""
        if len(self._selection) != 1:
            return None
        return self._object_for_key(self._selection[0])

    def _object_for_key(self, key: tuple[int, str, str]):
        page_index, kind, obj_id = key
        if (self.document is None or kind not in ("image", "text")
                or not 0 <= page_index < len(self.document.pages)):
            return None
        page = self.document.pages[page_index]
        source = page.images if kind == "image" else page.texts
        for item in source:
            if item.id == obj_id:
                return item
        return None

    def _selected_objects(self) -> list[tuple[tuple[int, str, str], object]]:
        """当前选择里仍存在于模型中的 (key, 对象)，按选择顺序。"""
        result = []
        for key in self._selection:
            obj = self._object_for_key(key)
            if obj is not None:
                result.append((key, obj))
        return result

    def _object_item(self, page_index: int, kind: str, obj_id: str):
        if not 0 <= page_index < len(self._layers):
            return None
        if kind == "text":
            for item in self._layers[page_index].text_items:
                block = item.data(0)
                if isinstance(block, TextBlock) and block.id == obj_id:
                    return item
            return None
        for item in self._layers[page_index].image_items:
            image = item.data(0)
            if isinstance(image, NoteImage) and image.id == obj_id:
                return item
        return None

    def _object_local_rect(self, page_index: int, kind: str, obj) -> QRectF:
        layer = self._layers[page_index]
        if kind == "image":
            return QRectF(float(obj.x), float(obj.y), float(obj.w), float(obj.h))
        item = self._object_item(page_index, kind, obj.id)
        if item is not None:
            rect = item.sceneBoundingRect()
            return QRectF(rect.x() - layer.x, rect.y() - layer.y,
                          rect.width(), rect.height())
        return QRectF(float(obj.x), float(obj.y), float(obj.w),
                      max(16.0, float(obj.size) * 1.4))

    def _object_scene_rect(self, page_index: int, kind: str, obj) -> QRectF:
        layer = self._layers[page_index]
        local = self._object_local_rect(page_index, kind, obj)
        return local.translated(layer.x, layer.y)

    def _ensure_selection_overlay(self) -> None:
        if self._sel_box is not None and self._sel_box.scene() is self._scene:
            return
        self._sel_box = None
        self._sel_handles = []
        pen = QPen(SELECT_COLOR)
        pen.setCosmetic(True)
        pen.setWidthF(1.5)
        box = QGraphicsRectItem()
        box.setPen(pen)
        box.setBrush(Qt.NoBrush)
        box.setZValue(SELECT_BOX_Z)
        box.setAcceptedMouseButtons(Qt.NoButton)
        self._scene.addItem(box)
        self._sel_box = box
        for _mode in HANDLE_MODES:
            handle = QGraphicsRectItem(-HANDLE_SIZE / 2.0, -HANDLE_SIZE / 2.0,
                                       HANDLE_SIZE, HANDLE_SIZE)
            border = QPen(SELECT_COLOR)
            border.setCosmetic(True)
            handle.setPen(border)
            handle.setBrush(QBrush(QColor("#ffffff")))
            handle.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                           True)
            handle.setZValue(SELECT_HANDLE_Z)
            handle.setAcceptedMouseButtons(Qt.NoButton)
            handle.hide()
            self._scene.addItem(handle)
            self._sel_handles.append(handle)

    def _hide_selection_overlay(self) -> None:
        if self._sel_box is not None:
            try:
                self._sel_box.hide()
            except RuntimeError:
                self._sel_box = None
        for handle in self._sel_handles:
            try:
                handle.hide()
            except RuntimeError:
                pass

    def _clear_object_selection(self) -> None:
        self._selection = []
        self._sel_page = None
        self._sel_kind = None
        self._sel_id = None
        self._obj_drag = None
        self._marquee = None
        self._hide_selection_overlay()
        self.viewport().update()

    def _set_selection(self, keys) -> None:
        """整表换选择：去重、同步单选镜像字段、重画包围框/手柄。"""
        unique: list[tuple[int, str, str]] = []
        for key in keys:
            if key not in unique:
                unique.append(key)
        self._selection = unique
        if len(unique) == 1:
            self._sel_page, self._sel_kind, self._sel_id = unique[0]
        else:
            # 多选不保留单选手柄状态：所有单对象入口（复制/手柄）都让位给批量路径
            self._sel_page = self._sel_kind = self._sel_id = None
        self._update_selection_overlay()

    def _select_object(self, page_index: int, kind: str, obj) -> None:
        self._ensure_selection_overlay()
        self._set_selection([(page_index, kind, obj.id)])

    def _toggle_object_selection(self, page_index: int, kind: str, obj) -> None:
        """Ctrl+单击：该对象在集合里就去掉，不在就加入（可减到空）。"""
        key = (page_index, kind, obj.id)
        if key in self._selection:
            self._set_selection([k for k in self._selection if k != key])
        else:
            self._set_selection(self._selection + [key])

    def _extend_object_selection(self, page_index: int, kind: str, obj) -> None:
        """Shift+单击：只加入、不移除。"""
        self._set_selection(self._selection + [(page_index, kind, obj.id)])

    def _update_selection_overlay(self) -> None:
        if self.tool != "select":
            self._hide_selection_overlay()
            return
        pairs = self._selected_objects()
        if not pairs:
            self._hide_selection_overlay()
            return
        self._ensure_selection_overlay()
        if len(pairs) == 1:
            key, obj = pairs[0]
            rect = self._object_scene_rect(key[0], key[1], obj)
            self._place_selection_overlay(rect, show_handles=True)
        else:
            rect = QRectF()
            for key, obj in pairs:
                part = self._object_scene_rect(key[0], key[1], obj)
                rect = part if rect.isNull() else rect.united(part)
            self._place_selection_overlay(rect, show_handles=False)
        self._sel_box.show()

    def _place_selection_overlay(self, rect: QRectF,
                                 show_handles: bool = True) -> None:
        self._sel_box.setRect(rect)
        if not show_handles:
            for handle in self._sel_handles:
                handle.hide()
            return
        x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
        xs = (x0, (x0 + x1) / 2.0, x1)
        ys = (y0, (y0 + y1) / 2.0, y1)
        positions = {
            "nw": (xs[0], ys[0]), "n": (xs[1], ys[0]), "ne": (xs[2], ys[0]),
            "e": (xs[2], ys[1]), "se": (xs[2], ys[2]), "s": (xs[1], ys[2]),
            "sw": (xs[0], ys[2]), "w": (xs[0], ys[1]),
        }
        for handle, mode in zip(self._sel_handles, HANDLE_MODES):
            if self._sel_kind == "text" and mode not in ("e", "w"):
                handle.hide()
                continue
            handle.setPos(QPointF(*positions[mode]))
            handle.show()

    def _handle_at_view(self, view_pos: QPointF) -> str | None:
        if self._sel_kind is None:
            return None
        for handle, mode in zip(self._sel_handles, HANDLE_MODES):
            if not handle.isVisible():
                continue
            mapped = self.mapFromScene(handle.scenePos())
            if QLineF(QPointF(mapped.x(), mapped.y()),
                      QPointF(view_pos)).length() <= 9.0:
                return mode
        return None

    def _object_at_scene(self, page_index: int, scene_point: QPointF):
        """命中最上面的页面对象：文字优先于图片（与 z 序一致）。"""
        if not 0 <= page_index < len(self._layers):
            return None
        layer = self._layers[page_index]
        for item in reversed(layer.text_items):
            if item.sceneBoundingRect().contains(scene_point):
                return "text", item.data(0), item
        for item in reversed(layer.image_items):
            if item.sceneBoundingRect().contains(scene_point):
                return "image", item.data(0), item
        return None

    @staticmethod
    def _resize_cursor(mode: str) -> Qt.CursorShape:
        return {
            "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
            "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
            "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
            "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
        }.get(mode, Qt.ArrowCursor)

    def _begin_object_drag(self, page_index: int, kind: str, obj,
                           mode: str, scene_point: QPointF) -> None:
        """按下对象：move 时带上整个选择集合（多选拖动），手柄缩放只服务单选。"""
        key = (page_index, kind, obj.id)
        if key not in self._selection:
            self._select_object(page_index, kind, obj)
        keys = list(self._selection) if mode == "move" else [key]
        entries = []
        for target_key in keys:
            target = self._object_for_key(target_key)
            if target is None:
                continue
            rect = QRectF(self._object_local_rect(target_key[0], target_key[1],
                                                  target))
            entries.append({"page": target_key[0], "kind": target_key[1],
                            "id": target_key[2], "orig": rect,
                            "rect": QRectF(rect)})
        if not entries:
            return
        self._obj_drag = {
            "page": page_index,
            "kind": kind,
            "id": obj.id,
            "mode": mode,
            "start": QPointF(scene_point),
            # 单选兼容：原矩形（resize 的几何都从 orig 出发，不再累加位移）
            "rect": QRectF(entries[0]["orig"]),
            "entries": entries,
            "moved": False,
        }
        self.viewport().setCursor(
            Qt.SizeAllCursor if mode == "move" else self._resize_cursor(mode))

    def _apply_live_entry(self, entry: dict, rect: QRectF) -> QRectF | None:
        """把一条拖动中的矩形贴到场景项上；返回该项当前的场景包围盒。"""
        page_index = entry["page"]
        layer = self._layers[page_index]
        item = self._object_item(page_index, entry["kind"], entry["id"])
        if item is None:
            return None
        if entry["kind"] == "image":
            if isinstance(item, QGraphicsPixmapItem):
                size = item.pixmap().deviceIndependentSize()
                if size.width() > 0 and size.height() > 0:
                    item.setTransform(QTransform.fromScale(
                        rect.width() / size.width(), rect.height() / size.height()))
            else:
                item.setRect(0.0, 0.0, rect.width(), rect.height())
            item.setPos(layer.x + rect.x(), layer.y + rect.y())
            return QRectF(layer.x + rect.x(), layer.y + rect.y(),
                          rect.width(), rect.height())
        item.setPos(layer.x + rect.x(), layer.y + rect.y())
        item.setTextWidth(max(20.0, rect.width()))
        return item.sceneBoundingRect()

    def _clamp_move_delta(self, entries: list[dict], dx: float,
                          dy: float) -> tuple[float, float]:
        """整组移动的位移：取所有对象在各自页面内都合法的交集。"""
        if not entries:
            return dx, dy
        lower_x = max(-entry["orig"].x() for entry in entries)
        upper_x = min(max(0.0, self.document.pages[entry["page"]].width
                          - entry["orig"].width()) for entry in entries)
        lower_y = max(-entry["orig"].y() for entry in entries)
        upper_y = min(max(0.0, self.document.pages[entry["page"]].height
                          - entry["orig"].height()) for entry in entries)
        dx = min(max(dx, lower_x), upper_x) if upper_x >= lower_x else upper_x
        dy = min(max(dy, lower_y), upper_y) if upper_y >= lower_y else upper_y
        return dx, dy

    def _drag_object(self, view_pos: QPointF, modifiers) -> None:
        drag = self._obj_drag
        if drag is None:
            return
        start = drag["start"]
        scene_pos = self.mapToScene(view_pos)
        dx = scene_pos.x() - start.x()
        dy = scene_pos.y() - start.y()
        if abs(dx) > 0.6 or abs(dy) > 0.6:
            drag["moved"] = True
        mode = drag["mode"]
        if mode == "move":
            dx, dy = self._clamp_move_delta(drag["entries"], dx, dy)
            scene_rect = QRectF()
            for entry in drag["entries"]:
                rect = entry["orig"].translated(dx, dy)
                entry["rect"] = rect
                part = self._apply_live_entry(entry, rect)
                if part is not None:
                    scene_rect = part if scene_rect.isNull() else scene_rect.united(part)
            if not scene_rect.isNull():
                self._place_selection_overlay(scene_rect, show_handles=False)
                self._sel_box.show()
            return
        # 缩放手柄：只可能作用于单选对象
        entry = drag["entries"][0]
        orig: QRectF = entry["orig"]
        page = self.document.pages[entry["page"]]
        x0, y0 = orig.left(), orig.top()
        x1, y1 = orig.right(), orig.bottom()
        if "w" in mode:
            x0 += dx
        if "e" in mode:
            x1 += dx
        if "n" in mode:
            y0 += dy
        if "s" in mode:
            y1 += dy
        if entry["kind"] == "text":
            y0, y1 = orig.top(), orig.bottom()      # 文字只改宽度
        min_w = 60.0 if entry["kind"] == "text" else 12.0
        if x1 - x0 < min_w:
            if "w" in mode:
                x0 = x1 - min_w
            else:
                x1 = x0 + min_w
        if entry["kind"] == "image" and y1 - y0 < 12.0:
            if "n" in mode:
                y0 = y1 - 12.0
            else:
                y1 = y0 + 12.0
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)
        # 钳在页面内；移动时保持原尺寸
        width = min(rect.width(), max(1.0, page.width))
        height = min(rect.height(), max(1.0, page.height))
        x = min(max(0.0, rect.x()), max(0.0, page.width - width))
        y = min(max(0.0, rect.y()), max(0.0, page.height - height))
        rect = QRectF(x, y, width, height)
        entry["rect"] = rect
        drag["rect"] = rect
        scene_rect = self._apply_live_entry(entry, rect)
        if scene_rect is not None:
            self._place_selection_overlay(scene_rect, show_handles=True)
            self._sel_box.show()

    def _finish_object_drag(self) -> None:
        drag, self._obj_drag = self._obj_drag, None
        if drag is None or self.document is None:
            return
        if not drag["moved"]:
            self._update_selection_overlay()
            self._update_cursor()
            return
        mode = drag["mode"]
        updates = []
        for entry in drag["entries"]:
            key = (entry["page"], entry["kind"], entry["id"])
            obj = self._object_for_key(key)
            if obj is None:
                continue
            rect = entry["rect"]
            if mode == "move":
                updated = replace(obj, x=rect.x(), y=rect.y())
            elif entry["kind"] == "image":
                updated = replace(obj, x=rect.x(), y=rect.y(),
                                  w=max(12.0, rect.width()),
                                  h=max(12.0, rect.height()))
            else:
                updated = replace(obj, x=rect.x(), y=rect.y(),
                                  w=max(60.0, rect.width()))
            updates.append((key, updated))
        if not updates:
            self._clear_object_selection()
            self._update_cursor()
            return
        self._replace_objects(updates)
        self._update_cursor()

    def _entries_for_updates(self, updates) -> list[tuple[int, str, list, list]]:
        """把 (key, 新对象) 按 (页, 字段) 归并成快照条目；同页同字段只出现一次。"""
        if self.document is None:
            return []
        grouped: dict[tuple[int, str], dict[str, object]] = {}
        for key, updated in updates:
            page_index, kind, _obj_id = key
            field = "images" if kind == "image" else "texts"
            grouped.setdefault((page_index, field), {})[updated.id] = updated
        entries = []
        for (page_index, field), by_id in grouped.items():
            if not 0 <= page_index < len(self.document.pages):
                continue
            before = list(getattr(self.document.pages[page_index], field))
            after = [by_id.get(item.id, item) for item in before]
            if after != before:
                entries.append((page_index, field, before, after))
        return entries

    def _apply_entries(self, entries) -> None:
        """应用一批快照：一条命令、一次数据信号，跨页/跨字段也只占一格撤销。"""
        if not entries:
            return
        if len(entries) == 1:
            page_index, field, before, after = entries[0]
            cmd = ReplacePageItems(self.document, page_index, field, before, after)
            pages = {page_index}
        else:
            cmd = ReplaceMany(self.document, entries)
            pages = {page_index for page_index, _field, _b, _a in entries}
        cmd.apply()
        self.history.push(cmd)
        for page_index in pages:
            self._rebind_page(page_index)
        self.changed.emit()
        self.strokesChanged.emit()

    def _replace_objects(self, updates) -> bool:
        """批量替换对象并保持选择；返回是否真的落了一条命令。"""
        entries = self._entries_for_updates(updates)
        if not entries:
            return False
        keys = [key for key, _updated in updates]
        self._apply_entries(entries)
        self._set_selection(keys)
        return True

    def _delete_selected_object(self) -> bool:
        """删除全部选中对象：一条撤销记录（跨页/跨图文也是一格）。"""
        pairs = self._selected_objects()
        if not pairs:
            return False
        removed: dict[tuple[int, str], set[str]] = {}
        for key, obj in pairs:
            field = "images" if key[1] == "image" else "texts"
            removed.setdefault((key[0], field), set()).add(obj.id)
        entries = []
        for (page_index, field), ids in removed.items():
            page = self.document.pages[page_index]
            before = list(getattr(page, field))
            after = [item for item in before if item.id not in ids]
            if after != before:
                entries.append((page_index, field, before, after))
        if not entries:
            return False
        self._clear_object_selection()
        self._apply_entries(entries)
        return True

    def _copy_selected_object(self) -> bool:
        """只复制单选对象；多选的提示在 copy_selection 里统一给。"""
        obj = self._selection_object()
        if obj is None or self._sel_kind is None:
            return False
        if self._sel_kind == "image" and self.pdf_path is not None:
            try:
                path = noteassets.asset_path(self.pdf_path, obj.asset)
            except ValueError:
                return False
            image = QImage(str(path))
            if image.isNull():
                return False
            QGuiApplication.clipboard().setImage(image)
            self.objectCopied.emit("已复制图片；Ctrl+V 可再次插入")
            return True
        if self._sel_kind == "text":
            text = obj.text
            if not text:
                return False
            QGuiApplication.clipboard().setText(text)
            self.selectionCopied.emit(len(text))
            return True
        return False

    def _nudge_selected(self, dx: float, dy: float) -> bool:
        """方向键：全部选中对象一起微调，一条撤销记录。"""
        pairs = self._selected_objects()
        if not pairs:
            return False
        updates = []
        for key, obj in pairs:
            page = self.document.pages[key[0]]
            if key[1] == "image":
                x = min(max(0.0, obj.x + dx), max(0.0, page.width - obj.w))
                y = min(max(0.0, obj.y + dy), max(0.0, page.height - obj.h))
            else:
                x = min(max(0.0, obj.x + dx), max(0.0, page.width - obj.w))
                y = min(max(0.0, obj.y + dy), max(0.0, page.height - obj.size))
            updates.append((key, replace(obj, x=x, y=y)))
        return self._replace_objects(updates)

    # --- Ctrl+左键拖空白：框选页面图文对象 ---

    def _begin_marquee(self, scene_point: QPointF) -> None:
        self._marquee = {"start": QPointF(scene_point),
                         "current": QPointF(scene_point)}
        self.viewport().setCursor(Qt.CrossCursor)
        self.viewport().update()

    def _update_marquee(self, view_pos: QPointF) -> None:
        if self._marquee is None:
            return
        self._marquee["current"] = QPointF(self.mapToScene(view_pos))
        self.viewport().update()

    def _finish_marquee(self) -> None:
        """松手：选中与虚线框相交的所有文字/图片（跨页也行），一条选择集合。"""
        marquee, self._marquee = self._marquee, None
        self._update_cursor()
        self.viewport().update()
        if marquee is None or self.document is None:
            return
        rect = QRectF(marquee["start"], marquee["current"]).normalized()
        keys: list[tuple[int, str, str]] = []
        for layer in self._layers:
            for item in layer.text_items:
                block = item.data(0)
                if (isinstance(block, TextBlock)
                        and rect.intersects(item.sceneBoundingRect())):
                    keys.append((layer.index, "text", block.id))
            for item in layer.image_items:
                image = item.data(0)
                if (isinstance(image, NoteImage)
                        and rect.intersects(item.sceneBoundingRect())):
                    keys.append((layer.index, "image", image.id))
        self._set_selection(keys)

    def _rebind_page(self, index: int) -> None:
        """按模型重建一页的笔迹项；撤销/重做只动命令带着的那一页。

        不变量：已烘进缓存的笔迹不再建矢量项（cache_strokes 与 stroke_items 互补，
        合起来正好是模型里的笔迹）。缓存里若有笔迹已不在模型里（清页/擦掉/撤掉），
        先过滤再整体重渲，否则被清掉的笔迹会继续留在缓存里显示。
        """
        if self.document is None or not 0 <= index < len(self._layers):
            return
        layer = self._layers[index]
        strokes = self.document.pages[index].strokes
        for item in layer.stroke_items:
            self._scene.removeItem(item)
        baked_ids = {id(s) for s in layer.cache_strokes}
        layer.stroke_items = [self._add_stroke_item(layer, stroke)
                              for stroke in strokes if id(stroke) not in baked_ids]
        model_ids = {id(s) for s in strokes}
        if any(id(s) not in model_ids for s in layer.cache_strokes):
            layer.cache_strokes = [s for s in layer.cache_strokes
                                   if id(s) in model_ids]
            # 视口外的页缓存位图已被回收：不重渲，等页回到视口再画
            if self._cache_is_live(layer) or self._layer_on_screen(layer):
                self._render_stroke_cache(layer)
        self._bake_layer(layer)
        self._rebind_page_rich(layer)

    def _update_pen_widths(self) -> None:
        """缩放变了重算场景线宽，保证缩到 10% 时细线仍有 1 屏幕像素。"""
        for layer in self._layers:
            for item in layer.stroke_items:
                stroke = item.data(0)
                if stroke.tool == TEXT_HIGHLIGHT:
                    item.setPen(Qt.NoPen)          # 填充矩形没有描边
                    item.setBrush(_highlight_brush(stroke))
                else:
                    item.setPen(_stroke_pen(stroke, self._zoom))

    def _update_pixmap_transforms(self) -> None:
        for layer in self._layers:
            self._fit_pixmap(layer)

    def _fit_pixmap(self, layer: _PageLayer) -> None:
        """位图按 宽/设备无关图宽 缩放，包围盒正好是 0..页宽、0..页高。

        位图自带 devicePixelRatio，QGraphicsPixmapItem 的包围盒按设备无关尺寸
        （像素数 / DPR）算；仍按物理像素数除会多缩 DPR 倍。
        """
        pixmap = layer.pixmap_item.pixmap()
        if pixmap.isNull() or layer.width <= 0 or layer.height <= 0:
            return
        size = pixmap.deviceIndependentSize()
        if size.width() <= 0 or size.height() <= 0:
            return
        layer.pixmap_item.setTransform(QTransform.fromScale(
            layer.width / size.width(), layer.height / size.height()))

    def _page_pixmap(self, image: QImage, ratio: float | None = None) -> QPixmap:
        """页面位图按“图像像素 / 页面矩形”的真实倍率标 DPR，贴图后正好缩回页面。

        低倍率时图像是超采样过的（`SUPERSAMPLE`），ratio = 渲染倍率 / 缩放，
        Qt 按平滑插值缩回，字形比直接按 1× 栅格更实；笔迹缓存仍按屏幕 DPR 传值。
        """
        pixmap = QPixmap.fromImage(image)
        if ratio is None:
            ratio = self._page_render_scale() / self._zoom if self._zoom > 0 else 1.0
        pixmap.setDevicePixelRatio(ratio)
        return pixmap

    # --- 笔迹位图缓存（v2.2）---

    def _cache_zoom(self) -> float:
        """笔迹缓存的实际像素倍率：逻辑缩放封顶 4×，再乘设备 DPR（副屏 150% 要更密）。"""
        return min(self._zoom, CACHE_MAX_ZOOM) * self._device_ratio()

    def _device_ratio(self) -> float:
        """当前屏幕的设备像素比（主屏 1.0、150% 副屏 1.5、200% 屏 2.0）。"""
        return float(self.viewport().devicePixelRatioF()) or 1.0

    def _page_render_scale(self) -> float:
        """页面底图渲染倍率：逻辑缩放 × DPR，缩放到 350% 都再乘 2× 超采样，最后封顶。

        位图按这个倍率渲染、场景项再按 页宽/位图宽 缩回页面矩形；低倍率时
        图像是超采样后平滑缩回的，字形边缘比直接 1× 栅格更实。
        """
        base = self._zoom * self._device_ratio()
        if self._zoom <= SUPERSAMPLE_MAX_ZOOM:
            base *= SUPERSAMPLE
        return min(base, MAX_RENDER_SCALE)

    def _page_render_ratio(self) -> float:
        """页面位图应标的 DPR：渲染倍率 / 缩放；低倍率超采样时为 2×屏幕 DPR。"""
        return self._page_render_scale() / self._zoom if self._zoom > 0 else 1.0

    def _render_stroke_cache(self, layer: _PageLayer) -> None:
        """把该页已固化笔迹整体烘成一张透明位图（缓存倍率见 `_cache_zoom`），叠在页面底图上。

        位图像素 = 页面点 × 缓存倍率；位图标 DPR 后按设备无关尺寸回缩到页面矩形，
        因此任何缩放下都正好盖住页面，且 1 图像像素对应 1 物理像素。
        """
        cache = layer.cache_item
        if cache is None:
            return
        zoom = self._cache_zoom()
        width = max(1, round(layer.width * zoom))
        height = max(1, round(layer.height * zoom))
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        if layer.cache_strokes and layer.width > 0 and layer.height > 0:
            scale = zoom / self._device_ratio()   # 与增量绘制同一套最小线宽口径
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.scale(zoom, zoom)          # 页面局部点 → 缓存像素
            for stroke in _highlights_first(layer.cache_strokes):
                if id(stroke) in self._erased_cache_ids:
                    continue                 # 拖动中已命中：位图里即时消失，等抬笔落命令
                _paint_cached_stroke(painter, stroke, scale)
            painter.end()
        pixmap = self._page_pixmap(image, self._device_ratio())   # 缓存仍按屏幕 DPR 贴
        cache.setPixmap(pixmap)
        cache.setPos(layer.x, layer.y)
        size = pixmap.deviceIndependentSize()
        cache.setTransform(QTransform.fromScale(
            layer.width / size.width(), layer.height / size.height()))
        layer.cache_zoom = zoom

    def _paint_into_cache(self, layer: _PageLayer, strokes: list[Stroke]) -> None:
        """把新固化的笔迹增量画进现有缓存位图——只花一条笔迹的代价，不重渲整页。"""
        cache = layer.cache_item
        if cache is None or not strokes:
            return
        pixmap = cache.pixmap()
        if pixmap.isNull():
            self._render_stroke_cache(layer)
            return
        zoom = layer.cache_zoom or self._cache_zoom()
        dpr = pixmap.devicePixelRatio() or 1.0
        scale = zoom / dpr                      # QPainter 的用户坐标 = 物理像素 / DPR
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(scale, scale)             # 与整页渲染同一套页面局部坐标
        for stroke in _highlights_first(strokes):
            _paint_cached_stroke(painter, stroke, scale)
        painter.end()
        cache.setPixmap(pixmap)                # 改过位图必须重设一次，场景才知道变了

    def _bake_layer(self, layer: _PageLayer) -> None:
        """尾部矢量笔迹超出 STROKE_TAIL 的部分烘进位图缓存（增量，不整页重建）。

        不变量（_rebind_page/_finish_erase 维护）：尾部项的笔迹都不在缓存里。
        缓存缩放还新鲜 → 增量画；否则整页重渲。抬笔、打开后首次 settle 都走它。
        橡皮拖动期间不烘：被隐藏的尾部项一旦烘进缓存会重新出现。
        """
        if self._erasing:
            return
        excess = len(layer.stroke_items) - STROKE_TAIL
        if excess <= 0:
            return
        fresh = [item.data(0) for item in layer.stroke_items[:excess]]
        live = self._cache_is_live(layer)
        if live and layer.cache_zoom == self._cache_zoom():
            self._paint_into_cache(layer, fresh)
            layer.cache_strokes.extend(fresh)
        else:
            layer.cache_strokes = layer.cache_strokes + fresh
            # 屏幕上的页马上出图（首次烘焙时缓存还是空的，别让新烘的笔迹消失）；
            # 视口外的页已回收位图，跳过分配，回到视口由 _render_visible 统一重画
            if live or self._layer_on_screen(layer):
                self._render_stroke_cache(layer)
        for item in layer.stroke_items[:excess]:
            self._scene.removeItem(item)
        del layer.stroke_items[:excess]

    def render_visible(self) -> None:
        """立刻渲染视口内的页（去抖计时器到点也调它）。"""
        self._render_visible()

    def _render_visible(self) -> None:
        if self._source is None or self.document is None or not self._layers:
            return
        visible = self._visible_scene_rect().adjusted(
            -RENDER_MARGIN, -RENDER_MARGIN, RENDER_MARGIN, RENDER_MARGIN)
        for layer in self._layers:
            if not visible.intersects(_rect_of(layer)):
                continue
            render_scale = self._page_render_scale()
            ratio = self._page_render_ratio()
            if layer.rendered_zoom != render_scale or layer.rendered_ratio != ratio:
                try:
                    image = apply_page_mode(
                        self._source.render(layer.index, render_scale), self.page_mode)
                except PdfError:
                    continue                      # 关文档/页码失效：留旧位图，不崩
                layer.pixmap_item.setPixmap(self._page_pixmap(image, ratio))
                self._fit_pixmap(layer)
                layer.rendered_zoom = render_scale
                layer.rendered_ratio = ratio
            # 缩放变了就把该页笔迹缓存按新缓存倍率重渲（旧的先顶着，不闪空）；
            # 打开后第一次进来也走这条：把打开时建的满尾笔迹烘进缓存
            if layer.cache_zoom != self._cache_zoom() and layer.cache_strokes:
                self._render_stroke_cache(layer)
            self._bake_layer(layer)
        self._evict_offscreen_layers()

    def _evict_offscreen_layers(self) -> None:
        """回收视口外的页面位图与笔迹缓存位图（30 页文档实测约 192MB → 只剩几页）。

        保留集合 = 与 `_render_visible` 同一渲染范围（含 `RENDER_MARGIN`，避免
        "刚预渲下一页又立刻回收"）内的页 ∪ `current_page ± KEEP_PAGE_RANGE`。
        其余页把底图与笔迹缓存置空（`setPixmap(QPixmap())`，释放像素内存），
        并把对应缩放记号清成 None，这样页再回到视口时 `_render_visible` 会按
        当前缩放重新渲染两者。**笔迹数据不动**：`cache_strokes` 保留，回到视口
        直接从模型重画缓存。
        """
        visible = self._visible_scene_rect().adjusted(
            -RENDER_MARGIN, -RENDER_MARGIN, RENDER_MARGIN, RENDER_MARGIN)
        if visible.isEmpty():                 # 视口还没尺寸：判断不了，别乱回收
            return
        current = self.current_page
        keep = set(range(max(0, current - KEEP_PAGE_RANGE),
                         min(len(self._layers) - 1, current + KEEP_PAGE_RANGE) + 1))
        keep.update(layer.index for layer in self._layers
                    if visible.intersects(_rect_of(layer)))
        for layer in self._layers:
            if layer.index in keep:
                continue
            if not layer.pixmap_item.pixmap().isNull():
                layer.pixmap_item.setPixmap(QPixmap())
                layer.rendered_zoom = None
                layer.rendered_ratio = None
            cache = layer.cache_item
            if cache is not None and not cache.pixmap().isNull():
                cache.setPixmap(QPixmap())
                layer.cache_zoom = None
            # cache_strokes / stroke_items 保留：回到视口时按它们重渲

    def _cache_is_live(self, layer: _PageLayer) -> bool:
        """该页笔迹缓存位图是否还在（视口外的页被回收后为 False）。"""
        cache = layer.cache_item
        return (cache is not None and layer.cache_zoom is not None
                and not cache.pixmap().isNull())

    def _layer_on_screen(self, layer: _PageLayer) -> bool:
        """该页是否在严格可见矩形内（回收口径，不含 RENDER_MARGIN）。"""
        return self._visible_scene_rect().intersects(_rect_of(layer))

    def _schedule_render(self) -> None:
        self._render_timer.start()

    def _on_scroll(self, _value: int) -> None:
        self._update_current_page()
        self._schedule_render()

    def _update_current_page(self) -> None:
        if not self._layers:
            return
        index = self.current_page
        if index != self._current_page:
            self._current_page = index
            self.pageChanged.emit(index)

    def _visible_scene_rect(self) -> QRectF:
        viewport = self.viewport()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return QRectF()
        inv, ok = self.viewportTransform().inverted()
        if not ok:
            return QRectF()
        return inv.mapRect(QRectF(viewport.rect()))

    def _page_at(self, point: QPointF) -> int | None:
        for layer in self._layers:
            if _rect_of(layer).contains(point):
                return layer.index
        return None

    # --- 鼠标工具：平移 / 拖图钉 / × 删除 ---

    def _pin_local(self, pin: NotePin, pos: QPointF) -> QPointF:
        """视口坐标 → 图钉自身坐标。

        图钉带 `ItemIgnoresTransformations`，1 图钉单位 = 1 视口像素（与缩放无关），
        所以只减图钉中心的视口位置。`pin.mapFromScene()` 反而不对：它按场景单位给
        差值，缩放到 200% 时会把 14 屏幕像素的偏移算成 7，× 的热区会跟着缩。
        """
        center = self.mapFromScene(pin.scenePos())
        return QPointF(pos.x() - center.x(), pos.y() - center.y())

    def _over_close(self, pin: NotePin, pos: QPointF) -> bool:
        """视口坐标是否压在这枚图钉右上角的 × 上。"""
        return pin.is_over_close(self._pin_local(pin, pos))

    def _pin_at(self, pos: QPointF) -> tuple[NotePin | None, bool]:
        """视口坐标命中的最上层图钉，以及它是否压在 × 上（图钉任何工具都优先）。"""
        if not self._note_pins:
            return None, False
        for item in self.items(pos.toPoint()):
            if isinstance(item, NotePin):
                return item, self._over_close(item, pos)
        return None, False

    def _note_for(self, pin: NotePin) -> Note | None:
        """图钉背后的 Note（可能已被卡片删掉/文档已关，一律返回 None 不抛）。"""
        if self.document is None or not 0 <= pin.page_index < len(self.document.pages):
            return None
        return next((note for note in self.document.pages[pin.page_index].notes
                     if note.id == pin.note_id), None)

    def _hit_text(self, index: int | None, local: Point | None) -> bool:
        """页面局部坐标是否落在可选中文字上（与按下起选同一套容差）。"""
        if index is None or local is None:
            return False
        return self._unit_index_at(self._units(index), local, slack_ratio=0.6) is not None

    def _hit_text_at_view(self, pos: QPointF) -> bool:
        """视口坐标是否落在文字上（鼠标工具的光标判定用）。"""
        if self.document is None:
            return False
        scene_point = self.mapToScene(QPointF(pos))
        index = self._page_at(scene_point)
        if index is None:
            return False
        layer = self._layers[index]
        return self._hit_text(index, (scene_point.x() - layer.x,
                                      scene_point.y() - layer.y))

    def _set_close_hover(self, pin: NotePin | None, over: bool) -> None:
        """× 的高亮跟着指针走：只有当前悬停的那枚图钉点亮，其余一律熄灭。

        图钉 `ItemIgnoresTransformations`，场景自己送来的悬停坐标不除缩放，所以
        热区判定必须由视图按视口坐标算（与按下/松手同一套数学），否则 200% 缩放
        下高亮的位置会比真正的点击位置偏一倍。
        """
        if self._hover_pin is not pin:
            if self._hover_pin is not None:
                self._hover_pin.set_close_hover(False)
            self._hover_pin = pin
        if pin is not None:
            pin.set_close_hover(over)

    def _hover_cursor(self, pos: QPointF) -> Qt.CursorShape:
        """悬停光标：图钉（含 ×）→ 手形；鼠标工具下文字 → I 形、空白/页外 → 张开的手。"""
        pin, over_close = self._pin_at(pos)
        self._set_close_hover(pin, over_close)
        if pin is not None:
            return Qt.PointingHandCursor
        if self.tool == "select":
            mode = self._handle_at_view(pos)
            if mode is not None:
                return self._resize_cursor(mode)
            scene_point = self.mapToScene(pos)
            index = self._page_at(scene_point)
            if (index is not None
                    and self._object_at_scene(index, scene_point) is not None):
                return Qt.SizeAllCursor
            return (Qt.IBeamCursor if self._hit_text_at_view(pos)
                    else Qt.OpenHandCursor)
        if self.tool == "note":
            return Qt.CrossCursor
        if self.tool == "text":
            return Qt.IBeamCursor
        return Qt.BlankCursor

    def _begin_pan(self, pos: QPointF) -> None:
        """按下空白：进入平移。只动滚动条，缩放与自适应页宽一律不碰。"""
        self._panning = True
        self._pan_last = QPointF(pos)
        self.viewport().setCursor(Qt.ClosedHandCursor)

    def _pan_to(self, pos: QPointF) -> None:
        """跟手平移：内容随鼠标走 = 滚动条反向减位移。"""
        delta = pos - (self._pan_last or pos)
        self._pan_last = QPointF(pos)
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        hbar.setValue(hbar.value() - int(round(delta.x())))
        vbar.setValue(vbar.value() - int(round(delta.y())))

    def _end_pan(self) -> None:
        self._panning = False
        self._pan_last = None

    def _begin_pin_press(self, pin: NotePin, pos: QPointF) -> None:
        """按住图钉：先当"可能只是点击"，位移超过阈值才升级成拖动。"""
        self._pin_press = pin
        self._pin_press_pos = QPointF(pos)
        self._pin_dragging = False
        self._set_close_hover(pin, False)         # 按在图钉身上：× 的高亮先撤掉
        self._pin_offset = pin.scenePos() - self.mapToScene(QPointF(pos))

    def _move_pin(self, pos: QPointF) -> None:
        """拖动图钉：只挪它的场景位置，锚点等松手再一次性写回（一次拖动 = 一次提交）。"""
        if QLineF(self._pin_press_pos, pos).length() < DRAG_THRESHOLD_PX:
            return
        pin = self._pin_press
        if pin is None or pin.scene() is not self._scene:
            self._end_pin_press()
            return
        self._pin_dragging = True
        pin.setPos(self.mapToScene(QPointF(pos)) + self._pin_offset)
        self.viewport().setCursor(Qt.SizeAllCursor)

    def _end_pin_press(self) -> None:
        self._pin_press = None
        self._pin_dragging = False
        self._pin_offset = QPointF()

    def _commit_pin_move(self, pin: NotePin) -> None:
        """松手提交：图钉位置换算回页面局部显示坐标、钳进页框写进 Note.x/y。

        `rebuild_note_pins` 会按新锚点重建图钉（序号重排、越界的图钉自动弹回页边），
        再发 `notesChanged` 让接线层标脏 + 自动暂存。
        """
        page_index = pin.page_index
        note = self._note_for(pin)
        if (note is None or self.document is None
                or not 0 <= page_index < len(self._layers)
                or not 0 <= page_index < len(self.document.pages)):
            self.rebuild_note_pins(page_index)      # 文档已换/笔记已删：图钉弹回原锚点
            return
        page = self.document.pages[page_index]
        layer = self._layers[page_index]
        pos = pin.scenePos()
        note.x, note.y = clamp_note_anchor(pos.x() - layer.x, pos.y() - layer.y,
                                           page.width, page.height)
        self.rebuild_note_pins(page_index)
        self.notesChanged.emit(page_index)

    # --- 输入 ---

    def keyPressEvent(self, ev) -> None:
        """文档模式的工具/粗细/复位热键（与草稿画布一致）。

        焦点在视图上才收；不做窗口级 QShortcut，否则搜索框里敲 B/Q/数字
        也会被抢走。
        """
        key = ev.key()
        if self._text_editing is not None:
            if (ev.modifiers() & Qt.ControlModifier
                    and key in TEXT_FORMAT_KEYS):
                self._toggle_editor_format(key)
                ev.accept()
                return
            if key == Qt.Key_Escape:
                self._cancel_text_edit()
                ev.accept()
                return
            super().keyPressEvent(ev)      # 其余键（含 B/Q/数字）都归文字编辑器
            return
        if ev.modifiers() & Qt.ControlModifier and key in TEXT_FORMAT_KEYS:
            text_keys = self._text_keys()
            if text_keys:
                self._toggle_text_format(text_keys, key)
                ev.accept()
                return
        if self._selection:
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                if self._delete_selected_object():
                    ev.accept()
                    return
            if key == Qt.Key_Escape:
                self._clear_object_selection()
                self._update_cursor()
                ev.accept()
                return
            if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                step = 10.0 if ev.modifiers() & Qt.ShiftModifier else 1.0
                dx = -step if key == Qt.Key_Left else (step if key == Qt.Key_Right else 0.0)
                dy = -step if key == Qt.Key_Up else (step if key == Qt.Key_Down else 0.0)
                if self._nudge_selected(dx, dy):
                    ev.accept()
                    return
        if key == Qt.Key_T:
            self.set_tool("text")
            ev.accept()
            return
        if key == Qt.Key_C and ev.modifiers() & Qt.ControlModifier:
            self.copy_selection()
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
        if key == Qt.Key_V:
            self.set_tool("select")
            ev.accept()
            return
        if Qt.Key_1 <= key <= Qt.Key_9:
            index = key - Qt.Key_1
            if index < len(PALETTE):
                self.set_color(PALETTE[index])   # 1–9 = 色板第 1–9 色
                ev.accept()
                return
        if key == Qt.Key_0:
            self.set_zoom(1.0)             # README：0 = 缩放复位 100%
            ev.accept()
            return
        super().keyPressEvent(ev)

    def wheelEvent(self, ev) -> None:
        steps = ev.angleDelta().y() / 120.0
        if steps == 0.0:
            ev.ignore()
            return
        if ev.modifiers() & Qt.ControlModifier:
            self.zoom_at(ev.position(), WHEEL_ZOOM_STEP ** steps)
        elif ev.modifiers() & Qt.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - int(round(steps * bar.singleStep() * 3)))
        else:
            super().wheelEvent(ev)            # 默认：纵向滚动，绝不改缩放
        ev.accept()

    def _add_text_format_actions(self, menu: QMenu, keys) -> None:
        """右键里的整段格式与字号：勾选态按“全部选中文字都开”显示。"""
        state = self._text_format_state(keys)
        for label, field in (("粗体", "bold"), ("斜体", "italic"),
                             ("下划线", "underline")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(state[field])
            action.triggered.connect(
                lambda checked=False, f=field, ks=list(keys):
                self._set_text_format(ks, f, checked))
        size_menu = menu.addMenu("字号")
        for size in TEXT_SIZE_PRESETS:
            action = size_menu.addAction(str(size))
            action.triggered.connect(
                lambda _checked=False, s=size, ks=list(keys):
                self._set_text_size(ks, float(s)))
        size_menu.addSeparator()
        custom = size_menu.addAction("自定义…")
        custom.triggered.connect(
            lambda _checked=False, ks=list(keys): self._change_text_size(ks))

    def contextMenuEvent(self, ev) -> None:
        """右键命中图钉：只发 `noteContextRequested`（页号 / id / 全局坐标）。

        菜单由接线层弹，视图自己不开卡、不开始拖动、不落笔；没命中图钉时
        交回父类（保留 QGraphicsView 的默认右键行为）。
        """
        if self._note_pins:
            pin, _over_close = self._pin_at(QPointF(ev.pos()))
            if pin is not None:
                self.noteContextRequested.emit(pin.page_index, pin.note_id,
                                               ev.globalPos())
                ev.accept()
                return
        if self.document is not None:
            if len(self._selection) > 1:
                # 多选：格式/字号作用于全部文字；不提供复制（规格明确不支持）
                menu = QMenu(self)
                text_keys = self._text_keys()
                if text_keys:
                    self._add_text_format_actions(menu, text_keys)
                    menu.addSeparator()
                act_delete = menu.addAction("删除选中项")
                chosen = menu.exec(ev.globalPos())
                if chosen is act_delete:
                    self._delete_selected_object()
                ev.accept()
                return
            scene_point = self.mapToScene(ev.pos())
            page_index, image = self._image_at_scene(scene_point)
            menu = None
            if image is not None:
                self._select_object(page_index, "image", image)
                menu = QMenu(self)
                act_copy = menu.addAction("复制图片")
                act_delete = menu.addAction("删除图片")
                chosen = menu.exec(ev.globalPos())
                if chosen is act_copy:
                    self._copy_selected_object()
                elif chosen is act_delete:
                    self._delete_selected_object()
                ev.accept()
                return
            index = self._page_at(scene_point)
            block = self._text_block_at_scene(index, scene_point)
            if block is not None and index is not None:
                self._select_object(index, "text", block)
                menu = QMenu(self)
                act_edit = menu.addAction("编辑文字")
                self._add_text_format_actions(menu, self._text_keys())
                menu.addSeparator()
                act_copy = menu.addAction("复制文字")
                act_delete = menu.addAction("删除文字")
                chosen = menu.exec(ev.globalPos())
                if chosen is act_edit:
                    self.set_tool("text")
                    self._begin_text_edit(index, block, scene_point)
                elif chosen is act_copy:
                    self._copy_selected_object()
                elif chosen is act_delete:
                    self._delete_selected_object()
                ev.accept()
                return
        super().contextMenuEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        """鼠标工具双击文字对象 = 进入编辑（OneNote 式）。"""
        if (ev.button() == Qt.LeftButton and self.tool == "select"
                and self.document is not None):
            scene_point = self.mapToScene(ev.position())
            index = self._page_at(scene_point)
            if index is not None:
                hit = self._object_at_scene(index, scene_point)
                if hit is not None and hit[0] == "text":
                    self._begin_text_edit(index, hit[1], scene_point)
                    ev.accept()
                    return
        super().mouseDoubleClickEvent(ev)

    def mousePressEvent(self, ev) -> None:
        # 图钉在任何工具下都优先：按在 × 上走删除，其余位置=开卡或拖动，都不落到笔迹/选字
        if ev.button() == Qt.LeftButton:
            pin, over_close = self._pin_at(ev.position())
            if pin is not None:
                if over_close:
                    # 松手时仍压在 × 上才发删除；这里只 arm，不发任何信号
                    self._close_press = pin
                else:
                    self._begin_pin_press(pin, ev.position())
                ev.accept()
                return
        if ev.button() == Qt.LeftButton and self.document is not None:
            scene_point = self.mapToScene(ev.position())
            index = self._page_at(scene_point)
            if self.tool == "note":
                # 落点只上报页面局部坐标；建 Note/开卡由主窗负责（note 工具不平移）
                if index is not None:
                    layer = self._layers[index]
                    self.notePlacementRequested.emit(
                        index, scene_point.x() - layer.x, scene_point.y() - layer.y)
                ev.accept()
                return
            if self.tool == "text":
                self._commit_text_edit()
                if index is not None:
                    layer = self._layers[index]
                    existing = self._text_block_at_scene(index, scene_point)
                    self._begin_text_edit(index, existing, scene_point)
                ev.accept()
                return
            if self.tool == "select":
                handle = self._handle_at_view(ev.position())
                if handle is not None and self._sel_page is not None:
                    obj = self._selection_object()
                    if obj is not None:
                        self._begin_object_drag(self._sel_page, self._sel_kind,
                                                obj, handle, scene_point)
                        ev.accept()
                        return
                modifiers = ev.modifiers()
                ctrl = bool(modifiers & Qt.ControlModifier)
                shift = bool(modifiers & Qt.ShiftModifier)
                if index is not None:
                    hit = self._object_at_scene(index, scene_point)
                    if hit is not None:
                        kind, obj, _item = hit
                        key = (index, kind, obj.id)
                        if ctrl:
                            self._toggle_object_selection(index, kind, obj)
                            ev.accept()
                            return
                        if shift:
                            self._extend_object_selection(index, kind, obj)
                            ev.accept()
                            return
                        if key not in self._selection:
                            self._select_object(index, kind, obj)
                        self._begin_object_drag(index, kind, obj, "move",
                                                scene_point)
                        ev.accept()
                        return
                self._clear_object_selection()
                layer = self._layers[index] if index is not None else None
                local = (None if layer is None
                         else (scene_point.x() - layer.x, scene_point.y() - layer.y))
                if ctrl and not self._hit_text(index, local):
                    # Ctrl+拖空白 = 框选；普通拖空白仍是平移（页上文字仍可拖选）
                    self.clear_selection()
                    self._begin_marquee(scene_point)
                    ev.accept()
                    return
                if self._hit_text(index, local):
                    self._begin_selection(index, scene_point)
                else:
                    # 空白/页外：鼠标工具就是平移视图，顺手清掉旧选区
                    self.clear_selection()
                    self._begin_pan(ev.position())
                ev.accept()
                return
            if self.tool == "highlighter" and index is not None:
                layer = self._layers[index]
                local = (scene_point.x() - layer.x, scene_point.y() - layer.y)
                if self._unit_index_at(self._units(index), local,
                                       slack_ratio=0.6) is not None:
                    self._reset_gesture()
                    self._begin_selection(index, scene_point, purpose="highlight")
                    ev.accept()
                    return
            if index is not None:
                layer = self._layers[index]
                local = (scene_point.x() - layer.x, scene_point.y() - layer.y)
                self._reset_gesture()
                if self.tool == "eraser":
                    self._begin_erase(index, local)
                else:
                    self._compact_cursor_for_writing()
                    self._drawing = True
                    self._drawing_page = index
                    self._points = [local]
                    pressure = self._mouse_pressure()
                    self._pressures = [pressure] if pressure is not None else []
                    self._tablet_stroke = pressure is not None
                self.viewport().update()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def _begin_selection(self, index: int | None, scene_point: QPointF,
                         purpose: str = "copy") -> None:
        """按下：记下起选词；点在页外则清空选区。

        `purpose="highlight"` 是荧光笔选字：预览用荧光色，松手落成高亮批注；
        `"copy"` 是鼠标工具的蓝色选字，松手后保留选区等 Ctrl+C。
        """
        self._select_page = None
        self._select_anchor = None
        self._selection_words = []
        self._remove_selection_items()
        self._selection_purpose = purpose
        if purpose == "highlight":
            fill = QColor(self.tool_color)
            fill.setAlpha(round(255 * HIGHLIGHTER_OPACITY))
            self._selection_fill = fill
        else:
            self._selection_fill = QColor(SELECT_FILL)
        if index is None:
            self.viewport().update()
            return
        layer = self._layers[index]
        local = (scene_point.x() - layer.x, scene_point.y() - layer.y)
        anchor = self._unit_index_at(self._units(index), local, slack_ratio=0.6)
        self._select_page = index
        self._select_anchor = anchor
        self._select_to(index, local)
        self.viewport().update()

    def _commit_text_highlight(self) -> None:
        """把当前选区按行矩形落成一条选字荧光批注；一次拖动 = 一步撤销。"""
        page_index = self._select_page
        runs = self._selection_runs()
        self.clear_selection()
        if (page_index is None or not runs or self.document is None
                or not 0 <= page_index < len(self._layers)):
            return
        layer = self._layers[page_index]
        points: list[Point] = []
        for x0, y0, x1, y1 in runs:
            # 行框上下各放宽一点，盖住字身与行距，视觉上像真正的荧光笔
            x0 = max(0.0, x0 - 0.5)
            y0 = max(0.0, y0 - 0.8)
            x1 = min(layer.width, x1 + 0.5)
            y1 = min(layer.height, y1 + 0.8)
            points.extend([(x0, y0), (x1, y1)])
        stroke = Stroke(points, self.tool_color, 0.0, TEXT_HIGHLIGHT)
        cmd = AddStrokeToPage(self.document, page_index, stroke)
        cmd.apply()
        self.history.push(cmd)
        layer.stroke_items.append(self._add_stroke_item(layer, stroke))
        self._bake_layer(layer)
        self.changed.emit()
        self.strokesChanged.emit()
        self.viewport().update()

    def mouseMoveEvent(self, ev) -> None:
        # 光标位置每次都更新：按住橡皮/画笔拖动时圆点也要跟着走，不能冻在按下点
        old_cursor = self._cursor_pos
        self._cursor_pos = ev.position()
        if self.tool in ("pen", "eraser", "highlighter"):
            self._update_cursor_region(old_cursor, self._cursor_pos)
        if ev.buttons() & Qt.LeftButton:
            if self._marquee is not None:
                self._update_marquee(ev.position())
                ev.accept()
                return
            if self._obj_drag is not None:
                self._drag_object(ev.position(), ev.modifiers())
                ev.accept()
                return
            if self._pin_press is not None:
                # 拖图钉优先于一切手势：图钉吃掉了按下，就不会同时落笔
                self._move_pin(ev.position())
                ev.accept()
                return
            if self._panning:
                self._pan_to(ev.position())
                ev.accept()
                return
            if (self._select_page is not None
                    and not self._drawing and not self._erasing):
                if self._select_anchor is not None:
                    scene_point = self.mapToScene(ev.position())
                    index = self._page_at(scene_point)
                    if index == self._select_page:
                        layer = self._layers[index]
                        self._select_to(index, (scene_point.x() - layer.x,
                                                scene_point.y() - layer.y))
                ev.accept()
                return
            if self._drawing and self._drawing_page is not None:
                self._append(self._layers[self._drawing_page],
                             self._local_point(ev.position()),
                             self._mouse_pressure())
                ev.accept()
                return
            if self._erasing and self._erase_page is not None:
                self._erase_at(self._local_point(ev.position()))
                ev.accept()
                return
        if (not self._drawing and not self._erasing
                and self._pin_press is None and self._close_press is None):
            # 悬停（无按键）：图钉手形 / 文字 I 形 / 鼠标工具空白处张开的手
            self.viewport().setCursor(self._hover_cursor(ev.position()))
        super().mouseMoveEvent(ev)            # 悬停：只更新光标，不落笔

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            if self._marquee is not None:
                self._update_marquee(ev.position())
                self._finish_marquee()
                ev.accept()
                return
            if self._obj_drag is not None:
                self._finish_object_drag()
                ev.accept()
                return
            if self._close_press is not None:
                pin = self._close_press
                self._close_press = None
                # 松手仍压在同一个 × 上才算删除请求；拖出去了什么都不发
                if (pin.scene() is self._scene
                        and self._pin_at(ev.position())[0] is pin
                        and self._over_close(pin, ev.position())):
                    self.noteDeleteRequested.emit(pin.page_index, pin.note_id)
                self._update_cursor()
                self.viewport().update()
                ev.accept()
                return
            if self._pin_press is not None:
                pin = self._pin_press
                dragged = self._pin_dragging
                self._end_pin_press()
                if dragged:
                    self._commit_pin_move(pin)        # 挪了位置：不开卡，只写回锚点
                elif pin.scene() is self._scene:
                    self.noteRequested.emit(pin.page_index, pin.note_id)
                self._update_cursor()
                self.viewport().update()
                ev.accept()
                return
            if self._panning:
                self._end_pan()
                self._update_cursor()
                ev.accept()
                return
        if ev.button() == Qt.LeftButton and self._select_page is not None:
            if self._selection_purpose == "highlight":
                # 抬笔位置再扩选一次：快速划过去时可能没有落在终点的 move
                scene_point = self.mapToScene(ev.position())
                index = self._page_at(scene_point)
                if index == self._select_page and self._select_anchor is not None:
                    layer = self._layers[index]
                    self._select_to(index, (scene_point.x() - layer.x,
                                            scene_point.y() - layer.y))
                self._commit_text_highlight()
            # 鼠标工具的选区留在原地，等 Ctrl+C 或下一次点击
            ev.accept()
            return
        if ev.button() == Qt.LeftButton and (self._drawing or self._erasing):
            if self._drawing:
                self._finish_stroke(self._local_point(ev.position()),
                                    self._mouse_pressure())
                reset_pressure()
            else:
                # 抬笔位置再采样一次：快速划过去时可能没有落在终点上的 move 事件
                self._erase_at(self._local_point(ev.position()))
                self._finish_erase()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def tabletEvent(self, ev: QTabletEvent) -> None:
        """数位板：钢笔采压力；荧光笔/橡皮恒定宽度。鼠标路径完全不变。"""
        if self.document is None or self.tool not in ("pen", "highlighter", "eraser"):
            ev.ignore()
            return
        old_cursor = self._cursor_pos
        self._cursor_pos = ev.position()
        self._update_cursor_region(old_cursor, self._cursor_pos)
        kind = ev.type()
        pressure = self._tablet_pressure(ev)
        if kind == QEvent.TabletPress and (ev.buttons() & Qt.LeftButton):
            pin, _over_close = self._pin_at(ev.position())
            if pin is not None:
                self.noteRequested.emit(pin.page_index, pin.note_id)
                ev.accept()
                return
            scene_point = self.mapToScene(ev.position())
            index = self._page_at(scene_point)
            if index is None:
                ev.ignore()
                return
            layer = self._layers[index]
            local = (scene_point.x() - layer.x, scene_point.y() - layer.y)
            self._reset_gesture()
            if self.tool == "eraser":
                self._begin_erase(index, local)
            else:
                self._compact_cursor_for_writing()
                self._drawing = True
                self._drawing_page = index
                self._points = [local]
                self._tablet_stroke = pressure is not None
                self._pressures = [pressure] if pressure is not None else []
            self.viewport().update()
            ev.accept()
            return
        if (kind == QEvent.TabletMove and (ev.buttons() & Qt.LeftButton)
                and (self._drawing or self._erasing)):
            if self._drawing and self._drawing_page is not None:
                self._append(self._layers[self._drawing_page],
                             self._local_point(ev.position()), pressure)
            else:
                self._erase_at(self._local_point(ev.position()))
            ev.accept()
            return
        if kind == QEvent.TabletRelease and (self._drawing or self._erasing):
            if self._drawing:
                self._finish_stroke(self._local_point(ev.position()), pressure)
            else:
                self._finish_erase()
            ev.accept()
            return
        ev.ignore()

    def _tablet_pressure(self, ev: QTabletEvent) -> float | None:
        """只在钢笔 + 开关打开时采压力；其它工具返回 None = 恒定宽度。"""
        if not self.pressure_enabled or self.tool != "pen":
            return None
        value = float(ev.pressure())
        if not value > 0.0:
            value = 0.05
        return min(1.0, max(0.0, value))

    def _mouse_pressure(self) -> float | None:
        """鼠标路径下的压力：钢笔 + 开关打开时，直连 WinTab 取笔压。

        Qt6 在部分国产板上把笔识别成普通鼠标（探针实测 0 条 QTabletEvent），
        实际落笔走的是鼠标分支，压力改从驱动挂出的 WinTab 服务直取。
        取不到就是 None = 恒定宽度，与没有压感时的旧行为一致。
        """
        debug_log(f"pdfview._mouse_pressure tool={self.tool} "
                  f"enabled={self.pressure_enabled}")
        if not self.pressure_enabled or self.tool != "pen":
            return None
        value = pen_pressure(self._wintab_hwnd())
        debug_log(f"  -> pen_pressure={value}")
        if value is None:
            return None
        if not value > 0.0:
            value = 0.05
        return min(1.0, max(0.0, value))

    def _wintab_hwnd(self) -> int:
        """WinTab context 绑顶层窗口；不给视图强建原生 HWND（会让 Qt 的 alien
        优化失效且子窗口未必适合绑 context）。只取一次并缓存。"""
        hwnd = getattr(self, "_wintab_hwnd_cache", 0)
        if hwnd:
            return hwnd
        try:
            hwnd = int(self.window().winId())
        except Exception:
            hwnd = 0
        self._wintab_hwnd_cache = hwnd
        return hwnd

    def enterEvent(self, ev) -> None:
        if self.tool in ("pen", "eraser", "highlighter"):
            self.viewport().setCursor(Qt.BlankCursor)
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._cursor_pos = None                  # 移出视图：不画圆点，恢复箭头
        self.viewport().setCursor(Qt.ArrowCursor)
        self.viewport().update()
        super().leaveEvent(ev)

    def dot_radius_px(self) -> float:
        """笔尖圆点的屏幕像素半径：所有画笔粗细统一 6px；橡皮 12px。

        实际线宽/压感不变，光标只表示笔尖位置。
        """
        if self.tool == "eraser":
            return ERASER_RADIUS_PX
        if self.tool == "pen" and (self._drawing or self._cursor_compact):
            return WRITING_CURSOR_RADIUS_PX
        return CURSOR_RADIUS_PX

    def _cursor_update_rect(self, pos: QPointF | None) -> QRectF:
        if pos is None:
            return QRectF()
        # 用最大半径+余量：圆点由大变小/变小时旧区域也能一起擦掉
        radius = max(ERASER_RADIUS_PX, CURSOR_RADIUS_PX) + 3.0
        return QRectF(pos.x() - radius, pos.y() - radius,
                      radius * 2.0, radius * 2.0)

    def _update_cursor_region(self, *positions: QPointF | None) -> None:
        """只刷光标圆点附近，避免鼠标每动一下就重绘整页。"""
        rect = QRectF()
        for pos in positions:
            rect = rect.united(self._cursor_update_rect(pos))
        if not rect.isEmpty():
            self.viewport().update(rect.toAlignedRect())

    def _update_scene_region(self, scene_rect: QRectF) -> None:
        if scene_rect.isEmpty():
            return
        polygon = self.mapFromScene(scene_rect)
        rect = polygon.boundingRect().adjusted(-2, -2, 2, 2)
        self.viewport().update(rect)

    def _update_stroke_region(self, layer: _PageLayer,
                              p0: Point, p1: Point) -> None:
        margin = self._tool_width(layer) * self._zoom / 2.0 + 6.0
        a = QPointF(layer.x + p0[0], layer.y + p0[1])
        b = QPointF(layer.x + p1[0], layer.y + p1[1])
        scene_rect = QRectF(a, b).normalized().adjusted(
            -margin, -margin, margin, margin)
        self._update_scene_region(scene_rect)

    def _compact_cursor_for_writing(self) -> None:
        if self.tool != "pen":
            return
        self._cursor_restore_timer.stop()
        if not self._cursor_compact:
            self._cursor_compact = True
            self._update_cursor_region(self._cursor_pos)

    def _schedule_cursor_restore(self) -> None:
        if self.tool != "pen":
            return
        self._cursor_restore_timer.start()

    def _restore_cursor_size(self) -> None:
        if self._cursor_compact:
            self._cursor_compact = False
            self._update_cursor_region(self._cursor_pos)

    def drawForeground(self, painter: QPainter, rect) -> None:
        """未固化笔迹的实时预览 + 笔尖圆点（按印在页面上的位置画，不参与命中测试）。"""
        if self._drawing and self._points and self._drawing_page is not None:
            layer = self._layers[self._drawing_page]
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.translate(layer.x, layer.y)
            # 预览夹在页面矩形内：跨页拖笔时笔迹（含粗笔的圆头）不会漏到下一页
            painter.setClipRect(QRectF(0.0, 0.0, layer.width, layer.height))
            if self._pressures and len(self._pressures) == len(self._points):
                preview = Stroke(list(self._points), self.tool_color,
                                 self._tool_width(layer), self.tool,
                                 list(self._pressures))
                draw_ink_stroke(painter, preview)
            else:
                opacity = HIGHLIGHTER_OPACITY if self.tool == "highlighter" else 1.0
                painter.setPen(_make_pen(self.tool_color, self._tool_width(layer),
                                         self._zoom, opacity))
                path = self._preview_path
                if path is None:
                    path = _stroke_path(self._points)
                painter.drawPath(path)
            painter.restore()
        if self._marquee is not None:
            # Ctrl+拖空白的框选：虚线蓝框 + 极淡填充（只画在前景，不接收鼠标）
            rect = QRectF(self._marquee["start"],
                          self._marquee["current"]).normalized()
            pen = QPen(QColor(SELECT_COLOR))
            pen.setCosmetic(True)
            pen.setWidthF(1.2)
            pen.setStyle(Qt.DashLine)
            fill = QColor(SELECT_COLOR)
            fill.setAlpha(28)
            painter.save()
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawRect(rect)
            painter.restore()
        if self._cursor_pos is None or self.tool not in ("pen", "eraser", "highlighter"):
            return
        if self._zoom <= 0:
            return
        inv, ok = self.viewportTransform().inverted()
        if not ok:
            return
        center = inv.map(self._cursor_pos)
        radius = self.dot_radius_px() / self._zoom
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.tool == "eraser":
            # 橡皮：白色光晕 + 红色轮廓；白页/夜间/护眼底色上都清楚
            halo = QPen(QColor(ERASER_HALO_COLOR))
            halo.setWidthF(4.0 / self._zoom)
            painter.setPen(halo)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, radius, radius)
            ring = QPen(QColor(ERASER_RING_COLOR))
            ring.setWidthF(2.0 / self._zoom)
            painter.setPen(ring)
        else:
            fill = QColor(self.tool_color)
            fill.setAlpha(204)                   # 80%：不遮住落点
            # 浅色笔（默认 #e8eaed）在白页上会看不见，补一圈深色描边
            edge = QPen(QColor(0, 0, 0, 110))
            edge.setWidthF(max(0.5, 1.0 / self._zoom))
            painter.setPen(edge)
            painter.setBrush(fill)
        painter.drawEllipse(center, radius, radius)
        painter.restore()

    def viewportEvent(self, ev) -> bool:
        """滚动条出现/消失也改视口尺寸（不带 view.resizeEvent），这里补 fit。"""
        handled = super().viewportEvent(ev)
        if (ev.type() == QEvent.Resize and getattr(self, "_auto_fit", False)
                and not getattr(self, "_fitting", False)
                and getattr(self, "_layers", None)):
            self.fit_width()
        return handled

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if getattr(self, "_layers", None) is None:      # 构造期回调，模型还没建
            return
        if self._auto_fit:
            self.fit_width()                # 自适应期间窗口变了就重新铺满
        self._update_current_page()
        self._schedule_render()

    def showEvent(self, ev) -> None:
        """窗口换屏（DPR 1.0 ↔ 1.5）时重渲：页面底图按设备像素渲染才不糊。"""
        super().showEvent(ev)
        handle = self.window().windowHandle()
        if handle is not None and not self._screen_connected:
            handle.screenChanged.connect(lambda *_args: self._schedule_render())
            self._screen_connected = True
        self._schedule_render()

    def clear_page(self) -> None:
        """清空当前页（可撤销）。"""
        if self.document is None or not self._layers:
            return
        self._commit_text_edit()
        index = self.current_page
        self._reset_gesture()
        cmd = ClearPage(self.document, index)
        cmd.apply()
        self.history.push(cmd)
        self._rebind_page(index)
        self.changed.emit()
        self.strokesChanged.emit()

    # --- 落笔 / 橡皮 ---

    def _effective_width(self, layer: _PageLayer) -> float:
        """工具栏粗细（UI 像素）→ PDF 点：固定按 900px 页宽折算，再乘 0.5。

        基准必须与窗口/当前缩放无关：以前用“当前视口宽 / 页宽”，同一个 3 在
        宽窗口落笔记 2.0pt、窄窗口落笔记 3.7pt，之后一起看粗细就对不上。
        0.5 是用户 2026-10-25 反馈“3 都很大”后的整体倍率；旧笔迹存的是点宽，
        不受影响。
        """
        return pen_points_for(getattr(layer, "width", 0.0) * PEN_WIDTH_SCALE,
                              self.width)

    def _tool_width(self, layer: _PageLayer) -> float:
        """当前工具落笔的线宽（PDF 点）：荧光笔固定按同一基准约 16 屏幕像素。"""
        if self.tool != "highlighter":
            return self._effective_width(layer)
        return pen_points_for(getattr(layer, "width", 0.0), HIGHLIGHTER_WIDTH)

    def _local_point(self, pos: QPointF) -> Point:
        if self._drawing:
            layer = self._layers[self._drawing_page]
        else:
            layer = self._layers[self._erase_page]
        scene_point = self.mapToScene(QPointF(pos))
        return (scene_point.x() - layer.x, scene_point.y() - layer.y)

    @staticmethod
    def _inside(layer: _PageLayer, point: Point) -> bool:
        """点是否落在页面矩形内（页边留 `PAGE_TOLERANCE` 容差）。"""
        return (-PAGE_TOLERANCE <= point[0] <= layer.width + PAGE_TOLERANCE
                and -PAGE_TOLERANCE <= point[1] <= layer.height + PAGE_TOLERANCE)

    @staticmethod
    def _clamp(layer: _PageLayer, point: Point) -> Point:
        """把点钳回页面矩形：抬笔落在页外时用它，笔迹数据永远不越出页框。"""
        return (min(max(point[0], 0.0), layer.width),
                min(max(point[1], 0.0), layer.height))

    def _append(self, layer: _PageLayer, point: Point,
                pressure: float | None = None) -> None:
        """只在起始页矩形内追点：跨页拖笔的页外移动直接丢弃，笔迹停在页边。"""
        if len(self._points) >= MAX_POINTS_PER_STROKE:
            return                        # 到上限停手，抬笔仍正常成条
        if not self._inside(layer, point):
            return                        # 越过页框（页间空隙/下一页）：不把点带出去
        if self._points and point == self._points[-1]:
            return
        self._points.append(point)
        if self._tablet_stroke:
            self._pressures.append(pressure if pressure is not None else 0.5)
        else:
            if self._preview_path is None:
                self._preview_path = QPainterPath(QPointF(*self._points[0]))
            self._preview_path.lineTo(QPointF(*point))
        # 只刷这一小段；整页重绘在 120/144Hz 笔输入下会明显掉帧
        if len(self._points) >= 2:
            self._update_stroke_region(layer, self._points[-2], self._points[-1])
        else:
            self._update_cursor_region(self._cursor_pos)

    def _finish_stroke(self, end: Point, pressure: float | None = None) -> None:
        points = self._points
        pressures = self._pressures or None
        page_index = self._drawing_page
        self._points = []
        self._preview_path = None
        self._pressures = []
        self._drawing = False
        self._drawing_page = None
        self._schedule_cursor_restore()
        layer = (self._layers[page_index]
                 if page_index is not None and 0 <= page_index < len(self._layers)
                 else None)
        if points and layer is not None:
            if len(points) < MAX_POINTS_PER_STROKE:
                # 抬笔可能已在页外（跨页拖笔）：终点钳回起始页，导出才不会被裁
                end = self._clamp(layer, end)
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
        if points and layer is not None and self.document is not None:
            stroke = Stroke(points, self.tool_color, self._tool_width(layer), self.tool,
                            pressures)
            cmd = AddStrokeToPage(self.document, page_index, stroke)
            cmd.apply()
            self.history.push(cmd)
            layer.stroke_items.append(self._add_stroke_item(layer, stroke))
            self._bake_layer(layer)             # 尾部超长就增量烘一条进缓存
            self.changed.emit()
            self.strokesChanged.emit()
        self.viewport().update()

    def _begin_erase(self, index: int, local: Point) -> None:
        self._erasing = True
        self._erase_page = index
        layer = self._layers[index]
        # 候选 = 尾部矢量项 + 已烘进缓存的笔迹；每条预先算页面局部包围盒，
        # 拖动时先用 O(1) 盒子排除，再对少数命中候选细算距离。
        pairs = ([(item.data(0), item) for item in layer.stroke_items]
                 + [(stroke, None) for stroke in layer.cache_strokes])
        self._candidates = [(stroke, item, self._stroke_local_bbox(stroke))
                            for stroke, item in pairs]
        self._erased = []
        self._erase_at(local)

    def _stroke_local_bbox(self, stroke: Stroke) -> QRectF:
        if not stroke.points:
            return QRectF()
        xs = [point[0] for point in stroke.points]
        ys = [point[1] for point in stroke.points]
        margin = float(stroke.width) / 2.0 + 2.0
        return QRectF(min(xs) - margin, min(ys) - margin,
                      max(xs) - min(xs) + margin * 2.0,
                      max(ys) - min(ys) + margin * 2.0)

    def _erase_at(self, local: Point) -> None:
        """擦除只作用于起始页内的点：拖到页间空隙/下一页的移动直接忽略。"""
        if self._erase_page is None:
            return
        if not self._inside(self._layers[self._erase_page], local):
            return                          # 跨页拖橡皮：下一页面一个字都不动
        tol = max(self.width, 6.0) / 2.0        # 命中半径，与画布一致
        point = QPointF(*local)
        layer = self._layers[self._erase_page]
        dirty = QRectF()
        center = self.mapFromScene(QPointF(layer.x + local[0], layer.y + local[1]))
        dirty = dirty.united(self._cursor_update_rect(
            QPointF(center.x(), center.y())))
        remaining: list[tuple[Stroke, QGraphicsPathItem | None, QRectF]] = []
        cache_dirty = False
        for stroke, item, bbox in self._candidates:
            # 先用预计算包围盒排除绝大多数无关笔迹（缓存笔迹没有 item 可借力）
            if not bbox.adjusted(-tol, -tol, tol, tol).contains(point):
                remaining.append((stroke, item, bbox))
                continue
            hit = stroke_distance(stroke, local) <= tol
            if hit:
                self._erased.append((stroke, item))
                if item is not None:
                    item.setVisible(False)      # 矢量项：隐藏 item 即时生效
                    dirty = dirty.united(item.sceneBoundingRect())
                else:
                    # 缓存笔迹没有独立 item：把它从缓存位图里剔除并立刻重渲，
                    # 否则要等抬笔才消失（用户报的"松开后才清除"）
                    self._erased_cache_ids.add(id(stroke))
                    cache_dirty = True
                    if stroke.points:
                        xs = [p[0] for p in stroke.points]
                        ys = [p[1] for p in stroke.points]
                        margin = float(stroke.width) * self._zoom / 2.0 + 4.0
                        scene_rect = QRectF(layer.x + min(xs) - margin,
                                            layer.y + min(ys) - margin,
                                            max(xs) - min(xs) + margin * 2.0,
                                            max(ys) - min(ys) + margin * 2.0)
                        dirty = dirty.united(self.mapFromScene(scene_rect).boundingRect())
            else:
                remaining.append((stroke, item, bbox))
        self._candidates = remaining
        if cache_dirty:
            self._render_stroke_cache(layer)
        if not dirty.isEmpty():
            self.viewport().update(dirty.toAlignedRect())

    def _finish_erase(self) -> None:
        hits = self._erased
        page_index = self._erase_page
        had_cache_hits = bool(self._erased_cache_ids)
        self._erased = []
        self._candidates = []
        self._erasing = False
        self._erase_page = None
        self._erased_cache_ids.clear()
        if not hits or self.document is None or page_index is None:
            # 手势没收成（文档已关等）：把拖动中临时剔除的缓存笔迹画回来
            if had_cache_hits and page_index is not None and 0 <= page_index < len(self._layers):
                self._render_stroke_cache(self._layers[page_index])
            self.viewport().update()
            return
        cmd = EraseStrokesFromPage(self.document, page_index,
                                   [stroke for stroke, _ in hits])
        cmd.apply()
        self.history.push(cmd)
        layer = self._layers[page_index]
        for stroke, item in hits:
            if item is not None:
                if item in layer.stroke_items:
                    layer.stroke_items.remove(item)
                self._scene.removeItem(item)
        # 擦掉的若有已烘进缓存的：缓存失效，按剩余笔迹整体重渲（一次划过的代价）
        hit_ids = {id(s) for s, _ in hits}
        if any(id(s) in hit_ids for s in layer.cache_strokes):
            layer.cache_strokes = [s for s in layer.cache_strokes
                                   if id(s) not in hit_ids]
            self._render_stroke_cache(layer)
        self.changed.emit()
        self.strokesChanged.emit()
        self.viewport().update()

    def _reset_gesture(self) -> None:
        # 中断的手势（丢抬笔/换工具/撤销）：把隐藏的矢量项和临时剔除的缓存笔迹都恢复
        for _stroke, item in self._erased:
            if item is not None and item.scene() is self._scene:
                item.setVisible(True)
        page_index = self._erase_page
        had_cache_hits = bool(self._erased_cache_ids)
        self._drawing = False
        self._drawing_page = None
        self._points = []
        self._preview_path = None
        self._pressures = []
        self._tablet_stroke = False
        self._erasing = False
        self._schedule_cursor_restore()
        self._erase_page = None
        self._candidates = []
        self._erased = []
        self._erased_cache_ids.clear()
        self._end_pan()
        self._end_pin_press()
        self._close_press = None
        if had_cache_hits and page_index is not None and 0 <= page_index < len(self._layers):
            self._render_stroke_cache(self._layers[page_index])

    def _after_history_change(self, cmd) -> None:
        """撤销/重做后只重绑命令带着的那一页；手势一律作废。"""
        self._reset_gesture()
        self._clear_object_selection()
        page_index = getattr(cmd, "page_index", None)
        if page_index is None:
            for index in range(len(self._layers)):
                self._rebind_page(index)
        else:
            self._rebind_page(page_index)
        self.changed.emit()
        self.strokesChanged.emit()


def _rect_of(layer: _PageLayer) -> QRectF:
    return QRectF(layer.x, layer.y, layer.width, layer.height)
