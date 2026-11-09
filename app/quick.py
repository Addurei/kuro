"""Select-anywhere quick actions.

Highlight text in any app, press the hotkey: Kuro copies the selection
(putting your clipboard back afterwards), and offers to summarise,
explain, rewrite, fix or translate it.
"""
from __future__ import annotations

import time

ACTIONS = {
    "summarize": ("Summarize", "Summarize this in 2-4 short bullet points. Keep key facts, "
                  "names and numbers exactly as written."),
    "explain": ("Explain", "Explain this simply, like to a smart friend, in a few short "
                "sentences. Define any jargon."),
    "rewrite": ("Rewrite", "Rewrite this to be clearer and more natural. Keep the meaning, "
                "tone and language. Return only the rewritten text."),
    "fix": ("Fix", "Fix spelling, grammar and punctuation only. Change nothing else. "
            "Return only the corrected text."),
    "translate": ("Translate", "Translate this into {lang}. Return only the translation."),
}
REPLACEABLE = {"rewrite", "fix", "translate"}       # results that can be pasted back


def prompt_for(action: str, text: str, lang: str = "English") -> str:
    _label, how = ACTIONS[action]
    return (how.format(lang=lang) + "\n\nText:\n\"\"\"\n" + text.strip()[:12000] + "\n\"\"\"")


def grab_selection(wait: float = 0.6) -> str:
    """Copy whatever is selected in the app in front, then restore the
    clipboard. Returns '' if nothing was selected."""
    import keyboard
    import pyperclip
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait and any(
            keyboard.is_pressed(k) for k in ("ctrl", "alt", "shift", "windows")):
        time.sleep(0.02)                       # let go of the hotkey first
    try:
        old = pyperclip.paste()
    except Exception:
        old = ""
    marker = f"⁣kuro{time.time_ns()}"
    try:
        pyperclip.copy(marker)
        keyboard.send("ctrl+c")
        text = ""
        for _ in range(25):                     # up to ~0.5 s for the app to copy
            time.sleep(0.02)
            text = pyperclip.paste()
            if text != marker:
                break
        return "" if text == marker else (text or "")
    finally:
        try:
            pyperclip.copy(old)
        except Exception:
            pass


def paste_text(text: str) -> None:
    """Replace the selection in the app in front with `text`."""
    import keyboard
    import pyperclip
    try:
        old = pyperclip.paste()
    except Exception:
        old = ""
    pyperclip.copy(text)
    time.sleep(0.05)
    keyboard.send("ctrl+v")
    time.sleep(0.25)
    try:
        pyperclip.copy(old)
    except Exception:
        pass
