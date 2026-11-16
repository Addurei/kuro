"""Who lives in this install.

Kuro ships with the app. But the engine is character-driven: every colour,
name, pronoun and accessory the rig and UI use comes from the dict below —
so a custom character is just data.

To reskin your install, drop a CHARACTER.json next to main.py. Any keys you
provide deep-merge over Kuro's defaults, so a minimal file only needs the
bits you want to change:

    {"name": "Mochi", "folder": "Mochi", "accent": [255, 170, 60],
     "accent_hex": "#ffaa3c", "fur": {"LIT": [250, 240, 228]}}

scripts/make_release.py never ships CHARACTER.json and the self-updater
never overwrites it, so updates keep whoever your install already is.
"""
from __future__ import annotations

import json
from pathlib import Path

CHARACTERS: dict[str, dict] = {
    "kuro": {
        "name": "Kuro",
        "folder": "Kuro",                 # %APPDATA%\Kuro, %LOCALAPPDATA%\Kuro
        "fur_word": "black",
        "pronouns": ("he", "him", "his"),
        "accent": (186, 226, 92),         # his lime: eyes, UI, glow
        "accent_hex": "#bae25c",
        "icon": "icon.png",
        "ribbon": False,
        "personality": "",                # config.py keeps its own default
        "wake_words": ("kuro", "kurro", "curo", "kuru", "cuero", "kudo", "kiro", "cuddo", "kura"),
        "wake_stem": "kur",
        # the rig's shading ramp, dark fur
        "fur": {"LIT": (94, 98, 124), "MID": (42, 43, 56), "SHADE": (16, 16, 21),
                "DEEP": (3, 3, 5), "RIM": (255, 255, 255, 36), "RIM_FUR": (92, 104, 150),
                "INNER_EAR": (244, 242, 248)},
        "face": {"MOUTH": (120, 122, 132), "MOUTH_HAPPY": (150, 152, 160),
                 "WHISKER": (205, 208, 216, 150), "SHADOW": (0, 0, 0), "SHADOW_K": 1.0,
                 "BLUSH_K": 1.0, "LASHES": False},
        "eyes": {"EYE": (186, 226, 92), "IRIS_LIGHT": (232, 250, 150),
                 "IRIS_DEEP": (128, 180, 52), "IRIS_RIM": (62, 104, 26),
                 "IRIS_SHADOW": (52, 92, 22),
                 # the darks inside the eye (lid shadow, pupil well, outer limb)
                 # and the lights on its glass (bounce, bright crescent)
                 "EYE_DARK": (2, 12, 0), "WELL": (10, 30, 0), "LIMB": (18, 40, 6),
                 "BOUNCE": (150, 205, 80), "CRESCENT": (244, 255, 200)},
    },
}


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def character_key(root: Path | None = None) -> str:
    try:
        key = (root or _root()).joinpath("CHARACTER").read_text(encoding="utf-8").strip().lower()
        return key if key in CHARACTERS else "kuro"
    except OSError:
        return "kuro"


def _tuplify(value):
    """JSON has no tuples; colours and pronouns come back as lists."""
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, dict):
        return {k: _tuplify(v) for k, v in value.items()}
    return value


def load_custom(root: Path | None = None) -> dict | None:
    """A CHARACTER.json next to main.py reskins this install.

    Returns the merged character dict, or None when the file is absent or
    unreadable (the app then falls back to the built-in characters).
    """
    path = (root or _root()) / "CHARACTER.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("name"):
        return None
    merged: dict = {}
    for key, default in CHARACTERS["kuro"].items():
        if isinstance(default, dict):
            merged[key] = {**default, **_tuplify(data.get(key, {}))}
        else:
            merged[key] = _tuplify(data.get(key, default))
    for key, value in data.items():          # keys the defaults don't have
        if key not in merged:
            merged[key] = _tuplify(value)
    return merged


CURRENT: dict = load_custom() or CHARACTERS[character_key()]
