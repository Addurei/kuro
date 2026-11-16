"""Matte-black HUD look: colours, fonts, the panel painter, the moving
border light and Kuro's hand-drawn icons."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QRadialGradient, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen)

from ..character import CURRENT as _CHAR

_ACC_RGB = "%d, %d, %d" % tuple(_CHAR["accent"][:3])


def _shade(base: tuple, k: float) -> str:
    """base accent scaled toward light (k>1) or dark (k<1), as #rrggbb."""
    r, g, b = (max(0, min(255, int(v * k + (255 - 255 * min(1, k)) * 0.55))) for v in base[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


_A = tuple(_CHAR["accent"][:3])
ACC_LIGHT = _shade(_A, 1.06)           # primary button, top of the gradient
ACC_DARK = "#%02x%02x%02x" % tuple(int(v * 0.82) for v in _A)
ACC_LIGHT2 = _shade(_A, 1.18)
ACC_DARK2 = "#%02x%02x%02x" % tuple(int(v * 0.9) for v in _A)
ACC_TINT_DK = "#%02x%02x%02x" % tuple(int(v * 0.16) for v in _A)   # checked seg, dark wash
ACC_TINT_DK2 = "#%02x%02x%02x" % tuple(int(v * 0.11) for v in _A)

LIME = QColor(*_CHAR["accent"])        # the character's accent (lime / rose)
RED = QColor(255, 93, 108)
LIME_HEX = _CHAR["accent_hex"]
DANGER_HEX = "#ff5d6c"
TEXT_HEX = "#e9e9ec"
DIM_HEX = "#868991"
FAINT_HEX = "#4a4d55"

# Geist ships in assets/fonts (loaded by fonts.load at startup); the rest are
# fallbacks for a missing file.
MONO = ["Geist Mono", "Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono"]
SANS = ["Geist", "Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans"]
MONO_CSS = ", ".join(f"'{f}'" for f in MONO)
SANS_CSS = ", ".join(f"'{f}'" for f in SANS)


def mono(size: float, bold: bool = False, spacing: float = 112) -> QFont:
    f = QFont()
    f.setFamilies(MONO)
    f.setPointSizeF(size)
    f.setBold(bold)
    f.setLetterSpacing(QFont.PercentageSpacing, spacing)
    return f


def sans(size: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(SANS)
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def lime(alpha: float = 1.0) -> QColor:
    c = QColor(LIME)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def with_alpha(c: QColor, a: float) -> QColor:
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


# ------------------------------------------------------------------ panel
def paint_matte(p: QPainter, path: QPainterPath) -> None:
    """A smoked-glass slab: a cool deep gradient, a faint lime glow bleeding in
    from the top-left, a lit top edge and a hairline that fades as it goes
    down. Reads as one premium object rather than a flat black box."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    r = path.boundingRect()
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    # fully opaque: at 98% whatever sat behind the window (desktop icons,
    # another app's text) bled through as faint ghosts
    g.setColorAt(0.0, QColor(24, 25, 30))
    g.setColorAt(0.35, QColor(15, 16, 19))
    g.setColorAt(1.0, QColor(9, 9, 11))
    p.fillPath(path, g)
    p.setClipPath(path)
    glow = QRadialGradient(QPointF(r.left() + r.width() * 0.12, r.top()), max(120.0, r.width() * 0.8))
    g0, g1 = QColor(LIME), QColor(LIME)
    g0.setAlpha(16)
    g1.setAlpha(0)
    glow.setColorAt(0.0, g0)
    glow.setColorAt(1.0, g1)
    p.fillRect(r, glow)
    sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + 70))
    sheen.setColorAt(0, QColor(255, 255, 255, 14))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(r.left(), r.top(), r.width(), 70), sheen)
    p.setClipping(False)
    edge = QLinearGradient(r.topLeft(), r.bottomLeft())
    edge.setColorAt(0.0, QColor(255, 255, 255, 46))
    edge.setColorAt(0.25, QColor(255, 255, 255, 16))
    edge.setColorAt(1.0, QColor(255, 255, 255, 8))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QBrush(edge), 1))
    p.drawPath(path)
    p.restore()


# ------------------------------------------------------------------ icons
def icon(p: QPainter, kind: str, c: QPointF, s: float, color: QColor) -> None:
    """Draw a line icon of size ~s centred on c."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color, max(1.4, s * 0.09), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    x, y, h = c.x(), c.y(), s / 2
    if kind == "chat":
        path = QPainterPath()
        path.addRoundedRect(QRectF(x - h, y - h * 0.8, s, h * 1.35), h * 0.45, h * 0.45)
        p.drawPath(path)
        tail = QPainterPath(QPointF(x - h * 0.45, y + h * 0.55))
        tail.lineTo(QPointF(x - h * 0.65, y + h * 0.95))
        tail.lineTo(QPointF(x - h * 0.05, y + h * 0.55))
        p.drawPath(tail)
        for dx in (-0.4, 0, 0.4):
            p.drawPoint(QPointF(x + dx * h, y - h * 0.12))
    elif kind == "mic":
        p.drawRoundedRect(QRectF(x - h * 0.32, y - h, h * 0.64, h * 1.15), h * 0.32, h * 0.32)
        arc = QPainterPath(QPointF(x - h * 0.65, y - h * 0.15))
        arc.quadTo(QPointF(x - h * 0.65, y + h * 0.55), QPointF(x, y + h * 0.55))
        arc.quadTo(QPointF(x + h * 0.65, y + h * 0.55), QPointF(x + h * 0.65, y - h * 0.15))
        p.drawPath(arc)
        p.drawLine(QPointF(x, y + h * 0.55), QPointF(x, y + h * 0.95))
    elif kind == "paw":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(x, y + h * 0.3), h * 0.5, h * 0.42)
        for dx, dy in ((-0.62, -0.25), (-0.22, -0.68), (0.22, -0.68), (0.62, -0.25)):
            p.drawEllipse(QPointF(x + dx * h, y + dy * h), h * 0.2, h * 0.24)
    elif kind == "gear":
        p.drawEllipse(c, h * 0.32, h * 0.32)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(x + math.cos(a) * h * 0.62, y + math.sin(a) * h * 0.62),
                       QPointF(x + math.cos(a) * h * 0.92, y + math.sin(a) * h * 0.92))
        p.drawEllipse(c, h * 0.62, h * 0.62)
    elif kind == "power":
        p.drawArc(QRectF(x - h * 0.8, y - h * 0.7, h * 1.6, h * 1.6), 125 * 16, 290 * 16)
        p.drawLine(QPointF(x, y - h * 0.95), QPointF(x, y - h * 0.1))
    elif kind == "timer":
        p.drawEllipse(QPointF(x, y + h * 0.12), h * 0.8, h * 0.8)
        p.drawLine(QPointF(x, y + h * 0.12), QPointF(x, y - h * 0.38))
        p.drawLine(QPointF(x, y + h * 0.12), QPointF(x + h * 0.36, y + h * 0.3))
        p.drawLine(QPointF(x - h * 0.25, y - h * 0.95), QPointF(x + h * 0.25, y - h * 0.95))
    elif kind == "close":
        d = h * 0.55
        p.drawLine(QPointF(x - d, y - d), QPointF(x + d, y + d))
        p.drawLine(QPointF(x + d, y - d), QPointF(x - d, y + d))
    elif kind == "refresh":
        p.drawArc(QRectF(x - h * 0.7, y - h * 0.7, h * 1.4, h * 1.4), 40 * 16, 280 * 16)
        tip = QPointF(x + h * 0.54, y - h * 0.45)
        p.drawLine(tip, QPointF(tip.x() - h * 0.38, tip.y() - h * 0.06))
        p.drawLine(tip, QPointF(tip.x() + h * 0.02, tip.y() + h * 0.38))
    elif kind == "back":
        d = h * 0.5
        p.drawLine(QPointF(x + d * 0.4, y - d), QPointF(x - d * 0.6, y))
        p.drawLine(QPointF(x - d * 0.6, y), QPointF(x + d * 0.4, y + d))
    elif kind == "send":
        p.drawLine(QPointF(x, y + h * 0.7), QPointF(x, y - h * 0.7))
        p.drawLine(QPointF(x - h * 0.55, y - h * 0.15), QPointF(x, y - h * 0.7))
        p.drawLine(QPointF(x + h * 0.55, y - h * 0.15), QPointF(x, y - h * 0.7))
    p.restore()


CSS = f"""
QWidget {{ color: {TEXT_HEX}; font-family: {SANS_CSS}; font-size: 10pt; background: transparent; }}
QLabel {{ background: transparent; }}
QTextBrowser {{ background: transparent; border: none; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 5px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2a2c33; border-radius: 2px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {LIME_HEX}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page,
QScrollBar::sub-page {{ height: 0; background: none; }}
QLineEdit {{ background: transparent; border: none; padding: 6px 2px;
             selection-background-color: {LIME_HEX}; selection-color: #0a0a0b; }}
QFrame#field {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0b0b0e, stop:1 #111216);
                border: 1px solid #23252c; border-top-color: #1a1b20; border-radius: 13px; }}
QFrame#field[focus="true"] {{ border-color: rgba({_ACC_RGB}, 0.65); }}
QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1c1d22, stop:1 #141519);
               border: 1px solid #26282e; border-top-color: #31333a; border-radius: 10px;
               padding: 7px 16px; font-family: {MONO_CSS}; font-size: 8.5pt; }}
QPushButton:hover {{ border-color: #3a3d45; background: #1b1c20; }}
QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {ACC_LIGHT}, stop:1 {ACC_DARK});
                       color: #0a0a0b; border: none; font-weight: 700; }}
QPushButton#primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {ACC_LIGHT2}, stop:1 {ACC_DARK2}); }}
QPushButton#danger {{ background: transparent; color: {DANGER_HEX};
                      border: 1px solid {DANGER_HEX}; font-weight: 700; }}
QPushButton#danger:hover {{ background: rgba(255, 93, 108, 0.12); }}
QPushButton#ghost {{ background: transparent; border: none; color: {DIM_HEX}; padding: 7px 8px; }}
QPushButton#dangerlink {{ background: transparent; border: none; color: {DANGER_HEX};
                          padding: 4px 2px; font-family: {SANS_CSS}; font-size: 8.5pt; }}
QPushButton#dangerlink:hover {{ text-decoration: underline; }}
QPushButton#ghost:hover {{ color: {TEXT_HEX}; }}
QPushButton#seg {{ background: #101115; border: 1px solid #1f2127; border-radius: 9px;
                   padding: 5px 9px; color: {DIM_HEX}; font-family: {SANS_CSS}; font-size: 8.5pt; }}
QPushButton#seg:hover {{ color: {TEXT_HEX}; }}
QPushButton#seg:checked {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {ACC_TINT_DK}, stop:1 {ACC_TINT_DK2});
                           border-color: rgba({_ACC_RGB}, 0.7); color: {LIME_HEX}; }}
QPushButton#option {{ background: #111216; border: 1px solid #202228; border-radius: 10px;
                      padding: 8px 12px; text-align: left; font-family: {SANS_CSS};
                      font-size: 9pt; color: {TEXT_HEX}; }}
QPushButton#option:hover {{ border-color: #3a3d45; }}
QPushButton#option[state="right"] {{ background: #1a2410; border-color: {LIME_HEX}; color: {LIME_HEX}; }}
QPushButton#option[state="wrong"] {{ background: #2a1215; border-color: {DANGER_HEX}; color: {DANGER_HEX}; }}
"""
