"""工具栏图标：用 QPainter 现画，不依赖图片资源，任意 DPI 都清晰。

统一 24×24 逻辑坐标、主题色描边（theme.TEXT），选中态由按钮背景变色区分。
"""
from __future__ import annotations

from functools import lru_cache
from math import cos, radians, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from sketchpad.ui import theme

_SIZE = 24          # 逻辑尺寸
_SCALE = 2          # 实际画 2 倍再交给 QIcon，缩放不发虚


def _icon(draw) -> QIcon:
    pm = QPixmap(_SIZE * _SCALE, _SIZE * _SCALE)
    pm.setDevicePixelRatio(float(_SCALE))   # QPainter 会自动按 DPR 放大，别再手动 scale
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    draw(painter)
    painter.end()
    return QIcon(pm)


def _stroke(width: float = 2.0) -> QPen:
    pen = QPen(QColor(theme.TEXT), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    return pen


@lru_cache(maxsize=None)
def pen_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.2))
        p.drawLine(QPointF(8.0, 16.0), QPointF(17.0, 7.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawPolygon([QPointF(4.6, 19.4), QPointF(10.3, 17.6), QPointF(6.4, 13.7)])
    return _icon(draw)


@lru_cache(maxsize=None)
def highlighter_icon() -> QIcon:
    """荧光笔：斜握的粗笔身 + 半透明荧光带。"""
    def draw(p: QPainter) -> None:
        # 底下的半透明荧光带：先画，让笔尖压在上面
        p.setPen(Qt.NoPen)
        band = QColor("#f1c40f")
        band.setAlpha(110)
        p.setBrush(band)
        p.drawRoundedRect(QRectF(3.0, 14.2, 18.0, 4.6), 1.4, 1.4)
        # 粗笔身：从右下斜向左上
        p.setPen(QPen(QColor(theme.TEXT), 3.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(9.8, 12.6), QPointF(17.0, 5.4))
        # 斜切笔尖：压在荧光带上
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawPolygon([QPointF(4.8, 16.6), QPointF(7.6, 10.6), QPointF(11.8, 13.6)])
    return _icon(draw)


@lru_cache(maxsize=None)
def eraser_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.translate(12.0, 12.0)
        p.rotate(-40.0)
        p.setPen(_stroke(2.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(-8.0, -5.0, 16.0, 10.0), 2.0, 2.0)
        p.drawLine(QPointF(-8.0, 0.0), QPointF(0.0, 0.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def text_select_icon() -> QIcon:
    """选择文字：I 形文本光标（上下横线 + 中间竖线）。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.9))
        p.drawLine(QPointF(12.0, 6.0), QPointF(12.0, 18.0))
        p.drawLine(QPointF(9.5, 6.0), QPointF(14.5, 6.0))
        p.drawLine(QPointF(9.5, 18.0), QPointF(14.5, 18.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def pointer_icon() -> QIcon:
    """鼠标：经典箭头轮廓（尖角朝左上 + 底部小尾巴），鼠标工具的图标。

    鼠标工具不只选字：拖空白平移、拖笔记图标移动、点 × 删除都在这一个工具里，
    所以用指针箭头而不是 I 形文本光标。
    """
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.8))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon([QPointF(7.0, 3.6), QPointF(7.0, 17.4), QPointF(10.6, 13.8),
                       QPointF(13.4, 20.2), QPointF(15.8, 19.0), QPointF(12.9, 12.8),
                       QPointF(17.4, 12.6)])
    return _icon(draw)


@lru_cache(maxsize=None)
def split_icon() -> QIcon:
    """窗口内分屏：矩形 + 中间竖分隔线。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.7))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(4.0, 6.0, 16.0, 12.0), 2.0, 2.0)
        p.drawLine(QPointF(12.0, 6.0), QPointF(12.0, 18.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def popout_icon() -> QIcon:
    """独立窗口：两个错位矩形 + 右上箭头。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.7))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(4.0, 8.0, 11.0, 11.0), 2.0, 2.0)
        p.drawRoundedRect(QRectF(9.0, 4.0, 11.0, 11.0), 2.0, 2.0)
        p.drawLine(QPointF(13.5, 10.5), QPointF(20.0, 4.5))
        p.drawLine(QPointF(15.5, 4.5), QPointF(20.0, 4.5))
        p.drawLine(QPointF(20.0, 4.5), QPointF(20.0, 9.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def width_icon(radius: float) -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawEllipse(QPointF(12.0, 12.0), radius, radius)
    return _icon(draw)


@lru_cache(maxsize=None)
def clear_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(5.0, 7.0), QPointF(19.0, 7.0))
        p.drawLine(QPointF(10.0, 4.5), QPointF(14.0, 4.5))
        p.drawRoundedRect(QRectF(7.0, 7.0, 10.0, 12.5), 2.0, 2.0)
        p.drawLine(QPointF(10.5, 10.0), QPointF(10.5, 16.5))
        p.drawLine(QPointF(13.5, 10.0), QPointF(13.5, 16.5))
    return _icon(draw)


@lru_cache(maxsize=None)
def note_icon() -> QIcon:
    """便签：圆角纸片 + 右上折角 + 两行字（笔记卡片/图钉共用）。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.8))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(7.0, 4.5)
        path.lineTo(15.0, 4.5)
        path.lineTo(19.5, 9.0)
        path.lineTo(19.5, 17.0)
        path.quadTo(19.5, 19.5, 17.0, 19.5)
        path.lineTo(7.0, 19.5)
        path.quadTo(4.5, 19.5, 4.5, 17.0)
        path.lineTo(4.5, 7.0)
        path.quadTo(4.5, 4.5, 7.0, 4.5)
        p.drawPath(path)
        fold = QPainterPath()               # 折角：纸片右上角折下来的一小块
        fold.moveTo(15.0, 4.5)
        fold.lineTo(15.0, 9.0)
        fold.lineTo(19.5, 9.0)
        p.drawPath(fold)
        p.setPen(_stroke(1.3))
        p.drawLine(QPointF(7.6, 13.0), QPointF(16.2, 13.0))
        p.drawLine(QPointF(7.6, 16.0), QPointF(13.8, 16.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def paper_icon(dark: bool) -> QIcon:
    """纸张：左浅右深的圆；当前纸色那半边用强调色描一圈。

    两个半边就是笔记纸面的真实底色（与 note_canvas.PAPER_BG 同值），
    这样按钮一眼能看出「现在是深纸还是浅纸」。
    """
    def draw(p: QPainter) -> None:
        rect = QRectF(3.5, 3.5, 17.0, 17.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f6f1e6"))                  # 浅纸
        p.drawPie(rect, 90 * 16, 180 * 16)             # 左半
        p.setBrush(QColor("#2b2c2f"))                  # 深纸
        p.drawPie(rect, 270 * 16, 180 * 16)            # 右半
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.TEXT), 1.2))
        p.drawLine(QPointF(12.0, 4.0), QPointF(12.0, 20.0))
        p.setPen(QPen(QColor(theme.TEXT), 1.4))
        p.drawEllipse(rect)
        p.setPen(QPen(QColor(theme.ACCENT), 1.8))
        outline = rect.adjusted(-1.0, -1.0, 1.0, 1.0)
        if dark:
            p.drawArc(outline, 270 * 16, 180 * 16)     # 右半：深纸选中
        else:
            p.drawArc(outline, 90 * 16, 180 * 16)      # 左半：浅纸选中
    return _icon(draw)


@lru_cache(maxsize=None)
def delete_icon() -> QIcon:
    """删除：垃圾桶 + 桶内一条减号（与 clear_icon 的两条竖线区分）。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.8))
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(5.0, 7.0), QPointF(19.0, 7.0))
        p.drawLine(QPointF(9.5, 4.7), QPointF(14.5, 4.7))
        body = QPainterPath()
        body.moveTo(7.0, 7.0)
        body.lineTo(7.8, 18.6)
        body.quadTo(7.9, 20.0, 9.3, 20.0)
        body.lineTo(14.7, 20.0)
        body.quadTo(16.1, 20.0, 16.2, 18.6)
        body.lineTo(17.0, 7.0)
        p.drawPath(body)
        p.setPen(_stroke(2.2))
        p.drawLine(QPointF(10.0, 13.4), QPointF(14.0, 13.4))
    return _icon(draw)


@lru_cache(maxsize=None)
def pin_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawEllipse(QPointF(12.0, 8.6), 4.4, 4.4)
        p.setPen(_stroke(2.2))
        p.drawLine(QPointF(12.0, 13.0), QPointF(12.0, 19.6))
    return _icon(draw)


def _arrow_curve(p: QPainter) -> None:
    """顺时针弯钩 + 箭头，undo/redo 共用；redo 由调用方镜像。"""
    pen = _stroke(2.0)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(7.5, 15.2)
    path.cubicTo(6.0, 8.6, 17.2, 7.0, 18.2, 13.2)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.TEXT))
    p.drawPolygon([QPointF(4.2, 16.8), QPointF(10.2, 14.8), QPointF(8.8, 10.2)])


@lru_cache(maxsize=None)
def undo_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        _arrow_curve(p)
    return _icon(draw)


@lru_cache(maxsize=None)
def redo_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.translate(24.0, 0.0)
        p.scale(-1.0, 1.0)
        _arrow_curve(p)
    return _icon(draw)


@lru_cache(maxsize=None)
def minimize_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.6))
        p.drawLine(QPointF(6.0, 12.0), QPointF(18.0, 12.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def maximize_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.6))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(6.5, 6.5, 11.0, 11.0), 1.5, 1.5)
    return _icon(draw)


@lru_cache(maxsize=None)
def restore_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.6))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(5.5, 8.5, 9.5, 9.5), 1.5, 1.5)
        p.drawLine(QPointF(9.0, 8.5), QPointF(9.0, 6.0))
        p.drawLine(QPointF(9.0, 6.0), QPointF(18.0, 6.0))
        p.drawLine(QPointF(18.0, 6.0), QPointF(18.0, 15.0))
        p.drawLine(QPointF(18.0, 15.0), QPointF(15.5, 15.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def close_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.6))
        p.drawLine(QPointF(7.0, 7.0), QPointF(17.0, 17.0))
        p.drawLine(QPointF(17.0, 7.0), QPointF(7.0, 17.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def gear_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        # 8 个齿：外/内半径交替 16 个顶点，中心再挖一个圆孔
        p.setPen(_stroke(1.8))
        p.setBrush(Qt.NoBrush)
        outer, inner, teeth = 9.0, 6.6, 8
        points = []
        for index in range(teeth * 2):
            radius = outer if index % 2 == 0 else inner
            angle = radians(index * 180.0 / teeth)
            points.append(QPointF(12.0 + radius * sin(angle),
                                  12.0 - radius * cos(angle)))
        p.drawPolygon(points)
        p.drawEllipse(QPointF(12.0, 12.0), 2.8, 2.8)
    return _icon(draw)


@lru_cache(maxsize=None)
def palette_icon() -> QIcon:
    def draw(p: QPainter) -> None:
        rect = QRectF(3.5, 3.5, 17.0, 17.0)
        for index, color in enumerate(("#ff453a", "#ffd60a", "#30d158", "#0a84ff")):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawPie(rect, index * 90 * 16, 90 * 16)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#1c1c1e"), 1.5))
        p.drawEllipse(rect)
        p.setBrush(QColor(theme.WINDOW_BG))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12.0, 12.0), 3.2, 3.2)
    return _icon(draw)


@lru_cache(maxsize=None)
def toc_icon() -> QIcon:
    """目录：三行「圆点 + 横线」的条目列表。"""
    def draw(p: QPainter) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        for y in (7.0, 12.0, 17.0):
            p.drawEllipse(QPointF(5.8, y), 1.5, 1.5)
        p.setPen(_stroke(1.8))
        for y in (7.0, 12.0, 17.0):
            p.drawLine(QPointF(9.6, y), QPointF(19.0, y))
    return _icon(draw)


@lru_cache(maxsize=None)
def search_icon() -> QIcon:
    """查找：放大镜（圆 + 右下斜柄）。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(10.2, 10.2), 5.4, 5.4)
        p.drawLine(QPointF(14.2, 14.2), QPointF(19.4, 19.4))
    return _icon(draw)


@lru_cache(maxsize=None)
def scratch_icon() -> QIcon:
    """草稿纸：小本子（页面 + 顶部两环 + 两行字）叠一支斜铅笔。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.8))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(4.5, 5.0, 11.0, 14.0), 2.0, 2.0)
        p.drawLine(QPointF(8.0, 5.0), QPointF(8.0, 3.0))
        p.drawLine(QPointF(12.5, 5.0), QPointF(12.5, 3.0))
        p.drawLine(QPointF(7.6, 9.6), QPointF(11.0, 9.6))
        p.drawLine(QPointF(7.6, 12.6), QPointF(11.0, 12.6))
        p.setPen(_stroke(2.0))                       # 铅笔：从页面右下伸出去
        p.drawLine(QPointF(12.6, 17.8), QPointF(19.0, 11.4))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawPolygon([QPointF(11.2, 19.6), QPointF(13.8, 18.4), QPointF(12.4, 17.0)])
    return _icon(draw)


@lru_cache(maxsize=None)
def text_icon() -> QIcon:
    """文字工具：一个大写 T（顶横 + 竖笔 + 底端短横）。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        p.drawLine(QPointF(5.5, 6.0), QPointF(18.5, 6.0))
        p.drawLine(QPointF(12.0, 6.0), QPointF(12.0, 17.6))
        p.setPen(_stroke(1.8))
        p.drawLine(QPointF(9.6, 17.8), QPointF(14.4, 17.8))
    return _icon(draw)


@lru_cache(maxsize=None)
def image_icon() -> QIcon:
    """插入图片：圆角相框 + 山脉折线 + 左上太阳。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.7))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(3.5, 5.5, 17.0, 13.0), 2.0, 2.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawEllipse(QPointF(8.6, 9.6), 1.7, 1.7)
        p.setPen(_stroke(1.7))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline([QPointF(4.6, 17.0), QPointF(9.4, 12.0), QPointF(13.0, 15.4),
                        QPointF(15.6, 12.8), QPointF(19.4, 17.0)])
    return _icon(draw)


@lru_cache(maxsize=None)
def new_note_icon() -> QIcon:
    """新建笔记：一页纸 + 右下角加号。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.7))
        p.drawRoundedRect(QRectF(4.0, 3.5, 12.0, 15.0), 1.6, 1.6)
        p.setPen(_stroke(1.3))
        p.drawLine(QPointF(7.0, 8.0), QPointF(13.0, 8.0))
        p.drawLine(QPointF(7.0, 11.0), QPointF(13.0, 11.0))
        p.drawLine(QPointF(7.0, 14.0), QPointF(11.0, 14.0))
        p.setPen(_stroke(1.9))
        p.drawLine(QPointF(16.5, 16.0), QPointF(21.0, 16.0))
        p.drawLine(QPointF(18.75, 13.75), QPointF(18.75, 18.25))
    return _icon(draw)


