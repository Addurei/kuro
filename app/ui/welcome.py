"""First launch: the cat introduces itself, learns your name and gets a brain.

Designed like a product onboarding, not a form: a calm type hierarchy
(eyebrow, title, one line of support), one idea per step, a footer that
always says what happens next, and motion that explains (the progress fills,
each step slides in, the finish draws its own check mark).

Everything character-specific (name, fur, pronouns, accent) comes from the
character dict, so a custom character introduces itself, not Kuro.
"""
from __future__ import annotations

import html
import math
import threading

from PySide6.QtCore import (QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import theme
from .. import hotkeys as HK
from .. import providers as P
from ..character import CURRENT as _CHAR

TEXT = QColor(theme.TEXT_HEX)
DIM = QColor(theme.DIM_HEX)
SOFT = QColor(166, 169, 178)              # between text and dim: supporting copy
_A = _CHAR["accent"][:3]
ACC_RGB = "%d, %d, %d" % tuple(_A)

STEPS = 3                                  # hello, name, brain (the finish fills all)


def _font(size: float, weight: int = 400, spacing: float = 0.0, mono: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(theme.MONO if mono else theme.SANS)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


# the welcome's own look, layered over theme.CSS for this page only
CSS = f"""
QLabel#body {{ color: rgb(166, 169, 178); font-size: 9.5pt; }}
QLabel#rowTitle {{ color: {theme.TEXT_HEX}; font-size: 9.5pt; font-weight: 500; }}
QLabel#rowText {{ color: {theme.DIM_HEX}; font-size: 8.5pt; }}
QLabel#note {{ color: {theme.DIM_HEX}; font-size: 8.5pt; }}
QLabel#key {{ color: {theme.TEXT_HEX}; font-family: {theme.MONO_CSS}; font-size: 7.5pt;
              font-weight: 500; padding: 2px 6px; border-radius: 5px;
              background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #24262c, stop:1 #1a1b20);
              border: 1px solid #30323a; border-bottom: 2px solid #0c0c0f; }}
QLabel#chipText {{ color: rgb(206, 208, 214); font-size: 8.5pt; }}
QFrame#wfield {{ background: #0c0d10; border: 1px solid #25272e; border-radius: 10px; }}
QFrame#wfield[focus="true"] {{ border: 1px solid rgba({ACC_RGB}, 0.75); background: #0e0f12; }}
QFrame#wfield[error="true"] {{ border: 1px solid rgba(255, 93, 108, 0.7); }}
QFrame#wfield QLineEdit {{ font-size: 10.5pt; padding: 9px 2px; color: {theme.TEXT_HEX}; }}
QFrame#panel {{ background: rgba(255, 255, 255, 0.022); border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 12px; }}
QFrame#hair {{ background: rgba(255, 255, 255, 0.06); border: none; }}
QPushButton#primary {{ font-family: {theme.SANS_CSS}; font-size: 9.5pt; font-weight: 600;
                       padding: 0 18px; min-height: 34px; border-radius: 10px; color: #0b0b0c;
                       background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                   stop:0 {theme.ACC_LIGHT2}, stop:1 {theme.ACC_LIGHT}); }}
QPushButton#primary:hover {{ background: {theme.ACC_LIGHT2}; }}
QPushButton#primary:disabled {{ background: rgba({ACC_RGB}, 0.35); color: rgba(10, 10, 11, 0.6); }}
QPushButton#ghost {{ font-family: {theme.SANS_CSS}; font-size: 9pt; font-weight: 500;
                     color: {theme.DIM_HEX}; padding: 0 10px; min-height: 34px; }}
QPushButton#ghost:hover {{ color: {theme.TEXT_HEX}; }}
QPushButton#secondary {{ font-family: {theme.SANS_CSS}; font-size: 9pt; font-weight: 500;
                         color: {theme.TEXT_HEX}; padding: 0 12px; min-height: 30px;
                         border-radius: 8px; background: rgba(255, 255, 255, 0.045);
                         border: 1px solid rgba(255, 255, 255, 0.09); }}
QPushButton#secondary:hover {{ background: rgba(255, 255, 255, 0.08);
                               border-color: rgba(255, 255, 255, 0.16); }}
QPushButton#chip {{ font-family: {theme.SANS_CSS}; font-size: 8.5pt; color: rgb(206, 208, 214);
                    text-align: left; padding: 7px 11px; border-radius: 9px;
                    background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.07); }}
QPushButton#chip:hover {{ border-color: rgba({ACC_RGB}, 0.55); color: {theme.TEXT_HEX};
                          background: rgba({ACC_RGB}, 0.07); }}
"""


# ====================================================================== parts
class Text(QWidget):
    """A label that keeps its own font (theme.CSS sets fonts on every QWidget,
    which would erase the tight tracking display type needs)."""

    def __init__(self, text: str, font: QFont, color: QColor, wrap: bool = False):
        super().__init__()
        self._text, self._font, self._color, self._wrap = text, font, QColor(color), wrap
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def setText(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def _flags(self) -> int:
        return int(Qt.AlignLeft | Qt.AlignTop | (Qt.TextWordWrap if self._wrap else 0))

    def hasHeightForWidth(self) -> bool:
        return self._wrap

    def heightForWidth(self, w: int) -> int:
        fm = QFontMetricsF(self._font)
        return math.ceil(fm.boundingRect(QRectF(0, 0, max(1, w), 10000), self._flags(),
                                         self._text).height()) + 1

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self._font)
        w = math.ceil(fm.horizontalAdvance(self._text)) + 2
        if self._wrap:
            w = min(w, 320)
        return QSize(w, self.heightForWidth(w) if self._wrap else math.ceil(fm.height()) + 1)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, self.sizeHint().height()) if self._wrap else self.sizeHint()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self._font)
        p.setPen(self._color)
        p.drawText(QRectF(self.rect()), self._flags(), self._text)


