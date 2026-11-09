"""Opening apps for real, and checking that they actually opened.

The old way (`start "" "<name>"` in a hidden shell) always "succeeded":
Popen returned at once, Windows showed its "can't find" error somewhere
invisible, and Kuro said "Launched!" while nothing happened.

Now:
  1. resolve   the name to something that really exists on this PC:
               an already-open window, a known alias, an .exe on PATH,
               the registry's App Paths, a Start-menu shortcut, or a
               Store app (Get-StartApps). Fuzzy-matched, so "unity",
               "vs code" and "Discord" all work.
  2. launch    it with the right mechanism (os.startfile raises if
               Windows can't open it; Store apps go through
               shell:AppsFolder).
  3. verify    by waiting a few seconds for a new window or process.
               The result tells the model exactly what happened, so
               Kuro never claims an app is open when it isn't.
"""
from __future__ import annotations

import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

IS_WINDOWS = platform.system() == "Windows"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LaunchError(Exception):
    pass


# Friendly names -> (launch target, words to look for in its window/process).
# Targets may be an .exe name, a protocol ("spotify:"), or "shell:..." paths.
ALIASES: dict[str, tuple[str, tuple[str, ...]]] = {
    "notepad": ("notepad.exe", ("notepad",)),
    "calculator": ("calc.exe", ("calculator", "calc")),
    "calc": ("calc.exe", ("calculator", "calc")),
    "paint": ("mspaint.exe", ("paint", "mspaint")),
    "explorer": ("explorer.exe", ("file explorer", "explorer")),
    "file explorer": ("explorer.exe", ("file explorer", "explorer")),
    "files": ("explorer.exe", ("file explorer", "explorer")),
    "task manager": ("taskmgr.exe", ("task manager", "taskmgr")),
    "settings": ("ms-settings:", ("settings", "systemsettings")),
    "cmd": ("cmd.exe", ("command prompt", "cmd")),
    "command prompt": ("cmd.exe", ("command prompt", "cmd")),
    "terminal": ("wt.exe", ("terminal", "windowsterminal")),
    "powershell": ("powershell.exe", ("powershell",)),
    "snipping tool": ("ms-screenclip:", ("snipping", "screenclip")),
    "camera": ("microsoft.windows.camera:", ("camera",)),
    "store": ("ms-windows-store:", ("store",)),
    "microsoft store": ("ms-windows-store:", ("store",)),
    "spotify": ("spotify:", ("spotify",)),
    "discord": ("discord:", ("discord",)),
    "steam": ("steam:", ("steam",)),
    "vscode": ("code", ("visual studio code", "code")),
    "vs code": ("code", ("visual studio code", "code")),
    "visual studio code": ("code", ("visual studio code", "code")),
    "word": ("winword", ("word", "winword")),
    "excel": ("excel", ("excel",)),
    "powerpoint": ("powerpnt", ("powerpoint", "powerpnt")),
    "outlook": ("outlook", ("outlook",)),
}

# Things that are never what you meant when you say "open X".
NOISE = re.compile(r"\b(uninstall|readme|help|documentation|license|release notes|"
                   r"website|manual|support|repair|safe mode)\b", re.I)


@dataclass
class Candidate:
    name: str           # display name ("Unity Hub")
    target: str         # what to launch
    kind: str           # alias | path | lnk | appid | protocol | window
    score: float = 1.0
    hwnd: int = 0       # for kind == window


