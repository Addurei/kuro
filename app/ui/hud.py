"""Kuro's HUD: one panel pinned above his head.

It follows him in real time and morphs between pages:

    bubble    what he just said (click it to open the chat)
    chat      the conversation
    settings  your name, the brain (Gemini, Groq, Ollama, …) with a Test button, switches
    memory    what he has learned about you
    confirm   "allow this?" before anything risky

Only one page shows at a time. Opening one smoothly reshapes the panel
instead of spawning a new window. A lime light runs around the border
(faster while he's thinking, red when he's asking for permission).

The window itself is a fixed, mostly transparent canvas; only the panel
is painted. On Windows, clicks on the transparent part fall through to
whatever is underneath.
"""
from __future__ import annotations

import html
import math
import textwrap
import threading

from PySide6.QtCore import (QEasingCurve, QElapsedTimer, QEvent, QPoint, QPointF,
                            QPropertyAnimation, QRectF, QVariantAnimation,
                            Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import theme
from .. import hotkeys as HK
from .. import providers as P
from ..voice import VOICES
from .cat_rig import Spring
from .theme import DIM_HEX, FAINT_HEX, LIME_HEX, TEXT_HEX

CANVAS_W, CANVAS_H = 460, 620
GAP = 4            # between the panel's pointer and Kuro's head
POINTER = 8


# =================================================================== widgets
class IconButton(QAbstractButton):
    def __init__(self, kind: str, size: int = 30, filled: bool = False, parent=None):
        super().__init__(parent)
        self.kind, self.filled, self.active = kind, filled, False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._t = 0.0
        self._pulse = QTimer(self, interval=30, timeout=self._step)

    def set_active(self, on: bool) -> None:
        self.active = on
        self._pulse.start() if on else self._pulse.stop()
        self.update()

    def _step(self) -> None:
        self._t += 0.03
        self.update()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        c = r.center()
        hover = self.underMouse()
        if self.filled:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#cdf07a") if hover else theme.LIME)
            p.drawEllipse(r)
            theme.icon(p, self.kind, c, r.width() * 0.42, QColor(10, 10, 11))
            return
        if self.active:
            for k in range(2):
                ph = (self._t + k * 0.5) % 1.0
                p.setPen(QPen(theme.lime(0.7 * (1 - ph)), 1.4))
                p.setBrush(Qt.NoBrush)
                rr = r.width() / 2 * (0.7 + 0.35 * ph)
                p.drawEllipse(c, rr, rr)
        if hover or self.active:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawEllipse(r.adjusted(2, 2, -2, -2))
        col = theme.LIME if (self.active or (hover and self.kind != "close")) else \
            QColor(theme.DANGER_HEX) if (hover and self.kind == "close") else QColor("#9a9da5")
        theme.icon(p, self.kind, c, r.width() * 0.46, col)


class HoverFX(QWidget):
    """A soft highlight laid over any widget: fades in on hover, flashes on
    press, fades out on leave. Mouse events pass straight through."""

    def __init__(self, target: QWidget, radius: float = 9.0, strength: float = 1.0):
        super().__init__(target)
        self.target, self.radius, self.strength = target, radius, strength
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self._hover = Spring(0.0, 260, 0.9)
        self._press = Spring(0.0, 380, 0.9)
        self._timer = QTimer(self, interval=16, timeout=self._step)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._clock = QElapsedTimer()
        target.installEventFilter(self)
        self.resize(target.size())
        self.raise_()

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t == QEvent.Enter and obj.isEnabled():
            self._hover.target = 1.0
            self._go()
        elif t == QEvent.Leave:
            self._hover.target = 0.0
            self._go()
        elif t == QEvent.MouseButtonPress and obj.isEnabled():
            self._press.value, self._press.target = 1.0, 0.0
            self._go()
        elif t == QEvent.Resize:
            self.resize(obj.size())
        elif t == QEvent.EnabledChange and not obj.isEnabled():
            self._hover.target = 0.0
            self._go()
        return False

    def _go(self) -> None:
        self.raise_()
        if not self._timer.isActive():
            self._clock.restart()
            self._timer.start()

    def _step(self) -> None:
        dt = min(self._clock.restart() / 1000.0, 1 / 20) or 0.016
        self._hover.step(dt)
        self._press.step(dt)
        done = all(abs(sp.value - sp.target) < 0.003 and abs(sp.vel) < 0.02
                   for sp in (self._hover, self._press))
        if done:
            self._hover.value, self._press.value = self._hover.target, self._press.target
            self._timer.stop()
        self.update()

    def paintEvent(self, _e) -> None:
        h = max(0.0, min(1.0, self._hover.value))
        k = max(0.0, min(1.0, self._press.value))
        if h < 0.01 and k < 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(186, 226, 92, int((70 * h + 90 * k) * self.strength)), 1.0))
        p.setBrush(QColor(255, 255, 255, int((10 * h + 26 * k) * self.strength)))
        p.drawRoundedRect(r, self.radius, self.radius)


def hover_fx(root: QWidget) -> None:
    """Give every button and clickable row under `root` the hover highlight."""
    for w in [root] + root.findChildren(QWidget):
        if getattr(w, "_has_fx", False) or isinstance(w, HoverFX):
            continue
        name = w.objectName()
        if isinstance(w, QPushButton) and name in ("seg", "primary", "ghost"):
            radius = 7.0 if name == "seg" else 10.0
        elif name == "brainrow":
            radius = 11.0
        else:
            continue
        w._has_fx = True
        HoverFX(w, radius, 0.8 if name == "seg" else 1.0)