def eyebrow(text: str, accent: bool = False) -> Text:
    return Text(text.upper(), _font(7.2, 500, 1.4, mono=True),
                theme.lime(0.9) if accent else QColor(118, 121, 130))


def title(text: str) -> Text:
    return Text(text, _font(17.5, 600, -0.45), TEXT, wrap=True)


def body(text: str) -> QLabel:
    lab = QLabel(f'<p style="line-height:140%">{text}</p>')
    lab.setObjectName("body")
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    return lab


def button(text: str, kind: str = "primary") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    return b


def hairline() -> QFrame:
    f = QFrame()
    f.setObjectName("hair")
    f.setFixedHeight(1)
    return f


def glyph(p: QPainter, kind: str, c: QPointF, s: float, color: QColor) -> None:
    """The few line icons the welcome needs beyond theme.icon."""
    if kind not in ("bolt", "spark", "lock", "key", "check", "arrow", "info"):
        theme.icon(p, kind, c, s, color)
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(color, max(1.4, s * 0.09), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    x, y, h = c.x(), c.y(), s / 2
    path = QPainterPath()
    if kind == "bolt":
        for i, (dx, dy) in enumerate(((0.15, -1.0), (-0.55, 0.12), (0.0, 0.12),
                                      (-0.15, 1.0), (0.55, -0.12), (0.0, -0.12))):
            pt = QPointF(x + dx * h, y + dy * h)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "spark":
        for i, (dx, dy) in enumerate(((0, -1), (0.22, -0.22), (1, 0), (0.22, 0.22),
                                      (0, 1), (-0.22, 0.22), (-1, 0), (-0.22, -0.22))):
            pt = QPointF(x + dx * h * 0.95, y + dy * h * 0.95)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "lock":
        p.drawRoundedRect(QRectF(x - h * 0.7, y - h * 0.1, h * 1.4, h * 1.05), h * 0.22, h * 0.22)
        p.drawArc(QRectF(x - h * 0.42, y - h * 0.85, h * 0.84, h * 1.3), 0, 180 * 16)
    elif kind == "key":
        p.drawEllipse(QPointF(x - h * 0.42, y), h * 0.38, h * 0.38)
        p.drawLine(QPointF(x - h * 0.04, y), QPointF(x + h * 0.9, y))
        p.drawLine(QPointF(x + h * 0.62, y), QPointF(x + h * 0.62, y + h * 0.35))
    elif kind == "check":
        path.moveTo(x - h * 0.55, y + h * 0.02)
        path.lineTo(x - h * 0.15, y + h * 0.42)
        path.lineTo(x + h * 0.6, y - h * 0.38)
        p.drawPath(path)
    elif kind == "arrow":
        p.drawLine(QPointF(x - h * 0.6, y), QPointF(x + h * 0.6, y))
        p.drawLine(QPointF(x + h * 0.2, y - h * 0.4), QPointF(x + h * 0.6, y))
        p.drawLine(QPointF(x + h * 0.2, y + h * 0.4), QPointF(x + h * 0.6, y))
    elif kind == "info":
        p.drawEllipse(c, h * 0.85, h * 0.85)
        p.drawLine(QPointF(x, y - h * 0.05), QPointF(x, y + h * 0.42))
        p.drawPoint(QPointF(x, y - h * 0.38))
    p.restore()


class IconTile(QWidget):
    """A small rounded tile holding an accent line icon."""

    def __init__(self, kind: str, size: int = 30):
        super().__init__()
        self.kind = kind
        self.setFixedSize(size, size)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.lime(0.22), 1))
        p.setBrush(theme.lime(0.09))
        p.drawRoundedRect(r, 8, 8)
        glyph(p, self.kind, r.center(), r.width() * 0.48, theme.lime(0.95))


