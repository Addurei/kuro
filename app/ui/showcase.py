"""The Show panel: when you ask Kuro to spin a wheel, flip a coin, roll a die
or chart some numbers, he doesn't just answer in words — he opens this panel
and actually does it, animated.

The result is decided by the `show` tool BEFORE the animation starts (so Kuro
can talk about it in his reply); the wheel/coin/die then lands exactly there.
"Again" re-runs locally and tells the brain nothing — it's just for fun.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme

TEXT = QColor("#e8eaee")
DIM = QColor("#8b8f98")
# calm segment palette: lime first, then muted hues that sit well on the glass
SEG = ["#bae25c", "#5cc8e2", "#e2a65c", "#b18ae8", "#e8718a", "#6fe0b2", "#e2d35c", "#7f9cf5"]


def spin_spec(kind: str, options: list[str] | None = None, labels: list[str] | None = None,
              values: list[float] | None = None, style: str = "bar", sides: int = 6,
              title: str = "") -> dict:
    """Build (and pre-decide) what the panel will show. Raises ValueError when
    the brain sends something that can't be shown; the message is for Kuro."""
    kind = (kind or "").lower().strip()
    if kind == "wheel":
        opts = [str(o).strip()[:40] for o in (options or []) if str(o).strip()]
        if len(opts) < 2:
            raise ValueError("a wheel needs at least 2 options")
        opts = opts[:12]
        win = random.randrange(len(opts))
        return {"kind": "wheel", "title": title or "Spin the wheel", "options": opts,
                "result": win, "text": opts[win]}
    if kind == "coin":
        side = random.choice(("Heads", "Tails"))
        return {"kind": "coin", "title": title or "Coin flip", "result": side, "text": side}
    if kind in ("dice", "die"):
        sides = max(2, min(100, int(sides or 6)))
        roll = random.randint(1, sides)
        return {"kind": "dice", "title": title or f"d{sides}", "sides": sides,
                "result": roll, "text": str(roll)}
    if kind == "chart":
        if not values or labels is None or len(labels) != len(values):
            raise ValueError("a chart needs matching labels and values")
        try:
            vals = [float(v) for v in values][:12]
        except (TypeError, ValueError):
            raise ValueError("chart values must be numbers")
        labs = [str(x)[:16] for x in labels][:12]
        style = style if style in ("bar", "line", "pie") else "bar"
        if style == "pie" and (len(vals) > 8 or any(v < 0 for v in vals)):
            style = "bar"                         # a pie can't show those
        return {"kind": "chart", "title": title or "Chart", "labels": labs,
                "values": vals, "style": style, "text": style + " chart"}
    raise ValueError("kind must be wheel, coin, dice or chart")


