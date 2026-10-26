"""冷启动动画：图标少女 + 墨环 + 音符 + 加载提示。

只在 ``app.main()`` 建主窗之前出现；不参与业务状态，也不读写设置。
动画全部用 QPainter 现画（角色立绘直接取 ``assets/app.png``），所以不依赖
逐帧图片，也不会因为缩放/高 DPI 变糊。
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import (QElapsedTimer, QEventLoop, QPointF, QRectF, Qt,
                            QTimer)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetricsF, QGuiApplication,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget

from sketchpad.ui import icons, theme

SPLASH_W = 560
SPLASH_H = 330
FRAME_MS = 16
INTRO_MS = 480          # 建主窗会阻塞事件循环，先把动画完整播一小段
MIN_VISIBLE_MS = 1050   # 总显示时长下限（含阻塞的那段）
FADE_MS = 240

MESSAGES = ("正在展开书页", "正在整理笔画", "正在铺开画布")

_PORTRAIT_R = 84.0
_ART_ZOOM = 1.08        # 立绘自带肩部留白，轻微放大后五官更聚焦
_PINK = QColor("#F2A7B8")
_PINK_SOFT = QColor("#F8CBD6")
_GREEN = QColor("#2F6B57")
_CREAM = QColor("#FFFCF7")
_INK = QColor(theme.TEXT)
_ACCENT = QColor(theme.ACCENT)


def _font(size: float, weight: QFont.Weight = QFont.Weight.Normal,
          spacing: float = 0.0, family: str = "Microsoft YaHei UI") -> QFont:
    font = QFont(family)
    font.setPixelSize(max(1, int(round(size))))
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def _star_path(cx: float, cy: float, radius: float) -> QPainterPath:
    """四角星：先画一条竖线再横着穿过中心，比 QPolygon 更接近插画里的闪光。"""
    pinch = radius * 0.16
    path = QPainterPath(QPointF(cx, cy - radius))
    path.quadTo(cx + pinch, cy - pinch, cx + radius, cy)
    path.quadTo(cx + pinch, cy + pinch, cx, cy + radius)
    path.quadTo(cx - pinch, cy + pinch, cx - radius, cy)
    path.quadTo(cx - pinch, cy - pinch, cx, cy - radius)
    path.closeSubpath()
    return path


class LaunchSplash(QWidget):
    """无边框启动卡；计时器驱动，``finish()`` 后等够最短时长再淡出。"""

    def __init__(self, parent: QWidget | None = None, *,
                 min_visible_ms: int = MIN_VISIBLE_MS,
                 fade_ms: int = FADE_MS) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedSize(SPLASH_W, SPLASH_H)
        self._min_visible_ms = max(0, int(min_visible_ms))
        self._fade_ms = max(0, int(fade_ms))
        self._clock = QElapsedTimer()
        self._clock.start()
        self._elapsed = 0
        self._fade_started = -1
        self._closing = False
        self._finished = False
        self._art = self._load_art()
        rng = random.Random(20261026)
        self._sparks = [
            (rng.uniform(0, math.tau), rng.uniform(_PORTRAIT_R + 18, _PORTRAIT_R + 40),
             rng.uniform(3.0, 6.0), rng.uniform(0, math.tau),
             rng.choice((_PINK, _GREEN, QColor("#FFFFFF"))))
            for _ in range(7)
        ]
        self._notes = [
            (rng.uniform(-118, 108), rng.uniform(0, 1), rng.choice(("♪", "♫")),
             rng.choice((_PINK, _GREEN)))
            for _ in range(3)
        ]
        self._motes = [
            (rng.uniform(24, SPLASH_W - 24), rng.uniform(40, SPLASH_H - 30),
             rng.uniform(1.2, 2.4), rng.uniform(0, math.tau))
            for _ in range(6)
        ]
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---------- 生命周期 ----------

    def show_centered(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())
        self.show()
        self.raise_()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def hold(self, ms: int) -> None:
        """用嵌套事件循环播指定时长；没有这步，建主窗期间动画会冻在第一帧。"""
        if ms <= 0:
            return
        loop = QEventLoop()
        QTimer.singleShot(int(ms), loop.quit)
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def finish(self) -> None:
        """主窗已经可见：补足最短时长后淡出，不阻塞调用方。"""
        if self._closing:
            return
        self._closing = True
        wait = max(0, self._min_visible_ms - self._elapsed)
        if wait:
            QTimer.singleShot(wait, self._begin_fade)
        else:
            self._begin_fade()

    def _begin_fade(self) -> None:
        if self._finished or self._fade_started >= 0:
            return
        if self._fade_ms <= 0:
            self._complete()
            return
        self._fade_started = self._clock.elapsed()

    def _tick(self) -> None:
        self._elapsed = self._clock.elapsed()
        if self._fade_started >= 0:
            progress = (self._elapsed - self._fade_started) / self._fade_ms
            if progress >= 1.0:
                self._complete()
                return
        self.update()

    def _complete(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        self.close()

    @property
    def finished(self) -> bool:
        return self._finished

    def _alpha(self) -> float:
        intro = min(1.0, self._elapsed / 170.0)
        alpha = intro * intro * (3.0 - 2.0 * intro)     # smoothstep
        if self._fade_started >= 0:
            out = (self._elapsed - self._fade_started) / max(1, self._fade_ms)
            alpha *= max(0.0, 1.0 - out)
        return alpha

    # ---------- 绘制 ----------

    @staticmethod
    def _load_art() -> QPixmap:
        for name in ("splash_girl.png", "app.png"):
            path = icons._asset_path(name)
            if path is None:
                continue
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap
        return QPixmap()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setOpacity(max(0.0, min(1.0, self._alpha())))
        card = QRectF(6, 6, SPLASH_W - 12, SPLASH_H - 12)
        path = QPainterPath()
        path.addRoundedRect(card, 22, 22)
        self._paint_card(p, card, path)
        p.save()
        p.setClipPath(path)
        self._paint_decor(p, card)
        self._paint_portrait(p, card)
        self._paint_left_text(p, card)
        p.restore()
        self._paint_border(p, card)

    def _paint_card(self, p: QPainter, card: QRectF, path: QPainterPath) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for offset, alpha in ((8, 14), (5, 20), (3, 26)):
            shadow = QPainterPath()
            shadow.addRoundedRect(card.adjusted(-offset * 0.35, offset,
                                                offset * 0.35, offset),
                                  22 + offset * 0.3, 22 + offset * 0.3)
            p.fillPath(shadow, QColor(92, 45, 58, alpha))
        gradient = QLinearGradient(0, card.top(), 0, card.bottom())
        gradient.setColorAt(0.0, QColor("#FFFBFD"))
        gradient.setColorAt(0.62, QColor("#FFF1F6"))
        gradient.setColorAt(1.0, QColor("#FFE2EC"))
        p.fillPath(path, gradient)
        glow = QRadialGradient(QPointF(card.right() - 120, card.top() + 34), 210)
        glow.setColorAt(0.0, QColor(255, 255, 255, 120))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(path, glow)

    def _paint_decor(self, p: QPainter, card: QRectF) -> None:
        # 右侧淡墨晕 + 底部纸线；都压在角色后面，不抢脸
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(236, 171, 195, 26))
        p.drawEllipse(QPointF(card.right() - 96, card.bottom() - 34), 150, 108)
        p.setBrush(QColor(255, 255, 255, 90))
        p.drawEllipse(QPointF(card.left() + 92, card.top() + 22), 120, 74)
        pen = QPen(QColor(168, 78, 53, 16), 1.0)
        p.setPen(pen)
        for index in range(3):
            y = card.bottom() - 46 + index * 11
            p.drawLine(QPointF(card.right() - 226, y), QPointF(card.right() - 24, y))
        t = self._elapsed
        for x, y, radius, phase in self._motes:
            drift = math.sin(t / 1500 + phase) * 7
            rise = (t * 0.008 + phase * 20) % 22
            alpha = 28 + 26 * (0.5 + 0.5 * math.sin(t / 900 + phase))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, int(alpha)))
            p.drawEllipse(QPointF(x + drift, y - rise), radius, radius)

    def _paint_portrait(self, p: QPainter, card: QRectF) -> None:
        t = self._elapsed
        cx = card.right() - 128
        cy = card.top() + 154
        radius = _PORTRAIT_R

        halo = QRadialGradient(QPointF(cx, cy), radius + 40)
        halo.setColorAt(0.0, QColor(242, 167, 184, 84))
        halo.setColorAt(0.62, QColor(242, 167, 184, 34))
        halo.setColorAt(1.0, QColor(242, 167, 184, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(cx, cy), radius + 40, radius + 40)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(236, 171, 195, 130), 1.0))
        p.drawEllipse(QPointF(cx, cy), radius + 11, radius + 11)

        spin = (t * 0.12) % 360
        p.setPen(QPen(_PINK, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        ring = QRectF(cx - radius - 12, cy - radius - 12,
                      (radius + 12) * 2, (radius + 12) * 2)
        p.drawArc(ring, int((-90 - spin) * 16), -112 * 16)
        p.setPen(QPen(QColor(47, 107, 87, 150), 1.4,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        outer = QRectF(cx - radius - 19, cy - radius - 19,
                       (radius + 19) * 2, (radius + 19) * 2)
        p.drawArc(outer, int((-20 + spin * 0.7) * 16), 48 * 16)

        self._paint_sparks(p, cx, cy, t)
        self._paint_notes(p, cx, cy, t)

        bob = math.sin(t / 2400 * math.tau) * 3.2
        scale = 1.0 + math.sin(t / 3600 * math.tau) * 0.012
        tilt = math.sin(t / 3000 * math.tau) * 0.7

        p.save()
        p.translate(cx, cy + bob)
        p.rotate(tilt)
        p.scale(scale, scale)
        clip = QPainterPath()
        clip.addEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
        p.setClipPath(clip)
        if self._art.isNull():
            p.fillRect(QRectF(-radius, -radius, radius * 2, radius * 2),
                       QColor("#FFD9E4"))
            p.setPen(_INK)
            p.setFont(_font(52, QFont.Weight.Bold))
            p.drawText(QRectF(-radius, -radius, radius * 2, radius * 2),
                       Qt.AlignmentFlag.AlignCenter, "墨")
        else:
            side = radius * 2 * _ART_ZOOM
            p.drawPixmap(QRectF(-side / 2, -side / 2, side, side).toRect(),
                         self._art)
        p.restore()

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 220), 2.0))
        p.drawEllipse(QPointF(cx, cy), radius - 0.5, radius - 0.5)

    def _paint_sparks(self, p: QPainter, cx: float, cy: float, t: int) -> None:
        for angle, distance, radius, phase, color in self._sparks:
            pulse = max(0.0, math.sin(t / 420 + phase))
            if pulse <= 0.02:
                continue
            x = cx + math.cos(angle) * distance
            y = cy + math.sin(angle) * distance * 0.9
            fill = QColor(color)
            fill.setAlpha(int(70 + 170 * pulse))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawPath(_star_path(x, y, radius * (0.55 + 0.65 * pulse)))

    def _paint_notes(self, p: QPainter, cx: float, cy: float, t: int) -> None:
        p.setFont(_font(15, QFont.Weight.DemiBold, family="Segoe UI Symbol"))
        for dx, phase, glyph, color in self._notes:
            progress = ((t / 3000.0) + phase) % 1.0
            x = cx + dx + math.sin(progress * math.tau + phase) * 7
            y = cy + 108 - progress * 92
            alpha = math.sin(progress * math.pi) * 150
            ink = QColor(color)
            ink.setAlpha(int(max(0, alpha)))
            p.setPen(ink)
            p.drawText(QPointF(x, y), glyph)

    def _paint_left_text(self, p: QPainter, card: QRectF) -> None:
        left = card.left() + 40
        t = self._elapsed
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(_font(10, QFont.Weight.DemiBold, spacing=2.4))
        p.drawText(QRectF(left, card.top() + 48, 300, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "MOYE · 墨页")

        p.setPen(_INK)
        p.setFont(_font(42, QFont.Weight.Bold, spacing=3.0))
        p.drawText(QRectF(left - 2, card.top() + 66, 300, 58),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "墨页")

        grow = min(1.0, t / 620.0)
        grow = 1.0 - (1.0 - grow) ** 3
        if grow > 0:
            p.setPen(QPen(_ACCENT, 3.2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(left, card.top() + 132),
                       QPointF(left + 122 * grow, card.top() + 132))
            faded = QColor(_ACCENT)
            faded.setAlpha(60)
            p.setPen(QPen(faded, 1.4, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(left + 8, card.top() + 136),
                       QPointF(left + 104 * grow, card.top() + 136))

        p.setPen(QColor(theme.TEXT_DIM))
        p.setFont(_font(13.2))
        p.drawText(QRectF(left, card.top() + 146, 320, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "把读过的，写成自己的。")

        font = _font(11.6)
        slot = int(t / 1100)
        message = MESSAGES[slot % len(MESSAGES)]
        local = (t % 1100) / 1100.0
        fade = 1.0 if local < 0.84 else max(0.0, 1.0 - (local - 0.84) / 0.16)
        ink = QColor(theme.TEXT_MUTED)
        ink.setAlpha(int(255 * fade))
        p.setPen(ink)
        p.setFont(font)
        base = QRectF(left, card.top() + 198, 330, 20)
        p.drawText(base, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   message)
        text_width = QFontMetricsF(font).horizontalAdvance(message)
        dot_x = left + text_width + 9
        dot_y = card.top() + 208
        for index in range(3):
            pulse = 0.5 + 0.5 * math.sin(t / 190 - index * 0.9)
            dot = QColor(theme.TEXT_MUTED)
            dot.setAlpha(int((40 + 150 * pulse) * fade))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot)
            p.drawEllipse(QPointF(dot_x + index * 7, dot_y), 1.8, 1.8)

        bar = QRectF(left, card.top() + 234, 238, 4)
        bar_path = QPainterPath()
        bar_path.addRoundedRect(bar, 2, 2)
        p.fillPath(bar_path, QColor(236, 171, 195, 80))
        span = 72.0
        progress = (t % 1250) / 1250.0
        eased = progress * progress * (3.0 - 2.0 * progress)
        head = bar.left() + (bar.width() + span) * eased - span
        shimmer = QRectF(max(bar.left(), head), bar.top(),
                         min(span, bar.right() - head), bar.height())
        if shimmer.width() > 0:
            sweep = QLinearGradient(shimmer.left(), 0, shimmer.right(), 0)
            sweep.setColorAt(0.0, QColor(242, 167, 184, 0))
            sweep.setColorAt(0.5, QColor(242, 167, 184, 230))
            sweep.setColorAt(1.0, QColor(168, 78, 53, 0))
            p.fillPath(_rounded(shimmer, 2), sweep)

        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(_font(10.2))
        p.drawText(QRectF(left, card.top() + 268, 300, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "阅读  ·  批注  ·  草稿")

    @staticmethod
    def _paint_border(p: QPainter, card: QRectF) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(236, 171, 195, 220), 1.0))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 22, 22)


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def show_launch_splash() -> LaunchSplash:
    """显示启动卡并先播一小段；调用方建完主窗后再 ``finish()``。"""
    splash = LaunchSplash()
    splash.show_centered()
    splash.hold(INTRO_MS)
    return splash