class Progress(QWidget):
    """Three slim segments that fill as you go; the fill glides, not jumps."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(3)
        self._v = 0.0
        self._anim = QVariantAnimation(self, duration=520,
                                       easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set)

    def _set(self, v) -> None:
        self._v = float(v)
        self.update()

    def to(self, value: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._v)
        self._anim.setEndValue(float(value))
        self._anim.start()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        gap = 6.0
        seg = (self.width() - gap * (STEPS - 1)) / STEPS
        for i in range(STEPS):
            r = QRectF(i * (seg + gap), 0, seg, self.height())
            p.setBrush(QColor(255, 255, 255, 18))
            p.drawRoundedRect(r, 1.5, 1.5)
            fill = max(0.0, min(1.0, self._v - i))
            if fill > 0:
                p.setBrush(theme.lime(0.95))
                p.drawRoundedRect(QRectF(r.left(), 0, seg * fill, self.height()), 1.5, 1.5)


class Spinner(QWidget):
    """A quiet status line: spinning arc while working, then a dot + text."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(18)
        self._state, self._text, self._a = "", "", 0.0
        self._timer = QTimer(self, interval=16, timeout=self._spin)
        self._font = _font(8.5)

    def _spin(self) -> None:
        self._a = (self._a + 7.0) % 360
        self.update()

    def set(self, state: str, text: str) -> None:
        """state: '' (hidden), 'busy', 'error' or 'ok'."""
        self._state, self._text = state, text
        self._timer.start() if state == "busy" else self._timer.stop()
        self.update()

    def paintEvent(self, e) -> None:
        if not self._state:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(7, self.height() / 2)
        color = {"busy": SOFT, "error": QColor(theme.DANGER_HEX), "ok": theme.lime()}[self._state]
        if self._state == "busy":
            p.setPen(QPen(QColor(255, 255, 255, 30), 1.6))
            p.drawEllipse(c, 5, 5)
            p.setPen(QPen(theme.lime(), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(c.x() - 5, c.y() - 5, 10, 10), int(-self._a * 16), 100 * 16)
        else:
            glyph(p, "check" if self._state == "ok" else "info", c, 11, color)
        p.setFont(self._font)
        p.setPen(color)
        p.drawText(QRectF(20, 0, self.width() - 20, self.height()),
                   int(Qt.AlignVCenter | Qt.AlignLeft), self._text)


class CheckBadge(QWidget):
    """The finish line: a ring draws itself, then the check, with a soft glow."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(46, 46)
        self._t = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=900,
                                       easingCurve=QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_t", float(v)), self.update()))

    def play(self) -> None:
        self._t = 0.0
        self._anim.stop()
        self._anim.start()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        ring = min(1.0, self._t / 0.6)
        tick = max(0.0, (self._t - 0.45) / 0.55)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.lime(0.10 * ring))
        p.drawEllipse(c, 22, 22)
        p.setPen(QPen(theme.lime(0.9), 2.0, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(c.x() - 17, c.y() - 17, 34, 34), 90 * 16, int(-360 * 16 * ring))
        if tick > 0:
            path = QPainterPath(QPointF(c.x() - 7, c.y() + 0.5))
            path.lineTo(c.x() - 2, c.y() + 5.5)
            path.lineTo(c.x() + 8, c.y() - 5)
            p.setPen(QPen(theme.lime(), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setClipRect(QRectF(c.x() - 9, 0, 19 * tick, self.height()))
            p.drawPath(path)


class WField(QFrame):
    """A text field with a label-less, focus-lit frame."""

    def __init__(self, edit: QLineEdit, *extra: QWidget):
        super().__init__()
        self.setObjectName("wfield")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(6)
        lay.addWidget(edit, 1)
        for w in extra:
            lay.addWidget(w)
        self.edit = edit
        edit.installEventFilter(self)
        edit.textEdited.connect(lambda _t: self.mark("error", False))

    def mark(self, prop: str, on: bool) -> None:
        if (self.property(prop) == "true") == on:
            return
        self.setProperty(prop, "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == ev.Type.FocusIn:
            self.mark("focus", True)
        elif ev.type() == ev.Type.FocusOut:
            self.mark("focus", False)
        return False


def feature(kind: str, head: str, text: str) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)
    row.addWidget(IconTile(kind), 0, Qt.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(1)
    t = QLabel(head)
    t.setObjectName("rowTitle")
    d = QLabel(text)
    d.setObjectName("rowText")
    d.setWordWrap(True)
    col.addWidget(t)
    col.addWidget(d)
    row.addLayout(col, 1)
    return w


def shortcut(keys: list[str], what: str) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    d = QLabel(what)
    d.setObjectName("chipText")
    row.addWidget(d)
    row.addStretch()
    for k in keys:
        cap = QLabel(k)
        cap.setObjectName("key")
        row.addWidget(cap)
    return w


# ====================================================================== page
class WelcomePage(QWidget):
    """First launch in three short steps plus a finish. Groq is the
    recommended brain: free, fast, and far bigger free limits than Gemini."""
    step_changed = Signal(int)            # 0 hello, 1 name, 2 brain, 3 done (for reactions)
    finished = Signal()
    other_brains = Signal()
    try_prompt = Signal(str)              # a "try saying" chip on the finish
    height_wanted = Signal(int)           # each step asks for its own panel height
    _tested = Signal(bool, str)

    def __init__(self, settings, tester):
        super().__init__()
        self.s, self.tester = settings, tester
        self.setStyleSheet(CSS)
        self.cat = settings.cat_name or _CHAR["name"]
        self.progress = Progress()
        self.steps = QStackedWidget()
        for build in (self._hello, self._name, self._brain, self._done):
            self.steps.addWidget(build())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(0)
        lay.addWidget(self.progress)
        lay.addSpacing(18)
        lay.addWidget(self.steps, 1)
        self._tested.connect(self._show_test)
        self._index = -1
        self._slide = None
        self.go(0, animate=False)

    # ------------------------------------------------------------ layout
    def _page(self, eyebrow_text: str, title_text: str, sub: str, content: list[QWidget],
              left: list[QWidget], right: list[QWidget]) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        w.eyebrow = eyebrow(eyebrow_text)
        w.title = title(title_text)
        w.sub = body(sub)
        lay.addWidget(w.eyebrow)
        lay.addSpacing(8)
        lay.addWidget(w.title)
        lay.addSpacing(6)
        lay.addWidget(w.sub)
        lay.addSpacing(18)
        for c in content:
            lay.addWidget(c)
        lay.addStretch(1)
        lay.addSpacing(12)
        lay.addWidget(hairline())
        lay.addSpacing(12)
        foot = QHBoxLayout()
        foot.setSpacing(4)
        for b in left:
            foot.addWidget(b)
        foot.addStretch()
        for b in right:
            foot.addWidget(b)
        lay.addLayout(foot)
        return w

    @staticmethod
    def _stack(*widgets: QWidget, spacing: int = 14) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        for x in widgets:
            lay.addWidget(x)
        return w

    # ------------------------------------------------------------ steps
    def _hello(self) -> QWidget:
        name, fur = html.escape(self.cat), _CHAR.get("fur_word", "black")
        start = button("Get started")
        start.clicked.connect(lambda: self.go(1))
        hint = QLabel("Takes about a minute")
        hint.setObjectName("note")
        return self._page(
            f"Meet {self.cat}", f"Hi, I'm {self.cat}.",
            f"A little {fur} cat who lives on your desktop and quietly helps you get "
            "things done.",
            [self._stack(
                feature("chat", "Talk or type", f"Chat, ask anything, or just say “Hey {name}.”"),
                feature("bolt", "Gets things done", "Opens apps, plays music, finds files and more."),
                feature("timer", "Keeps you focused", "Focus sessions, reminders and study quizzes."),
                feature("spark", "Remembers you", "Learns what matters. Memories stay on this PC."),
            )],
            left=[hint], right=[start])

    def _name(self) -> QWidget:
        self.name_edit = QLineEdit(self.s.user_name if self.s.user_name != "friend" else "")
        self.name_edit.setPlaceholderText("Your name")
        self.name_edit.setMaxLength(40)
        self.name_edit.returnPressed.connect(self._save_name)
        back = button("Back", "ghost")
        back.clicked.connect(lambda: self.go(0))
        nxt = button("Continue")
        nxt.clicked.connect(self._save_name)
        note = QLabel("Only used to talk to you. Change it any time in Settings.")
        note.setObjectName("note")
        note.setWordWrap(True)
        return self._page(
            "Step 1 of 2  ·  About you", "What should I call you?",
            "A first name or a nickname both work.",
            [self._stack(WField(self.name_edit), note, spacing=10)],
            left=[back], right=[nxt])

    def _brain(self) -> QWidget:
        open_btn = button("Open Groq  ↗", "secondary")
        open_btn.clicked.connect(lambda: __import__("webbrowser").open(P.PRESETS["groq"]["key_url"]))
        steps = QFrame()
        steps.setObjectName("panel")
        sl = QVBoxLayout(steps)
        sl.setContentsMargins(14, 12, 12, 12)
        sl.setSpacing(9)
        for i, text in enumerate(("Sign in to Groq (Google works)",
                                  "Create an API key and copy it",
                                  "Paste it below"), 1):
            row = QHBoxLayout()
            row.setSpacing(10)
            num = QLabel(str(i))
            num.setObjectName("key")
            num.setFixedSize(20, 20)
            num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet("padding: 0;")
            row.addWidget(num, 0, Qt.AlignVCenter)
            lab = QLabel(text)
            lab.setObjectName("chipText")
            row.addWidget(lab, 1)
            if i == 1:
                row.addWidget(open_btn)
            sl.addLayout(row)

        self.key_edit = QLineEdit("")
        self.key_edit.setPlaceholderText("gsk_…")
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.returnPressed.connect(self._test)
        reveal = button("Show", "ghost")
        reveal.setStyleSheet("min-height: 26px; padding: 0 6px; font-size: 8.5pt;")
        reveal.clicked.connect(lambda: (
            self.key_edit.setEchoMode(QLineEdit.Normal if self.key_edit.echoMode()
                                      == QLineEdit.Password else QLineEdit.Password),
            reveal.setText("Hide" if self.key_edit.echoMode() == QLineEdit.Normal else "Show")))
        self.key_field = WField(self.key_edit, reveal)
        self.status = Spinner()

        skip = button("Skip for now", "ghost")
        skip.clicked.connect(lambda: self.go(3))
        other = button("Other providers", "ghost")
        other.clicked.connect(self.other_brains)
        self.test_btn = button("Connect")
        self.test_btn.clicked.connect(self._test)
        return self._page(
            "Step 2 of 2  ·  Connect a brain", "Give me a brain",
            "I think with <b style='color:#e9e9ec'>Groq</b>: free, very fast, and generous "
            "daily limits.",
            [self._stack(steps, self.key_field, self.status, spacing=10)],
            left=[skip, other], right=[self.test_btn])

    def _done(self) -> QWidget:
        self.badge = CheckBadge()
        hk = lambda combo: [HK.pretty(k) for k in combo.split("+") if k]  # noqa: E731
        keys = self._stack(
            shortcut(["Double-click"], "Open chat"),
            shortcut(["Right-click"], "Menu and settings"),
            shortcut(hk(self.s.hotkey_chat), "Chat from anywhere"),
            shortcut(hk(self.s.hotkey_voice), "Talk"),
            spacing=8)
        self.tries = self._stack(*(self._chip(t) for t in
                                   ("Open Spotify", "Focus for 25 minutes",
                                    "What's on my screen?")), spacing=6)
        self.try_label = eyebrow("Try saying")
        start = button(f"Start using {self.cat}")
        start.clicked.connect(self._finish)
        page = self._page("All set", "", "",
                          [keys, self._gap(18), self.try_label, self._gap(8), self.tries],
                          left=[], right=[start])
        # the badge sits above the eyebrow on this step
        page.layout().insertWidget(0, self.badge)
        page.layout().insertSpacing(1, 12)
        page.eyebrow._color = theme.lime(0.9)
        self.done_page = page
        return page

    def _chip(self, text: str) -> QPushButton:
        b = button(f"“{text}”", "chip")
        b.clicked.connect(lambda: (self._finish(), self.try_prompt.emit(text)))
        return b

    @staticmethod
    def _gap(h: int) -> QWidget:
        w = QWidget()
        w.setFixedHeight(h)
        return w

    # ------------------------------------------------------------ flow
    def go(self, i: int, animate: bool = True) -> None:
        back = i < self._index
        self._index = i
        self.steps.setCurrentIndex(i)
        self.progress.to(min(STEPS, i + 1) if i < 3 else STEPS)
        page = self.steps.widget(i)
        if i == 1:
            self.name_edit.setFocus()
        elif i == 2:
            self.status.set("", "")
            self.key_edit.setFocus()
        elif i == 3:
            user = self.s.user_name if self.s.user_name not in ("", "friend") else ""
            page.title.setText(f"You're all set, {user}." if user else "You're all set.")
            page.sub.setText(
                f'<p style="line-height:140%">{html.escape(self.cat)} is awake and ready '
                "when you are.</p>" if self.s.ready else
                f'<p style="line-height:140%">{html.escape(self.cat)} still needs a brain to '
                "reply. Add one any time: right-click → Settings → AI &amp; Voice.</p>")
            self.tries.setVisible(bool(self.s.ready))
            self.try_label.setVisible(bool(self.s.ready))
            self.badge.play()
        self.height_wanted.emit(self.wanted_height(i))
        if animate:
            self._enter(page, -1 if back else 1)
        self.step_changed.emit(i)

    WIDTH = 384

    def wanted_height(self, i: int) -> int:
        """The panel height step i needs at our width: no dead space, no clipping."""
        page = self.steps.widget(i)
        m = self.layout().contentsMargins()
        inner = self.WIDTH - m.left() - m.right()
        lay = page.layout()
        lay.activate()
        h = lay.totalHeightForWidth(inner) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        return int(m.top() + m.bottom() + self.progress.height() + 18 + h + 6)

    def _enter(self, page: QWidget, direction: int) -> None:
        """Slide the new step in from the side it logically comes from."""
        fx = QGraphicsOpacityEffect(page)
        fx.setOpacity(0.0)
        page.setGraphicsEffect(fx)
        if self._slide:
            self._slide.stop()
        anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=340,
                                 easingCurve=QEasingCurve.OutCubic)

        def step(v):
            v = float(v)
            fx.setOpacity(v)
            page.move(int(round(direction * 18 * (1 - v))), 0)

        def done():
            page.move(0, 0)
            page.setGraphicsEffect(None)    # don't keep paying for the effect

        anim.valueChanged.connect(step)
        anim.finished.connect(done)
        anim.start()
        self._slide = anim

    def _save_name(self) -> None:
        name = self.name_edit.text().strip()
        if name:
            self.s.user_name = name
            self._save()
        self.go(2)

    def _test(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.key_field.mark("error", True)
            self.status.set("error", "Paste your key first.")
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Connecting…")
        self.status.set("busy", "Checking your key with Groq…")

        def work():
            try:
                used = self.tester("groq", key, "")
                self._tested.emit(True, used)
            except Exception as e:
                kind = getattr(e, "kind", "")
                msg = {"auth": "That key didn't work. Copy it again from Groq.",
                       "connection": "Can't reach Groq. Is the internet on?",
                       "rate": "Groq is busy for a moment. Try again in a minute."
                       }.get(kind, getattr(e, "detail", "") or str(e))
                self._tested.emit(False, msg[:120])
        threading.Thread(target=work, daemon=True).start()

    def _show_test(self, ok: bool, text: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Connect")
        if ok:
            self.s.set_key("groq", self.key_edit.text().strip())
            self.s.provider, self.s.model = "groq", ""
            self._save()
            self.status.set("ok", "Connected")
            QTimer.singleShot(450, lambda: self.go(3))
            return
        self.key_field.mark("error", True)
        self.status.set("error", text)

    def _finish(self) -> None:
        self.s.onboarded = True
        self._save()
        self.finished.emit()

    def _save(self) -> None:
        try:
            self.s.save()
        except OSError:
            pass
