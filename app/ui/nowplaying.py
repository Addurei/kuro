"""The little now-playing HUD that sits beside Kuro while music plays:
the album art on a spinning vinyl disc, the song, and a tiny equalizer.

Click the disc to play/pause, double-click it to skip.
"""
from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QConicalGradient, QGuiApplication, QImage, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QWidget

from . import theme
from .cat_rig import Spring

W, H = 236, 66
DISC = 27            # disc radius


class NowPlaying(QWidget):
    def __init__(self, cat, on_toggle: Callable[[], None], on_next: Callable[[], None]):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(W, H)
        self.setCursor(Qt.PointingHandCursor)
        self.cat = cat
        self.on_toggle, self.on_next = on_toggle, on_next
        self.track = None
        self.art: QImage | None = None
        self.side = 1                          # 1 = right of Kuro, -1 = left
        self.angle = 0.0
        self.spin = Spring(0, 6, 1.0)          # revolutions per second
        self.alpha = Spring(0, 60, 1.0)
        self.slide = Spring(14, 120, 0.7)
        self.scroll = 0.0
        self.hidden_by_hud = False
        # paused / closed: let the disc spin down for a moment, then fade away
        self._linger = QTimer(self, singleShot=True, interval=1600, timeout=self._fade_out)
        self._t = 0.0
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self, interval=16, timeout=self._tick)
        self._timer.setTimerType(Qt.PreciseTimer)

    # ------------------------------------------------------------ data
    def set_track(self, track) -> None:
        if track is not None and track.art and (self.track is None or track.art != self.track.art):
            img = QImage.fromData(track.art)
            self.art = None if img.isNull() else img
        elif track is not None and self.track is not None and track.key != self.track.key \
                and not track.art:
            self.art = None
        if track is not None and (self.track is None or track.key != self.track.key):
            self.scroll = 0.0
        self.track = track
        playing = bool(track and track.playing)
        self.spin.target = 0.55 if playing else 0.0
        if playing and not self.hidden_by_hud:
            self._linger.stop()
            self.alpha.target = 1.0
        elif not playing:
            if self.alpha.target > 0 and not self._linger.isActive():
                self._linger.start()           # spin down, then disappear
        else:
            self.alpha.target = 0.0            # chat is open: step aside
        if playing and not self.isVisible() and not self.hidden_by_hud:
            self.slide.value = 14
            self.follow()
            self.show()
        self._clock.restart()
        self._timer.start()

    def _fade_out(self) -> None:
        if not (self.track and self.track.playing):
            self.alpha.target = 0.0

    def set_hud_open(self, open_: bool) -> None:
        """Step aside while the main HUD (chat, settings...) is open."""
        self.hidden_by_hud = open_
        self.set_track(self.track)

    # ------------------------------------------------------------ placement
    def follow(self) -> None:
        a = self.cat.head_anchor()
        scr = (QGuiApplication.screenAt(a) or QGuiApplication.primaryScreen()).availableGeometry()
        gap = int(58 * self.cat.k / 0.82)
        y = int(a.y() + 34 * self.cat.k / 0.82)
        x_right, x_left = a.x() + gap, a.x() - gap - W
        self.side = 1 if x_right + W <= scr.right() - 4 else -1
        x = x_right if self.side > 0 else x_left
        x = min(max(x, scr.left() + 4), scr.right() - W - 4)
        y = min(max(y, scr.top() + 4), scr.bottom() - H - 4)
        self.move(int(x + self.side * self.slide.value), y)

    # ------------------------------------------------------------ animation
    def _tick(self) -> None:
        dt = min(self._clock.restart() / 1000.0, 1 / 20)
        self._t += dt
        for s in (self.spin, self.alpha, self.slide):
            s.step(dt)
        self.slide.target = 0.0 if self.alpha.target > 0 else 14.0
        self.angle = (self.angle + 360 * max(0.0, self.spin.value) * dt) % 360
        self.scroll += dt * 28
        self.follow()
        if self.alpha.target == 0 and self.alpha.value < 0.02:
            self.hide()
            self._timer.stop()
            return
        want = 16 if abs(self.alpha.vel) > 0.05 or abs(self.slide.vel) > 1 else 33
        if self._timer.interval() != want:
            self._timer.setInterval(want)
        self.update()

    # ------------------------------------------------------------ input
    def _on_disc(self, pos: QPointF) -> bool:
        c = self._disc_centre()
        return (pos.x() - c.x()) ** 2 + (pos.y() - c.y()) ** 2 <= (DISC + 4) ** 2

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self._on_disc(e.position()):
            self._pending = True
            QTimer.singleShot(240, self._single)

    def _single(self) -> None:
        if getattr(self, "_pending", False):
            self._pending = False
            self.on_toggle()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self._on_disc(e.position()):
            self._pending = False
            self.on_next()

    # ------------------------------------------------------------ paint
    def _disc_centre(self) -> QPointF:
        return QPointF(H / 2, H / 2) if self.side > 0 else QPointF(W - H / 2, H / 2)

    def paintEvent(self, _e) -> None:
        a = max(0.0, min(1.0, self.alpha.value))
        if a < 0.01 or self.track is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(a)
        body = QPainterPath()
        body.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), H / 2, H / 2)
        theme.paint_matte(p, body)

        c = self._disc_centre()
        self._paint_disc(p, c)

        # text block
        tx = H + 4 if self.side > 0 else 16
        tw = W - H - 46
        p.save()
        p.setClipRect(QRectF(tx, 8, tw, H - 16))
        p.setFont(theme.sans(9.4, bold=True))
        fm = p.fontMetrics()
        title = self.track.title
        width = fm.horizontalAdvance(title)
        off = 0.0
        if width > tw:                                         # marquee
            cycle = width + 40
            off = -((self.scroll - 30) % cycle) if self.scroll > 30 else 0.0
        p.setPen(QColor(theme.TEXT_HEX))
        p.drawText(QPointF(tx + off, 28), title)
        if width > tw and off < 0:
            p.drawText(QPointF(tx + off + width + 40, 28), title)
        p.setFont(theme.sans(8.2))
        p.setPen(QColor(theme.DIM_HEX))
        artist = p.fontMetrics().elidedText(self.track.artist or self.track.source, Qt.ElideRight, int(tw))
        p.drawText(QPointF(tx, 45), artist)
        p.restore()

        # equalizer
        ex = W - 30 if self.side > 0 else W - H - 30
        playing = self.track.playing
        for i in range(3):
            h = 4 + (10 * abs(math.sin(self._t * (5.2 + i * 1.7) + i)) if playing else 0)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.lime(0.9 if playing else 0.35))
            p.drawRoundedRect(QRectF(ex + i * 6, H / 2 + 7 - h, 3.2, h), 1.6, 1.6)

    def _paint_disc(self, p: QPainter, c: QPointF) -> None:
        p.save()
        p.translate(c)
        p.rotate(self.angle)
        # vinyl
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(8, 8, 10))
        p.drawEllipse(QPointF(0, 0), DISC, DISC)
        shine = QConicalGradient(QPointF(0, 0), 30)
        for stop, al in ((0.0, 0), (0.12, 38), (0.25, 0), (0.5, 0), (0.62, 30), (0.75, 0), (1.0, 0)):
            shine.setColorAt(stop, QColor(255, 255, 255, al))
        p.setBrush(shine)
        p.drawEllipse(QPointF(0, 0), DISC, DISC)
        p.setBrush(Qt.NoBrush)
        for r in range(15, DISC - 1, 3):                       # grooves
            p.setPen(QPen(QColor(255, 255, 255, 13), 0.8))
            p.drawEllipse(QPointF(0, 0), r, r)
        # label = album art
        lr = 17.5
        clip = QPainterPath()
        clip.addEllipse(QPointF(0, 0), lr, lr)
        p.setClipPath(clip)
        if self.art is not None:
            side = min(self.art.width(), self.art.height())
            src = QRectF((self.art.width() - side) / 2, (self.art.height() - side) / 2, side, side)
            p.drawImage(QRectF(-lr, -lr, 2 * lr, 2 * lr), self.art, src)
        else:
            g = QRadialGradient(QPointF(0, 0), lr)
            g.setColorAt(0, QColor(186, 226, 92))
            g.setColorAt(1, QColor(96, 130, 40))
            p.setBrush(g)
            p.drawEllipse(QPointF(0, 0), lr, lr)
        p.setClipping(False)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(QColor(20, 20, 22))
        p.drawEllipse(QPointF(0, 0), 2.2, 2.2)                 # spindle hole
        p.restore()
        # lime ring when playing
        on = max(0.0, min(1.0, self.spin.value / 0.55))
        p.setPen(QPen(theme.lime(0.25 + 0.6 * on), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, DISC + 2.5, DISC + 2.5)
