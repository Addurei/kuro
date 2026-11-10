"""Matte-black HUD look: colours, fonts, the panel painter, the moving
border light and Kuro's hand-drawn icons."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen)

LIME = QColor(186, 226, 92)
RED = QColor(255, 93, 108)
LIME_HEX = "#bae25c"
DANGER_HEX = "#ff5d6c"
TEXT_HEX = "#e9e9ec"
DIM_HEX = "#868991"
FAINT_HEX = "#4a4d55"

MONO = ["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono"]
SANS = ["Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans"]
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
    """Matte black slab: deep gradient, soft top sheen, hairline edge."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    r = path.boundingRect()
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0.0, QColor(21, 21, 24, 248))
    g.setColorAt(0.4, QColor(12, 12, 14, 248))
    g.setColorAt(1.0, QColor(8, 8, 10, 250))
    p.fillPath(path, g)
    p.setClipPath(path)
    sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + 60))
    sheen.setColorAt(0, QColor(255, 255, 255, 12))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(r.left(), r.top(), r.width(), 60), sheen)
    p.setClipping(False)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 18), 1))
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
QScrollBar::handle:vertical {{ background: #26282d; border-radius: 2px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {LIME_HEX}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page,
QScrollBar::sub-page {{ height: 0; background: none; }}
QLineEdit {{ background: transparent; border: none; padding: 6px 2px;
             selection-background-color: {LIME_HEX}; selection-color: #0a0a0b; }}
QFrame#field {{ background: #0d0d10; border: 1px solid #1d1f24; border-radius: 12px; }}
QFrame#field[focus="true"] {{ border-color: {LIME_HEX}; }}
QPushButton {{ background: #15161a; border: 1px solid #24262c; border-radius: 10px;
               padding: 7px 16px; font-family: {MONO_CSS}; font-size: 8.5pt; }}
QPushButton:hover {{ border-color: #3a3d45; background: #1b1c20; }}
QPushButton#primary {{ background: {LIME_HEX}; color: #0a0a0b; border: none; font-weight: 700; }}
QPushButton#primary:hover {{ background: #cdf07a; }}
QPushButton#danger {{ background: transparent; color: {DANGER_HEX};
                      border: 1px solid {DANGER_HEX}; font-weight: 700; }}
QPushButton#danger:hover {{ background: rgba(255, 93, 108, 0.12); }}
QPushButton#ghost {{ background: transparent; border: none; color: {DIM_HEX}; padding: 7px 8px; }}
QPushButton#dangerlink {{ background: transparent; border: none; color: {DANGER_HEX};
                          padding: 4px 2px; font-family: {SANS_CSS}; font-size: 8.5pt; }}
QPushButton#dangerlink:hover {{ text-decoration: underline; }}
QPushButton#ghost:hover {{ color: {TEXT_HEX}; }}
QPushButton#seg {{ background: #111216; border: 1px solid #202228; border-radius: 9px;
                   padding: 5px 9px; color: {DIM_HEX}; font-family: {SANS_CSS}; font-size: 8.5pt; }}
QPushButton#seg:hover {{ color: {TEXT_HEX}; }}
QPushButton#seg:checked {{ background: #1a1d10; border-color: {LIME_HEX}; color: {LIME_HEX}; }}
QPushButton#option {{ background: #111216; border: 1px solid #202228; border-radius: 10px;
                      padding: 8px 12px; text-align: left; font-family: {SANS_CSS};
                      font-size: 9pt; color: {TEXT_HEX}; }}
QPushButton#option:hover {{ border-color: #3a3d45; }}
QPushButton#option[state="right"] {{ background: #1a2410; border-color: {LIME_HEX}; color: {LIME_HEX}; }}
QPushButton#option[state="wrong"] {{ background: #2a1215; border-color: {DANGER_HEX}; color: {DANGER_HEX}; }}
"""