class Toggle(QAbstractButton):
    """A small pill switch with a sliding knob."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(34, 18)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = Spring(1.0 if checked else 0.0, 300, 0.7)
        self._hov = Spring(0.0, 260, 0.9)
        self._timer = QTimer(self, interval=16, timeout=self._step)
        self.toggled.connect(lambda on: (setattr(self._pos, "target", 1.0 if on else 0.0),
                                         self._timer.start()))

    def enterEvent(self, e) -> None:
        self._hov.target = 1.0
        self._timer.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hov.target = 0.0
        self._timer.start()
        super().leaveEvent(e)

    def _step(self) -> None:
        self._pos.step(0.016)
        self._hov.step(0.016)
        if all(abs(sp.value - sp.target) < 0.002 and abs(sp.vel) < 0.01
               for sp in (self._pos, self._hov)):
            self._timer.stop()
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        v = max(0.0, min(1.0, self._pos.value))
        track = QColor(theme.LIME)
        track.setAlphaF(0.15 + 0.85 * v)
        off = QColor("#24262c")
        col = QColor(int(off.red() + (track.red() - off.red()) * v),
                     int(off.green() + (track.green() - off.green()) * v),
                     int(off.blue() + (track.blue() - off.blue()) * v))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 0, 34, 18), 9, 9)
        hov = max(0.0, min(1.0, self._hov.value))
        if hov > 0.01:                               # hover: a soft ring around the track
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(186, 226, 92, int(110 * hov)), 1.2))
            p.drawRoundedRect(QRectF(0.6, 0.6, 32.8, 16.8), 8.4, 8.4)
            p.setPen(Qt.NoPen)
        x = 9 + 16 * self._pos.value
        p.setBrush(QColor(10, 10, 11) if v > 0.5 else QColor("#9a9da5"))
        p.drawEllipse(QPointF(x, 9), 6.2, 6.2)


class StatusOrb(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.mode = "online"
        self._t = 0.0
        self._timer = QTimer(self, interval=33, timeout=self._step)

    def showEvent(self, e):            # only animate while it's on screen
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _step(self) -> None:
        self._t += 0.033 * (3.2 if self.mode in ("thinking", "listening") else 1.0)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(8, 8)
        base = QColor("#5a5d65") if self.mode == "offline" else theme.LIME
        ph = (self._t * 0.8) % 1.0
        p.setPen(QPen(theme.with_alpha(base, 0.55 * (1 - ph)), 1.3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 2.5 + 5 * ph, 2.5 + 5 * ph)
        p.setPen(Qt.NoPen)
        p.setBrush(base)
        p.drawEllipse(c, 2.8, 2.8)


class Field(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("field")

    def set_focus_look(self, on: bool) -> None:
        self.setProperty("focus", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class FocusLineEdit(QLineEdit):
    focus_changed = Signal(bool)

    def focusInEvent(self, e):
        self.focus_changed.emit(True)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.focus_changed.emit(False)
        super().focusOutEvent(e)


def _field(widget: QWidget, *extra: QWidget) -> Field:
    f = Field()
    lay = QHBoxLayout(f)
    lay.setContentsMargins(12, 2, 4, 2)
    lay.setSpacing(4)
    lay.addWidget(widget, 1)
    for w in extra:
        lay.addWidget(w)
    if isinstance(widget, FocusLineEdit):
        widget.focus_changed.connect(f.set_focus_look)
    return f


def _caption(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setFont(theme.mono(7.2, spacing=125))
    lab.setStyleSheet(f"color: {DIM_HEX};")
    return lab


def _title(text: str, color: str = LIME_HEX) -> QLabel:
    lab = QLabel(text)
    lab.setFont(theme.mono(8.5, bold=True, spacing=140))
    lab.setStyleSheet(f"color: {color};")
    return lab


# ===================================================================== pages
class BubblePage(QWidget):
    clicked = Signal()

    def __init__(self, cat_name: str):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setFont(theme.sans(10))
        self.label.setStyleSheet(f"color: {TEXT_HEX};")
        self._full = ""
        self._shown = 0
        self._typer = QTimer(self, interval=16, timeout=self._type)

    def set_text(self, text: str) -> tuple[int, int]:
        """Lay out for the full text; returns the page size it needs."""
        self._shown = 0
        return self.extend(text)

    def extend(self, text: str) -> tuple[int, int]:
        """Streaming: the text grew. Keep what's already typed, type the rest."""
        self._full = text
        fm = self.label.fontMetrics()
        width = min(260, max(90, fm.horizontalAdvance(text) + 6))
        self.label.setFixedWidth(width)
        self.label.setText(text)
        self.label.adjustSize()
        h = self.label.heightForWidth(width)
        self.label.setFixedHeight(h)
        self.label.move(16, 12)
        self.label.setText(text[: self._shown])
        self._typer.start()
        return width + 32, h + 24

    def _type(self) -> None:
        step = max(1, len(self._full) // 50)
        self._shown = min(len(self._full), self._shown + step)
        self.label.setText(self._full[: self._shown])
        if self._shown >= len(self._full):
            self._typer.stop()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class TypingDots(QWidget):
    """Three dots that bounce in turn while Kuro is thinking."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 14)
        self._t = 0.0
        self._timer = QTimer(self, interval=30, timeout=self._step)

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _step(self) -> None:
        self._t += 0.03
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for i in range(3):
            ph = (self._t * 2.2 - i * 0.18) % 1.0
            up = math.sin(min(ph, 0.5) * 2 * math.pi) if ph < 0.5 else 0.0
            p.setBrush(theme.lime(0.45 + 0.55 * max(0.0, up)))
            p.drawEllipse(QPointF(7 + i * 12, 8 - 3.2 * max(0.0, up)), 3, 3)


BUBBLE_CSS = {
    "cat": ("QLabel { background: #141519; border: 1px solid #1e2025; color: %s;"
            " border-radius: 14px; border-bottom-left-radius: 5px; padding: 8px 12px; }" % TEXT_HEX),
    "you": ("QLabel { background: #232832; color: #eef0f3;"
            " border-radius: 14px; border-bottom-right-radius: 5px; padding: 8px 12px; }"),
}


class Message(QWidget):
    """One chat bubble. (No per-bubble opacity effect: nesting it inside the
    HUD's own fade makes Qt's painter misbehave.)"""

    def __init__(self, who: str, text: str, max_width: int, full_text: str = ""):
        super().__init__()
        self.who = who
        self.full = full_text or text
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setTextFormat(Qt.PlainText)
        if who == "system":
            self.label.setAlignment(Qt.AlignCenter)
            self.label.setFont(theme.mono(7, spacing=115))
            self.label.setStyleSheet(f"color: {FAINT_HEX}; padding: 4px;")
            lay.addWidget(self.label, 1)
        else:
            self.label.setFont(theme.sans(9.8))
            self.label.setStyleSheet(BUBBLE_CSS[who])
            self.label.setMaximumWidth(max_width)
            fm = self.label.fontMetrics()           # size for the whole reply up front
            self.label.setMinimumWidth(min(max_width, fm.horizontalAdvance(full_text or text) + 30))
            if who == "you":
                lay.addStretch(1)
            lay.addWidget(self.label)
            if who == "cat":
                lay.addStretch(1)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.label.setText(text)


class ChatPage(QWidget):
    submitted = Signal(str)
    mic_clicked = Signal()
    clear_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, cat_name: str):
        super().__init__()
        self.cat_name = cat_name
        self.busy = False
        self.bubble_w = 236
        self._typing: tuple[Message, str, int] | None = None

        self.orb = StatusOrb()
        name = QLabel(cat_name)
        name.setFont(theme.sans(10.5, bold=True))
        new = IconButton("refresh", 26)
        new.setToolTip("New conversation")
        new.clicked.connect(self.clear_clicked)
        close = IconButton("close", 26)
        close.setToolTip("Close")
        close.clicked.connect(self.close_clicked)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.orb)
        head.addWidget(name)
        head.addStretch()
        head.addWidget(new)
        head.addWidget(close)

        # conversation: real widgets in a scroll area (no HTML re-rendering)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.rows = QVBoxLayout(inner)
        self.rows.setContentsMargins(0, 4, 6, 4)
        self.rows.setSpacing(0)
        self.rows.addStretch(1)
        self.empty = QLabel(f"Ask {html.escape(cat_name)} to open an app, find a file,\n"
                            f"set a reminder, or just talk.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {FAINT_HEX};")
        self.rows.addWidget(self.empty)
        self.dots_row = QWidget()
        dl = QHBoxLayout(self.dots_row)
        dl.setContentsMargins(0, 10, 0, 0)
        dots_bubble = QFrame()
        dots_bubble.setStyleSheet("QFrame { background: #141519; border: 1px solid #1e2025;"
                                  " border-radius: 13px; border-bottom-left-radius: 5px; }")
        db = QHBoxLayout(dots_bubble)
        db.setContentsMargins(10, 7, 10, 7)
        db.addWidget(TypingDots())
        dl.addWidget(dots_bubble)
        dl.addStretch(1)
        self.dots_row.hide()
        self.rows.addWidget(self.dots_row)
        self.scroll.setWidget(inner)
        self.scroll.verticalScrollBar().rangeChanged.connect(
            lambda _lo, hi: self.scroll.verticalScrollBar().setValue(hi))

        self.input = FocusLineEdit()
        self.input.setFont(theme.sans(10))
        self.input.setPlaceholderText(f"Message {cat_name}…")
        self.input.returnPressed.connect(self._send)
        self.mic = IconButton("mic", 30)
        self.mic.setToolTip("Talk")
        self.mic.clicked.connect(self.mic_clicked)
        send = IconButton("send", 30, filled=True)
        send.clicked.connect(self._send)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 14, 14)
        lay.setSpacing(8)
        lay.addLayout(head)
        lay.addWidget(self.scroll, 1)
        lay.addWidget(_field(self.input, self.mic, send))

        self._typer = QTimer(self, interval=20, timeout=self._type_step)
        self._last_who = ""

    # status lives in the orb only: calm when idle, quick while thinking
    def set_status(self, mode: str) -> None:
        self.orb.mode = mode
        self.mic.set_active(mode == "listening")

    def add(self, who: str, text: str, animate: bool = True) -> None:
        self._finish_typing()
        self.empty.hide()
        msg = Message(who, "" if (who == "cat" and animate) else text, self.bubble_w, text)
        gap = 3 if who == self._last_who and who != "system" else 10
        msg.setContentsMargins(0, gap, 0, 0)
        self._last_who = who
        self.rows.insertWidget(self.rows.count() - 1, msg)   # above the typing dots
        if who == "cat" and animate:
            self._typing = (msg, text, 0)
            self._typer.start()

    def stream(self, text: str) -> None:
        """A reply that's still arriving: one bubble that grows as words come in."""
        if getattr(self, "_stream_msg", None) is None:
            self._finish_typing()
            self.empty.hide()
            self.dots_row.hide()
            msg = Message("cat", text, self.bubble_w, text)
            gap = 3 if self._last_who == "cat" else 10
            msg.setContentsMargins(0, gap, 0, 0)
            self._last_who = "cat"
            self.rows.insertWidget(self.rows.count() - 1, msg)
            self._stream_msg = msg
        msg = self._stream_msg
        msg.full = text
        fm = msg.label.fontMetrics()
        msg.label.setMinimumWidth(min(self.bubble_w, fm.horizontalAdvance(text) + 30))
        msg.set_text(text)

    def end_stream(self, final: str) -> bool:
        """The reply is complete. False if nothing was streamed (caller adds it)."""
        msg, self._stream_msg = getattr(self, "_stream_msg", None), None
        if msg is None:
            return False
        msg.full = final
        msg.set_text(final)
        return True

    def set_bubble_width(self, w: int) -> None:
        """Chat size changed: resize the bubbles already on screen too."""
        if w == self.bubble_w:
            return
        self.bubble_w = w
        for i in range(self.rows.count()):
            m = self.rows.itemAt(i).widget()
            if isinstance(m, Message) and m.who != "system":
                m.label.setMaximumWidth(w)
                fm = m.label.fontMetrics()
                m.label.setMinimumWidth(min(w, fm.horizontalAdvance(m.full) + 30))

    def _type_step(self) -> None:
        if not self._typing:
            self._typer.stop()
            return
        msg, text, n = self._typing
        n = min(len(text), n + max(1, len(text) // 60))
        msg.set_text(text[:n])
        self._typing = (msg, text, n)
        if n >= len(text):
            self._typing = None
            self._typer.stop()

    def _finish_typing(self) -> None:
        if self._typing:
            msg, text, _ = self._typing
            msg.set_text(text)
            self._typing = None
            self._typer.stop()

    def clear(self) -> None:
        self._finish_typing()
        self._stream_msg = None
        for i in reversed(range(self.rows.count())):
            w = self.rows.itemAt(i).widget()
            if isinstance(w, Message):
                self.rows.removeWidget(w)
                w.deleteLater()
        self._last_who = ""
        self.empty.show()

    def texts(self) -> list[tuple[str, str]]:
        out = []
        for i in range(self.rows.count()):
            w = self.rows.itemAt(i).widget()
            if isinstance(w, Message):
                out.append((w.who, w.label.text()))
        return out

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.input.setEnabled(not busy)
        self.dots_row.setVisible(busy)
        if not busy:
            self.input.setFocus()

    def _send(self) -> None:
        text = self.input.text().strip()
        if text and not self.busy:
            self.input.clear()
            self.submitted.emit(text)


class Segmented(QWidget):
    """A row of pill buttons, one selected (like iOS segmented control).
    The selection highlight slides from the old choice to the new one."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, small: bool = False):
        super().__init__()
        self.buttons: dict[str, QPushButton] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        pad = "padding: 3px 8px;" if small else ""
        for key, label in options:
            b = QPushButton(label)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            # the selected look is painted by the sliding pill, not the button
            b.setStyleSheet(f"QPushButton#seg {{ {pad} background: transparent;"
                            f" border: 1px solid transparent; }}"
                            f"QPushButton#seg:checked {{ background: transparent;"
                            f" border-color: transparent; color: {LIME_HEX}; }}")
            b.clicked.connect(lambda _c=False, k=key: self.set_value(k, emit=True))
            self.buttons[key] = b
            lay.addWidget(b)
        self._x, self._w = Spring(0, 320, 0.82), Spring(0, 320, 0.82)
        self._placed = False
        self._timer = QTimer(self, interval=16, timeout=self._step)
        self._timer.setTimerType(Qt.PreciseTimer)
        self.set_value(value)

    def _target(self) -> QRectF:
        b = self.buttons[self.value]
        return QRectF(b.geometry())

    def set_value(self, key: str, emit: bool = False) -> None:
        if key not in self.buttons:
            key = next(iter(self.buttons))
        self.value = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        r = self._target()
        self._x.target, self._w.target = r.x(), r.width()
        if self._placed and self.isVisible():
            self._timer.start()
        else:
            self._snap()
        if emit:
            self.changed.emit(key)

    def _snap(self) -> None:
        r = self._target()
        self._x.value = self._x.target = r.x()
        self._w.value = self._w.target = r.width()
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        QTimer.singleShot(0, self._snap)
        self._placed = True

    def showEvent(self, e) -> None:
        super().showEvent(e)
        QTimer.singleShot(0, self._snap)
        self._placed = True

    def _step(self) -> None:
        for sp in (self._x, self._w):
            sp.step(0.016)
        if all(abs(sp.value - sp.target) < 0.6 and abs(sp.vel) < 8 for sp in (self._x, self._w)):
            self._x.value, self._w.value = self._x.target, self._w.target
            self._timer.stop()
        self.update()

    def paintEvent(self, _e) -> None:
        if not self.buttons:
            return
        r = self._target()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor("#202228"), 1.0))
        p.setBrush(QColor("#111216"))
        rad = min(10.0, track.height() / 2)
        p.drawRoundedRect(track, rad, rad)
        pill = QRectF(self._x.value + 0.5, r.y() + 0.5, max(8.0, self._w.value) - 1,
                      r.height() - 1)
        p.setPen(QPen(QColor(LIME_HEX), 1.0))
        p.setBrush(QColor(186, 226, 92, 30))
        p.drawRoundedRect(pill, rad - 2, rad - 2)


class Card(QFrame):
    """A rounded group of settings rows with thin dividers (iOS-style)."""

    def __init__(self, title: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet("QFrame#card { background: #121317; border: 1px solid #1d1f24;"
                           " border-radius: 12px; }")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 4, 12, 4)
        self._lay.setSpacing(0)
        self.title = title
        self._rows = 0

    def add(self, w: QWidget) -> QWidget:
        if self._rows:
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet("background: #1b1c21; border: none;")
            self._lay.addWidget(line)
            w._divider = line                  # hidden together with its row
        self._lay.addWidget(w)
        self._rows += 1
        return w


def _section(title: str) -> QLabel:
    lab = QLabel(title)
    lab.setStyleSheet(f"color: {DIM_HEX}; font-size: 8pt; padding: 8px 4px 4px 4px;")
    return lab


def _row(label: str, widget: QWidget | None = None, hint: str = "") -> QWidget:
    w = QWidget()
    w.setMinimumHeight(40)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 7, 0, 7)
    lay.setSpacing(10)
    col = QVBoxLayout()
    col.setSpacing(1)
    lab = QLabel(label)
    lab.setStyleSheet(f"color: {TEXT_HEX}; font-size: 9.5pt; background: transparent;")
    col.addWidget(lab)
    if hint:
        h = QLabel(hint)
        h.setWordWrap(True)
        h.setStyleSheet(f"color: {DIM_HEX}; font-size: 8pt; background: transparent;")
        col.addWidget(h)
    lay.addLayout(col, 1)
    if widget is not None:
        lay.addWidget(widget, 0, Qt.AlignVCenter)
    return w


class InlineEdit(FocusLineEdit):
    """A borderless right-aligned text box that lives inside a settings row."""

    def __init__(self, text: str = "", placeholder: str = "", width: int = 150,
                 secret: bool = False):
        super().__init__(text)
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setStyleSheet(f"QLineEdit {{ color: {LIME_HEX}; font-size: 9.5pt; padding: 2px 0; }}"
                           f"QLineEdit:focus {{ color: {TEXT_HEX}; }}")
        if secret:
            self.setEchoMode(QLineEdit.Password)


def _rgba(hex_: str, alpha: float) -> str:
    """'#bae25c', 0.13 -> 'rgba(186, 226, 92, 0.13)'. (Qt reads 8-digit hex as
    #AARRGGBB, not the web's #RRGGBBAA, so tints are spelled out.)"""
    c = QColor(hex_)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


class BrainRow(QFrame):
    """One brain in the picker. Compact (badge, name, tag, status) until it's
    the chosen one; then its one-line description shows too."""
    picked = Signal(str)

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        pre = P.PRESETS[name]
        self.setObjectName("brainrow")
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 6, 9, 6)
        lay.setSpacing(9)
        accent = P.ACCENT.get(name, LIME_HEX)
        badge = QLabel(pre["label"][0])
        badge.setFixedSize(24, 24)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"background: {_rgba(accent, 0.13)}; color: {accent};"
                            f" border: 1px solid {_rgba(accent, 0.4)};"
                            " border-radius: 12px; font-weight: 700; font-size: 9pt;")
        lay.addWidget(badge, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title = QLabel(pre["label"])
        self.title.setStyleSheet(f"color: {TEXT_HEX}; font-size: 9.5pt; font-weight: 600;"
                                 " background: transparent;")
        top.addWidget(self.title)
        tag = pre.get("badge") or P.TAG.get(name, "")
        self.tag = QLabel(tag)
        self.tag.setStyleSheet(f"color: {LIME_HEX if pre.get('badge') else DIM_HEX};"
                               " font-size: 7.5pt; background: transparent;")
        top.addWidget(self.tag)
        top.addStretch()
        col.addLayout(top)
        self.blurb = QLabel(P.BLURB.get(name, ""))
        self.blurb.setWordWrap(True)
        self.blurb.setStyleSheet(f"color: {DIM_HEX}; font-size: 8pt; background: transparent;")
        col.addWidget(self.blurb)
        lay.addLayout(col, 1)
        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.status, 0, Qt.AlignVCenter)
        self.set_state(False, "")

    def set_state(self, selected: bool, status: str) -> None:
        self.blurb.setVisible(selected)
        border = LIME_HEX if selected else "#1d1f24"
        bg = "#161911" if selected else "#121317"
        self.setStyleSheet(f"QFrame#brainrow {{ background: {bg}; border: 1px solid {border};"
                           " border-radius: 11px; }")
        styles = {"In use": ("#0e0f12", LIME_HEX, LIME_HEX),
                  "Backup": (LIME_HEX, "transparent", _rgba(LIME_HEX, 0.45)),
                  "Key saved": (TEXT_HEX, "transparent", "#3a3d45"),
                  "Local": (TEXT_HEX, "transparent", "#3a3d45"),
                  "Add key": (DIM_HEX, "transparent", "#2a2c32")}
        fg, bgc, bd = styles.get(status, (DIM_HEX, "transparent", "#2a2c32"))
        self.status.setText(status)
        self.status.setVisible(bool(status))
        self.status.setStyleSheet(f"color: {fg}; background: {bgc}; border: 1px solid {bd};"
                                  " border-radius: 7px; padding: 1px 7px; font-size: 7.5pt;")

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.picked.emit(self.name)


class HotkeyButton(QPushButton):
    """Click, press the new shortcut, done. Esc cancels."""
    captured = Signal(str)            # 'ctrl+alt+k'
    capturing = Signal(bool)          # global hotkeys pause while you choose

    def __init__(self, value: str):
        super().__init__(HK.pretty(value))
        self.setObjectName("seg")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.value, self.listening = value, False
        self.clicked.connect(self._start)

    def set_value(self, value: str) -> None:
        self.value = value
        if not self.listening:
            self.setText(HK.pretty(value))

    def _start(self) -> None:
        self.listening = True
        self.setText("Press keys…  (Esc cancels)")
        self.setChecked(True)
        self.capturing.emit(True)
        self.window().activateWindow()
        self.setFocus()

    def _stop(self) -> None:
        self.listening = False
        self.setText(HK.pretty(self.value))
        self.capturing.emit(False)

    def focusOutEvent(self, e) -> None:
        if self.listening:
            self._stop()
        super().focusOutEvent(e)

    def keyPressEvent(self, e) -> None:
        if not self.listening:
            return super().keyPressEvent(e)
        key = e.key()
        if key == Qt.Key_Escape:
            self._stop()
            return
        if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr):
            return                                   # wait for the real key
        m = e.modifiers()
        mods = {name for flag, name in ((Qt.ControlModifier, "ctrl"), (Qt.AltModifier, "alt"),
                                        (Qt.ShiftModifier, "shift"), (Qt.MetaModifier, "windows"))
                if m & flag}
        name = QKeySequence(key).toString() or e.text()
        if not name:
            return
        self.listening = False
        self.capturing.emit(False)
        self.captured.emit(HK.combo(mods, name))


class SettingsPage(QWidget):
    """Clean, grouped settings. Changes save automatically."""
    saved = Signal()
    hotkey_capture = Signal(bool)
    back = Signal()
    google_clicked = Signal()
    _tested = Signal(bool, str)

    TABS = [("general", "General"), ("ai", "AI && Voice"), ("focus", "Focus"),
            ("google", "Google")]

    def __init__(self, settings, tester):
        super().__init__()
        self.s = settings
        self.tester = tester          # callable(key) -> model name, raises on failure
        self.toggles: dict[str, Toggle] = {}
        self._loading = False

        back = IconButton("back", 26)
        back.setToolTip("Back")
        back.clicked.connect(self.back)
        title = QLabel("Settings")
        title.setFont(theme.sans(10.5, bold=True))
        self.saved_note = QLabel("")
        self.saved_note.setStyleSheet(f"color: {DIM_HEX}; font-size: 8pt;")
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(back)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.saved_note)

        self.tabbar = Segmented(self.TABS, "general", small=True)
        self.pages = QStackedWidget()
        self.tabbar.changed.connect(self._switch_tab)
        for key, _label in self.TABS:
            content = getattr(self, f"_tab_{key}")()
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sc.setWidget(content)
            self.pages.addWidget(sc)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 12, 14)
        lay.setSpacing(10)
        lay.addLayout(top)
        lay.addWidget(self.tabbar)
        lay.addWidget(self.pages, 1)
        self._tested.connect(self._show_result)
        self._note_timer = QTimer(self, singleShot=True, timeout=lambda: self.saved_note.setText(""))

    def _switch_tab(self, key: str) -> None:
        """New tab fades in while drifting up a few pixels into place."""
        i = [t for t, _ in self.TABS].index(key)
        if i == self.pages.currentIndex():
            return
        self.pages.setCurrentIndex(i)
        page = self.pages.widget(i)
        inner = page.widget() if isinstance(page, QScrollArea) else page
        lay = inner.layout()
        eff = QGraphicsOpacityEffect(page)
        eff.setOpacity(0.0)
        page.setGraphicsEffect(eff)
        anim = QVariantAnimation(self)
        anim.setDuration(230)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def frame(v, eff=eff, lay=lay):
            eff.setOpacity(float(v))
            if lay is not None:
                lay.setContentsMargins(0, int(round(8 * (1 - float(v)))), 6, 0)

        def done(page=page, lay=lay):
            page.setGraphicsEffect(None)          # no effect left running once settled
            if lay is not None:
                lay.setContentsMargins(0, 0, 6, 0)
        anim.valueChanged.connect(frame)
        anim.finished.connect(done)
        anim.start(QVariantAnimation.DeleteWhenStopped)

    # ------------------------------------------------------------ building blocks
    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(4)
        return w, lay

    def _toggle(self, attr: str, label: str, hint: str = "") -> QWidget:
        tg = Toggle(bool(getattr(self.s, attr)))
        tg.toggled.connect(lambda on, a=attr: self._set(a, on))
        self.toggles[attr] = tg
        return _row(label, tg, hint)

    def _edit(self, attr: str, placeholder: str, width: int = 150, secret: bool = False,
              fallback: str = "") -> InlineEdit:
        e = InlineEdit(str(getattr(self.s, attr)), placeholder, width, secret)
        e.editingFinished.connect(lambda a=attr, w=e, fb=fallback:
                                  self._set(a, w.text().strip() or fb))
        setattr(self, f"edit_{attr}", e)
        return e

    def _set(self, attr: str, value) -> None:
        if self._loading or getattr(self.s, attr) == value:
            return
        setattr(self.s, attr, value)
        try:
            self.s.save()
        except OSError:
            return
        self.saved_note.setText("Saved ✓")
        self._note_timer.start(1400)
        if attr in ("auto_fallback", "provider"):
            self._refresh_rows()
        self.saved.emit()

    # ------------------------------------------------------------ tabs
    def _tab_general(self) -> QWidget:
        w, lay = self._page()
        you = Card()
        you.add(_row("Your name", self._edit("user_name", "your name", fallback="friend")))
        self.lang_pick = Segmented([("English", "English"), ("Filipino", "Filipino"),
                                    ("Taglish", "Taglish")], self.s.language, small=True)
        self.lang_pick.changed.connect(lambda v: self._set("language", v))
        you.add(_row("Language", self.lang_pick))

        kuro = Card()
        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(70, 150)
        self.scale.setSingleStep(5)
        self.scale.setFixedWidth(120)
        self.scale.setCursor(Qt.PointingHandCursor)
        self.scale.setValue(int(round(self.s.scale * 100)))
        self.scale.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: #23252b; border-radius: 2px; }}"
            f"QSlider::sub-page:horizontal {{ background: {LIME_HEX}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ background: {LIME_HEX}; width: 14px; height: 14px;"
            f" margin: -6px 0; border-radius: 7px; }}"
            f"QSlider::handle:horizontal:hover {{ background: #d4f08e; width: 16px; height: 16px;"
            f" margin: -7px 0; border-radius: 8px; }}")
        self.scale.sliderReleased.connect(lambda: self._set("scale", self.scale.value() / 100))
        self.scale.valueChanged.connect(
            lambda v: None if self.scale.isSliderDown() else self._set("scale", v / 100))
        kuro.add(_row("Size", self.scale))
        kuro.add(self._toggle("wander", "Wander around"))
        kuro.add(self._toggle("dnd_fullscreen", "Hide in fullscreen"))
        kuro.add(self._toggle("morning_briefing", "Daily briefing"))

        priv = Card()
        priv.add(self._toggle("learn_enabled", "Learn from chats"))
        notice = Toggle(bool(self.s.aware and self.s.react_activity))
        notice.toggled.connect(self._set_notice)
        self.notice_toggle = notice
        priv.add(_row("Notice what you're doing", notice, "App names only, never contents"))

        keys = Card()
        self.hotkey_btns: dict[str, HotkeyButton] = {}
        for label, attr in (("Chat", "hotkey_chat"), ("Talk", "hotkey_voice"),
                            ("Quick actions", "hotkey_quick")):
            b = HotkeyButton(getattr(self.s, attr))
            b.captured.connect(lambda c, a=attr: self._hotkey(a, c))
            b.capturing.connect(self.hotkey_capture)
            self.hotkey_btns[attr] = b
            keys.add(_row(label, b))
        self.hotkey_note = QLabel("")
        self.hotkey_note.setWordWrap(True)
        self.hotkey_note.setVisible(False)
        keys.add(self.hotkey_note)

        for title, card in (("YOU", you), ("KURO", kuro), ("PRIVACY", priv),
                            ("SHORTCUTS", keys)):
            lay.addWidget(_section(title))
            lay.addWidget(card)
        lay.addStretch()
        return w

    def _set_notice(self, on: bool) -> None:
        """One switch for everything that looks at what you're doing: the active
        app, his reactions (props, typing along) and habit suggestions."""
        if self._loading:
            return
        self._loading = True
        self.s.aware = self.s.react_activity = self.s.suggestions = bool(on)
        self._loading = False
        try:
            self.s.save()
        except OSError:
            return
        self.saved_note.setText("Saved ✓")
        self._note_timer.start(1400)
        self.saved.emit()

    def _tab_ai(self) -> QWidget:
        w, lay = self._page()
        # brain picker: a list of brains; the chosen one opens up with its key
        picker = QWidget()
        self.brain_list = QVBoxLayout(picker)
        self.brain_list.setContentsMargins(0, 0, 0, 0)
        self.brain_list.setSpacing(6)
        self.brain_rows: dict[str, BrainRow] = {}
        names = P.ORDER + (["cloud"] if getattr(self.s, "cloud_url", "") else [])
        for name in names:
            row = BrainRow(name)
            row.picked.connect(self._pick_brain)
            self.brain_rows[name] = row
            self.brain_list.addWidget(row)

        # the details that open under the chosen brain
        self.detail = QFrame()
        self.detail.setObjectName("braindetail")
        self.detail.setStyleSheet("QFrame#braindetail { background: #0f1013; border: 1px solid #1d1f24;"
                                  " border-radius: 10px; }")
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(12, 4, 12, 8)
        dl.setSpacing(0)
        self.key = InlineEdit("", "paste key", 150, secret=True)
        self.key.editingFinished.connect(self._key_edited)
        self.test_btn = QPushButton("Test")
        self.test_btn.setObjectName("seg")
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.clicked.connect(self._test)
        keyrow = QWidget()
        kr = QHBoxLayout(keyrow)
        kr.setContentsMargins(0, 0, 0, 0)
        kr.setSpacing(6)
        kr.addWidget(self.key)
        kr.addWidget(self.test_btn)
        self.key_row = _row("Key", keyrow)
        self.key_label = self.key_row.findChildren(QLabel)[0]
        dl.addWidget(self.key_row)
        self.result = QLabel("")
        self.result.setOpenExternalLinks(True)
        self.result.setWordWrap(True)
        dl.addWidget(self.result)
        self._detail_anim = QPropertyAnimation(self.detail, b"maximumHeight", self)
        self._detail_anim.setDuration(220)
        self._detail_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.backup_note = QLabel("")
        self.backup_note.setWordWrap(True)
        self.backup_note.setStyleSheet(f"color: {DIM_HEX}; font-size: 8pt; padding: 6px 4px 0 4px;")

        voice = Card()
        voice.add(self._toggle("speak_replies", "Speak replies"))
        self.voice_pick = Segmented([(k, v[0]) for k, v in VOICES.items()] + [("windows", "Windows")],
                                    self.s.voice, small=True)
        self.voice_pick.changed.connect(lambda v: self._set("voice", v))
        self.voice_row = _row("Voice", self.voice_pick)
        voice.add(self.voice_row)
        self.cute_row = self._toggle("cute_voice", "Cute voice")
        voice.add(self.cute_row)
        self.toggles["speak_replies"].toggled.connect(self._voice_rows)
        voice.add(self._toggle("wake_word", "“Hey Kuro”", "Talk to him hands-free"))

        lay.addWidget(_section("BRAIN"))
        lay.addWidget(picker)
        lay.addWidget(self.backup_note)
        lay.addWidget(_section("VOICE"))
        lay.addWidget(voice)
        self._voice_rows(self.s.speak_replies)
        lay.addStretch()
        self._show_brain()
        return w

    def _voice_rows(self, on: bool) -> None:
        """Voice choices only matter if he speaks."""
        for row in (self.voice_row, self.cute_row):
            row.setVisible(bool(on))
            if getattr(row, "_divider", None) is not None:
                row._divider.setVisible(bool(on))

    def _hotkey(self, attr: str, value: str) -> None:
        current = {a: getattr(self.s, a) for a in self.hotkey_btns}
        why = HK.check(value, attr, current)
        btn = self.hotkey_btns[attr]
        self.hotkey_note.setVisible(True)
        if why:
            btn.set_value(getattr(self.s, attr))
            self.hotkey_note.setText(why)
            self.hotkey_note.setStyleSheet(f"color: {theme.DANGER_HEX}; font-size: 8pt;"
                                           " padding: 2px 0 8px 0;")
            return
        btn.set_value(value)
        self.hotkey_note.setText(f"Saved: {HK.pretty(value)}")
        self.hotkey_note.setStyleSheet(f"color: {LIME_HEX}; font-size: 8pt; padding: 2px 0 8px 0;")
        self._set(attr, value)

    def _brain_status(self, name: str) -> str:
        has_key = bool(self.s.key_for(name).strip())
        if name == self.s.provider:
            return "In use"
        if name in P.backups(self.s):
            return "Backup"
        if name == "ollama":
            return "Local"
        return "Key saved" if has_key else "Add key"

    def _show_brain(self, note: str = "", animate: bool = False) -> None:
        """Reflect the chosen brain: highlight its row, open its details
        right under it, and show every other brain's status."""
        name = self.s.provider if self.s.provider in P.PRESETS else "gemini"
        pre = P.PRESETS[name]
        for n, row in self.brain_rows.items():
            row.set_state(n == name, self._brain_status(n))
        self._backup_note()
        # move the details under the chosen row
        self.brain_list.removeWidget(self.detail)
        idx = list(self.brain_rows).index(name) if name in self.brain_rows else 0
        self.brain_list.insertWidget(idx + 1, self.detail)
        self.key.setVisible(pre["needs_key"])
        self.key_label.setText(f"{pre['label']} key" if pre["needs_key"] else "No key needed")
        self.key.setText(self.s.key_for(name))
        verb = "Get a free key" if pre["tag"] == "free" else \
            "Get Ollama" if name == "ollama" else "Get a key"
        link = (f' · <a style="color:{LIME_HEX}; text-decoration:none" '
                f'href="{pre["key_url"]}">{verb} ↗</a>') if pre["key_url"] else ""
        self.result.setText(f'{html.escape(note or pre["key_hint"])}{link}')
        self.result.setStyleSheet(f"color: {DIM_HEX}; font-size: 8pt; padding: 0 0 8px 0;")
        self.detail.show()
        full = self.detail.sizeHint().height()
        if animate:                                   # slide open under the new choice
            self._detail_anim.stop()
            self._detail_anim.setStartValue(0)
            self._detail_anim.setEndValue(full)
            self._detail_anim.start()
        else:
            self.detail.setMaximumHeight(full)

    def _pick_brain(self, name: str) -> None:
        if name == self.s.provider:
            self._show_brain()
            return
        self._loading = True              # one save for both changes
        self.s.model = ""
        self._loading = False
        self._set("provider", name)
        self._show_brain(animate=True)

    def _key_edited(self) -> None:
        if self._loading:
            return
        key = self.key.text().strip()
        if key == self.s.key_for():
            return
        self.s.set_key(self.s.provider, key)
        try:
            self.s.save()
        except OSError:
            return
        self.saved_note.setText("Saved ✓")
        self._note_timer.start(1400)
        self._refresh_rows()
        self.saved.emit()

    def _refresh_rows(self) -> None:
        name = self.s.provider
        for n, row in getattr(self, "brain_rows", {}).items():
            row.set_state(n == name, self._brain_status(n))
        self._backup_note()

    def _backup_note(self) -> None:
        if not hasattr(self, "backup_note"):
            return
        names = [P.PRESETS[n]["label"] for n in P.backups(self.s)]
        self.backup_note.setText(
            f"If {P.PRESETS[self.s.provider]['label']} runs out, {' then '.join(names)} "
            "takes over." if names else
            f"Tip: add a free {'Gemini' if self.s.provider == 'groq' else 'Groq'} key too, "
            "as a backup.")

    def _tab_focus(self) -> QWidget:
        w, lay = self._page()
        card = Card()
        self.focus_len = Segmented([(str(m), f"{m}") for m in (15, 25, 45, 50)],
                                   str(self.s.focus_minutes), small=True)
        self.focus_len.changed.connect(lambda v: self._set("focus_minutes", int(v)))
        self.break_len = Segmented([(str(m), f"{m}") for m in (5, 10, 15)],
                                   str(self.s.break_minutes), small=True)
        self.break_len.changed.connect(lambda v: self._set("break_minutes", int(v)))
        card.add(_row("Focus (min)", self.focus_len))
        card.add(_row("Break (min)", self.break_len))
        card.add(self._toggle("focus_nap", "Nap on the taskbar"))
        tip = QLabel("Start it from his right-click menu, or say “focus for 50 minutes”.")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {DIM_HEX}; font-size: 8pt; padding: 8px 4px;")
        lay.addWidget(_section("FOCUS TIMER"))
        lay.addWidget(card)
        lay.addWidget(tip)
        lay.addStretch()
        return w

    def _tab_google(self) -> QWidget:
        w, lay = self._page()
        acct = Card()
        self.g_btn = QPushButton("Connect")
        self.g_btn.setObjectName("seg")
        self.g_btn.clicked.connect(self._google)
        self.g_row = _row("Google", self.g_btn, "Not connected")
        self.g_status = self.g_row.findChildren(QLabel)[1]
        acct.add(self.g_row)
        setup = Card()
        setup.add(_row("Client ID", self._edit("google_client_id", "from Cloud Console", 170)))
        setup.add(_row("Secret", self._edit("google_client_secret", "client secret", 170,
                                            secret=True)))
        info = QLabel("Lets him read your calendar and inbox. He asks before adding "
                      "events and can never send or delete mail. One-time setup: see the README.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {DIM_HEX}; font-size: 8pt; padding: 8px 4px;")
        lay.addWidget(_section("CALENDAR & GMAIL"))
        lay.addWidget(acct)
        lay.addWidget(_section("SETUP"))
        lay.addWidget(setup)
        lay.addWidget(info)
        lay.addStretch()
        return w

    # ------------------------------------------------------------ behaviour
    def set_google_status(self, connected: bool, text: str) -> None:
        self.g_btn.setText("Disconnect" if connected else "Connect")
        self.g_btn.setEnabled(True)
        self.g_status.setText(text)

    def _google(self) -> None:
        for attr in ("google_client_id", "google_client_secret"):
            e = getattr(self, f"edit_{attr}")
            setattr(self.s, attr, e.text().strip())
        self.g_btn.setEnabled(False)
        self.g_status.setText("Opening Google sign-in in your browser…")
        self.google_clicked.emit()

    def reload(self) -> None:
        self._loading = True
        try:
            for attr in ("user_name", "google_client_id", "google_client_secret"):
                getattr(self, f"edit_{attr}").setText(str(getattr(self.s, attr)))
            self.notice_toggle.setChecked(bool(self.s.aware and self.s.react_activity))
            self.focus_len.set_value(str(self.s.focus_minutes))
            self.break_len.set_value(str(self.s.break_minutes))
            for attr, tg in self.toggles.items():
                tg.setChecked(bool(getattr(self.s, attr)))
            self._show_brain()
            self.voice_pick.set_value(self.s.voice)
            self.lang_pick.set_value(self.s.language)
            self.scale.setValue(int(round(self.s.scale * 100)))
            for attr, b in self.hotkey_btns.items():
                b.set_value(getattr(self.s, attr))
        finally:
            self._loading = False

    def _test(self) -> None:
        name = self.s.provider
        pre = P.PRESETS[name]
        key = self.key.text().strip()
        if pre["needs_key"] and not key:
            self._show_result(False, f"Paste your {pre['label']} key first.")
            return
        self._key_edited()
        model = self.s.model.strip()
        self.test_btn.setEnabled(False)
        self.result.setText("Testing…")

        def work():
            try:
                used = self.tester(name, key, model)
                self._tested.emit(True, f"Connected ✓  ({used})")
            except Exception as e:     # show the provider's own words
                detail = getattr(e, "detail", "") or str(e)
                kind = getattr(e, "kind", "")
                hint = {"auth": f"The key was rejected. Copy it again from {pre['label']}.",
                        "connection": ("Ollama isn't running. Open the Ollama app." if
                                       name == "ollama" else
                                       f"Can't reach {pre['label']}. Check the internet."),
                        "rate": "Limit reached for now. Try again in a minute.",
                        "model_missing": f"No {pre['label']} model is available."
                        }.get(kind, "")
                self._tested.emit(False, f"{hint}\n{detail[:220]}".strip())
        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, ok: bool, text: str) -> None:
        self.test_btn.setEnabled(True)
        self.result.setText(html.escape(text).replace("\n", "<br>"))
        self.result.setStyleSheet(f"color: {LIME_HEX if ok else theme.DANGER_HEX}; "
                                  f"font-size: 8pt; padding: 0 0 8px 0;")


