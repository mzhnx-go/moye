"""Standard-only: mind-map viewer.

Supported: FreeMind .mm, OPML, XMind (.xmind: Zen content.json / XMind 8
content.xml), MindManager .mmap, MindNode .mindnode (zip or macOS package),
iThoughts .itmz. Parsing lives in `mindmap_parse.py`; every failure is a
MindMapError so the app can clean up the half-open session.

Read-only, kind="text": the workspace hides annotation tools and page
navigation. Nodes are laid out left-to-right in a QGraphicsScene; outline,
find and zoom reuse the same contract as the text reader.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import (QGraphicsScene, QGraphicsTextItem,
                               QGraphicsView, QVBoxLayout, QWidget)

from sketchpad.reader.mindmap_parse import (MindMapError, MindNode,
                                            parse_mindmap)

H_GAP = 70
V_GAP = 26
MARGIN = 60
MAX_TEXT_WIDTH = 220
NODE_PADDING = 10
MIN_READABLE_FIT = 0.6      # 整图适配低于 60% 时不自动缩小，改用 100% + 滚动
ROOT_FILL = "#2d3436"
INK = "#1c1c1e"
WHITE = "#ffffff"
TINT_AMOUNT = 0.90           # 三级及更深：分支色只留 10%
BRANCH_COLORS = ("#7b61ff", "#fa541c", "#2f80ed", "#27ae60",
                 "#eb5757", "#13c2c2", "#f2994a", "#e84393")
HIT_FILL = "#ffec99"
HIT_CURRENT = "#ffd43b"
HIT_BORDER = "#f59f00"
HIT_CURRENT_BORDER = "#e67700"

MAP_QSS = """
QGraphicsView#MindMapCanvas { background: #ffffff; border: none; }
QGraphicsView#MindMapCanvas QScrollBar:vertical {
    background: #f4f4f6; width: 12px; margin: 0;
}
QGraphicsView#MindMapCanvas QScrollBar::handle:vertical {
    background: #c7c9cf; min-height: 30px; border-radius: 5px; margin: 1px;
}
QGraphicsView#MindMapCanvas QScrollBar::handle:vertical:hover { background: #aeb0b6; }
QGraphicsView#MindMapCanvas QScrollBar:horizontal {
    background: #f4f4f6; height: 12px; margin: 0;
}
QGraphicsView#MindMapCanvas QScrollBar::handle:horizontal {
    background: #c7c9cf; min-width: 30px; border-radius: 5px; margin: 1px;
}
QGraphicsView#MindMapCanvas QScrollBar::handle:horizontal:hover { background: #aeb0b6; }
QGraphicsView#MindMapCanvas QScrollBar::add-line,
QGraphicsView#MindMapCanvas QScrollBar::sub-line { width: 0; height: 0; }
"""


def _tint(color: QColor, amount: float = TINT_AMOUNT) -> QColor:
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(round(color.red() * (1 - amount) + 255 * amount),
                  round(color.green() * (1 - amount) + 255 * amount),
                  round(color.blue() * (1 - amount) + 255 * amount))


def flatten(root: MindNode) -> list[MindNode]:
    nodes = [root]
    for child in root.children:
        nodes.extend(flatten(child))
    return nodes


class _MapCanvas(QGraphicsView):
    zoomedByWheel = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.map_scene = QGraphicsScene(self)
        self.setScene(self.map_scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setObjectName("MindMapCanvas")
        self.setStyleSheet(MAP_QSS)
        self.setBackgroundBrush(QBrush(QColor("#ffffff")))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.node_items: dict[int, tuple[QGraphicsPathItem, QGraphicsTextItem]] = {}
        self.node_styles: dict[int, tuple[QColor, QColor, QColor, float]] = {}
        self._fit_scale = 1.0

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.zoomedByWheel.emit(factor)
            event.accept()
            return
        super().wheelEvent(event)

    def fit_all(self) -> None:
        rect = self.map_scene.itemsBoundingRect().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
        if rect.isEmpty():
            return
        self.resetTransform()
        self.fitInView(rect, Qt.KeepAspectRatio)
        transform = self.transform()
        self._fit_scale = max(transform.m11(), 1e-6)

    def fit_scale(self) -> float:
        """整图塞进视口需要多小的比例；视口还没布局时按 800×600 估算。"""
        rect = self.map_scene.itemsBoundingRect().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
        if rect.isEmpty():
            return 0.0
        vw = max(1, self.viewport().width() or 800)
        vh = max(1, self.viewport().height() or 600)
        return min(vw / max(1.0, rect.width()), vh / max(1.0, rect.height()))

    def show_readable(self, focus_item=None) -> None:
        """大图不要缩成看不见：100% 显示，居中根节点，靠滚动看其他分支。"""
        self.resetTransform()
        self._fit_scale = 1.0
        if focus_item is not None:
            self.centerOn(focus_item)


class MindMapView(QWidget):
    kind = "text"
    SUFFIXES = (".mm", ".opml", ".xmind", ".mmap", ".mindnode", ".itmz")

    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    changed = Signal()
    strokesChanged = Signal()
    selectionCopied = Signal(int)
    notePlacementRequested = Signal(int, float, float)
    noteRequested = Signal(int, str)
    noteDeleteRequested = Signal(int, str)
    notesChanged = Signal(int)
    noteContextRequested = Signal(int, str, QPoint)

    def __init__(self, settings=None, parent=None) -> None:
        super().__init__(parent)
        self._path: Path | None = None
        self._root: MindNode | None = None
        self._nodes: list[MindNode] = []
        self._zoom = 1.0
        self._auto_fit = True
        self._hits: list[int] = []
        self._current_hit = 0
        self.tool = "select"
        self.width = getattr(settings, "width", 2.0) if settings else 2.0
        self.color = getattr(settings, "color", "#e8eaed") if settings else "#e8eaed"
        self.highlighter_color = getattr(settings, "highlighter_color", "#d4a017") \
            if settings else "#d4a017"
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = _MapCanvas(self)
        self.canvas.zoomedByWheel.connect(
            lambda factor: self.set_zoom(self._zoom * factor))
        self._layout.addWidget(self.canvas)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocusProxy(self.canvas)

    # --- document ---

    def open(self, path, *, ignore_sidecar=False, document_override=None) -> None:
        path = Path(path)
        root = parse_mindmap(path)
        self._path = path
        self._root = root
        self._nodes = flatten(root)
        self._hits = []
        self._build_scene()
        # 230 节点的导图整图适配会缩到 6%，肉眼就是空白；大图默认 100%
        # 居中根节点，靠滚动查看；小图仍整图适配。
        if self.canvas.fit_scale() >= MIN_READABLE_FIT:
            self.canvas.fit_all()
            self._auto_fit = True
        else:
            root_item = self.canvas.node_items.get(0)
            self.canvas.show_readable(root_item[0] if root_item else None)
            self._auto_fit = False
        self._zoom = 1.0
        self.pageChanged.emit(0)
        self.zoomChanged.emit(self._zoom)
        self.changed.emit()

    def close_document(self) -> None:
        self.canvas.map_scene.clear()
        self.canvas.node_items.clear()
        self._path = None
        self._root = None
        self._nodes = []
        self._hits = []
        self.pageChanged.emit(0)

    @property
    def pdf_path(self):
        return self._path

    @property
    def path(self):
        return self._path

    @property
    def source(self):
        return None

    @property
    def document(self):
        return None

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    # --- layout ---

    @staticmethod
    def _font_for_depth(depth: int) -> QFont:
        font = QFont("Microsoft YaHei")
        if depth == 0:
            font.setPointSize(14)
            font.setBold(True)
        elif depth == 1:
            font.setPointSize(12)
            font.setBold(True)
        elif depth == 2:
            font.setPointSize(11)
        else:
            font.setPointSize(10)
        return font

    def _measure(self, node: MindNode) -> tuple[float, float]:
        metrics = QFontMetrics(self._font_for_depth(node.depth))
        rect = metrics.boundingRect(
            QRect(0, 0, MAX_TEXT_WIDTH, 4000),
            int(Qt.TextWordWrap) | int(Qt.AlignLeft), node.title)
        return (max(80.0, rect.width() + 2 * NODE_PADDING),
                max(32.0, rect.height() + 2 * NODE_PADDING))

    @staticmethod
    def _branch_colors(root: MindNode) -> dict[int, QColor]:
        """每个一级分支一种颜色，整条分支的连线/边框继承它。"""
        result: dict[int, QColor] = {}
        for branch_index, child in enumerate(root.children):
            color = QColor(BRANCH_COLORS[branch_index % len(BRANCH_COLORS)])
            stack = [child]
            while stack:
                node = stack.pop()
                result[node.index] = color
                stack.extend(node.children)
        return result

    def _style_for(self, node: MindNode, branch: QColor | None):
        """返回 (填充, 边框, 文字, 线宽)：一级实心、二级白底彩边、三级淡底。"""
        if node.depth == 0 or branch is None:
            return QColor(ROOT_FILL), QColor(ROOT_FILL), QColor(WHITE), 1.0
        if node.depth == 1:
            return QColor(branch), branch.darker(108), QColor(WHITE), 1.0
        if node.depth == 2:
            return QColor(WHITE), QColor(branch), QColor(INK), 1.8
        return _tint(branch), QColor(branch), QColor(INK), 1.2

    def _build_scene(self) -> None:
        scene = self.canvas.map_scene
        scene.clear()
        self.canvas.node_items.clear()
        self.canvas.node_styles.clear()
        if self._root is None:
            return
        branch_colors = self._branch_colors(self._root)
        sizes: dict[int, tuple[float, float]] = {}
        for node in self._nodes:
            sizes[node.index] = self._measure(node)
        depth_widths: dict[int, float] = {}
        for node in self._nodes:
            depth_widths[node.depth] = max(
                depth_widths.get(node.depth, 0.0), sizes[node.index][0])
        x_at: dict[int, float] = {}
        x = 0.0
        for depth in range(max(depth_widths) + 1):
            x_at[depth] = x
            x += depth_widths.get(depth, 0.0) + H_GAP
        next_y = [0.0]
        positions: dict[int, QPointF] = {}

        def place(node: MindNode) -> float:
            if node.children:
                centers = [place(child) for child in node.children]
                y = (min(centers) + max(centers)) / 2.0
            else:
                y = next_y[0]
                next_y[0] += sizes[node.index][1] + V_GAP
            positions[node.index] = QPointF(x_at[node.depth], y)
            return y

        place(self._root)
        for node in self._nodes:
            center = positions[node.index]
            width, height = sizes[node.index]
            rect = QRectF(center.x(), center.y() - height / 2.0, width, height)
            branch = branch_colors.get(node.index)
            fill, border, ink, pen_width = self._style_for(node, branch)

            # 轻阴影：白底上区分卡片层级，不做逐图元 QGraphicsEffect
            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(rect.translated(2, 3), 8, 8)
            shadow = scene.addPath(shadow_path, QPen(Qt.NoPen),
                                   QBrush(QColor(0, 0, 0, 24)))
            shadow.setZValue(-2)

            path = QPainterPath()
            path.addRoundedRect(rect, 8, 8)
            item = scene.addPath(path, QPen(border, pen_width), QBrush(fill))
            text = QGraphicsTextItem(node.title)
            text.setDefaultTextColor(ink)
            text.setFont(self._font_for_depth(node.depth))
            text.setTextWidth(MAX_TEXT_WIDTH)
            text.setPos(rect.left() + NODE_PADDING,
                        rect.top() + (height - text.boundingRect().height()) / 2.0)
            scene.addItem(text)
            self.canvas.node_items[node.index] = (item, text)
            self.canvas.node_styles[node.index] = (
                QColor(fill), QColor(border), QColor(ink), float(pen_width))

            if node.parent is not None:
                parent_pos = positions[node.parent.index]
                parent_width, _ = sizes[node.parent.index]
                start = QPointF(parent_pos.x() + parent_width, parent_pos.y())
                end = QPointF(rect.left(), center.y())
                edge = QPainterPath(start)
                mid = (start.x() + end.x()) / 2.0
                edge.cubicTo(QPointF(mid, start.y()), QPointF(mid, end.y()), end)
                edge_color = branch_colors.get(node.index, QColor(ROOT_FILL))
                edge_item = scene.addPath(
                    edge, QPen(edge_color, 1.7, Qt.SolidLine, Qt.RoundCap))
                edge_item.setZValue(-1)
        scene.setSceneRect(scene.itemsBoundingRect())

    # --- navigation ---

    def goto_page(self, anchor: int) -> None:
        try:
            index = int(anchor)
        except (TypeError, ValueError):
            return
        item = self.canvas.node_items.get(index)
        if item is None:
            return
        self.canvas.centerOn(item[0])

    def scroll_to_top(self) -> None:
        """没有阅读位置时的初始位置：居中根节点（大图 100% 时尤其重要）。"""
        root = self.canvas.node_items.get(0)
        if root is not None and not self._auto_fit:
            self.canvas.centerOn(root[0])
            return
        self.canvas.verticalScrollBar().setValue(0)
        self.canvas.horizontalScrollBar().setValue(0)

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        try:
            zoom = float(zoom)
        except (TypeError, ValueError):
            return
        zoom = min(4.0, max(0.25, zoom))
        if abs(zoom - self._zoom) < 1e-6:
            return
        factor = zoom / self._zoom
        self.canvas.scale(factor, factor)
        self.canvas._fit_scale *= factor
        self._zoom = zoom
        self._auto_fit = False
        self.zoomChanged.emit(zoom)

    def fit_width(self) -> None:
        self.canvas.fit_all()
        self._auto_fit = True
        self._zoom = 1.0
        self.zoomChanged.emit(1.0)

    def current_position(self):
        bar = self.canvas.verticalScrollBar()
        maximum = max(1, bar.maximum())
        return {"page": 0, "offset": bar.value() / maximum,
                "zoom": self._zoom, "auto_fit": False}

    def restore_position(self, page, offset, zoom, auto_fit) -> None:
        try:
            self.set_zoom(float(zoom))
        except (TypeError, ValueError):
            pass
        try:
            ratio = min(1.0, max(0.0, float(offset)))
        except (TypeError, ValueError):
            ratio = 0.0
        bar = self.canvas.verticalScrollBar()
        bar.setValue(round(ratio * bar.maximum()))

    def verticalScrollBar(self):
        return self.canvas.verticalScrollBar()

    # --- outline / find ---

    def outline(self) -> list[tuple[int, str, int]]:
        return [(node.depth + 1, node.title, node.index) for node in self._nodes]

    def search(self, text: str, max_hits: int = 500) -> list[tuple[int, int]]:
        needle = (text or "").strip().lower()
        if not needle:
            return []
        hits = [(0, node.index) for node in self._nodes
                if needle in node.title.lower()]
        return hits[:max_hits]

    def _restore_node_style(self, index: int) -> None:
        pair = self.canvas.node_items.get(index)
        style = self.canvas.node_styles.get(index)
        if pair is None or style is None:
            return
        item, text = pair
        fill, border, ink, width = style
        item.setBrush(QBrush(QColor(fill)))
        item.setPen(QPen(QColor(border), width))
        text.setDefaultTextColor(QColor(ink))

    def show_hits(self, page: int, offsets: list[int], current: int = 0) -> None:
        for index in list(self.canvas.node_items):
            self._restore_node_style(index)
        self._hits = [int(value) for value in offsets]
        for index in self._hits:
            pair = self.canvas.node_items.get(index)
            if pair is not None:
                pair[0].setBrush(QBrush(QColor(HIT_FILL)))
                pair[0].setPen(QPen(QColor(HIT_BORDER), 2.0))
                pair[1].setDefaultTextColor(QColor(INK))
        if self._hits:
            self._current_hit = current % len(self._hits)
            target = self.canvas.node_items.get(self._hits[self._current_hit])
            if target is not None:
                target[0].setBrush(QBrush(QColor(HIT_CURRENT)))
                target[0].setPen(QPen(QColor(HIT_CURRENT_BORDER), 2.2))
                target[1].setDefaultTextColor(QColor(INK))
                self.canvas.centerOn(target[0])

    def clear_hits(self) -> None:
        self._hits = []
        for index in list(self.canvas.node_items):
            self._restore_node_style(index)

    # --- compatibility API (same surface as TextViewer) ---

    def status_text(self) -> str:
        scale = max(1e-6, self.canvas.transform().m11())
        return (f"思维导图 · {len(self._nodes)} 个节点 · "
                f"缩放 {scale * 100:.0f}%")

    def copy_selection(self) -> bool:
        return False

    def clear_selection(self) -> None:
        return None

    def set_tool(self, tool: str) -> None:
        self.tool = tool

    def set_color(self, color: str) -> None:
        self.color = color

    def set_highlighter_color(self, color: str) -> None:
        self.highlighter_color = color

    def set_width(self, width: float) -> None:
        self.width = width

    def set_pressure_enabled(self, enabled: bool) -> None:
        return None

    def set_ink_smoothing(self, level: str) -> None:
        return None

    def set_page_mode(self, mode: str) -> None:
        return None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._root is not None and self._auto_fit:
            self.canvas.fit_all()
