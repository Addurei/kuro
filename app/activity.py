"""What you're doing right now, so Kuro can join in.

Only the active app's name and its window title are used (the same thing
the taskbar shows); never what's inside the window. Typing is counted, not
read: Kuro knows *that* you're typing, never which keys.

Everything here runs locally: no brain calls, so it costs nothing and
never touches the free daily quota.
"""
from __future__ import annotations

import random
import re
import time
from collections import deque
from dataclasses import dataclass

BROWSERS = ("chrome", "msedge", "opera", "firefox", "brave", "vivaldi", "arc", "zen")

APPS: dict[str, tuple[str, ...]] = {
    "coding": ("code", "code - insiders", "cursor", "windsurf", "zed", "devenv", "pycharm",
               "pycharm64", "idea64", "idea", "rider64", "webstorm64", "clion64", "goland64",
               "studio64", "sublime_text", "notepad++", "atom", "eclipse", "netbeans64",
               "android studio"),
    "writing": ("winword", "soffice", "swriter", "notion", "obsidian", "onenote", "wordpad",
                "notepad", "typora", "scrivener", "libreoffice"),
    "designing": ("blender", "unity", "photoshop", "illustrator", "figma", "krita",
                  "clipstudiopaint", "aseprite", "gimp-2.10", "gimp", "sketchup", "acad",
                  "revit", "3dsmax", "maya", "inkscape", "affinity", "substancepainter",
                  "zbrush", "houdini", "cinema 4d", "unreal", "unrealeditor", "godot"),
    "studying": ("acrord32", "acrobat", "sumatrapdf", "foxitpdfreader", "anki", "xodo"),
    "chatting": ("discord", "telegram", "messenger", "whatsapp", "slack", "teams", "ms-teams",
                 "signal", "viber"),
}
# in a browser, the tab's title says what it is
WEB: list[tuple[str, re.Pattern]] = [
    ("coding", re.compile(r"github|gitlab|stack ?overflow|leetcode|hackerrank|codewars|replit|"
                          r"codepen|colab|jupyter|codesandbox|vercel|w3schools|mdn web docs|"
                          r"tryhackme|hack ?the ?box|full ?stack", re.I)),
    ("writing", re.compile(r"google docs|overleaf|notion|medium|grammarly|quillbot|"
                           r"word online|microsoft word|hackmd|dropbox paper", re.I)),
    ("designing", re.compile(r"figma|canva|pixlr|photopea|sketchfab|spline|dribbble|behance", re.I)),
    ("studying", re.compile(r"\.pdf\b|canvas|classroom|moodle|blackboard|coursera|udemy|edx|"
                            r"khan academy|quizlet|brilliant|duolingo|\bums\b|course", re.I)),
    ("watching", re.compile(r"youtube|netflix|twitch|prime video|disney\+|crunchyroll|"
                            r"bilibili|viu|iwanttfc", re.I)),
    ("chatting", re.compile(r"messenger|whatsapp|discord|telegram|slack|teams|instagram|"
                            r"facebook|x\.com|twitter|reddit", re.I)),
]


@dataclass
class Activity:
    kind: str                  # coding | writing | designing | studying | watching | chatting | browsing | other
    app: str = ""              # "VS Code", "Word", "Blender"...
    subject: str = ""          # file / document / project name from the title, if any
    project: str = ""          # e.g. the folder open in VS Code

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.project or self.subject


NICE = {"code": "VS Code", "code - insiders": "VS Code", "devenv": "Visual Studio",
        "pycharm64": "PyCharm", "pycharm": "PyCharm", "idea64": "IntelliJ", "winword": "Word",
        "acrord32": "Acrobat", "sumatrapdf": "SumatraPDF", "studio64": "Android Studio",
        "sublime_text": "Sublime Text", "clipstudiopaint": "Clip Studio", "3dsmax": "3ds Max",
        "acad": "AutoCAD", "unrealeditor": "Unreal"}


