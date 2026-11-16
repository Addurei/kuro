"""The bundled typefaces: Geist and Geist Mono (SIL OFL, see assets/fonts).

Shipping our own type means the cat looks the same on every PC instead of
whatever Windows happens to have. Subset to Latin plus the arrows and marks
the UI uses, so all five weights together are ~250 KB.
"""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
_loaded: list[str] = []


def load() -> list[str]:
    """Register the fonts with Qt (needs a QGuiApplication). Safe to call
    again; returns the families that loaded (empty if the files are missing,
    in which case theme falls back to the system fonts)."""
    if _loaded:
        return _loaded
    from PySide6.QtGui import QFontDatabase
    for f in sorted(_DIR.glob("*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(f))
        if fid >= 0:
            for fam in QFontDatabase.applicationFontFamilies(fid):
                if fam not in _loaded:
                    _loaded.append(fam)
    return _loaded