class ShowPage(QWidget):
    """One page, four acts. Everything is painted; one animation drives it."""
    back = Signal()

    def __init__(self):
        super().__init__()
        self.spec: dict = {}
        self.k = 0.0                              # animation progress 0..1
        self.landed = False
        self._sparks: list[tuple[float, float, float, float, float]] = []
        self._extra_turns = 4

        from .hud import IconButton                # (import here: hud imports us too)
        b = IconButton("back", 26)
        b.setToolTip("Back to chat")
        b.clicked.connect(self.back)
        self.title = QLabel("Show")
        self.title.setFont(theme.sans(10.5, bold=True))
        self.title.setStyleSheet("background: transparent;")
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(b)
        top.addWidget(self.title)
        top.addStretch()

        self.canvas = QWidget()
        self.canvas.setMinimumHeight(240)

        self.result = QLabel("")
        self.result.setAlignment(Qt.AlignCenter)
        self.result.setFont(theme.sans(12, bold=True))
        self.result.setStyleSheet(f"color: {theme.LIME.name()}; background: transparent;")

        self.again = QPushButton("AGAIN")
        self.again.setObjectName("seg")
        self.again.setCursor(Qt.PointingHandCursor)
        self.again.setFixedWidth(110)
        self.again.clicked.connect(self.rerun)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.result)
        lay.addWidget(self.again, 0, Qt.AlignHCenter)

        self.anim = QVariantAnimation(self, startValue=0.0, endValue=1.0)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self._land)
        self._spark_timer = QTimer(self, interval=33, timeout=self._spark_step)
        self.canvas.paintEvent = self._paint      # paint the canvas ourselves

    # ------------------------------------------------------------- control
    def load(self, spec: dict) -> None:
        self.spec = spec
        self.title.setText(spec.get("title", "Show"))
        self.landed = False
        self._sparks.clear()
        self.result.setText("")
        kind = spec["kind"]
        self.again.setText("SPIN AGAIN" if kind == "wheel" else
                           "FLIP AGAIN" if kind == "coin" else
                           "ROLL AGAIN" if kind == "dice" else "REPLAY")
        self.anim.stop()
        if kind == "wheel":
            self.anim.setDuration(3400)
            self.anim.setEasingCurve(QEasingCurve.OutQuint)
            self._extra_turns = random.randint(4, 6)
        elif kind == "coin":
            self.anim.setDuration(1700)
            self.anim.setEasingCurve(QEasingCurve.OutCubic)
        elif kind == "dice":
            self.anim.setDuration(1400)
            self.anim.setEasingCurve(QEasingCurve.OutCubic)
        else:
            self.anim.setDuration(850)
            self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.k = 0.0
        self.anim.start()

    def rerun(self) -> None:
        """A fresh local result; the conversation isn't involved."""
        if not self.spec or self.anim.state() == QVariantAnimation.Running:
            return
        sp = self.spec
        if sp["kind"] == "wheel":
            sp["result"] = random.randrange(len(sp["options"]))
            sp["text"] = sp["options"][sp["result"]]
        elif sp["kind"] == "coin":
            sp["result"] = sp["text"] = random.choice(("Heads", "Tails"))
        elif sp["kind"] == "dice":
            sp["result"] = random.randint(1, sp["sides"])
            sp["text"] = str(sp["result"])
        self.load(sp)

    def _step(self, v) -> None:
        self.k = float(v)
        self.canvas.update()

    def _land(self) -> None:
        self.landed = True
        sp = self.spec
        if sp.get("kind") == "wheel":
            self.result.setText(sp["options"][sp["result"]])
        elif sp.get("kind") in ("coin", "dice"):
            self.result.setText(str(sp["result"]))
        if sp.get("kind") != "chart":             # a little celebration
            w, h = self.canvas.width(), self.canvas.height()
            for _ in range(26):
                a = random.uniform(0, 2 * math.pi)
                sp_v = random.uniform(40, 170)
                self._sparks.append((w / 2, h / 2 - 8, math.cos(a) * sp_v,
                                     math.sin(a) * sp_v - 60, random.uniform(0.5, 1.1)))
            self._spark_timer.start()
        self.canvas.update()

    def _spark_step(self) -> None:
        out = []
        for x, y, vx, vy, life in self._sparks:
            life -= 0.033
            if life > 0:
                out.append((x + vx * 0.033, y + vy * 0.033, vx * 0.96, vy * 0.96 + 9, life))
        self._sparks = out
        if not out:
            self._spark_timer.stop()
        self.canvas.update()

    # ------------------------------------------------------------- painting
    def _paint(self, _e) -> None:
        if not self.spec:
            return
        p = QPainter(self.canvas)
        p.setRenderHint(QPainter.Antialiasing)
        kind = self.spec["kind"]
        if kind == "wheel":
            self._paint_wheel(p)
        elif kind == "coin":
            self._paint_coin(p)
        elif kind == "dice":
            self._paint_dice(p)
        else:
            self._paint_chart(p)
        col = QColor(theme.LIME)
        for x, y, _vx, _vy, life in self._sparks:
            col.setAlphaF(max(0.0, min(1.0, life)))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(x, y), 2.2 * life + 0.8, 2.2 * life + 0.8)
        p.end()

    def _paint_wheel(self, p: QPainter) -> None:
        sp = self.spec
        opts = sp["options"]
        n = len(opts)
        w, h = self.canvas.width(), self.canvas.height()
        r = min(w, h) / 2 - 16
        c = QPointF(w / 2, h / 2 + 4)
        seg = 360.0 / n
        # the pointer sits at 12 o'clock; the winner's middle must stop there
        target = self._extra_turns * 360 + (90 - (sp["result"] + 0.5) * seg)
        ang = self.k * target
        p.save()
        p.translate(c)
        p.rotate(-ang)                            # clockwise spin
        for i, label in enumerate(opts):
            a0 = i * seg
            col = QColor(SEG[i % len(SEG)])
            if self.landed and i == sp["result"]:
                col = col.lighter(125)
            path = QPainterPath(QPointF(0, 0))
            path.arcTo(QRectF(-r, -r, 2 * r, 2 * r), a0, seg)
            path.closeSubpath()
            g = QRadialGradient(QPointF(0, 0), r)
            g.setColorAt(0.25, col.darker(150))
            g.setColorAt(1.0, col)
            p.setPen(QPen(QColor(12, 12, 15), 2))
            p.setBrush(g)
            p.drawPath(path)
            # label along the segment's middle
            mid = math.radians(a0 + seg / 2)
            p.save()
            p.rotate(-(a0 + seg / 2))
            p.setPen(QColor(12, 12, 15))
            f = theme.sans(8.6 if n <= 8 else 7.6, bold=True)
            p.setFont(f)
            fm = p.fontMetrics()
            text = label if fm.horizontalAdvance(label) < r - 34 else \
                fm.elidedText(label, Qt.ElideRight, int(r - 34))
            p.drawText(QRectF(r * 0.3, -9, r * 0.66, 18), Qt.AlignVCenter | Qt.AlignRight, text)
            p.restore()
            del mid
        # hub
        p.setPen(QPen(QColor(50, 52, 60), 2))
        p.setBrush(QColor(16, 17, 20))
        p.drawEllipse(QPointF(0, 0), 13, 13)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.LIME)
        p.drawEllipse(QPointF(0, 0), 4.5, 4.5)
        p.restore()
        # pointer (a little wobble while the wheel is fast)
        speed = 0.0 if self.landed else (1 - self.k)
        wob = math.sin(ang / 6) * 6 * speed
        p.save()
        p.translate(c.x(), c.y() - r - 2)
        p.rotate(wob)
        tri = QPainterPath(QPointF(-8, -6))
        tri.lineTo(QPointF(8, -6))
        tri.lineTo(QPointF(0, 12))
        tri.closeSubpath()
        p.setPen(QPen(QColor(12, 12, 15), 2))
        p.setBrush(QColor("#e8eaee"))
        p.drawPath(tri)
        p.restore()

    def _paint_coin(self, p: QPainter) -> None:
        w, h = self.canvas.width(), self.canvas.height()
        c = QPointF(w / 2, h / 2)
        r = min(w, h) / 2 - 30
        k = self.k
        # it flips (squashes in x) several times, hops, and lands on the result
        flips = 7
        phase = k * flips * math.pi
        squash = abs(math.cos(phase)) if k < 0.96 else 1.0
        hop = math.sin(min(1.0, k * 1.04) * math.pi) * h * 0.22
        showing_heads = (int(phase / math.pi) % 2 == 0) if k < 0.96 \
            else self.spec["result"] == "Heads"
        p.save()
        p.translate(c.x(), c.y() - hop)
        p.scale(max(0.06, squash), 1.0)
        g = QLinearGradient(QPointF(-r, -r), QPointF(r, r))
        base = QColor("#e2c35c") if showing_heads else QColor("#c9ccd3")
        g.setColorAt(0.0, base.lighter(130))
        g.setColorAt(0.55, base)
        g.setColorAt(1.0, base.darker(145))
        p.setPen(QPen(base.darker(170), 3))
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), r, r)
        p.setPen(QPen(base.darker(150), 2))
        p.drawEllipse(QPointF(0, 0), r * 0.78, r * 0.78)
        p.setPen(base.darker(180))
        p.setFont(theme.sans(min(26.0, r * 0.42), bold=True))
        p.drawText(QRectF(-r, -r, 2 * r, 2 * r), Qt.AlignCenter,
                   "H" if showing_heads else "T")
        p.restore()
        # its shadow on the "table"
        sq = 1 - hop / (h * 0.3)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, int(70 * sq)))
        p.drawEllipse(QPointF(c.x(), c.y() + r + 12), r * 0.8 * sq + 4, 7 * sq + 2)

    def _paint_dice(self, p: QPainter) -> None:
        sp = self.spec
        w, h = self.canvas.width(), self.canvas.height()
        c = QPointF(w / 2, h / 2)
        size = min(w, h) * 0.42
        k = self.k
        # tumbles and shrinks of its bounce; the face settles on the result
        ang = (1 - k) * (1 - k) * 720 * (1 if sp["result"] % 2 else -1)
        hop = abs(math.sin(k * math.pi * 2.2)) * (1 - k) * h * 0.18
        p.save()
        p.translate(c.x(), c.y() - hop)
        p.rotate(ang)
        rr = QRectF(-size / 2, -size / 2, size, size)
        g = QLinearGradient(rr.topLeft(), rr.bottomRight())
        g.setColorAt(0.0, QColor("#f2f3f6"))
        g.setColorAt(1.0, QColor("#b9bcc6"))
        p.setPen(QPen(QColor(40, 42, 50), 3))
        p.setBrush(g)
        p.drawRoundedRect(rr, size * 0.18, size * 0.18)
        # face: pips up to 9, else the number
        value = sp["result"] if k > 0.6 else random.randint(1, sp["sides"])
        p.setPen(Qt.NoPen)
        if sp["sides"] <= 9 and value <= 9:
            spots = {1: [(0, 0)], 2: [(-1, -1), (1, 1)], 3: [(-1, -1), (0, 0), (1, 1)],
                     4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
                     5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
                     6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)],
                     7: [(-1, -1), (1, -1), (-1, 0), (0, 0), (1, 0), (-1, 1), (1, 1)],
                     8: [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)],
                     9: [(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)]}
            p.setBrush(QColor(26, 27, 33))
            step = size * 0.26
            for dx, dy in spots.get(value, []):
                p.drawEllipse(QPointF(dx * step, dy * step), size * 0.065, size * 0.065)
        else:
            p.setPen(QColor(26, 27, 33))
            p.setFont(theme.sans(size * 0.3, bold=True))
            p.drawText(rr, Qt.AlignCenter, str(value))
        p.restore()
        sq = 1 - hop / (h * 0.25)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, int(70 * sq)))
        p.drawEllipse(QPointF(c.x(), c.y() + size * 0.72), size * 0.55 * sq + 4, 7 * sq + 2)

    def _paint_chart(self, p: QPainter) -> None:
        sp = self.spec
        labels, values, style = sp["labels"], sp["values"], sp["style"]
        w, h = self.canvas.width(), self.canvas.height()
        k = self.k
        if style == "pie":
            total = sum(values) or 1.0
            r = min(w, h) / 2 - 36
            c = QPointF(w / 2 - (0 if len(labels) <= 4 else 40), h / 2)
            a0 = 90.0
            for i, v in enumerate(values):
                span = 360.0 * v / total * k
                col = QColor(SEG[i % len(SEG)])
                path = QPainterPath(c)
                path.arcTo(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), a0, -span)
                path.closeSubpath()
                p.setPen(QPen(QColor(12, 12, 15), 2))
                p.setBrush(col)
                p.drawPath(path)
                a0 -= 360.0 * v / total * k
            p.setFont(theme.sans(8.2))
            y = 18
            for i, (lab, v) in enumerate(zip(labels, values)):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(SEG[i % len(SEG)]))
                p.drawRect(QRectF(w - 108, y, 9, 9))
                p.setPen(TEXT)
                pct = 100 * v / total
                p.drawText(QRectF(w - 94, y - 3, 92, 16), Qt.AlignVCenter | Qt.AlignLeft,
                           f"{lab} {pct:.0f}%")
                y += 17
            return
        # bar / line share the frame
        ml, mr, mt, mb = 40, 12, 10, 34
        plot = QRectF(ml, mt, w - ml - mr, h - mt - mb)
        lo, hi = min(0.0, min(values)), max(0.0, max(values))
        if hi == lo:
            hi = lo + 1
        span = hi - lo

        def ypix(v: float) -> float:
            return plot.bottom() - (v - lo) / span * plot.height()
        # gridlines + axis numbers
        p.setFont(theme.sans(7.4))
        for frac in (0.0, 0.5, 1.0):
            gy = plot.top() + plot.height() * frac
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawLine(QPointF(plot.left(), gy), QPointF(plot.right(), gy))
            p.setPen(DIM)
            val = hi - span * frac
            txt = f"{val:,.0f}" if abs(val) >= 10 else f"{val:,.3g}"
            p.drawText(QRectF(0, gy - 8, ml - 6, 16), Qt.AlignVCenter | Qt.AlignRight, txt)
        n = len(values)
        p.setFont(theme.sans(7.8))
        if style == "bar":
            bw = plot.width() / n
            for i, v in enumerate(values):
                col = QColor(SEG[i % len(SEG)]) if n <= len(SEG) else theme.LIME
                x = plot.left() + i * bw + bw * 0.18
                y0, y1 = ypix(0), ypix(v * k)
                rect = QRectF(x, min(y0, y1), bw * 0.64, abs(y0 - y1))
                g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                g.setColorAt(0.0, col)
                g.setColorAt(1.0, col.darker(160))
                p.setPen(Qt.NoPen)
                p.setBrush(g)
                p.drawRoundedRect(rect, 4, 4)
        else:
            pts = [QPointF(plot.left() + plot.width() * (i / max(1, n - 1)), ypix(v))
                   for i, v in enumerate(values)]
            reveal = k * (n - 1)
            path = QPainterPath(pts[0])
            area = QPainterPath(QPointF(pts[0].x(), ypix(max(lo, 0.0))))
            area.lineTo(pts[0])
            for i in range(1, n):
                if reveal >= i:
                    path.lineTo(pts[i])
                    area.lineTo(pts[i])
                else:
                    u = reveal - (i - 1)
                    if u > 0:
                        mx = pts[i - 1].x() + (pts[i].x() - pts[i - 1].x()) * u
                        my = pts[i - 1].y() + (pts[i].y() - pts[i - 1].y()) * u
                        path.lineTo(QPointF(mx, my))
                        area.lineTo(QPointF(mx, my))
                    break
            end = min(plot.right(), plot.left() + plot.width() * min(1.0, reveal / max(1, n - 1)))
            area.lineTo(QPointF(end, ypix(max(lo, 0.0))))
            area.closeSubpath()
            fill = QColor(theme.LIME)
            fill.setAlpha(36)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(area)
            p.setPen(QPen(theme.LIME, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.LIME)
            for i, pt in enumerate(pts):
                if reveal >= i:
                    p.drawEllipse(pt, 3.2, 3.2)
        # x labels (thin out when crowded)
        p.setPen(DIM)
        every = max(1, math.ceil(n / 7))
        for i, lab in enumerate(labels):
            if i % every:
                continue
            cx = plot.left() + (i + 0.5) * plot.width() / n if style == "bar" else \
                plot.left() + plot.width() * (i / max(1, n - 1))
            p.drawText(QRectF(cx - 40, plot.bottom() + 6, 80, 16), Qt.AlignHCenter | Qt.AlignTop,
                       p.fontMetrics().elidedText(lab, Qt.ElideRight, 78))
