"""Kuro's own lines in English, Filipino and Taglish.

His AI replies follow the language setting through his persona (brain.py).
These are the things he says by himself: waking up, focus mode, reminders...
Use t(settings, key, **values). Unknown keys or languages fall back to English.
"""
from __future__ import annotations

LANGS = ("English", "Filipino", "Taglish")

REPLY_RULE = {
    "English": "Reply in English, unless the user writes to you in another language.",
    "Filipino": ("Reply in natural, conversational Filipino (Tagalog), the way people in the "
                 "Philippines really talk, not textbook Tagalog. Keep app names and technical "
                 "words in English (\"i-open ko na ang Spotify\")."),
    "Taglish": ("Reply in Taglish: a casual, natural mix of Tagalog and English, the way "
                "Filipinos chat and text each other (\"Sige, i-open ko na yung Spotify!\")."),
}

LINES: dict[str, dict[str, str]] = {
    "woke": {"English": "I'm up!", "Filipino": "Gising na ako!", "Taglish": "Gising na ako!"},
    "missed": {"English": "Sorry, I didn't catch that.",
               "Filipino": "Pasensya, hindi ko narinig nang maayos.",
               "Taglish": "Sorry, di ko na-catch yun."},
    "recovered": {"English": "Oops, I tripped over something. I'm back!",
                  "Filipino": "Ay, natisod ako. Nandito na ulit ako!",
                  "Taglish": "Oops, natisod ako. I'm back!"},
    "reminder": {"English": "Reminder: {msg}", "Filipino": "Paalala: {msg}",
                 "Taglish": "Reminder: {msg}"},
    "setup": {"English": "Right-click me → Settings → AI & Voice to pick my brain.",
              "Filipino": "I-right-click mo ako → Settings → AI & Voice para bigyan ako ng utak.",
              "Taglish": "Right-click mo ako → Settings → AI & Voice para may brain na ako."},
    "switched": {"English": "({frm} is out of breath, so {to} is thinking for me now.)",
                 "Filipino": "(Hingal na si {frm}, kaya si {to} muna ang nag-iisip para sa akin.)",
                 "Taglish": "(Pagod na si {frm}, kaya si {to} muna ang thinking para sa akin.)"},
    "focus_start": {"English": "Focus mode: {mins} minutes. I'll nap; you've got this.",
                    "Filipino": "Focus mode: {mins} minuto. Iidlip muna ako; kaya mo 'yan.",
                    "Taglish": "Focus mode: {mins} minutes. Nap muna ako; kaya mo 'yan!"},
    "focus_break": {"English": "Break time! {mins} minutes. Stretch with me, drink some water.",
                    "Filipino": "Pahinga muna! {mins} minuto. Mag-unat tayo, uminom ka ng tubig.",
                    "Taglish": "Break time! {mins} minutes. Stretch tayo, drink ka ng water."},
    "focus_back": {"English": "Back to it! I'll keep quiet.",
                   "Filipino": "Balik trabaho! Tahimik lang ako.",
                   "Taglish": "Back to work! Quiet lang ako."},
    "focus_done": {"English": "All {n} sessions done! {mins} minutes of focus today. I'm proud of you.",
                   "Filipino": "Tapos na ang {n} session! {mins} minutong focus ngayong araw. "
                               "Proud ako sa'yo.",
                   "Taglish": "Done na lahat ng {n} sessions! {mins} minutes of focus today. "
                              "Proud ako sa'yo!"},
    "focus_stopped": {"English": "Focus stopped. {mins} minutes in. Good effort!",
                      "Filipino": "Itinigil ang focus. {mins} minuto. Magaling!",
                      "Taglish": "Focus stopped. {mins} minutes. Good job pa rin!"},
    "suggest": {"English": "It's {when}. You usually open {app} around now. Want me to? "
                           "Click here and say yes.",
                "Filipino": "{when} na. Kadalasan binubuksan mo ang {app} sa ganitong oras. "
                            "Buksan ko na? I-click mo ako at sabihin mong oo.",
                "Taglish": "{when} na. Usually binubuksan mo yung {app} around this time. "
                           "Open ko na? Click mo ako tapos say yes."},
}


# Chat's empty state: a hint, then things to try in one tap: (label, what gets sent)
CHAT_HINT = {"English": "Ask me anything, or try one of these",
             "Filipino": "Magtanong ka lang, o subukan ang isa sa mga ito",
             "Taglish": "Ask me anything, o i-try mo 'to"}
CHAT_CHIPS = {
    "English": [("Open Spotify", "open Spotify"),
                ("Focus for 25 minutes", "start a 25 minute focus session"),
                ("What can you do?", "what can you do?")],
    "Filipino": [("Buksan ang Spotify", "buksan mo ang Spotify"),
                 ("Mag-focus nang 25 minuto", "mag-focus tayo nang 25 minuto"),
                 ("Ano'ng kaya mong gawin?", "ano ang mga kaya mong gawin?")],
    "Taglish": [("I-open ang Spotify", "i-open mo yung Spotify"),
                ("Focus for 25 minutes", "mag-focus tayo for 25 minutes"),
                ("Ano'ng kaya mong gawin?", "ano yung mga kaya mong gawin?")],
}


def chat_starters(settings) -> tuple[str, list[tuple[str, str]]]:
    v = lang(settings)
    return CHAT_HINT[v], CHAT_CHIPS[v]


def lang(settings) -> str:
    v = getattr(settings, "language", "English")
    return v if v in LANGS else "English"


def t(settings, key: str, **values) -> str:
    row = LINES.get(key, {})
    text = row.get(lang(settings)) or row.get("English") or key
    try:
        return text.format(**values)
    except (KeyError, IndexError):
        return text
