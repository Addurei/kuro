"""The matte-black HUD: chat console, confirmation and settings dialogs."""
from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import (QEasingCurve, QParallelAnimationGroup, QPoint, QPointF,
                            QPropertyAnimation, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog,
                               QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from . import theme
from .theme import DIM_HEX, FAINT_HEX, LIME_HEX, MONO_CSS, TEXT_HEX


# ------------------------------------------------------------------ widgets
class IconButton(QAbstractButton):
    """Small painted icon buttons: mic, send, close. No font glyphs needed."""

    def __init__(self, kind: str, size: int = 34, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.active = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._t = 0.0
        self._pulse = QTimer(self, interval=30, timeout=self._step)

    def set_active(self, on: bool) -> None:
        self.active = on
        if on:
            self._pulse.start()
        else:
            self._pulse.stop()
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
        hover = self.underMouse()
        c = r.center()

        if self.kind == "send":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#cdf07a") if hover else theme.LIME)
            p.drawEllipse(r)
            pen = QPen(QColor(10, 10, 11), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            p.drawLine(QPointF(c.x(), c.y() + 6), QPointF(c.x(), c.y() - 6))
            p.drawLine(QPointF(c.x() - 5, c.y() - 1), QPointF(c.x(), c.y() - 6))
            p.drawLine(QPointF(c.x() + 5, c.y() - 1), QPointF(c.x(), c.y() - 6))
            return

        if self.kind == "mic":
            if self.active:
                for k in range(2):
                    ph = (self._t + k * 0.5) % 1.0
                    p.setPen(QPen(theme.lime(0.7 * (1 - ph)), 1.5))
                    p.setBrush(Qt.NoBrush)
                    rr = r.width() / 2 * (0.75 + 0.3 * ph)
                    p.drawEllipse(c, rr, rr)
            p.setPen(QPen(theme.LIME if (self.active or hover) else QColor("#2a2c31"), 1.2))
            p.setBrush(QColor(28, 34, 14) if self.active else QColor(18, 18, 21))
            p.drawEllipse(r.adjusted(2, 2, -2, -2))
            col = theme.LIME if (self.active or hover) else QColor("#b9bcc4")
            p.setPen(QPen(col, 1.7, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(c.x() - 3.2, c.y() - 8, 6.4, 10.5), 3.2, 3.2)
            arc = QPainterPath(QPointF(c.x() - 6, c.y() - 1))
            arc.quadTo(QPointF(c.x() - 6, c.y() + 6), QPointF(c.x(), c.y() + 6))
            arc.quadTo(QPointF(c.x() + 6, c.y() + 6), QPointF(c.x() + 6, c.y() - 1))
            p.drawPath(arc)
            p.drawLine(QPointF(c.x(), c.y() + 6), QPointF(c.x(), c.y() + 9))
            return

        if self.kind == "close":
            col = QColor(theme.DANGER_HEX) if hover else QColor(DIM_HEX)
            p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap))
            d = 4.5
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))


