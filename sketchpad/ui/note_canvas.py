"""笔记卡片画布：横线纸背景 + 复用 SketchView 的落笔/橡皮/撤销，外加富内容。

本模块只认识 `Note` / `Settings` / `pdf_path`，不依赖卡片/遮罩：卡片负责尺寸与
工具栏，画布负责把 560x720 的设计坐标整页适配进自己的视口（fit_page），并把
`note.texts / images / attachments` 投影成图元。

坐标约定：富内容与笔迹一样，全部是**设计坐标**（相对卡片左上角）；卡片/窗口的
缩放只影响显示。素材一律经 `noteassets.asset_path(pdf_path, 纯文件名)` 解析，
`pdf_path` 为 None 时图片直接跳过、附件画成「附件缺失」，模型数据原样保留。

鼠标路由：落在文字/图片/附件上的按下/移动/松开/双击**直接交给 QGraphicsView**
（绕开 SketchView 的落笔逻辑），其它位置保持原有落笔/橡皮语义。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (QBuffer, QIODevice, QLineF, QPoint, QPointF, QRectF,
                            Qt, QUrl)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics,
                           QGuiApplication, QImage, QPainter, QPainterPath,
                           QPen, QPixmap, QTextCursor, QTransform)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPixmapItem, QGraphicsTextItem,
                               QGraphicsView)

from sketchpad.core import noteassets
from sketchpad.core.notes import (DEFAULT_TEXT_COLOR, NOTE_PAPERS, Note,
                                  NoteAttachment, NoteImage, TextBlock, new_item_id)
from sketchpad.core.settings import WIDTHS, Settings
from sketchpad.ui.toolbox import PALETTE
from sketchpad.core.strokes import Document, Point
from sketchpad.ui.canvas import SketchScene, SketchView

PAPER_BG = {"dark": "#2b2c2f", "light": "#f6f1e6"}
PAPER_LINE = {"dark": "#4a4b50", "light": "#d8cdb8"}
LINE_GAP = 28.0      # 横线行距（设计坐标）
MARGIN_X = 42.0      # 左侧页边线位置（设计坐标）

# 富内容图元的层序：都压在笔迹（z=0）下面，写字/贴图不会盖住刚画的重点
TEXT_Z = -0.2
IMAGE_Z = -0.4
ATTACH_Z = -0.2

TEXT_DEFAULT_W = 360.0           # 新文字块的默认行宽（设计坐标）
IMAGE_MAX_W, IMAGE_MAX_H = 260.0, 320.0   # 插入图片的显示上限（设计坐标）
ATTACH_W, ATTACH_H = 220.0, 46.0          # 附件小卡片尺寸（设计坐标）
SELECT_COLOR = "#8ab4f8"         # 选中描边（浅蓝，深浅纸上都看得见）
MISSING_COLOR = "#9aa0a6"        # 附件/图片缺失时的灰字
MISSING_TEXT = "附件缺失"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif",
                  ".tiff"}


def _human_size(size: int) -> str:
    """字节数 → 人读的大小（未知按 0 处理，显示「未知大小」）。"""
    value = float(size or 0)
    if value <= 0.0:
        return "未知大小"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


def _ink_for(paper: str) -> str:
    """纸面上的正文颜色：深纸浅字、浅纸深字（默认文字色是深纸那一套）。"""
    return DEFAULT_TEXT_COLOR if paper == "dark" else "#2c2c2e"


class NoteScene(SketchScene):
    """横线纸背景的笔迹场景。线宽用 cosmetic 画笔，缩放到任何比例都是 1 屏幕像素。

    `rebind()` 会把场景里所有图元清掉再按笔迹重建，富内容图元也会跟着消失；所以
    视图在这里挂一个 `on_rebind` 回调，清完立刻按模型重建文字/图片/附件。
    """

    def __init__(self, document: Document, paper: str = "dark") -> None:
        super().__init__(document)
        self.paper = paper if paper in NOTE_PAPERS else "dark"
        self.on_rebind = None
        self.setBackgroundBrush(QColor(PAPER_BG[self.paper]))

    def set_paper(self, paper: str) -> None:
        paper = paper if paper in NOTE_PAPERS else "dark"
        if paper == self.paper:
            return
        self.paper = paper
        self.setBackgroundBrush(QColor(PAPER_BG[paper]))
        self.update()

    def rebind(self) -> None:
        """先按笔迹整体重建，再让视图把富内容图元补回来（模型是唯一真相）。"""
        super().rebind()
        callback = self.on_rebind
        if callback is not None:
            callback()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        """整块铺纸色，再画横线与竖向页边线。

        `SketchScene` 只设了 backgroundBrush，QGraphicsScene 默认实现不会画线圈，
        所以这里必须整体接管（fillRect 覆盖 backgroundBrush 的效果）。
        """
        painter.save()
        painter.fillRect(rect, QColor(PAPER_BG[self.paper]))
        pen = QPen(QColor(PAPER_LINE[self.paper]))
        pen.setCosmetic(True)       # 按设备像素走：缩小整页后线仍然清晰、不因 scale 变粗
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        # 只画可见行，多算一条，避免 rect 边界上的半行
        start = int(rect.top() // LINE_GAP) - 1
        end = int(rect.bottom() // LINE_GAP) + 2
        for index in range(start, end):
            y = index * LINE_GAP
            painter.drawLine(QLineF(rect.left(), y, rect.right(), y))
        painter.drawLine(QLineF(MARGIN_X, rect.top(), MARGIN_X, rect.bottom()))
        painter.restore()


# --- 富内容图元 ---


class TextBlockItem(QGraphicsTextItem):
    """一段文字：可选中、可拖动、双击进入编辑；改动实时写回 `TextBlock`。

    「空文本且失焦」直接从模型里删掉——否则页面上会留下一块看不见、却还能被
    选中的空图元，用户既删不掉也不知道它在那儿。
    """

    is_rich = True

    def __init__(self, block: TextBlock, view: "NoteView") -> None:
        super().__init__(block.text)
        self.block = block
        self.view = view
        self._editing = False
        self._removed = False

        font = QFont()
        font.setPointSizeF(max(1.0, float(block.size)))
        self.setFont(font)
        self.setDefaultTextColor(QColor(block.color or DEFAULT_TEXT_COLOR))
        self.setTextWidth(max(1.0, float(block.w)))
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.ItemIsFocusable, True)
        self.setZValue(TEXT_Z)
        self.setToolTip("双击编辑文字")
        self.setPos(float(block.x), float(block.y))
        self.document().contentsChanged.connect(self._on_contents_changed)

    @property
    def item_id(self) -> str:
        return self.block.id

    # --- 模型写回 ---

    def _on_contents_changed(self) -> None:
        """每次输入都写回模型：卡片据此自动暂存，不用等失焦。"""
        if self._removed:
            return
        text = self.toPlainText()
        if text == self.block.text:
            return
        self.block.text = text
        self.view.notify_changed()

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and not self._removed:
            pos = self.pos()
            if self.block.x != pos.x() or self.block.y != pos.y():
                self.block.x, self.block.y = pos.x(), pos.y()
                self.view.notify_changed()
        return result

    # --- 编辑态 ---

    @property
    def editing(self) -> bool:
        return self._editing

    def begin_edit(self) -> None:
        if self._removed or self._editing:
            return
        self._editing = True
        # 编辑时先关掉拖动：在字里按下是「放光标/选字」，不是搬块
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus(Qt.MouseFocusReason)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)
        # 清屏键默认是 E：编辑文字时按 E 不能把整张卡片的笔迹清空
        self.view.set_clear_shortcut_enabled(False)

    def end_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.view.set_clear_shortcut_enabled(True)
        if self._removed:
            return
        text = self.toPlainText()
        if not text.strip():
            self.view.discard_empty_text(self)
            return
        if text != self.block.text:
            self.block.text = text
            self.view.notify_changed()

    # --- 事件 ---

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and not self._editing:
            self.begin_edit()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def focusOutEvent(self, ev) -> None:
        super().focusOutEvent(ev)
        self.end_edit()

    def keyPressEvent(self, ev) -> None:
        # 编辑中 Esc 收笔；其余按键照 QGraphicsTextItem 的默认行为走
        if ev.key() == Qt.Key_Escape and self._editing:
            self.end_edit()
            self.view.setFocus(Qt.OtherFocusReason)
            ev.accept()
            return
        super().keyPressEvent(ev)


class NoteImageItem(QGraphicsPixmapItem):
    """一张笔记图片：原图不重采样，用 transform 拉到模型给的 w/h。"""

    is_rich = True

    def __init__(self, image: NoteImage, view: "NoteView", pixmap: QPixmap) -> None:
        super().__init__(pixmap)
        self.image = image
        self.view = view
        self._removed = False
        width = pixmap.width() or 1
        height = pixmap.height() or 1
        self.setTransformationMode(Qt.SmoothTransformation)
        self.setTransform(QTransform.fromScale(max(1.0, float(image.w)) / width,
                                               max(1.0, float(image.h)) / height))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(IMAGE_Z)
        self.setToolTip(f"图片 {image.asset}")
        self.setPos(float(image.x), float(image.y))

    @property
    def item_id(self) -> str:
        return self.image.id

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and not self._removed:
            pos = self.pos()
            if self.image.x != pos.x() or self.image.y != pos.y():
                self.image.x, self.image.y = pos.x(), pos.y()
                self.view.notify_changed()
        return result


class AttachmentItem(QGraphicsItem):
    """附件标记：一张圆角小卡片（回形针 + 文件名 + 大小），双击用系统程序打开。

    附件本体不在 sidecar 里，只记 assets 目录内的文件名；文件丢了照样画一块
    灰字卡片——用户得能看见、能选中、能删掉这条记录。
    """

    is_rich = True

    def __init__(self, attachment: NoteAttachment, view: "NoteView",
                 missing: bool = False) -> None:
        super().__init__()
        self.attachment = attachment
        self.view = view
        self.missing = bool(missing)
        self._removed = False
        self._rect = QRectF(0.0, 0.0, ATTACH_W, ATTACH_H)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(ATTACH_Z)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self._tooltip())
        self.setPos(float(attachment.x), float(attachment.y))

    def _tooltip(self) -> str:
        if self.missing:
            return f"{self.attachment.name}\n{MISSING_TEXT}（双击无动作，Delete 可删除）"
        return f"{self.attachment.name}\n双击用系统程序打开"

    @property
    def item_id(self) -> str:
        return self.attachment.id

    # --- 几何/绘制 ---

    def boundingRect(self) -> QRectF:
        return self._rect

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self._rect, 8.0, 8.0)
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        dark = getattr(self.view.scene, "paper", "dark") == "dark"
        rect = self._rect.adjusted(0.75, 0.75, -0.75, -0.75)
        fill = QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 14)
        border = QColor(255, 255, 255, 92) if dark else QColor(0, 0, 0, 82)
        if self.isSelected():
            border = QColor(SELECT_COLOR)
        painter.setPen(QPen(border, 2.0 if self.isSelected() else 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 8.0, 8.0)

        ink = QColor(MISSING_COLOR if self.missing else _ink_for(
            "dark" if dark else "light"))
        clip = QColor(MISSING_COLOR if self.missing else ink)
        _draw_paperclip(painter, 12.0, 14.0, clip)

        title_font = QFont()
        title_font.setPointSizeF(9.0)
        sub_font = QFont()
        sub_font.setPointSizeF(7.5)
        text_left = 32.0
        available = int(self._rect.right() - text_left - 10.0)
        painter.setFont(title_font)
        painter.setPen(ink)
        title = QFontMetrics(title_font).elidedText(
            self.attachment.name or "附件", Qt.ElideMiddle, max(20, available))
        painter.drawText(QRectF(text_left, 7.0, available, 16.0),
                         Qt.AlignLeft | Qt.AlignVCenter, title)

        painter.setFont(sub_font)
        painter.setPen(QColor(MISSING_COLOR if self.missing else
                              (QColor(255, 255, 255, 150) if dark
                               else QColor(0, 0, 0, 140))))
        subtitle = MISSING_TEXT if self.missing else _human_size(self.attachment.size)
        painter.drawText(QRectF(text_left, 25.0, available, 14.0),
                         Qt.AlignLeft | Qt.AlignVCenter, subtitle)
        painter.restore()

    # --- 交互 ---

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged and not self._removed:
            pos = self.pos()
            if self.attachment.x != pos.x() or self.attachment.y != pos.y():
                self.attachment.x, self.attachment.y = pos.x(), pos.y()
                self.view.notify_changed()
        return result

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.view.open_attachment(self.attachment)
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def hoverEnterEvent(self, ev) -> None:
        self.update()
        super().hoverEnterEvent(ev)

    def hoverLeaveEvent(self, ev) -> None:
        self.update()
        super().hoverLeaveEvent(ev)


def _draw_paperclip(painter: QPainter, x: float, y: float, color: QColor) -> None:
    """回形针线稿（10x17 的逻辑框），附件卡片与图标共用同一套形状。"""
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.save()
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(x + 8.0, y + 3.0)
    path.lineTo(x + 8.0, y + 13.5)
    path.quadTo(x + 8.0, y + 16.5, x + 5.0, y + 16.5)
    path.quadTo(x + 2.0, y + 16.5, x + 2.0, y + 13.5)
    path.lineTo(x + 2.0, y + 3.5)
    path.quadTo(x + 2.0, y + 0.5, x + 5.0, y + 0.5)
    path.quadTo(x + 8.0, y + 0.5, x + 8.0, y + 3.5)
    path.lineTo(x + 8.0, y + 12.0)
    painter.drawPath(path)
    painter.restore()


def is_rich_item(item) -> bool:
    """是不是富内容图元（文字/图片/附件）；笔迹图元一律返回 False。"""
    return bool(getattr(item, "is_rich", False))


class NoteView(SketchView):
    """笔记画布：整页适配视口、共享 note.strokes，用户不能缩放/平移。

    笔迹列表直接复用 `note.strokes`（同一个 list 对象），所以撤销/重做与卡片
    的自动暂存都只需看 `note.strokes`。富内容同理：模型是唯一真相，图元只是
    投影，`scene.rebind()`（撤销/清屏）后会按模型重建。
    """

    def __init__(self, note: Note, settings: Settings, parent=None,
                 pdf_path: Path | None = None) -> None:
        # 先备好设计尺寸：SketchView.__init__ 末尾的 _apply_view 会回调 _sync_scene_rect
        self.note = note
        note.paper = note.paper if note.paper in NOTE_PAPERS else "dark"
        self.pdf_path = Path(pdf_path) if pdf_path is not None else None
        self._design_w = float(note.w)
        self._design_h = float(note.h)
        self._text_mode = False
        self._rich_action = False                 # 正在把鼠标交给富内容图元
        self._text_items: dict[str, TextBlockItem] = {}
        self._image_items: dict[str, NoteImageItem] = {}
        self._attachment_items: dict[str, AttachmentItem] = {}
        self._retired: list[QGraphicsItem] = []   # 移出场景的图元：先留着别在事件里析构
        scene = NoteScene(Document(strokes=note.strokes), note.paper)
        super().__init__(scene, settings)
        if parent is not None:
            self.setParent(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 基类用 AlignLeft|AlignTop：整页比视口窄时 Qt 会把 scene 贴左，fit_page 算好的
        # origin 会被丢掉。居中后再画，页面才真的落在视口中间（离线抓图实测）。
        self.setAlignment(Qt.AlignCenter)
        self.setFocusPolicy(Qt.StrongFocus)
        self.set_tool("pen")
        self.scene.on_rebind = self.rebuild_rich_items
        self.rebuild_rich_items()

    def _sync_scene_rect(self) -> None:
        """场景矩形恒为整页 0..w / 0..h：滚动范围由 fit_page 的缩放决定，不随笔迹变。"""
        self.setSceneRect(QRectF(0.0, 0.0, self._design_w, self._design_h))

    def fit_page(self) -> None:
        """把整页缩放到视口内并居中；卡片 resize 后重算。"""
        document = getattr(self, "document", None)
        if document is None or self._design_w <= 0.0 or self._design_h <= 0.0:
            return
        vw, vh = self.viewport().width(), self.viewport().height()
        if vw <= 0 or vh <= 0:
            return
        scale = min(vw / self._design_w, vh / self._design_h)
        document.viewport.scale = scale
        document.viewport.origin = ((vw - self._design_w * scale) / 2.0,
                                    (vh - self._design_h * scale) / 2.0)
        self._apply_view()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.fit_page()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.fit_page()

    def set_paper(self, paper: str) -> None:
        paper = paper if paper in NOTE_PAPERS else "dark"
        self.note.paper = paper
        self.scene.set_paper(paper)
        for item in self._attachment_items.values():
            item.update()

    # --- 固定视口：笔记页只做整页适配，禁止平移/缩放 ---

    def pan_screen(self, dx: float, dy: float) -> None:
        """笔记页的变换权威是 AlignCenter + fit_page：平移只会改写 origin 而不动画面，
        导致之后的落笔换算错位，所以基类的平移入口在这里一律无效。"""

    def zoom_at_screen(self, sx: float, sy: float, factor: float) -> None:
        """笔记页不自行缩放：卡片负责放大/还原（任务 3）。"""

    def reset_zoom(self) -> None:
        """应用级 `0` 回到整页适配；不能用基类的 100% + 笔迹包围盒居中。"""
        self.fit_page()

    # --- 富内容：素材路径 ---

    def asset_path(self, name: str) -> Path | None:
        """纯文件名 → assets 目录内的绝对路径；没有 PDF 或名字非法时返回 None。"""
        if self.pdf_path is None:
            return None
        try:
            return noteassets.asset_path(self.pdf_path, name)
        except (ValueError, OSError):
            return None

    def open_attachment(self, attachment: NoteAttachment) -> bool:
        """双击附件：用系统默认程序打开；文件丢了就什么都不做（返回 False）。"""
        path = self.asset_path(attachment.asset)
        if path is None or not path.exists():
            return False
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        return True

    # --- 富内容：投影 ---

    def notify_changed(self) -> None:
        """模型改了就广播一次：卡片转发 contentChanged → 自动暂存。"""
        self.changed.emit()

    def rebuild_rich_items(self) -> None:
        """按模型全量重建富内容图元（scene.rebind / 初次构造时调用）。

        `SketchScene.rebind` 里的 scene.clear() 已经把旧图元析构，这里只清索引，
        绝不回头碰旧包装对象。
        """
        self._text_items.clear()
        self._image_items.clear()
        self._attachment_items.clear()
        self._retired.clear()
        # 旧图元被整体清掉时，可能有一块文字正在编辑（清屏键被它临时关掉），
        # 重建后没人会再把清屏键打开，所以这里兜底恢复
        self.set_clear_shortcut_enabled(True)
        for block in list(getattr(self.note, "texts", []) or ()):
            self._text_items[block.id] = self._add_text_item(block)
        for image in list(getattr(self.note, "images", []) or ()):
            item = self._add_image_item(image)
            if item is not None:
                self._image_items[image.id] = item
        for attachment in list(getattr(self.note, "attachments", []) or ()):
            self._attachment_items[attachment.id] = self._add_attachment_item(attachment)

    def _add_text_item(self, block: TextBlock) -> TextBlockItem:
        item = TextBlockItem(block, self)
        self.scene.addItem(item)
        return item

    def _add_image_item(self, image: NoteImage) -> NoteImageItem | None:
        """缺素材/读不出来就跳过这一张（模型数据保留，换台机器还能复原）。"""
        path = self.asset_path(image.asset)
        if path is None or not path.exists():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        item = NoteImageItem(image, self, pixmap)
        self.scene.addItem(item)
        return item

    def _add_attachment_item(self, attachment: NoteAttachment) -> AttachmentItem:
        path = self.asset_path(attachment.asset)
        missing = path is None or not path.exists()
        item = AttachmentItem(attachment, self, missing=missing)
        self.scene.addItem(item)
        return item

    # --- 富内容：新增/删除 ---

    def add_text_block(self, x: float, y: float, text: str = "",
                       width: float = TEXT_DEFAULT_W) -> TextBlock:
        """在设计坐标 (x, y) 放一段文字并立刻进入编辑态。"""
        block = TextBlock(
            id=new_item_id("t", {b.id for b in self.note.texts}),
            x=float(x), y=float(y), w=max(1.0, float(width)), text=str(text))
        self.note.texts.append(block)
        item = self._add_text_item(block)
        self._text_items[block.id] = item
        self.notify_changed()
        self._begin_text_edit(item)
        return block

    def add_image_file(self, source: Path, center=None) -> NoteImage | None:
        """把本地图片复制进 `<pdf>.sketch-assets/` 并放到视口中心（或给定中心）。

        复制/读取失败返回 None，界面与模型都不动。
        """
        if self.pdf_path is None:
            return None
        source = Path(source)
        try:
            name = noteassets.store_asset(self.pdf_path, source)
        except OSError:
            return None
        path = self.asset_path(name)
        pixmap = QPixmap(str(path)) if path is not None else QPixmap()
        if pixmap.isNull():
            if path is not None:
                noteassets.remove_assets(self.pdf_path, [name])
            return None
        return self._add_image_pixmap(name, pixmap, center)

    def add_image_data(self, image: QImage, center=None) -> NoteImage | None:
        """剪贴板位图 → PNG 写入 assets 并放进笔记（无需临时文件）。"""
        if self.pdf_path is None or image is None or image.isNull():
            return None
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            return None
        data = bytes(buffer.data())
        buffer.close()
        try:
            name = noteassets.store_png(self.pdf_path, data)
        except OSError:
            return None
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            noteassets.remove_assets(self.pdf_path, [name])
            return None
        return self._add_image_pixmap(name, pixmap, center)

    def _add_image_pixmap(self, name: str, pixmap: QPixmap,
                          center=None) -> NoteImage:
        width = float(max(1, pixmap.width()))
        height = float(max(1, pixmap.height()))
        factor = min(IMAGE_MAX_W / width, IMAGE_MAX_H / height, 1.0)
        w, h = width * factor, height * factor
        cx, cy = self._center_xy(center)
        x, y = self._clamp_origin(cx - w / 2.0, cy - h / 2.0, w, h)
        image = NoteImage(id=new_item_id("img", {i.id for i in self.note.images}),
                          x=x, y=y, w=w, h=h, asset=name)
        self.note.images.append(image)
        self._image_items[image.id] = self._add_image_item(image)
        self.notify_changed()
        return image

    def paste_image_from_clipboard(self, center=None) -> NoteImage | None:
        """Ctrl+V：优先剪贴板位图，其次剪贴板里的本地图片文件。"""
        if self.pdf_path is None:
            return None
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                inserted = self.add_image_data(image, center)
                if inserted is not None:
                    return inserted
        if mime is not None:
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
                    continue
                inserted = self.add_image_file(path, center)
                if inserted is not None:
                    return inserted
        return None

    def add_attachment_file(self, source: Path, center=None) -> NoteAttachment | None:
        """把本地文件复制进 assets 目录，并在视口中心放一张附件卡片。"""
        if self.pdf_path is None:
            return None
        source = Path(source)
        try:
            size = source.stat().st_size
        except OSError:
            size = 0
        try:
            name = noteassets.store_asset(self.pdf_path, source)
        except OSError:
            return None
        cx, cy = self._center_xy(center)
        x, y = self._clamp_origin(cx - ATTACH_W / 2.0, cy - ATTACH_H / 2.0,
                                  ATTACH_W, ATTACH_H)
        attachment = NoteAttachment(
            id=new_item_id("att", {a.id for a in self.note.attachments}),
            x=x, y=y, asset=name, name=source.name, size=int(size))
        self.note.attachments.append(attachment)
        self._attachment_items[attachment.id] = self._add_attachment_item(attachment)
        self.notify_changed()
        return attachment

    def remove_item(self, item_id: str) -> bool:
        """按 id 删掉一条富内容：模型、图元一起走，找不到返回 False。"""
        for sequence, items in ((self.note.texts, self._text_items),
                                (self.note.images, self._image_items),
                                (self.note.attachments, self._attachment_items)):
            for entry in list(sequence):
                if entry.id != item_id:
                    continue
                sequence.remove(entry)
                item = items.pop(item_id, None)
                if isinstance(item, TextBlockItem):
                    # 正在编辑的文字被 API 删掉：先收好键盘/清屏键状态再移出场景
                    item._removed = True
                    item._editing = False
                    self.set_clear_shortcut_enabled(True)
                    if self.scene.focusItem() is item:
                        self.scene.setFocusItem(None)
                self._drop_item(item)
                self.notify_changed()
                return True
        return False

    def discard_empty_text(self, item: TextBlockItem) -> None:
        """空文字块失焦：从模型里删掉，避免页面上留一块看不见的空图元。

        图元先移出场景、但仍被 `_retired` 持有——此刻大概率正处在它自己的
        focusOut 事件里，立刻析构 C++ 对象不安全。
        """
        if getattr(item, "_removed", False):
            return
        item._removed = True
        item._editing = False
        self.set_clear_shortcut_enabled(True)
        block = item.block
        try:
            self.note.texts.remove(block)
        except ValueError:
            pass
        self._text_items.pop(block.id, None)
        if self.scene.focusItem() is item:
            self.scene.setFocusItem(None)
        self._drop_item(item)
        self.notify_changed()

    def _drop_item(self, item) -> None:
        if item is None:
            return
        try:
            self.scene.removeItem(item)
        except RuntimeError:            # 已经被 scene.clear() 收走过
            return
        self._retired.append(item)
        if len(self._retired) > 128:
            del self._retired[:64]

    def _clamp_origin(self, x: float, y: float, w: float, h: float) -> tuple[float, float]:
        """把新增内容钳进页面矩形，避免整块落在页外看不见。"""
        return (min(max(0.0, x), max(0.0, self._design_w - w)),
                min(max(0.0, y), max(0.0, self._design_h - h)))

    def viewport_center_scene(self) -> QPointF:
        """视口中心对应的设计坐标（插入图片/附件默认落在这里）。"""
        return QPointF(self.mapToScene(self.viewport().rect().center()))

    def _center_xy(self, center) -> tuple[float, float]:
        """插入位置：QPointF / (x, y) / None（= 视口中心）。"""
        point = center if center is not None else self.viewport_center_scene()
        if isinstance(point, QPointF):
            return float(point.x()), float(point.y())
        x, y = point
        return float(x), float(y)

    def _begin_text_edit(self, item: TextBlockItem) -> None:
        self.scene.clearSelection()
        item.setSelected(True)
        item.begin_edit()

    # --- 文字模式 ---

    @property
    def text_mode(self) -> bool:
        return self._text_mode

    def set_text_mode(self, on: bool) -> None:
        """文字模式：画布上单击 = 在该处放一块文字，随后自动退出。"""
        on = bool(on)
        if on == self._text_mode:
            return
        self._text_mode = on
        self.viewport().setCursor(Qt.IBeamCursor if on else Qt.BlankCursor)
        self.notify_changed()

    # --- 鼠标路由 ---

    def _rich_item_at(self, pos) -> QGraphicsItem | None:
        """视口坐标下的富内容图元（含被笔迹盖住的情况，所以遍历 items(pos)）。"""
        point = QPoint(int(pos.x()), int(pos.y()))
        for item in self.items(point):
            if is_rich_item(item):
                return item
        return None

    def mousePressEvent(self, ev) -> None:
        """吞掉中键：不启动基类的平移手势；左键落笔仍走基类。"""
        if ev.button() == Qt.MiddleButton:
            ev.accept()
            return
        pos = ev.position()
        # 点到别处（含空白与其它图元）先结束文字编辑，否则光标会一直留在旧块里
        if ev.button() == Qt.LeftButton:
            editing = self._editing_text()
            if editing is not None and self._rich_item_at(pos) is not editing:
                editing.end_edit()
        if self._rich_item_at(pos) is not None:
            self._rich_action = True
            if self._text_mode:
                self.set_text_mode(False)
            # 直接调 QGraphicsView：绕开 SketchView 的落笔/橡皮
            QGraphicsView.mousePressEvent(self, ev)
            return
        if ev.button() == Qt.LeftButton and self._text_mode:
            scene_pos = self.mapToScene(pos.toPoint())
            self.set_text_mode(False)
            self.add_text_block(scene_pos.x(), scene_pos.y())
            ev.accept()
            return
        # 点空白落笔：顺手清掉富内容的选中态（否则下一次 Delete 会误删上次选中的块）
        if ev.button() == Qt.LeftButton and self.scene.selectedItems():
            self.scene.clearSelection()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._rich_action:
            QGraphicsView.mouseMoveEvent(self, ev)
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._rich_action:
            self._rich_action = False
            QGraphicsView.mouseReleaseEvent(self, ev)
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        if self._rich_item_at(ev.position()) is not None:
            QGraphicsView.mouseDoubleClickEvent(self, ev)
            return
        super().mouseDoubleClickEvent(ev)

    def tabletEvent(self, ev) -> None:
        """数位板落在富内容上时不落笔（交给随后的合成鼠标事件）。"""
        if self._rich_item_at(ev.position()) is not None:
            ev.ignore()
            return
        super().tabletEvent(ev)

    # --- 键盘 ---

    def _editing_text(self) -> TextBlockItem | None:
        focus = self.scene.focusItem()
        if isinstance(focus, TextBlockItem) and focus.editing:
            return focus
        return None

    def _delete_selected_rich(self) -> bool:
        ids = [item.item_id for item in self.scene.selectedItems()
               if is_rich_item(item)]
        for item_id in ids:
            self.remove_item(item_id)
        return bool(ids)

    def _world(self, pos: QPointF) -> Point:
        """屏幕点 → 设计坐标，并钳进页内：页外 Slack 落笔不产生页外笔迹。

        只影响落笔/擦除的坐标；笔尖圆点在 drawForeground 里直接走
        viewportTransform，不经过这里，仍跟随真实光标。
        """
        x, y = super()._world(pos)
        return (min(max(x, 0.0), self._design_w),
                min(max(y, 0.0), self._design_h))

    def wheelEvent(self, ev) -> None:
        # 笔记页不缩放、不平移：缩放由卡片的放大/还原负责（任务 3）
        ev.accept()

    def keyPressEvent(self, ev) -> None:
        """文字编辑时所有按键交给 QGraphicsView（否则打不进字）；
        其余只保留提笔工具/粗细/撤销/文字模式/删除富内容，0、空格、方向键什么都不做。
        """
        if self._editing_text() is not None:
            QGraphicsView.keyPressEvent(self, ev)
            return
        key, mods = ev.key(), ev.modifiers()
        if mods & Qt.ControlModifier and key == Qt.Key_V:
            if self.paste_image_from_clipboard() is not None:
                ev.accept()
                return
        if mods & Qt.ControlModifier and key in (Qt.Key_Z, Qt.Key_Y):
            (self.history.pop_undo if key == Qt.Key_Z else self.history.pop_redo)()
            ev.accept()
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace) and self._delete_selected_rich():
            ev.accept()
            return
        if key == Qt.Key_T:
            self.set_text_mode(not self._text_mode)
            ev.accept()
            return
        if key == Qt.Key_B:
            self.set_text_mode(False)
            self.set_tool("pen")
            ev.accept()
            return
        if key == Qt.Key_Q:
            self.set_text_mode(False)
            self.set_tool("eraser")
            ev.accept()
            return
        if Qt.Key_1 <= key <= Qt.Key_9:
            index = key - Qt.Key_1
            if index < len(PALETTE):
                self.set_color(PALETTE[index])   # 1–9 = 色板第 1–9 色
                ev.accept()
                return
        ev.ignore()
