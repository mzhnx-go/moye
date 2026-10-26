"""PDF 页上的笔记图钉：常屏幕尺寸的小便签图标 + 序号角标 + 下方名字标签。

图钉只是**入口**：点了发信号，真正打开卡片由接线层（主窗）决定。因此它
不接收鼠标按键（`Qt.NoButton`），由 `PdfView` 用 `items()` 命中后转发，
避免与视图自己的落笔/选择手势抢事件。

`ItemIgnoresTransformations` 让图标不随 PDF 缩放变大变小（始终约 24px）；
`setPos` 仍是场景坐标（PDF 点），图标以锚点为中心。

**名字标签**：画在图钉正下方（`笔记 N` 或用户自定义名），`boundingRect`
按标签的最大宽度预留，但 `shape()` 仍只包含 28×28 图钉本体——标签只是
展示，不扩大点击/拖拽的热区（点在标签上等于点在空白页面上）。

图钉自己不管拖动与删除：它只提供 **× 热区的几何**（`close_rect` /
`is_over_close`）供 `PdfView` 命中判定，× 的亮/红由 `set_close_hover` 由外而内地
驱动（图钉忽略视图变换，场景给的悬停坐标不除缩放，只有视图知道指针的真实位置）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

from sketchpad.ui import icons, theme

PIN_SIZE = 28          # 命中/绘制包围盒（屏幕像素；ItemIgnoresTransformations）
ICON_SIZE = 22         # 便签图标逻辑尺寸，比包围盒略小留一圈边
BADGE_RADIUS = 5.5     # 右下角序号圆点
CLOSE_RADIUS = 6.5     # 右上角 × 的圆形热区半径（图钉坐标 ≈ 屏幕像素）
CLOSE_CENTER = QPointF(PIN_SIZE / 2.0 - CLOSE_RADIUS,
                       -(PIN_SIZE / 2.0 - CLOSE_RADIUS))    # (7.5, -7.5)
CLOSE_COLOR = "#ff453a"
PIN_Z = 4              # 压在页面底图、高亮、笔迹（z ≤ 2）之上

# 名字标签：图钉下方居中，宽度上限 96px（中文按像素截断），高度 14px。
LABEL_MAX_WIDTH = 96.0
LABEL_PAD_X = 4.0      # 文字与圆角底左右各留一点
LABEL_GAP = 2.0        # 图钉底边到标签的间隙
LABEL_HEIGHT = 14.0
LABEL_TOP = PIN_SIZE / 2.0 + LABEL_GAP          # 16.0：图钉下沿再往下 2px
LABEL_FONT_PX = 9
LABEL_BG = QColor(32, 33, 36, 204)              # 与图钉同色系，约 80% 不透明
LABEL_FG = QColor("#ffffff")


class NotePin(QGraphicsItem):
    """一个笔记锚点的图钉项；属性由 `PdfView.rebuild_note_pins` 维护。"""

    def __init__(self, note_id: str, page_index: int, number: int,
                 parent: QGraphicsItem | None = None, name: str = "") -> None:
        super().__init__(parent)
        self.note_id = note_id
        self.page_index = page_index
        self.number = number
        self.name = name or ""
        self._hover = False
        self._close_hover = False         # 悬停在 × 上：删除键变红
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setAcceptedMouseButtons(Qt.NoButton)   # 点击由 PdfView 统一命中转发
        self.setAcceptHoverEvents(True)
        self.setZValue(PIN_Z)

    # --- 几何 ---

    def pin_rect(self) -> QRectF:
        """28×28 图钉本体的包围盒（图钉自身坐标，中心在原点）。"""
        half = PIN_SIZE / 2.0
        return QRectF(-half, -half, PIN_SIZE, PIN_SIZE)

    def boundingRect(self) -> QRectF:
        """图钉本体 + 下方名字标签的最大占位（标签宽度按上限预留，重绘不漏）。

        高度恒定（不随名字长短变），所以 `set_name` 不需要 prepareGeometryChange。
        """
        half = PIN_SIZE / 2.0
        bottom = LABEL_TOP + LABEL_HEIGHT
        return QRectF(-LABEL_MAX_WIDTH / 2.0, -half, LABEL_MAX_WIDTH, half + bottom)

    def shape(self) -> QPainterPath:
        """命中/悬停只看 28×28 图钉本体：名字标签不扩大点击热区。"""
        path = QPainterPath()
        path.addRoundedRect(self.pin_rect(), 6.0, 6.0)
        return path

    def close_rect(self) -> QRectF:
        """× 的方形外接框（图钉坐标）；命中判定用圆，绘制用这个框定位。"""
        return QRectF(CLOSE_CENTER.x() - CLOSE_RADIUS, CLOSE_CENTER.y() - CLOSE_RADIUS,
                      CLOSE_RADIUS * 2.0, CLOSE_RADIUS * 2.0)

    def is_over_close(self, point: QPointF) -> bool:
        """图钉自身坐标的 `point` 是否落在右上角 × 的圆形热区内。

        图钉忽略视图变换，所以 1 图钉单位 ≈ 1 视口像素：`PdfView` 把视口坐标
        减去图钉中心映射过来即可直接判定（按下与松手都要判，防误删）。
        场景自己的悬停事件**不**除缩放，热区高亮因此由 `set_close_hover` 外部驱动。
        """
        dx = point.x() - CLOSE_CENTER.x()
        dy = point.y() - CLOSE_CENTER.y()
        return dx * dx + dy * dy <= CLOSE_RADIUS * CLOSE_RADIUS

    def set_close_hover(self, over: bool) -> None:
        """指针是否正好压在 × 上（由 `PdfView` 按视口坐标判定）：变了才重画。"""
        over = bool(over)
        if over != self._close_hover:
            self._close_hover = over
            self.update()

    def set_name(self, name: str) -> None:
        """外部改名（app 里重命名后即时刷新）：值变了才重画；标签几何固定。"""
        name = name or ""
        if name != self.name:
            self.name = name
            self.update()

    def label_text(self) -> str:
        """标签实际显示的文字：空名回退「笔记 N」（与 core 的兜底规则一致）。"""
        return self.name.strip() or f"笔记 {self.number}"

    # --- 绘制 ---

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 深色圆角底：白色便签线稿在白页上也看得见；悬停换强调色描边
        body = QRectF(-12.5, -12.5, 25.0, 25.0)
        painter.setBrush(QColor(32, 33, 36, 235 if self._hover else 220))
        painter.setPen(QPen(QColor(theme.ACCENT if self._hover else "#8e8e93"),
                            1.6 if self._hover else 1.0))
        painter.drawRoundedRect(body, 6.0, 6.0)

        icon = icons.note_icon()
        pixmap = icon.pixmap(QSize(ICON_SIZE, ICON_SIZE))
        if not pixmap.isNull():
            # 按设备无关尺寸贴图：QIcon 已按目标尺寸与 DPR 裁好
            painter.drawPixmap(QPointF(-ICON_SIZE / 2.0, -ICON_SIZE / 2.0), pixmap)

        # 右下角序号角标：一眼看出这一页有几张笔记、点的是第几张
        center = QPointF(PIN_SIZE / 2.0 - BADGE_RADIUS - 0.5,
                         PIN_SIZE / 2.0 - BADGE_RADIUS - 0.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawEllipse(center, BADGE_RADIUS, BADGE_RADIUS)
        painter.setPen(QColor("#ffffff"))
        font = QFont()
        font.setPixelSize(8 if self.number < 10 else 6)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(center.x() - BADGE_RADIUS, center.y() - BADGE_RADIUS,
                                BADGE_RADIUS * 2, BADGE_RADIUS * 2),
                         Qt.AlignCenter, str(self.number))

        self._paint_close(painter)
        self._paint_label(painter)
        painter.restore()

    def _paint_label(self, painter: QPainter) -> None:
        """图钉正下方的名字标签：小圆角深色底 + 白字，超宽按像素省略号截断。"""
        font = QFont()
        font.setPixelSize(LABEL_FONT_PX)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        # elidedText 按像素算宽度：中文/英文/混排都卡在标签上限内
        text = metrics.elidedText(self.label_text(), Qt.ElideRight,
                                  int(LABEL_MAX_WIDTH - LABEL_PAD_X * 2))
        width = min(LABEL_MAX_WIDTH,
                    float(metrics.horizontalAdvance(text)) + LABEL_PAD_X * 2)
        rect = QRectF(-width / 2.0, LABEL_TOP, width, LABEL_HEIGHT)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(LABEL_BG))
        painter.drawRoundedRect(rect, 4.0, 4.0)
        painter.setPen(QColor(LABEL_FG))
        painter.drawText(rect, Qt.AlignCenter, text)

    def _paint_close(self, painter: QPainter) -> None:
        """右上角的删除键：常态半透明灰圆 + 白 ×，整枚图钉悬停时更亮，
        指针正好压在 × 上时整圆转红——一眼看出这一点下去会删掉笔记。"""
        alpha = 235 if self._close_hover else (190 if self._hover else 120)
        bg = QColor(CLOSE_COLOR if self._close_hover else "#636366")
        bg.setAlpha(alpha)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(CLOSE_CENTER, CLOSE_RADIUS, CLOSE_RADIUS)
        reach = CLOSE_RADIUS - 2.4
        stroke = QPen(QColor("#ffffff"), 1.5, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(stroke)
        for sign in (-1.0, 1.0):
            painter.drawLine(QPointF(CLOSE_CENTER.x() - reach,
                                     CLOSE_CENTER.y() - sign * reach),
                             QPointF(CLOSE_CENTER.x() + reach,
                                     CLOSE_CENTER.y() + sign * reach))

    # --- 悬停 ---

    def hoverEnterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hover = False
        self._close_hover = False
        self.update()
        super().hoverLeaveEvent(event)