class StatusOrb(QWidget):
    """Breathing lime dot: calm when online, fast when thinking, grey when offline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.mode = "online"
        self._t = 0.0
        QTimer(self, interval=33, timeout=self._step).start()

    def _step(self) -> None:
        self._t += 0.033 * (3.2 if self.mode in ("thinking", "listening") else 1.0)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(9, 9)
        base = QColor("#5a5d65") if self.mode == "offline" else theme.LIME
        ph = (self._t * 0.8) % 1.0
        ring = QColor(base)
        ring.setAlphaF(0.55 * (1 - ph))
        p.setPen(QPen(ring, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 3 + 5.5 * ph, 3 + 5.5 * ph)
        glow = QColor(base)
        glow.setAlphaF(0.25)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, 5, 5)
        p.setBrush(base)
        p.drawEllipse(c, 3, 3)


class Divider(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(9)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        y = self.height() / 2
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0, theme.lime(0.7))
        g.setColorAt(0.35, QColor(255, 255, 255, 22))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(QPen(g, 1))
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.setPen(Qt.NoPen)
        p.setBrush(theme.LIME)
        p.drawRect(QRectF(0, y - 1.5, 18, 3))


class Field(QFrame):
    """Input pill whose border lights up lime while focused."""

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


# ------------------------------------------------------------------ base window
class MatteWindow(QWidget):
    """Frameless, translucent, draggable window painted as a matte HUD slab."""

    RADIUS = 16

    def __init__(self, flags=Qt.Tool, brackets: bool = True):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(theme.CSS)
        self._brackets = brackets
        self._drag: QPoint | None = None

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        path = theme.matte_path(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                self.RADIUS)
        theme.paint_matte(p, path, brackets=self._brackets)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and e.position().y() < 52:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None


# ------------------------------------------------------------------ chat
STATUS_TEXT = {"online": "ONLINE", "thinking": "PROCESSING", "listening": "LISTENING",
               "speaking": "SPEAKING", "offline": "NO API KEY"}


class ChatPanel(MatteWindow):
    submitted = Signal(str)
    mic_clicked = Signal()

    def __init__(self, cat_name: str):
        super().__init__()
        self.cat_name = cat_name
        self.resize(392, 500)
        self.messages: list[dict] = []
        self.busy = False
        self._spin = 0

        # header
        self.orb = StatusOrb()
        title = QLabel(cat_name.upper())
        title.setFont(theme.mono(11, bold=True, spacing=135))
        self.status = QLabel("ONLINE")
        self.status.setFont(theme.mono(7.5, spacing=125))
        self.status.setStyleSheet(f"color: {LIME_HEX};")
        self.clock = QLabel()
        self.clock.setFont(theme.mono(8.5, spacing=110))
        self.clock.setStyleSheet(f"color: {DIM_HEX};")
        close = IconButton("close", 28)
        close.clicked.connect(self.dismiss)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(title)
        titles.addWidget(self.status)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self.orb)
        head.addLayout(titles)
        head.addStretch()
        head.addWidget(self.clock)
        head.addWidget(close)

        # log
        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)
        self.log.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.log.document().setDocumentMargin(2)

        # input
        field = Field()
        prompt = QLabel("›")
        prompt.setFont(theme.mono(13, bold=True))
        prompt.setStyleSheet(f"color: {LIME_HEX};")
        self.input = FocusLineEdit()
        self.input.setFont(theme.sans(10))
        self.input.setPlaceholderText(f"Talk to {cat_name}…")
        self.input.returnPressed.connect(self._send)
        self.input.focus_changed.connect(field.set_focus_look)
        self.mic = IconButton("mic", 32)
        self.mic.setToolTip("Talk (push-to-talk)")
        self.mic.clicked.connect(self.mic_clicked)
        send = IconButton("send", 32)
        send.setToolTip("Send")
        send.clicked.connect(self._send)
        row = QHBoxLayout(field)
        row.setContentsMargins(12, 4, 5, 4)
        row.setSpacing(6)
        row.addWidget(prompt)
        row.addWidget(self.input, 1)
        row.addWidget(self.mic)
        row.addWidget(send)

        hint = QLabel("ESC HIDE  ·  CTRL+ALT+K TOGGLE  ·  CTRL+ALT+SPACE TALK")
        hint.setFont(theme.mono(6.5, spacing=115))
        hint.setStyleSheet(f"color: {FAINT_HEX};")
        hint.setAlignment(Qt.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(8)
        lay.addLayout(head)
        lay.addWidget(Divider())
        lay.addWidget(self.log, 1)
        lay.addWidget(field)
        lay.addWidget(hint)

        self._typer = QTimer(self, interval=16, timeout=self._type_step)
        self._spinner = QTimer(self, interval=120, timeout=self._spin_step)
        QTimer(self, interval=1000, timeout=self._tick_clock).start()
        self._tick_clock()

        self._anim = QParallelAnimationGroup(self)
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._slide = QPropertyAnimation(self, b"pos")
        self._anim.addAnimation(self._fade)
        self._anim.addAnimation(self._slide)
        self._anim.finished.connect(self._anim_done)
        self._closing = False

    # ---- status / clock --------------------------------------------
    def set_status(self, mode: str, brain: str | None = None) -> None:
        if brain is not None:
            self.brain = brain
        self.orb.mode = mode
        text = STATUS_TEXT.get(mode, mode.upper())
        if mode == "online" and getattr(self, "brain", ""):
            text += f"  ·  {self.brain.upper()}"
        if mode == "offline":
            text = "NO BRAIN SET"
        self.status.setText(text)
        color = DIM_HEX if mode == "offline" else LIME_HEX
        self.status.setStyleSheet(f"color: {color};")
        self.mic.set_active(mode == "listening")

    def _tick_clock(self) -> None:
        self.clock.setText(datetime.now().strftime("%H:%M"))

    # ---- messages ---------------------------------------------------
    def add(self, who: str, text: str, animate: bool = True) -> None:
        msg = {"who": who, "text": text, "time": datetime.now().strftime("%H:%M"),
               "shown": 0 if (who == "cat" and animate) else len(text)}
        self.messages.append(msg)
        if msg["shown"] < len(text):
            self._typer.start()
        self._render()

    def clear(self) -> None:
        self.messages.clear()
        self._render()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.input.setEnabled(not busy)
        self.input.setPlaceholderText("Processing…" if busy else f"Talk to {self.cat_name}…")
        if busy:
            self._spinner.start()
        else:
            self._spinner.stop()
            self.input.setFocus()
        self._render()

    def _type_step(self) -> None:
        pending = [m for m in self.messages if m["shown"] < len(m["text"])]
        if not pending:
            self._typer.stop()
            return
        for m in pending:
            m["shown"] = min(len(m["text"]), m["shown"] + max(1, len(m["text"]) // 70))
        self._render()

    def _spin_step(self) -> None:
        self._spin += 1
        self._render()

    def _render(self) -> None:
        bar = self.log.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        mono = f"font-family:{MONO_CSS}; font-size:7pt;"
        name = html.escape(self.cat_name.upper())
        parts = []
        for m in self.messages:
            text = html.escape(m["text"][:m["shown"]]).replace("\n", "<br>")
            if m["who"] == "system":
                parts.append(
                    f'<p align="center" style="margin:10px 0; {mono} color:{DIM_HEX}">'
                    f'&mdash; {text} &mdash;</p>')
            elif m["who"] == "you":
                parts.append(
                    f'<p align="right" style="margin:14px 0 3px 0; {mono} color:{DIM_HEX}">'
                    f'{m["time"]} <span style="color:{FAINT_HEX}">&middot;</span> YOU</p>'
                    f'<p align="right" style="margin:0; color:#b9bcc4">{text}</p>')
            else:
                typing = m["shown"] < len(m["text"])
                cursor = f'<span style="color:{LIME_HEX}">&#9613;</span>' if typing else ""
                parts.append(
                    f'<p style="margin:14px 0 3px 0; {mono} font-weight:700; color:{LIME_HEX}">'
                    f'{name} <span style="font-weight:400; color:{FAINT_HEX}">&middot; '
                    f'{m["time"]}</span></p>'
                    f'<p style="margin:0; color:{TEXT_HEX}">{text}{cursor}</p>')
        if self.busy:
            k = self._spin % 4
            dots = "&#9648;" * k + "&#9649;" * (3 - k)
            parts.append(
                f'<p style="margin:14px 0 0 0; {mono} color:{LIME_HEX}">'
                f'{dots}&nbsp;&nbsp;PROCESSING</p>')
        if not parts:
            parts.append(
                f'<p align="center" style="margin-top:120px; {mono} color:{FAINT_HEX}">'
                f'SYSTEMS NOMINAL</p>'
                f'<p align="center" style="color:{DIM_HEX}; font-size:9.5pt">'
                f'Ask {html.escape(self.cat_name)} to open an app, find a file,<br>'
                f'set a reminder, or just chat.</p>')
        self.log.setHtml("".join(parts))
        if at_bottom or self._typer.isActive() or self.busy:
            bar.setValue(bar.maximum())

    def _send(self) -> None:
        text = self.input.text().strip()
        if text and not self.busy:
            self.input.clear()
            self.submitted.emit(text)

    # ---- show / hide with animation ---------------------------------
    def open_near(self, anchor: QWidget) -> None:
        geo = anchor.frameGeometry()
        screen = (anchor.screen() or QApplication.primaryScreen()).availableGeometry()
        x = geo.left() + int(geo.width() * 0.1) - self.width()
        if x < screen.left():
            x = geo.right() - int(geo.width() * 0.1)
        y = min(max(geo.bottom() - self.height() - 10, screen.top()),
                screen.bottom() - self.height())
        end = QPoint(x, y)
        self._closing = False
        self._anim.stop()
        self.setWindowOpacity(0.0)
        self.move(end + QPoint(0, 14))
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self._animate(0.0, 1.0, end + QPoint(0, 14), end, 200, QEasingCurve.OutCubic)

    def dismiss(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._anim.stop()
        self._animate(self.windowOpacity(), 0.0, self.pos(), self.pos() + QPoint(0, 10),
                      140, QEasingCurve.InCubic)

    def _animate(self, o0, o1, p0, p1, ms, curve) -> None:
        for a in (self._fade, self._slide):
            a.setDuration(ms)
            a.setEasingCurve(curve)
        self._fade.setStartValue(o0)
        self._fade.setEndValue(o1)
        self._slide.setStartValue(p0)
        self._slide.setEndValue(p1)
        self._anim.start()

    def _anim_done(self) -> None:
        if self._closing:
            self.hide()
            self.setWindowOpacity(1.0)
            self._closing = False

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(e)


# ------------------------------------------------------------------ dialogs
class MatteDialog(QDialog):
    RADIUS = 16

    def __init__(self, title: str, accent_hex: str = LIME_HEX):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(theme.CSS)
        self._accent = QColor(accent_hex)
        self._drag: QPoint | None = None
        self.title = QLabel(title)
        self.title.setFont(theme.mono(8.5, bold=True, spacing=135))
        self.title.setStyleSheet(f"color: {accent_hex};")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 20, 24, 20)
        self.body.setSpacing(12)
        self.body.addWidget(self.title)
        self.body.addWidget(Divider())

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        path = theme.matte_path(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                self.RADIUS)
        theme.paint_matte(p, path, brackets=True, accent=self._accent)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None


class ConfirmDialog(MatteDialog):
    """Shown before any risky tool runs. Deny is the default button."""

    def __init__(self, cat_name: str, action_text: str):
        super().__init__("⚠  AUTHORIZATION REQUIRED", theme.DANGER_HEX)
        self.setMinimumWidth(420)
        who = QLabel(f"{html.escape(cat_name)} wants to:")
        who.setStyleSheet(f"color: {DIM_HEX};")
        detail = QLabel(action_text)
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail.setFont(theme.mono(9))
        detail.setStyleSheet("background: #070708; border: 1px solid #22242a;"
                             " border-radius: 10px; padding: 12px;")
        deny = QPushButton("DENY")
        allow = QPushButton("ALLOW")
        allow.setObjectName("danger")
        deny.setDefault(True)
        deny.setAutoDefault(True)
        allow.setAutoDefault(False)
        deny.clicked.connect(self.reject)
        allow.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(deny)
        row.addWidget(allow)
        self.body.addWidget(who)
        self.body.addWidget(detail)
        self.body.addLayout(row)
        deny.setFocus()


class SettingsDialog(MatteDialog):
    def __init__(self, settings):
        super().__init__("SETTINGS")
        from ..providers import PRESETS
        self.presets = PRESETS
        self.s = settings
        self.setMinimumWidth(500)
        # edit copies; written back only on SAVE
        self.keys = dict(settings.keys)
        self.models = dict(settings.models)
        self.base_urls = dict(settings.base_urls)
        self.current = settings.provider if settings.provider in PRESETS else "gemini"

        def field(widget):
            f = Field()
            lay = QHBoxLayout(f)
            lay.setContentsMargins(10, 2, 10, 2)
            lay.addWidget(widget)
            if isinstance(widget, FocusLineEdit):
                widget.focus_changed.connect(f.set_focus_look)
            return f

        def label(text):
            lab = QLabel(text)
            lab.setFont(theme.mono(7.5, spacing=125))
            lab.setStyleSheet(f"color: {DIM_HEX};")
            return lab

        self.name = FocusLineEdit(settings.cat_name)
        self.user = FocusLineEdit(settings.user_name)
        self.brain = QComboBox()
        self.backup = QComboBox()
        self.backup.addItem("None", "")
        for key, p in PRESETS.items():
            self.brain.addItem(p["label"], key)
            self.backup.addItem(p["label"], key)
        self.brain.setCurrentIndex(self.brain.findData(self.current))
        self.backup.setCurrentIndex(max(0, self.backup.findData(settings.fallback)))
        self.key = FocusLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.model = FocusLineEdit()
        self.base = FocusLineEdit()
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setOpenExternalLinks(True)
        self.note.setStyleSheet(f"color: {DIM_HEX}; font-size: 8.5pt;")

        self.speak = QCheckBox("Read replies aloud")
        self.speak.setChecked(settings.speak_replies)
        self.wander = QCheckBox("Let him wander along the taskbar")
        self.wander.setChecked(settings.wander)
        self.learn = QCheckBox("Learn about me from our chats")
        self.learn.setChecked(settings.learn_enabled)
        self.suggest = QCheckBox("Suggest things I usually do at this time")
        self.suggest.setChecked(settings.suggestions)
        self.shell = QCheckBox("Allow PowerShell (always asks first)")
        self.shell.setChecked(settings.allow_shell)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow(label("CAT NAME"), field(self.name))
        form.addRow(label("YOUR NAME"), field(self.user))
        form.addRow(label("BRAIN"), self.brain)
        form.addRow(label(""), self.note)
        self.key_row = (label("API KEY"), field(self.key))
        form.addRow(*self.key_row)
        form.addRow(label("MODEL"), field(self.model))
        self.base_row = (label("SERVER"), field(self.base))
        form.addRow(*self.base_row)
        form.addRow(label("BACKUP"), self.backup)
        self.body.addLayout(form)
        for cb in (self.speak, self.wander, self.learn, self.suggest, self.shell):
            self.body.addWidget(cb)
        restart = QLabel("Name and PowerShell changes apply after a restart.")
        restart.setStyleSheet(f"color: {FAINT_HEX}; font-size: 8.5pt;")
        self.body.addWidget(restart)

        cancel = QPushButton("CANCEL")
        save = QPushButton("SAVE")
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(save)
        self.body.addLayout(row)

        self.brain.currentIndexChanged.connect(self._switch)
        self._load_fields()

    # ---- per-brain fields ----------------------------------------------
    def _stash(self) -> None:
        p = self.presets[self.current]
        self.keys[self.current] = self.key.text().strip()
        model = self.model.text().strip()
        if model and model != p["model"]:
            self.models[self.current] = model
        else:
            self.models.pop(self.current, None)
        base = self.base.text().strip()
        if base and base != p["base_url"]:
            self.base_urls[self.current] = base
        else:
            self.base_urls.pop(self.current, None)

    def _load_fields(self) -> None:
        p = self.presets[self.current]
        self.key.setText(self.keys.get(self.current, ""))
        self.key.setPlaceholderText("required" if p["needs_key"] else "optional")
        self.model.setText(self.models.get(self.current) or p["model"])
        self.base.setText(self.base_urls.get(self.current) or p["base_url"])
        for w in self.key_row:
            w.setVisible(p["kind"] != "ollama")
        for w in self.base_row:
            w.setVisible(p["kind"] in ("ollama",) or self.current == "custom")
        link = ""
        if p["key_url"]:
            what = "Download Ollama" if p["kind"] == "ollama" else "Get a key"
            link = (f' <a style="color:{LIME_HEX}" href="{p["key_url"]}">{what} →</a>')
        self.note.setText(html.escape(p["note"]) + link)
        self.adjustSize()

    def _switch(self) -> None:
        self._stash()
        self.current = self.brain.currentData()
        self._load_fields()

    def _save(self) -> None:
        self._stash()
        self.s.cat_name = self.name.text().strip() or "Kuro"
        self.s.user_name = self.user.text().strip() or "friend"
        self.s.provider = self.current
        self.s.fallback = self.backup.currentData() or ""
        self.s.keys = {k: v for k, v in self.keys.items() if v}
        self.s.models = self.models
        self.s.base_urls = self.base_urls
        self.s.speak_replies = self.speak.isChecked()
        self.s.wander = self.wander.isChecked()
        self.s.learn_enabled = self.learn.isChecked()
        self.s.suggestions = self.suggest.isChecked()
        self.s.allow_shell = self.shell.isChecked()
        self.s.save()
        self.accept()


class MemoryDialog(MatteDialog):
    """See, delete and wipe what Kuro has learned about you."""

    def __init__(self, memory, settings):
        super().__init__("MEMORY")
        self.memory = memory
        self.setMinimumSize(500, 460)
        intro = QLabel(f"What {html.escape(settings.cat_name)} knows about you. Stored only "
                       f"on this computer. Select a fact and press Forget to remove it.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {DIM_HEX};")
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.count = QLabel()
        self.count.setFont(theme.mono(7.5, spacing=120))
        self.count.setStyleSheet(f"color: {DIM_HEX};")

        wipe = QPushButton("FORGET EVERYTHING")
        wipe.setObjectName("danger")
        forget = QPushButton("FORGET")
        close = QPushButton("CLOSE")
        close.setObjectName("primary")
        wipe.clicked.connect(self._wipe)
        forget.clicked.connect(self._forget)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(self.count)
        row.addStretch()
        row.addWidget(wipe)
        row.addWidget(forget)
        row.addWidget(close)
        self.body.addWidget(intro)
        self.body.addWidget(self.list, 1)
        self.body.addLayout(row)
        self._wiping = False
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        facts = sorted(self.memory.all(), key=lambda f: (f.category, f.text.lower()))
        for f in facts:
            tag = f.category.upper() + ("" if f.source == "user" else " · LEARNED")
            item = QListWidgetItem(f"{tag}\n{f.text}")
            item.setData(Qt.UserRole, f.id)
            self.list.addItem(item)
        self.count.setText(f"{len(facts)} FACTS")

    def _forget(self) -> None:
        for item in self.list.selectedItems():
            self.memory.forget(item.data(Qt.UserRole))
        self._fill()

    def _wipe(self) -> None:
        btn = self.sender()
        if not self._wiping:          # two clicks: arm, then confirm
            self._wiping = True
            btn.setText("CLICK AGAIN TO CONFIRM")
            QTimer.singleShot(3000, lambda: (setattr(self, "_wiping", False),
                                             btn.setText("FORGET EVERYTHING")))
            return
        self.memory.clear()
        self._wiping = False
        btn.setText("FORGET EVERYTHING")
        self._fill()