# --------------------------------------------------------------- helpers
def norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\.(exe|lnk)$", "", s)
    s = re.sub(r"[^a-z0-9+#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_score(query: str, name: str) -> float:
    """0..1: how well a typed/spoken app name matches an installed one."""
    q, n = norm(query), norm(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if n.startswith(q + " ") or n.startswith(q):
        return 0.92 - 0.002 * (len(n) - len(q))
    words = n.split()
    if q in words or all(w in words for w in q.split()):
        return 0.86 - 0.002 * (len(n) - len(q))
    if q.replace(" ", "") == n.replace(" ", ""):
        return 0.95
    if q in n:
        return 0.75
    return difflib.SequenceMatcher(None, q, n).ratio() * 0.8


def start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    home = Path.home()
    dirs.append(home / "Desktop")
    pub = os.environ.get("PUBLIC")
    if pub:
        dirs.append(Path(pub) / "Desktop")
    return dirs


def shortcuts(dirs: list[Path] | None = None) -> list[tuple[str, Path]]:
    out = []
    for d in dirs if dirs is not None else start_menu_dirs():
        if not d.is_dir():
            continue
        try:
            for lnk in d.rglob("*"):
                if lnk.suffix.lower() in (".lnk", ".url", ".appref-ms") \
                        and not NOISE.search(lnk.stem):
                    out.append((lnk.stem, lnk))
        except OSError:
            continue
    return out


def app_paths(exe: str) -> str | None:
    """Registry 'App Paths': where installers register their .exe."""
    if not IS_WINDOWS:
        return None
    import winreg  # type: ignore[import-not-found]
    name = exe if exe.lower().endswith(".exe") else exe + ".exe"
    key = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key) as k:
                val, _ = winreg.QueryValueEx(k, "")
                val = os.path.expandvars(str(val).strip('"'))
                if os.path.isfile(val):
                    return val
        except OSError:
            continue
    return None


_START_APPS: tuple[float, list[tuple[str, str]]] = (0.0, [])


def start_apps() -> list[tuple[str, str]]:
    """Every app in the Start menu, including Store apps: (name, AppID).
    Cached for 10 minutes (the PowerShell call takes ~1s)."""
    global _START_APPS
    if not IS_WINDOWS:
        return []
    if time.monotonic() - _START_APPS[0] < 600 and _START_APPS[1]:
        return _START_APPS[1]
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW)
        data = json.loads(r.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        apps = [(d["Name"], d["AppID"]) for d in data
                if d.get("Name") and d.get("AppID") and not NOISE.search(d["Name"])]
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, TypeError):
        apps = []
    _START_APPS = (time.monotonic(), apps)
    return apps


# --------------------------------------------------------------- windows
def visible_windows() -> list[tuple[int, str, str]]:
    """(hwnd, title, process exe name) of visible top-level windows."""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    found: list[tuple[int, str, str]] = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def exe_of(hwnd) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value).stem.lower()
        finally:
            kernel32.CloseHandle(h)
        return ""

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value.strip()
                if title and title not in ("Program Manager", "Kuro"):
                    found.append((int(hwnd), title, exe_of(hwnd)))
        return True
    user32.EnumWindows(proto(cb), 0)
    return found


def focus(hwnd: int) -> None:
    if not IS_WINDOWS:
        return
    import ctypes
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)        # SW_RESTORE
    # Windows only lets the foreground app steal focus; a tiny Alt tap
    # counts as user input and unlocks SetForegroundWindow.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)


def window_matches(words: tuple[str, ...], title: str, exe: str) -> bool:
    t, e = norm(title), norm(exe)
    for w in words:
        w = norm(w)
        if not w:
            continue
        if w == e or w.replace(" ", "") == e.replace(" ", ""):
            return True
        if re.search(rf"(^|\s){re.escape(w)}($|\s)", t):
            return True
    return False