@lru_cache(maxsize=None)
def annotate_icon() -> QIcon:
    """全屏批注：显示器轮廓 + 一支斜钢笔。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(1.6))
        p.drawRoundedRect(QRectF(3.0, 4.0, 18.0, 12.0), 1.6, 1.6)
        p.drawLine(QPointF(9.0, 20.0), QPointF(15.0, 20.0))
        p.drawLine(QPointF(12.0, 16.0), QPointF(12.0, 20.0))
        p.setPen(_stroke(1.8))
        p.drawLine(QPointF(10.0, 13.0), QPointF(16.0, 7.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT))
        p.drawPolygon([QPointF(8.8, 14.2), QPointF(11.2, 13.6),
                       QPointF(10.6, 11.2)])
    return _icon(draw)


@lru_cache(maxsize=None)
def capture_icon() -> QIcon:
    """截图：取景框（四个直角）+ 中间快门线。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        for x, y, dx, dy in ((5.0, 5.0, 1, 1), (19.0, 5.0, -1, 1),
                             (5.0, 19.0, 1, -1), (19.0, 19.0, -1, -1)):
            p.drawLine(QPointF(x, y + 4.0 * dy), QPointF(x, y))
            p.drawLine(QPointF(x, y), QPointF(x + 4.0 * dx, y))
        p.drawLine(QPointF(8.0, 13.0), QPointF(16.0, 13.0))
    return _icon(draw)