def _strip(title: str) -> str:
    t = re.sub(r"^\(\d+\)\s*|^[●•*]\s*", "", title or "")
    return t.strip(" -–—")


def _clean_name(s: str) -> str:
    s = re.sub(r"\.(py|js|ts|tsx|jsx|cs|cpp|c|h|java|kt|go|rs|rb|php|html|css|md|json|blend|"
               r"docx?|pdf|txt|pptx?|xlsx?|unity|psd|fig)$", "", s.strip(), flags=re.I)
    return s.strip()[:40]


def detect(app: str, title: str) -> Activity:
    exe = (app or "").lower().removesuffix(".exe")
    t = _strip(title)
    nice = NICE.get(exe, (app or "").removesuffix(".exe").title() if app else "")
    for kind, exes in APPS.items():
        if exe in exes or any(exe.startswith(e) for e in exes if len(e) > 4):
            act = Activity(kind, nice)
            parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", t) if p.strip()]
            if kind == "coding" and len(parts) >= 3:          # file - project - VS Code
                act.subject, act.project = parts[0], parts[1]
            elif kind == "coding" and len(parts) == 2 and exe.startswith(("pycharm", "idea",
                                                                          "rider", "webstorm")):
                act.project, act.subject = parts[0], parts[1]    # JetBrains: project – file
            elif exe == "blender":
                m = re.search(r"([^\\/\[\]]+?)\.blend", t)
                act.subject = m.group(1) if m else ""
            elif parts and len(parts) >= 2:
                act.subject = parts[0]
            act.subject = _clean_name(act.subject)
            if act.subject.lower() in ("untitled", "document1", "new tab", "home", "blender"):
                act.subject = ""
            return act
    if exe in BROWSERS or any(exe.startswith(b) for b in BROWSERS):
        for kind, rx in WEB:
            if rx.search(t):
                parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", t) if p.strip()]
                subject = parts[0] if len(parts) >= 2 else ""
                site = rx.search(t).group(0)
                return Activity(kind, site.title() if len(site) > 3 else site.upper(),
                                _clean_name(subject))
        return Activity("browsing", "the browser")
    if not exe:
        return Activity("other")
    return Activity("other", nice)


class Typing:
    """Counts key presses (never which keys) from the global keyboard hook."""

    def __init__(self):
        self.times: deque[float] = deque(maxlen=40)

    def press(self, *_a) -> None:
        self.times.append(time.monotonic())

    def rate(self, window: float = 1.5, now: float | None = None) -> float:
        """Key presses per second over the last moment."""
        now = time.monotonic() if now is None else now
        n = sum(1 for t in self.times if now - t <= window)
        return n / window


# ------------------------------------------------------------ what he says
# {s} = subject (file/doc), {p} = project, {a} = app. Lines needing a value
# are only used when it's known. Short: they show in his bubble, unspoken.
LINES: dict[str, dict[str, list[str]]] = {
    "coding": {
        "start_p": ["Working on {p} again? I'll keep the bugs away.",
                    "{p} time! Glasses on.", "Ooh, {p}. Want a rubber duck? I'm a cat, close enough."],
        "start": ["Coding in {a}? I'll sit right here and look smart.",
                  "Glasses on. Let's write some clean code.", "Time to make the computer do things!"],
        "long": ["{m} minutes of coding. Stretch your paws?",
                 "You've been coding for {m} minutes. Water break?",
                 "{m} minutes in. Even I blink sometimes. Rest your eyes for a sec?"],
        "typing": ["You're typing so fast!", "Tap tap tap. I'm helping.", "Keyboard go brrr."],
    },
    "writing": {
        "start_s": ["Writing {s}? I'll be quiet. Mostly.", "Ooh, {s}. Can't wait to read it.",
                    "{s}! Pencil ready."],
        "start": ["Writing time. I've got my pencil.", "I'll keep you company while you write."],
        "long": ["{m} minutes of writing! Your words deserve a little break.",
                 "You've written for {m} minutes. Stretch?"],
        "typing": ["The words are flowing!", "Look at you go!"],
    },
    "designing": {
        "start_s": ["Back on {s}? Looking good already.", "{s}! Show me when it's done?"],
        "start": ["Art mode! Beret on.", "Making something pretty in {a}?"],
        "long": ["{m} minutes in {a}. Rest your wrist for a sec?",
                 "{m} minutes of creating. Step back and admire it?"],
    },
    "studying": {
        "start_s": ["Studying {s}? I'll quiz you later if you want.",
                    "{s}, huh. You've got this."],
        "start": ["Study time. I'll be your study buddy.", "Reading something? Glasses on."],
        "long": ["{m} minutes of studying. Your brain's earned a snack.",
                 "{m} minutes in. Want me to start a focus timer?"],
    },
    "watching": {"start": ["Movie night? I'll watch too.", "Ooh, what are we watching?"]},
    "chatting": {"start": ["Say hi to your friends for me!"]},
}
LONG_AT = (45, 90, 150)          # minutes of one activity before a gentle check-in


