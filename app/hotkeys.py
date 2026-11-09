"""Turning a key press into a hotkey string the `keyboard` package understands,
and checking it's a sensible, conflict-free choice."""
from __future__ import annotations

MODS = ("ctrl", "alt", "shift", "windows")
# keys that already mean something everywhere: never steal them
RESERVED = {"ctrl+c", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+a", "ctrl+s", "ctrl+w",
            "ctrl+t", "ctrl+n", "ctrl+f", "ctrl+p", "alt+f4", "alt+tab", "ctrl+alt+delete",
            "windows+l", "windows+d", "windows+e", "windows+r", "windows+tab"}
NAMES = {"return": "enter", "del": "delete", "esc": "esc", "pgup": "page up",
         "pgdown": "page down", "ins": "insert", "backspace": "backspace", " ": "space",
         "space": "space"}


def combo(mods: set[str], key: str) -> str:
    """{'ctrl','alt'}, 'K' -> 'ctrl+alt+k' (modifiers in a fixed order)."""
    k = NAMES.get(key.lower(), key.lower())
    return "+".join([m for m in MODS if m in mods] + [k])


def check(new: str, attr: str, current: dict[str, str]) -> str:
    """'' if fine, otherwise why not."""
    parts = new.split("+")
    key = parts[-1]
    mods = set(parts[:-1])
    if not key or key in MODS:
        return "Add a letter or key after the modifiers."
    if not mods & {"ctrl", "alt", "windows"} and not (key.startswith("f") and key[1:].isdigit()):
        return "Use Ctrl, Alt or Win with it, so it doesn't fire while you type."
    if new in RESERVED:
        return f"{pretty(new)} is a Windows shortcut. Pick another."
    for other, value in current.items():
        if other != attr and value == new:
            return f"{pretty(new)} is already used for something else in Kuro."
    return ""


def pretty(c: str) -> str:
    return " + ".join(p.title() if len(p) > 1 else p.upper() for p in c.split("+"))