class StudyPage(QWidget):
    """Quiz (pick an answer, see why) or flashcards (flip, next)."""
    back = Signal()

    def __init__(self):
        super().__init__()
        self.items: list[dict] = []
        self.mode = "quiz"
        self.i = 0
        self.score = 0
        self.answered = False
        self.flipped = False

        b = IconButton("back", 26)
        b.setToolTip("Back to chat")
        b.clicked.connect(self.back)
        self.title = QLabel("Study")
        self.title.setFont(theme.sans(10.5, bold=True))
        self.count = QLabel("")
        self.count.setStyleSheet(f"color: {DIM_HEX}; font-size: 8.5pt;")
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(b)
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(self.count)

        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet("QFrame#card { background: #141519; border: 1px solid #23252b;"
                                " border-radius: 14px; }")
        self.q = QLabel()
        self.q.setWordWrap(True)
        self.q.setFont(theme.sans(10.5, bold=True))
        self.q.setStyleSheet(f"color: {TEXT_HEX}; background: transparent; border: none;")
        self.q.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.back_text = QLabel()
        self.back_text.setWordWrap(True)
        self.back_text.setFont(theme.sans(9.8))
        self.back_text.setStyleSheet(f"color: {LIME_HEX}; background: transparent; border: none;")
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(14, 14, 14, 14)
        cl.addWidget(self.q)
        cl.addWidget(self.back_text)
        cl.addStretch()

        self.opts: list[QPushButton] = []
        self.opt_box = QVBoxLayout()
        self.opt_box.setSpacing(6)
        for k in range(4):
            o = QPushButton()
            o.setObjectName("option")
            o.setCursor(Qt.PointingHandCursor)
            o.clicked.connect(lambda _c=False, k=k: self._pick(k))
            self.opts.append(o)
            self.opt_box.addWidget(o)
        self.why = QLabel()
        self.why.setWordWrap(True)
        self.why.setStyleSheet(f"color: {DIM_HEX}; font-size: 8.8pt;")

        self.prev_btn = QPushButton("BACK")
        self.prev_btn.setObjectName("ghost")
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn = QPushButton("NEXT")
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self._next)
        nav = QHBoxLayout()
        nav.addWidget(self.prev_btn)
        nav.addStretch()
        nav.addWidget(self.next_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addWidget(self.card, 1)
        lay.addLayout(self.opt_box)
        lay.addWidget(self.why)
        lay.addLayout(nav)

    def load(self, items: list[dict], mode: str, title: str) -> None:
        self.items, self.mode = items, mode
        self.title.setText(title[:32] or "Study")
        self.i, self.score = 0, 0
        self._show()

    def _show(self) -> None:
        n = len(self.items)
        done = self.i >= n
        self.answered, self.flipped = False, False
        self.why.setText("")
        self.prev_btn.setVisible(self.mode == "cards" and 0 < self.i < n)
        if done:
            self.count.setText("")
            if self.mode == "quiz":
                pct = round(100 * self.score / max(1, n))
                msg = ("Purr-fect!" if pct == 100 else "Nice work!" if pct >= 70
                       else "Good start. Try again?")
                self.q.setText(f"{self.score} / {n} correct ({pct}%)\n\n{msg}")
            else:
                self.q.setText(f"You went through all {n} cards!")
            self.back_text.setText("")
            for o in self.opts:
                o.hide()
            self.next_btn.setText("AGAIN")
            return
        self.count.setText(f"{self.i + 1} / {n}")
        it = self.items[self.i]
        if self.mode == "quiz":
            self.q.setText(it["q"])
            self.back_text.setText("")
            for k, o in enumerate(self.opts):
                o.show()
                o.setEnabled(True)
                o.setProperty("state", "")
                o.setText(f"{'ABCD'[k]}   " + textwrap.fill(it['options'][k], 46)
                          .replace("\n", "\n      "))
                o.style().unpolish(o)
                o.style().polish(o)
            self.next_btn.setText("SKIP")
        else:
            for o in self.opts:
                o.hide()
            self.q.setText(it["front"])
            self.back_text.setText("")
            self.next_btn.setText("FLIP")

    def _pick(self, k: int) -> None:
        if self.answered or self.i >= len(self.items):
            return
        it = self.items[self.i]
        self.answered = True
        if k == it["answer"]:
            self.score += 1
        for j, o in enumerate(self.opts):
            o.setEnabled(False)
            o.setProperty("state", "right" if j == it["answer"] else
                          "wrong" if j == k else "")
            o.style().unpolish(o)
            o.style().polish(o)
        self.why.setText(("✓ " if k == it["answer"] else "✗ ") + (it.get("why") or ""))
        self.next_btn.setText("NEXT")

    def _next(self) -> None:
        if self.i >= len(self.items):              # AGAIN
            self.i, self.score = 0, 0
            self._show()
            return
        if self.mode == "cards" and not self.flipped:
            self.flipped = True
            self.back_text.setText(self.items[self.i]["back"])
            self.next_btn.setText("NEXT")
            return
        self.i += 1
        self._show()

    def _prev(self) -> None:
        if self.i > 0:
            self.i -= 1
            self._show()


class ConfirmPage(QWidget):
    answered = Signal(bool)

    def __init__(self, cat_name: str):
        super().__init__()
        self.cat_name = cat_name
        self.what = QLabel()
        self.what.setStyleSheet(f"color: {DIM_HEX};")
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.setFont(theme.mono(8.5))
        self.detail.setStyleSheet("background: #060607; border: 1px solid #1d1f24;"
                                  " border-radius: 10px; padding: 10px;")
        deny = QPushButton("DENY")
        allow = QPushButton("ALLOW")
        allow.setObjectName("danger")
        deny.clicked.connect(lambda: self.answered.emit(False))
        allow.clicked.connect(lambda: self.answered.emit(True))
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(deny)
        row.addWidget(allow)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(_title("ALLOW THIS?", theme.DANGER_HEX))
        lay.addWidget(self.what)
        lay.addWidget(self.detail)
        lay.addStretch()
        lay.addLayout(row)
        self.deny = deny

    def set_request(self, text: str) -> None:
        self.what.setText(f"{html.escape(self.cat_name)} wants to:")
        self.detail.setText(text)


class WelcomePage(QWidget):
    """First launch: Kuro says hi, learns your name and gets a brain in under
    a minute. Groq is recommended: free, fast, and its free limits are far
    bigger than Gemini's."""
    step_changed = Signal(int)            # 0 hello, 1 name, 2 brain, 3 done (for his reactions)
    finished = Signal()
    other_brains = Signal()
    _tested = Signal(bool, str)

    def __init__(self, settings, tester):
        super().__init__()
        self.s, self.tester = settings, tester
        self.steps = QStackedWidget()
        self.steps.addWidget(self._hello())
        self.steps.addWidget(self._name())
        self.steps.addWidget(self._brain())
        self.steps.addWidget(self._done())
        self.dots = QLabel()
        self.dots.setAlignment(Qt.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(8)
        lay.addWidget(self.steps, 1)
        lay.addWidget(self.dots)
        self._tested.connect(self._show_test)
        self.go(0)

    # ------------------------------------------------------------ pieces
    @staticmethod
    def _text(t: str, dim: bool = False, size: float = 9.5) -> QLabel:
        lab = QLabel(t)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        lab.setOpenExternalLinks(True)
        lab.setStyleSheet(f"color: {DIM_HEX if dim else TEXT_HEX}; font-size: {size}pt;")
        return lab

    def _page(self, title: str, *widgets: QWidget, buttons: list[QWidget] = ()) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        t = QLabel(title)
        t.setStyleSheet(f"color: {LIME_HEX}; font-size: 14pt; font-weight: 700;")
        lay.addWidget(t)
        for x in widgets:
            lay.addWidget(x)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        for b in buttons:
            row.addWidget(b)
        lay.addLayout(row)
        w.title_label = t
        return w

    @staticmethod
    def _button(text: str, primary: bool = True) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("primary" if primary else "ghost")
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _hello(self) -> QWidget:
        go = self._button("Let's go  →")
        go.clicked.connect(lambda: self.go(1))
        return self._page(
            "Hi! I'm Kuro.",
            self._text("A little black cat with small magical powers. I live on your desktop, "
                       "chat with you, open apps, play music, keep you focused and remember "
                       "what matters to you."),
            self._text("Let's set me up. It takes about a minute.", dim=True),
            buttons=[go])

    def _name(self) -> QWidget:
        self.name_edit = FocusLineEdit(self.s.user_name if self.s.user_name != "friend" else "")
        self.name_edit.setPlaceholderText("your name")
        self.name_edit.returnPressed.connect(self._save_name)
        nxt = self._button("Next  →")
        nxt.clicked.connect(self._save_name)
        return self._page("What should I call you?", _field(self.name_edit),
                          buttons=[nxt])

    def _brain(self) -> QWidget:
        get = self._button("Get my free Groq key  ↗", primary=False)
        get.setObjectName("seg")
        get.clicked.connect(lambda: __import__("webbrowser").open(P.PRESETS["groq"]["key_url"]))
        self.key_edit = FocusLineEdit("")
        self.key_edit.setPlaceholderText("paste the key here (starts with gsk_)")
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.returnPressed.connect(self._test)
        self.test_note = self._text("", dim=True, size=8.5)
        self.test_btn = self._button("Test && finish")
        self.test_btn.clicked.connect(self._test)
        other = self._button("Other brains…", primary=False)
        other.clicked.connect(self.other_brains)
        skip = self._button("Later", primary=False)
        skip.clicked.connect(lambda: self.go(3))
        return self._page(
            "Give me a brain",
            self._text("I think with <b>Groq</b>: free, very fast, and plenty of daily uses."),
            self._text("1. Click the button and sign in (Google works).<br>"
                       "2. Press <b>Create API Key</b>, then copy it.<br>"
                       "3. Paste it below.", dim=True, size=8.8),
            get, _field(self.key_edit), self.test_note,
            buttons=[skip, other, self.test_btn])

    def _done(self) -> QWidget:
        start = self._button("Start  ✦")
        start.clicked.connect(self._finish)
        tips = self._text(
            "• <b>Double-click</b> me to chat, <b>right-click</b> for my menu<br>"
            "• <b>Ctrl+Alt+K</b> chat · <b>Ctrl+Alt+Space</b> talk<br>"
            "• Try: <i>“open Spotify”</i>, <i>“focus for 25 minutes”</i>, "
            "<i>“what's on my screen?”</i>", size=9)
        self.done_note = self._text("", dim=True, size=8.5)
        page = self._page("All set!", tips, self.done_note, buttons=[start])
        self.done_title = page.title_label
        return page

    # ------------------------------------------------------------ flow
    def go(self, i: int) -> None:
        self.steps.setCurrentIndex(i)
        self.dots.setText("  ".join(
            f'<span style="color:{LIME_HEX if k == i else FAINT_HEX}">●</span>'
            for k in range(4)))
        if i == 1:
            self.name_edit.setFocus()
        elif i == 2:
            self.key_edit.setFocus()
        elif i == 3:
            name = self.s.user_name or "friend"
            self.done_title.setText(f"All set, {html.escape(name)}!")
            self.done_note.setText("" if self.s.ready else
                                   "I still need a brain to answer you: right-click me → "
                                   "Settings → AI & Voice whenever you're ready.")
        self.step_changed.emit(i)

    def _save_name(self) -> None:
        name = self.name_edit.text().strip()
        if name:
            self.s.user_name = name
            self._save()
        self.go(2)

    def _test(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self._show_test(False, "Paste the key first.")
            return
        self.test_btn.setEnabled(False)
        self.test_note.setText("Testing…")

        def work():
            try:
                used = self.tester("groq", key, "")
                self._tested.emit(True, used)
            except Exception as e:
                kind = getattr(e, "kind", "")
                msg = {"auth": "That key didn't work. Copy it again from Groq.",
                       "connection": "I can't reach Groq. Is the internet on?",
                       "rate": "Groq is busy for a moment. Try again in a minute."
                       }.get(kind, getattr(e, "detail", "") or str(e))
                self._tested.emit(False, msg[:200])
        threading.Thread(target=work, daemon=True).start()

    def _show_test(self, ok: bool, text: str) -> None:
        self.test_btn.setEnabled(True)
        if ok:
            self.s.set_key("groq", self.key_edit.text().strip())
            self.s.provider, self.s.model = "groq", ""
            self._save()
            self.go(3)
            return
        self.test_note.setText(html.escape(text))
        self.test_note.setStyleSheet(f"color: {theme.DANGER_HEX}; font-size: 8.5pt;")

    def _finish(self) -> None:
        self.s.onboarded = True
        self._save()
        self.finished.emit()

    def _save(self) -> None:
        try:
            self.s.save()
        except OSError:
            pass


# ======================================================================= hud
SIZES = {"chat": (340, 400), "settings": (390, 520),
         "confirm": (340, 230), "study": (360, 470), "welcome": (360, 340)}
CHAT_SIZES = {"small": (300, 330), "medium": (340, 400), "large": (420, 540)}


class Hud(QWidget):
    submitted = Signal(str)
    mic_clicked = Signal()
    clear_clicked = Signal()
    settings_saved = Signal()
    confirm_answered = Signal(bool)

    def __init__(self, cat, settings, memory, tester):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(theme.CSS)
        self.setFixedSize(CANVAS_W, CANVAS_H)
        self.cat = cat
        self.s = settings
        self.page = ""                       # "" = hidden
        self.below = False
        self.busy = False

        self.bubble = BubblePage(settings.cat_name)
        self.chat = ChatPage(settings.cat_name)
        self.settings = SettingsPage(settings, tester)
        self.confirm = ConfirmPage(settings.cat_name)
        self.study = StudyPage()
        self.welcome = WelcomePage(settings, tester)
        self.stack = QStackedWidget(self)
        self.pages = {"bubble": self.bubble, "chat": self.chat, "settings": self.settings,
                      "confirm": self.confirm, "study": self.study, "welcome": self.welcome}
        for w in self.pages.values():
            self.stack.addWidget(w)
        self.fade = QGraphicsOpacityEffect(self.stack)
        self.fade.setOpacity(0.0)
        self.stack.setGraphicsEffect(self.fade)

        # wiring
        self.bubble.clicked.connect(lambda: self.open("chat"))
        self.chat.submitted.connect(self.submitted)
        self.chat.mic_clicked.connect(self.mic_clicked)
        self.chat.clear_clicked.connect(self.clear_clicked)
        self.chat.close_clicked.connect(self.close_panel)
        self.settings.back.connect(lambda: self.open("chat"))
        self.settings.saved.connect(self._saved)
        self.hotkey_capture = self.settings.hotkey_capture
        for page in (self.settings, self.welcome, self.chat, self.study, self.confirm):
            hover_fx(page)                  # soft hover / press feedback everywhere
        self.study.back.connect(lambda: self.open("chat"))
        self.confirm.answered.connect(self._answer)
        self.welcome.finished.connect(self.close_panel)
        self.welcome.other_brains.connect(self._other_brains)

        # animation state
        self.w = Spring(40, 240, 0.72)
        self.h = Spring(16, 240, 0.72)
        self.alpha = Spring(0, 160, 1.0)
        self.content = Spring(0, 200, 1.0)
        self.target = (40, 16)
        self._prev_page = ""
        self._bubble_timer = QTimer(self, singleShot=True, timeout=self._bubble_done)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._t = 0.0
        self._frame = QTimer(self, interval=16, timeout=self._tick)
        self._frame.setTimerType(Qt.PreciseTimer)

    # ------------------------------------------------------------ public api
    def is_open(self) -> bool:
        return self.page in ("chat", "settings", "confirm", "study", "welcome")

    def say(self, text: str, seconds: float | None = None) -> None:
        """Show a line above Kuro. If a panel is open, it goes in the chat."""
        if self.is_open():
            return
        w, h = self.bubble.set_text(text)
        self._show("bubble", (w, h))
        if seconds is None:
            seconds = min(max(3.5, len(text) / 14), 20.0)
        self._bubble_timer.start(int(seconds * 1000))

    def stream_bubble(self, text: str) -> None:
        """A streaming reply above his head: the bubble grows instead of
        restarting, and stays up until a few seconds after the last word."""
        if self.is_open():
            return
        if self.page != "bubble":
            self.say(text)
            return
        w, h = self.bubble.extend(text)
        self._show("bubble", (w, h))
        self._bubble_timer.start(int(min(max(3.5, len(text) / 14), 20.0) * 1000))

    def open(self, page: str) -> None:
        if page == "settings":
            self.settings.reload()
        self._bubble_timer.stop()
        if page == "chat":
            size = CHAT_SIZES.get(self.s.chat_size, CHAT_SIZES["medium"])
            self.chat.set_bubble_width(int(size[0] * 0.72))
        else:
            size = SIZES[page]
        self._show(page, size)
        self.activateWindow()
        self.raise_()
        if page == "chat":
            self.chat.input.setFocus()

    def toggle_chat(self) -> None:
        if self.page == "chat":
            self.close_panel()
        else:
            self.open("chat")

    def close_panel(self) -> None:
        if self.page == "confirm":
            return                            # must be answered
        self._show("", (40, 16))

    def ask(self, text: str) -> None:
        self.confirm.set_request(text)
        self._prev_page = self.page if self.page not in ("", "bubble", "confirm") else ""
        self.open("confirm")
        self.confirm.deny.setFocus()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.chat.set_busy(busy)
        self.chat.set_status("thinking" if busy else "online")

    # ------------------------------------------------------------ internals
    def _show(self, page: str, size: tuple[int, int]) -> None:
        if page != self.page:
            self.content.value = 0.0          # fade the new page in once shaped
        self.page = page
        self.target = size
        self.w.target, self.h.target = size
        self.alpha.target = 1.0 if page else 0.0
        if page:
            self.stack.setCurrentWidget(self.pages[page])
            self.stack.setFixedSize(*size)
            self.setAttribute(Qt.WA_ShowWithoutActivating, page == "bubble")
            if not self.isVisible():
                self.w.value, self.h.value = 40, 16
                self.follow()
                self.show()
        self._frame.start()
        self._clock.restart()

    def _bubble_done(self) -> None:
        if self.page == "bubble":
            self._show("", (40, 16))

    def _other_brains(self) -> None:
        self.open("settings")
        self.settings.tabbar.set_value("ai", emit=True)

    def _saved(self) -> None:
        self.settings_saved.emit()        # auto-save: stay on the settings page

    def _answer(self, allowed: bool) -> None:
        self.confirm_answered.emit(allowed)
        back = self._prev_page
        if back:
            self.open(back)
        else:
            self._show("", (40, 16))

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            if self.page == "confirm":
                self.confirm.answered.emit(False)
            else:
                self.close_panel()
        else:
            super().keyPressEvent(e)

    def panel_rect(self) -> QRectF:
        w, h = max(20.0, self.w.value), max(10.0, self.h.value)
        anchor = self.mapFromGlobal(self.cat.head_anchor())
        scr = (QGuiApplication.screenAt(self.cat.head_anchor())
               or QGuiApplication.primaryScreen()).availableGeometry()
        lo = max(2.0, scr.left() - self.x() + 6.0)            # keep the whole panel on screen
        hi = min(CANVAS_W - w - 2.0, scr.right() - self.x() - w - 6.0)
        x = min(max(anchor.x() - w / 2, lo), max(lo, hi))
        if self.below:
            y = POINTER + 2
        else:
            y = CANVAS_H - h - POINTER - 2
        return QRectF(x, y, w, h)

    def follow(self) -> None:
        """Pin the canvas so the panel sits just above (or below) Kuro's head."""
        a = self.cat.head_anchor()
        scr = (QGuiApplication.screenAt(a) or QGuiApplication.primaryScreen()).availableGeometry()
        need = self.target[1] + POINTER + GAP + 6
        self.below = a.y() - need < scr.top()
        x = min(max(a.x() - CANVAS_W // 2, scr.left() - 40), scr.right() - CANVAS_W + 40)
        if self.below:
            y = self.cat.frameGeometry().bottom() - 4
        else:
            y = a.y() - CANVAS_H - GAP
        self.move(int(x), int(y))

    def _tick(self) -> None:
        dt = min(self._clock.restart() / 1000.0, 1 / 20)
        self._t += dt
        for s in (self.w, self.h, self.alpha):
            s.step(dt)
        settled = abs(self.w.value - self.target[0]) < 18 and abs(self.h.value - self.target[1]) < 18
        self.content.target = 1.0 if (self.page and settled) else 0.0
        self.content.step(dt)
        self.fade.setOpacity(max(0.0, min(1.0, self.content.value)) * max(0.0, min(1.0, self.alpha.value)))
        r = self.panel_rect()
        self.stack.move(int(r.x()), int(r.y()))
        self.follow()
        if not self.page and self.alpha.value < 0.02:
            self._frame.stop()
            self.hide()
            return
        # 60 fps while morphing, 30 once settled (the comet border is slow)
        moving = abs(self.w.vel) + abs(self.h.vel) > 4 or abs(self.alpha.vel) > 0.05
        want = 16 if moving else 33
        if self._frame.interval() != want:
            self._frame.setInterval(want)
        self.update()

    def paintEvent(self, _e) -> None:
        a = max(0.0, min(1.0, self.alpha.value))
        if a <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(a)
        r = self.panel_rect()
        path = QPainterPath()
        radius = min(16.0, r.height() / 2)
        path.addRoundedRect(r, radius, radius)
        # pointer toward Kuro's head
        anchor = self.mapFromGlobal(self.cat.head_anchor())
        px = min(max(anchor.x(), r.left() + 18), r.right() - 18)
        tri = QPainterPath()
        if self.below:
            tri.moveTo(px - POINTER, r.top() + 1)
            tri.lineTo(px, r.top() - POINTER)
            tri.lineTo(px + POINTER, r.top() + 1)
        else:
            tri.moveTo(px - POINTER, r.bottom() - 1)
            tri.lineTo(px, r.bottom() + POINTER)
            tri.lineTo(px + POINTER, r.bottom() - 1)
        tri.closeSubpath()
        shape = path.united(tri)
        theme.paint_matte(p, shape)

        # the running light
        color = theme.RED if self.page == "confirm" else theme.LIME
        speed = 0.42 if (self.busy or self.page == "confirm") else 0.13
        head = (self._t * speed) % 1.0
        border = QPainterPath()
        border.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        theme.paint_comet(p, border, head, color, 0.16 if self.page != "bubble" else 0.24)
        if self.busy or self.page == "confirm":
            theme.paint_comet(p, border, (head + 0.5) % 1.0, color, 0.16)


# ================================================================ radial menu
class RadialMenu(QWidget):
    """Icons that fan out in an arc above Kuro's head."""

    def __init__(self, items: list[tuple[str, str, object]]):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.items = items
        self.setFixedSize(260, 170)
        self.centre = QPointF(130, 150)
        self.radius = 92
        self.springs = [Spring(0, 260, 0.6) for _ in items]
        self._t = 0.0
        self.hover = -1
        self._timer = QTimer(self, interval=16, timeout=self._step)

    def popup(self, head: QPoint) -> None:
        self.follow(head)
        self.show()
        self._timer.start()

    def follow(self, head: QPoint) -> None:
        """Stay fanned out above his head wherever he is."""
        if self.isVisible() or not self._timer.isActive():
            self.move(int(head.x() - self.centre.x()), int(head.y() - self.centre.y() + 30))

    def hideEvent(self, e) -> None:
        self._timer.stop()                   # no animating a closed menu
        super().hideEvent(e)

    def _pos(self, i: int) -> QPointF:
        n = len(self.items)
        a = math.radians(-168 + 156 * (i / max(1, n - 1)))
        k = max(0.0, self.springs[i].value)
        return QPointF(self.centre.x() + math.cos(a) * self.radius * k,
                       self.centre.y() + math.sin(a) * self.radius * k)

    def _step(self) -> None:
        self._t += 0.016
        for i, s in enumerate(self.springs):
            if self._t > i * 0.035:
                s.target = 1.0
            s.step(0.016)
        if self._t > 1.0 and self.hover < 0 and all(abs(s.value - 1) < 0.002 for s in self.springs):
            return                               # settled: no need to repaint 60x a second
        self.update()

    def _hit(self, pos: QPointF) -> int:
        for i in range(len(self.items)):
            c = self._pos(i)
            if (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2 < 22 ** 2:
                return i
        return -1

    def mouseMoveEvent(self, e) -> None:
        hover = self._hit(e.position())
        if hover != self.hover:
            self.hover = hover
            self.update()
        self.setCursor(Qt.PointingHandCursor if self.hover >= 0 else Qt.ArrowCursor)

    def mousePressEvent(self, e) -> None:
        i = self._hit(e.position())
        self.close()
        if i >= 0:
            QTimer.singleShot(0, self.items[i][2])

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i, (kind, label, _cb) in enumerate(self.items):
            k = max(0.0, min(1.0, self.springs[i].value))
            if k < 0.02:
                continue
            c = self._pos(i)
            hov = i == self.hover
            r = 19 * (0.6 + 0.4 * k) * (1.12 if hov else 1.0)
            p.setOpacity(k)
            path = QPainterPath()
            path.addEllipse(c, r, r)
            theme.paint_matte(p, path)
            p.setPen(QPen(theme.lime(0.9 if hov else 0.25), 1.3 if hov else 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r, r)
            col = QColor(theme.DANGER_HEX) if (hov and kind == "power") else \
                theme.LIME if hov else QColor("#c9ccd3")
            theme.icon(p, kind, c, r * 0.95, col)
        if self.hover >= 0:
            p.setOpacity(1.0)
            label = self.items[self.hover][1]
            p.setFont(theme.mono(7.5, bold=True, spacing=135))
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(label) + 18
            box = QRectF(self.centre.x() - w / 2, self.centre.y() - 34, w, 20)
            path = QPainterPath()
            path.addRoundedRect(box, 10, 10)
            theme.paint_matte(p, path)
            p.setPen(theme.LIME)
            p.drawText(box, Qt.AlignCenter, label)