@lru_cache(maxsize=None)
def attach_icon() -> QIcon:
    """插入附件：回形针（外圈大弯 + 内芯回折，与附件卡片上的线稿同一形状）。"""
    def draw(p: QPainter) -> None:
        p.save()
        p.translate(12.0, 12.0)
        p.rotate(-28.0)                     # 斜着放更像真回形针，也避开直角撞边
        p.translate(-12.0, -12.0)
        p.setPen(_stroke(1.8))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(14.6, 7.4)
        path.lineTo(14.6, 16.0)
        path.quadTo(14.6, 18.6, 12.0, 18.6)
        path.quadTo(9.4, 18.6, 9.4, 16.0)
        path.lineTo(9.4, 7.0)
        path.quadTo(9.4, 4.4, 12.0, 4.4)
        path.quadTo(14.6, 4.4, 14.6, 7.0)
        path.lineTo(14.6, 15.0)
        p.drawPath(path)
        p.restore()
    return _icon(draw)


# --- 应用图标：任务栏/Alt-Tab 用，沿用托盘「深底 + 一点色」的视觉 ---

APP_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
_APP_ICON_BG = "#202124"        # 与画布 CANVAS_BG 同色（canvas.py 不反向依赖 icons）


def _app_pixmap(size: int) -> QPixmap:
    """按 64×64 逻辑坐标画一枚，再交给 QIcon 按尺寸重采样。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(size / 64.0, size / 64.0)
    # 深底圆角方块：与草稿纸/托盘同一块"画板"
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(_APP_ICON_BG))
    painter.drawRoundedRect(QRectF(2.0, 2.0, 60.0, 60.0), 13.0, 13.0)
    # 斜握的笔（复用 pen_icon 的线 + 笔尖语言，加粗到图标尺度）
    painter.setPen(QPen(QColor(theme.TEXT), 6.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(QPointF(24.0, 42.0), QPointF(45.0, 21.0))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(theme.TEXT))
    painter.drawPolygon([QPointF(14.0, 52.0), QPointF(28.0, 47.0), QPointF(19.0, 38.0)])
    # 右下角一点强调色：与托盘图标的红点呼应
    painter.setBrush(QColor("#ff453a"))
    painter.drawEllipse(QPointF(48.0, 48.0), 6.0, 6.0)
    painter.end()
    return pm


@lru_cache(maxsize=None)
def app_icon() -> QIcon:
    """应用图标：优先用素材图 `sketchpad/assets/app.png`，缺文件时回退到现画。

    打包后素材在 PyInstaller 的 _MEIPASS 下（spec 里按 datas 收入）。
    """
    path = _asset_path("app.png")
    if path is not None:
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    icon = QIcon()
    for size in APP_ICON_SIZES:
        icon.addPixmap(_app_pixmap(size))
    return icon


def _asset_path(name: str):
    """定位素材文件：开发态在 sketchpad/assets，打包态在 _MEIPASS 下。"""
    import sys
    from pathlib import Path

    base = getattr(sys, "_MEIPASS", None)
    candidates = []
    if base:
        candidates.append(Path(base) / "sketchpad" / "assets" / name)
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@lru_cache(maxsize=None)
def search_icon() -> QIcon:
    """放大镜：查找条按钮。"""
    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(10.6, 10.6), 6.0, 6.0)
        p.drawLine(QPointF(15.0, 15.0), QPointF(19.6, 19.6))
    return _icon(draw)


# 折叠箭头四个方向：横向工具栏用上/下，纵向右侧栏用右（收起）/左（展开）
_CHEVRON_POINTS = {
    "up": ((7.0, 14.5), (12.0, 9.5), (17.0, 14.5)),
    "down": ((7.0, 9.5), (12.0, 14.5), (17.0, 9.5)),
    "left": ((14.5, 7.0), (9.5, 12.0), (14.5, 17.0)),
    "right": ((9.5, 7.0), (14.5, 12.0), (9.5, 17.0)),
}


@lru_cache(maxsize=None)
def chevron_icon(direction: str = "up") -> QIcon:
    """工具栏折叠箭头：direction 取 up/down/left/right。"""
    points = _CHEVRON_POINTS.get(direction)
    if points is None:
        raise ValueError(f"chevron_icon: 未知方向 {direction!r}")

    def draw(p: QPainter) -> None:
        p.setPen(_stroke(2.2))
        p.drawPolyline([QPointF(x, y) for x, y in points])
    return _icon(draw)
