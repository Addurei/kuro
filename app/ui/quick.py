"""Quick actions panel: pops up next to your cursor after you select text
anywhere and press the hotkey. Same matte HUD look as the chat."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea, QFrame,
                               QVBoxLayout, QWidget)

from . import theme
from .hud import IconButton, TypingDots
from .theme import DIM_HEX, TEXT_HEX
from ..quick import ACTIONS, REPLACEABLE

W, H = 380, 300


class QuickPanel(QWidget):
    action = Signal(str)          # "summarize" | "explain" | ...
    copy_clicked = Signal()
    replace_clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(theme.CSS)
        self.setFixedSize(W, H)
        self.result_text = ""
        self.current = ""

        title = QLabel("Quick actions")
        title.setFont(theme.sans(10, bold=True))
        close = IconButton("close", 24)
        close.clicked.connect(self.hide)
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(close)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(f"color: {DIM_HEX}; font-size: 8.5pt;")

        self.buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        row.setSpacing(4)
        for key, (label, _how) in ACTIONS.items():
            b = QPushButton(label)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("padding: 5px 6px;")
            b.clicked.connect(lambda _c=False, k=key: self._pick(k))
            self.buttons[key] = b
            row.addWidget(b)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.setFont(theme.sans(9.6))
        self.result.setStyleSheet(f"color: {TEXT_HEX};")
        self.result.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.result)
        self.dots = TypingDots()
        self.dots.hide()

        self.copy_btn = QPushButton("COPY")
        self.copy_btn.setObjectName("ghost")
        self.copy_btn.clicked.connect(self.copy_clicked)
        self.replace_btn = QPushButton("REPLACE")
        self.replace_btn.setObjectName("primary")
        self.replace_btn.clicked.connect(self.replace_clicked)
        bottom = QHBoxLayout()
        bottom.addWidget(self.dots)
        bottom.addStretch()
        bottom.addWidget(self.copy_btn)
        bottom.addWidget(self.replace_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 14, 14)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addWidget(self.preview)
        lay.addLayout(row)
        lay.addWidget(self.scroll, 1)
        lay.addLayout(bottom)

    # ------------------------------------------------------------ api
    def open_for(self, text: str) -> None:
        snippet = " ".join(text.split())
        self.preview.setText(f"“{snippet[:110]}{'…' if len(snippet) > 110 else ''}”  "
                             f"· {len(text.split())} words")
        self.set_result("Pick what to do with it.", final=False, hint=True)
        for b in self.buttons.values():
            b.setChecked(False)
        self.current = ""
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_loading(self) -> None:
        self.dots.show()
        self.result.setText("")
        self.copy_btn.setEnabled(False)
        self.replace_btn.setEnabled(False)

    def set_result(self, text: str, final: bool = True, hint: bool = False) -> None:
        self.dots.hide()
        self.result_text = text if final else ""
        self.result.setText(text)
        self.result.setStyleSheet(f"color: {DIM_HEX if hint else TEXT_HEX};")
        self.copy_btn.setEnabled(final)
        self.replace_btn.setEnabled(final and self.current in REPLACEABLE)
        self.replace_btn.setVisible(self.current in REPLACEABLE or not final)

    # ------------------------------------------------------------ internals
    def _pick(self, key: str) -> None:
        self.current = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        self.set_loading()
        self.action.emit(key)

    def _place(self) -> None:
        c = QCursor.pos()
        scr = (QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()).availableGeometry()
        x = min(max(c.x() + 14, scr.left() + 6), scr.right() - W - 6)
        y = min(max(c.y() + 14, scr.top() + 6), scr.bottom() - H - 6)
        self.move(QPoint(int(x), int(y)))

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        theme.paint_matte(p, path)
