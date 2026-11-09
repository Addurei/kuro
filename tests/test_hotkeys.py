"""Rebindable shortcuts: sane, not Windows' own, no clashes."""
from app import hotkeys as HK


def test_combo_names_and_order():
    assert HK.combo({"alt", "ctrl"}, "K") == "ctrl+alt+k"
    assert HK.combo({"windows", "shift"}, "Space") == "shift+windows+space"
    assert HK.combo(set(), "F9") == "f9"


def test_rules():
    cur = {"hotkey_chat": "ctrl+alt+k", "hotkey_voice": "ctrl+alt+space"}
    assert HK.check("ctrl+alt+j", "hotkey_chat", cur) == ""
    assert HK.check("f9", "hotkey_chat", cur) == ""                      # F-keys alone are fine
    assert "Ctrl, Alt or Win" in HK.check("shift+k", "hotkey_chat", cur)  # would fire while typing
    assert "Windows shortcut" in HK.check("ctrl+c", "hotkey_chat", cur)
    assert "already used" in HK.check("ctrl+alt+space", "hotkey_chat", cur)
    assert HK.check("ctrl+alt+k", "hotkey_chat", cur) == ""               # same one: fine
    assert HK.pretty("ctrl+alt+space") == "Ctrl + Alt + Space"