class Tracker:
    """Turns window samples into moments worth reacting to, without nagging:
    a new activity only counts once it's held for a bit, and Kuro speaks up
    at most once every `gap` seconds."""

    def __init__(self, settle: float = 20.0, gap: float = 15 * 60):
        self.settle, self.gap = settle, gap
        self.current: Activity = Activity("other")
        self._candidate: Activity | None = None
        self._cand_since = 0.0
        self._since = time.monotonic()
        self._greeted: set[tuple[str, str]] = set()
        self._longs: set[tuple[str, int]] = set()
        self._last_said = -1e9
        self._typing_said = -1e9

    @property
    def minutes(self) -> int:
        return int((time.monotonic() - self._since) / 60)

    def update(self, act: Activity, now: float | None = None) -> str:
        """Feed one sample; returns a line for Kuro to say, or ''."""
        now = time.monotonic() if now is None else now
        if act.kind == self.current.kind and (act.key == self.current.key or not act.key[1]):
            self._candidate = None
            if act.subject:
                self.current = act
            return self._long(now)
        if self._candidate is None or self._candidate.key != act.key:
            self._candidate, self._cand_since = act, now
            return ""
        if now - self._cand_since < self.settle:
            return ""
        same_kind = act.kind == self.current.kind
        self.current, self._candidate = act, None
        if not same_kind:
            self._since = self._cand_since
            self._longs = {x for x in self._longs if x[0] == act.kind}
        if act.key in self._greeted or act.kind not in LINES:
            return ""
        self._greeted.add(act.key)
        return self._say(now, act, "start")

    def _long(self, now: float) -> str:
        mins = int((now - self._since) / 60)
        for m in LONG_AT:
            if mins >= m and (self.current.kind, m) not in self._longs:
                self._longs.add((self.current.kind, m))
                return self._say(now, self.current, "long", m=m)
        return ""

    def typing_burst(self, rate: float, now: float | None = None) -> str:
        """A rare compliment when you're on fire (once an hour at most)."""
        now = time.monotonic() if now is None else now
        if rate < 6 or now - self._typing_said < 3600:
            return ""
        self._typing_said = now
        return self._say(now, self.current, "typing")

    def _say(self, now: float, act: Activity, what: str, m: int = 0) -> str:
        if now - self._last_said < self.gap:
            return ""
        lines = LINES.get(act.kind, {})
        pool = []
        if what == "start":
            if act.project and "start_p" in lines:
                pool = lines["start_p"]
            elif act.subject and "start_s" in lines:
                pool = lines["start_s"]
            pool = pool or lines.get("start", [])
        else:
            pool = lines.get(what, [])
        if not pool:
            return ""
        self._last_said = now
        return random.choice(pool).format(s=act.subject, p=act.project, a=act.app or "there",
                                          m=m)


PROPS = {"coding": "glasses", "studying": "glasses", "writing": "pencil", "designing": "beret"}