# --------------------------------------------------------------- resolve
def resolve(name: str, *, lnks: list[tuple[str, Path]] | None = None,
            store: list[tuple[str, str]] | None = None,
            which: Callable[[str], str | None] = shutil.which) -> tuple[list[Candidate], tuple[str, ...]]:
    """All the ways `name` could be launched, best first, plus the words to
    look for in its window to confirm it opened."""
    raw = name.strip().strip('"')
    key = norm(raw)
    if not key:
        raise LaunchError("no app name given")
    cands: list[Candidate] = []
    words: tuple[str, ...] = (key,)

    # a full path to an exe / shortcut
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if p.suffix and p.is_file():
        return [Candidate(p.stem, str(p), "path")], (norm(p.stem),)

    if key in ALIASES:
        target, words = ALIASES[key]
        words = tuple(dict.fromkeys(words + (key,)))
        if target.endswith(":"):
            cands.append(Candidate(raw, target, "protocol", 0.99))
        else:
            hit = which(target) or app_paths(target)
            if hit:
                cands.append(Candidate(raw, hit, "path", 1.0))

    exe = which(raw) or (app_paths(raw) if IS_WINDOWS else None)
    if exe:
        cands.append(Candidate(raw, exe, "path", 0.97))

    best: dict[str, Candidate] = {}
    for disp, lnk in (lnks if lnks is not None else shortcuts()):
        s = match_score(raw, disp)
        if s >= 0.6 and (disp not in best or best[disp].score < s):
            best[disp] = Candidate(disp, str(lnk), "lnk", s)
    for disp, appid in (store if store is not None else start_apps()):
        s = match_score(raw, disp)
        if s >= 0.6 and disp not in best:
            best[disp] = Candidate(disp, appid, "appid", s - 0.01)
    cands.extend(sorted(best.values(), key=lambda c: -c.score))

    if cands:
        top = max(cands, key=lambda c: c.score)
        words = tuple(dict.fromkeys(words + (norm(top.name),)))
    return cands, words


def suggestions(name: str, lnks=None, store=None, n: int = 3) -> list[str]:
    names = {d for d, _ in (lnks if lnks is not None else shortcuts())}
    names |= {d for d, _ in (store if store is not None else start_apps())}
    scored = sorted(names, key=lambda d: -match_score(name, d))
    return [d for d in scored[:n] if match_score(name, d) > 0.35]


# --------------------------------------------------------------- launch
def _start(c: Candidate) -> None:
    if not IS_WINDOWS:
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", c.target])
            return
        raise LaunchError("opening apps is only supported on Windows")
    if c.kind == "appid":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{c.target}"],
                         creationflags=NO_WINDOW)
    else:
        os.startfile(c.target)            # type: ignore[attr-defined]  # raises if it can't


def _wait_for(words: tuple[str, ...], before: set[int], seconds: float) -> tuple[int, str] | None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(0.4)
        for hwnd, title, exe in visible_windows():
            if hwnd not in before and window_matches(words, title, exe):
                return hwnd, title
    return None


def open_app(name: str, wait: float = 8.0) -> str:
    cands, words = resolve(name)

    # already open? just bring it to the front
    for hwnd, title, exe in visible_windows():
        if window_matches(words, title, exe):
            focus(hwnd)
            return f"{name} was already open, so I brought it to the front ('{title}')."

    if not cands:
        alt = suggestions(name)
        hint = f" Closest installed apps: {', '.join(alt)}." if alt else ""
        raise LaunchError(f"couldn't find an app called '{name}' on this PC.{hint} "
                          "Nothing was opened.")

    before = {h for h, _t, _e in visible_windows()}
    errors = []
    for c in cands[:3]:
        try:
            _start(c)
        except OSError as e:
            errors.append(f"{c.name}: {e.strerror or e}")
            continue
        if not IS_WINDOWS:
            return f"Asked the system to open {c.name}."
        hit = _wait_for(words, before, wait)
        if hit:
            focus(hit[0])
            return f"Opened {c.name}; its window is up ('{hit[1]}')."
        # some apps (launchers, tray apps) open without a matching title:
        # accept any brand-new window as a likely success, but say so
        new = [t for h, t, _e in visible_windows() if h not in before]
        if new:
            return (f"Started {c.name}. A new window appeared ('{new[0]}'), "
                    "so it's probably that.")
        return (f"Started {c.name}, but no window has appeared yet after {int(wait)}s. "
                "It may still be loading, or it may have opened in the tray. "
                "Tell the user exactly this; don't say it's open.")
    raise LaunchError(f"Windows couldn't open '{name}' ({'; '.join(errors)}). "
                      "Nothing was opened.")
