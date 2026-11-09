"""Everything the cat can *do* on your PC.

Each tool is a plain Python function plus a JSON schema that Claude
reads to decide when to call it. Tools marked `risky=True` never run
until you click "Allow" in a confirmation dialog - Claude cannot skip it.

Windows is the target. Every Windows-only call is guarded, so the code
still imports (and the tests still run) on other systems.
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import os
import platform
import re
import shutil
import subprocess
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .reminders import ReminderStore

IS_WINDOWS = platform.system() == "Windows"


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    func: Callable[..., str]
    risky: bool = False
    # Human-readable sentence shown in the "Allow?" dialog.
    describe: Callable[[dict], str] | None = None

    def as_api(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.schema}


class ToolError(Exception):
    """A tool failed in a way the cat should explain, not crash on."""


# --------------------------------------------------------------------------
# Apps, files, URLs
# --------------------------------------------------------------------------
def _launch(target: str) -> None:
    """Open a file, folder or URL with its default handler. Raises if Windows
    can't (unlike `start` in a hidden shell, which failed silently)."""
    if IS_WINDOWS:
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except OSError as e:
            raise ToolError(f"Windows couldn't open {target}: {e.strerror or e}") from e
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", target])
    else:
        raise ToolError("opening things is only supported on Windows")


def open_app(name: str) -> str:
    from . import launcher
    try:
        return launcher.open_app(name)
    except launcher.LaunchError as e:
        raise ToolError(str(e)) from e


# Where browsers usually live on Windows: (environment folder, path inside it)
BROWSERS = {
    "opera gx": [("LOCALAPPDATA", "Programs", "Opera GX", "opera.exe")],
    "opera": [("LOCALAPPDATA", "Programs", "Opera", "opera.exe")],
    "chrome": [("PROGRAMFILES", "Google", "Chrome", "Application", "chrome.exe"),
               ("PROGRAMFILES(X86)", "Google", "Chrome", "Application", "chrome.exe"),
               ("LOCALAPPDATA", "Google", "Chrome", "Application", "chrome.exe")],
    "edge": [("PROGRAMFILES(X86)", "Microsoft", "Edge", "Application", "msedge.exe"),
             ("PROGRAMFILES", "Microsoft", "Edge", "Application", "msedge.exe")],
    "firefox": [("PROGRAMFILES", "Mozilla Firefox", "firefox.exe")],
    "brave": [("LOCALAPPDATA", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
              ("PROGRAMFILES", "BraveSoftware", "Brave-Browser", "Application", "brave.exe")],
}
BROWSER_ALIASES = {"operagx": "opera gx", "opera-gx": "opera gx", "google chrome": "chrome",
                   "microsoft edge": "edge", "msedge": "edge", "mozilla firefox": "firefox"}


def find_browser(name: str) -> str | None:
    key = BROWSER_ALIASES.get(name.lower().strip(), name.lower().strip())
    for env, *parts in BROWSERS.get(key, []):
        base = os.environ.get(env)
        if base:
            path = os.path.join(base, *parts)
            if os.path.isfile(path):
                return path
    return None


def _open_in(url: str, browser: str | None) -> str:
    if browser:
        exe = find_browser(browser)
        if exe:
            subprocess.Popen([exe, url])
            return f" in {browser}"
        raise ToolError(f"couldn't find {browser} on this PC; opened nothing")
    webbrowser.open(url)
    return ""


def open_url(url: str, browser: str | None = None) -> str:
    parsed = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError("only http/https links can be opened")
    where = _open_in(parsed.geturl(), browser)
    return f"Opened {parsed.geturl()}{where}"


def web_search(query: str, browser: str | None = None) -> str:
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    where = _open_in(url, browser)
    return f"Opened a web search for '{query}'{where}."


# --------------------------------------------------------------------------
# Playing music / videos
# --------------------------------------------------------------------------
LAST_PLAYED: dict = {}          # what play_media opened last (for seeking)


def parse_timestamp(value) -> int:
    """'1:30' -> 90, '0:10' -> 10, '1:02:03' -> 3723, '90' -> 90,
    '1m30s' / '2m' / '45s' / '1h2m' -> seconds."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", text):
        secs = 0
        for part in text.split(":"):
            secs = secs * 60 + int(part)
        return secs
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(float(text))
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m(?:in)?)?(?:(\d+)s(?:ec)?)?", text)
    if m and any(m.groups()):
        h, mi, se = (int(g) if g else 0 for g in m.groups())
        return h * 3600 + mi * 60 + se
    raise ToolError(f"couldn't understand the time '{value}' (try 1:30)")


def youtube_first_video(query: str) -> tuple[str, str]:
    """Top YouTube search result as (video_id, title). Reads the public
    results page, so no API key is needed."""
    import httpx
    try:
        r = httpx.get("https://www.youtube.com/results",
                      params={"search_query": query}, timeout=12, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                                             "Chrome/124.0 Safari/537.36",
                               "Accept-Language": "en-US,en;q=0.9"})
        r.raise_for_status()
    except Exception as e:
        raise ToolError(f"couldn't reach YouTube ({type(e).__name__})")
    html = r.text
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html) \
        or re.search(r'"videoId":"([\w-]{11})"', html)
    if not m:
        raise ToolError("YouTube didn't return any videos for that")
    vid = m.group(1)
    t = re.search(r'"videoId":"' + re.escape(vid) + r'".{0,600}?"title":\{"runs":\[\{"text":"(.*?)"\}',
                  html, re.S)
    title = t.group(1).encode().decode("unicode_escape", "ignore") if t else query
    return vid, title


def play_media(query: str, start_at=None, browser: str | None = None) -> str:
    start = parse_timestamp(start_at)
    vid, title = youtube_first_video(query)
    url = f"https://www.youtube.com/watch?v={vid}" + (f"&t={start}s" if start else "")
    where = _open_in(url, browser)
    LAST_PLAYED.update(video_id=vid, title=title, browser=browser)
    when = f" from {start // 60}:{start % 60:02d}" if start else ""
    return f"Now playing '{title}' on YouTube{when}{where}. It starts automatically."


# --------------------------------------------------------------------------
# Reading documents
# --------------------------------------------------------------------------
TEXT_EXT = {".txt", ".md", ".py", ".csv", ".json", ".log", ".html", ".css", ".js",
            ".ts", ".java", ".c", ".cpp", ".cs", ".xml", ".yaml", ".yml", ".ini", ".sql"}


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _resolve_file(path: str, roots: list[str]) -> Path:
    """A full path, or just a file name to look for in the search folders."""
    p = Path(os.path.expandvars(os.path.expanduser(path.strip().strip('"'))))
    if p.is_file():
        return p
    name = p.name.lower()
    best = None
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and d not in ("node_modules", "__pycache__", "AppData", ".venv")]
            for fn in filenames:
                low = fn.lower()
                if low == name:
                    return Path(dirpath) / fn
                if best is None and name in low:
                    best = Path(dirpath) / fn
    if best is not None:
        return best
    raise ToolError(f"couldn't find a file called '{path}'")


def _image_to_jpeg(p: Path) -> bytes:
    from PySide6.QtGui import QImage
    img = QImage(str(p))
    if img.isNull():
        raise ToolError(f"couldn't open the image {p.name}")
    img = img.convertToFormat(QImage.Format_RGB32)
    return encode_jpeg(bytes(img.constBits()), img.width(), img.height(), 1600)


def _text_stats(text: str, name: str) -> str:
    words = len(text.split())
    lines = text.count("\n") + 1
    mins = max(1, round(words / 230))
    return f"{name}: {words:,} words, {len(text):,} characters, {lines:,} lines, ~{mins} min read."


def read_file(path: str, max_chars: int = 8000) -> str:
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    if not p.is_file():
        raise ToolError(f"{p} isn't a file")
    ext = p.suffix.lower()
    if ext in TEXT_EXT:
        text = p.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ToolError("reading PDFs needs the pypdf package (run setup.bat)")
        reader = PdfReader(str(p))
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:40])
    elif ext == ".docx":
        try:
            import docx
        except ImportError:
            raise ToolError("reading Word files needs python-docx (run setup.bat)")
        text = "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
    else:
        raise ToolError(f"I can read text, PDF and Word files, not {ext or 'this type'}")
    text = text.strip() or "(the file has no readable text)"
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[truncated, {len(text) - max_chars} more characters]"
    return f"Contents of {p.name}:\n{text}"


# --------------------------------------------------------------------------
# Windows, media, typing
# --------------------------------------------------------------------------
def _windows() -> list[tuple[int, str]]:
    if not IS_WINDOWS:
        raise ToolError("window control is only supported on Windows")
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    found: list[tuple[int, str]] = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value.strip()
                if title and title not in ("Program Manager", "Kuro"):
                    found.append((hwnd, title))
        return True
    user32.EnumWindows(proto(cb), 0)
    return found


def list_windows() -> str:
    wins = _windows()
    return "\n".join(t for _, t in wins[:30]) or "No open windows."


def window_action(title: str, action: str = "focus") -> str:
    user32 = ctypes.windll.user32 if IS_WINDOWS else None  # type: ignore[attr-defined]
    q = title.lower()
    match = [(h, t) for h, t in _windows() if q in t.lower()]
    if not match:
        raise ToolError(f"no open window matching '{title}'")
    hwnd, name = match[0]
    codes = {"focus": 9, "restore": 9, "minimize": 6, "maximize": 3}
    if action not in codes:
        raise ToolError("action must be focus, minimize, maximize or restore")
    user32.ShowWindow(hwnd, codes[action])
    if action in ("focus", "restore", "maximize"):
        user32.SetForegroundWindow(hwnd)
    return f"{action.title()}d '{name}'." if action != "focus" else f"Focused '{name}'."


def media(action: str) -> str:
    keys = {"play_pause": "play/pause media", "next": "next track",
            "previous": "previous track", "stop": "stop media"}
    if action not in keys:
        raise ToolError("action must be play_pause, next, previous or stop")
    import keyboard
    keyboard.send(keys[action])
    return f"Media: {action.replace('_', '/')}."


def type_text(text: str) -> str:
    import keyboard
    keyboard.write(text, delay=0.005)
    return f"Typed {len(text)} characters into the active window."


def capture_screen_jpeg(max_width: int = 1280) -> bytes:
    """Primary monitor as a compact JPEG (for 'look at my screen')."""
    import mss
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
    return encode_jpeg(shot.bgra, shot.size[0], shot.size[1], max_width)


def encode_jpeg(bgra: bytes, w: int, h: int, max_width: int = 1280, quality: int = 72) -> bytes:
    """Raw BGRA pixels -> JPEG bytes, using Qt (no Pillow needed)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage
    img = QImage(bgra, w, h, 4 * w, QImage.Format_RGB32)
    if w > max_width:
        img = img.scaledToWidth(max_width, Qt.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG", quality)
    buf.close()
    return bytes(data)


def search_files(query: str, roots: list[str], limit: int = 15) -> str:
    q = query.lower()
    hits: list[str] = []
    skip = {"node_modules", ".git", "__pycache__", "AppData", ".venv", "venv"}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
            for name in dirnames + filenames:
                if q in name.lower():
                    hits.append(os.path.join(dirpath, name))
                    if len(hits) >= limit:
                        return "\n".join(hits)
    return "\n".join(hits) if hits else f"No files or folders matching '{query}'."


def open_path(path: str) -> str:
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    if not p.exists():
        raise ToolError(f"{p} doesn't exist")
    if IS_WINDOWS:
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        _launch(str(p))
    return f"Opened {p}"


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------
def system_info() -> str:
    now = _dt.datetime.now()
    lines = [f"Local time: {now:%A %d %B %Y, %I:%M %p}"]
    try:
        import psutil
        lines.append(f"CPU: {psutil.cpu_percent(interval=0.3):.0f}%")
        vm = psutil.virtual_memory()
        lines.append(f"RAM: {vm.percent:.0f}% used of {vm.total / 2**30:.1f} GB")
        bat = psutil.sensors_battery()
        if bat:
            plug = "charging" if bat.power_plugged else "on battery"
            lines.append(f"Battery: {bat.percent:.0f}% ({plug})")
        du = psutil.disk_usage(str(Path.home().anchor or "/"))
        lines.append(f"Disk: {du.free / 2**30:.0f} GB free")
    except ImportError:
        pass
    return "\n".join(lines)


def set_volume(action: str, level: int | None = None) -> str:
    action = action.lower()
    if action == "set":
        if level is None or not 0 <= level <= 100:
            raise ToolError("level must be 0-100")
        try:
            from pycaw.pycaw import AudioUtilities
            vol = AudioUtilities.GetSpeakers().EndpointVolume
            vol.SetMasterVolumeLevelScalar(level / 100, None)
            vol.SetMute(0, None)
            return f"Volume set to {level}%."
        except Exception as e:  # pycaw missing or audio device busy
            raise ToolError(f"couldn't set exact volume: {e}")
    keys = {"up": "volume up", "down": "volume down", "mute": "volume mute"}
    if action not in keys:
        raise ToolError("action must be up, down, mute or set")
    import keyboard
    for _ in range(5 if action != "mute" else 1):  # one press = 2%
        keyboard.send(keys[action])
    return f"Volume {action}."


def take_screenshot() -> str:
    import mss
    folder = Path.home() / "Pictures" / "Kuro"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"screenshot_{_dt.datetime.now():%Y%m%d_%H%M%S}.png"
    with mss.mss() as sct:
        sct.shot(mon=-1, output=str(out))  # -1 = all monitors
    return f"Saved screenshot to {out}"


def read_clipboard_raw() -> str:
    try:
        import pyperclip
        return pyperclip.paste() or ""
    except Exception:
        return ""


def read_clipboard() -> str:
    import pyperclip
    text = pyperclip.paste()
    if not text:
        return "The clipboard is empty (or holds an image)."
    return text[:4000] + ("\n...[truncated]" if len(text) > 4000 else "")


def write_clipboard(text: str) -> str:
    import pyperclip
    pyperclip.copy(text)
    return "Copied to clipboard."


def lock_pc() -> str:
    if not IS_WINDOWS:
        raise ToolError("locking is only supported on Windows")
    ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
    return "Locked."


def power(action: str) -> str:
    cmds = {"shutdown": "shutdown /s /t 30",
            "restart": "shutdown /r /t 30",
            "cancel": "shutdown /a",
            "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"}
    if action not in cmds:
        raise ToolError(f"unknown power action {action}")
    if not IS_WINDOWS:
        raise ToolError("power actions are only supported on Windows")
    subprocess.run(cmds[action], shell=True, check=False)
    if action in ("shutdown", "restart"):
        return f"{action.title()} in 30 seconds. Say 'cancel shutdown' to stop it."
    return f"{action.title()} done."


def run_powershell(command: str) -> str:
    if not IS_WINDOWS:
        raise ToolError("PowerShell is only available on Windows")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    out = out.strip() or f"(no output, exit code {r.returncode})"
    return out[:4000] + ("\n...[truncated]" if len(out) > 4000 else "")


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def build_tools(reminders: ReminderStore, search_roots: list[str],
                allow_shell: bool = True, memory=None, brain=None,
                capture=None, music=None, seek=None, scan=None,
                scan_fx=None) -> dict[str, Tool]:
    """`brain` is a callable returning the current brain provider (for seeing the
    screen and live web answers); `capture` grabs the screen as JPEG."""
    def add_reminder(message: str, in_minutes: float | None = None,
                     at: str | None = None) -> str:
        try:
            r = reminders.add(message, in_minutes=in_minutes, at=at)
        except ValueError as e:
            raise ToolError(str(e))
        return f"Reminder {r.id} set for {r.due_dt:%a %d %b, %I:%M %p}: {r.message}"

    def list_reminders() -> str:
        items = reminders.all()
        if not items:
            return "No reminders set."
        return "\n".join(f"[{r.id}] {r.due_dt:%a %d %b %I:%M %p} - {r.message}"
                         for r in items)

    def cancel_reminder(reminder_id: str) -> str:
        if reminders.cancel(reminder_id):
            return f"Cancelled reminder {reminder_id}."
        raise ToolError(f"no reminder with id {reminder_id}")

    tools = [
        Tool("open_app", "Launch a desktop app by name (e.g. 'notepad', 'spotify', "
             "'vscode', 'blender', 'unity hub') or by an .exe name. Finds installed and "
             "Store apps, brings it to the front if it's already open, and waits to "
             "confirm its window appeared. Repeat the result honestly: if it says no "
             "window appeared or the app wasn't found, tell the user that.",
             _obj({"name": {"type": "string"}}, ["name"]), open_app),
        Tool("open_url", "Open a website. Pass `browser` (e.g. 'opera gx', 'chrome', "
             "'edge', 'firefox', 'brave') if the user names one or prefers one; "
             "otherwise the default browser is used.",
             _obj({"url": {"type": "string"}, "browser": {"type": "string"}}, ["url"]),
             open_url),
        Tool("web_search", "Open a Google search results page in the browser, for when "
             "the user wants to browse results themselves. To just answer a question "
             "about current events, use web_answer instead.",
             _obj({"query": {"type": "string"}, "browser": {"type": "string"}}, ["query"]),
             web_search),
        Tool("list_windows", "List the titles of the windows currently open.",
             _obj({}), list_windows),
        Tool("window_action", "Focus, minimize, maximize or restore an open window, "
             "matched by part of its title.",
             _obj({"title": {"type": "string"},
                   "action": {"type": "string",
                              "enum": ["focus", "minimize", "maximize", "restore"]}},
                  ["title"]), window_action),
        Tool("media", "Control music/video playback (Spotify, YouTube, etc.) with "
             "the media keys.",
             _obj({"action": {"type": "string",
                              "enum": ["play_pause", "next", "previous", "stop"]}},
                  ["action"]), media),
        Tool("type_text", "Type text into whatever window is active, as if the user "
             "typed it. Only when the user asks you to type something.",
             _obj({"text": {"type": "string"}}, ["text"]), type_text),
        Tool("search_files", "Find files or folders whose name contains the "
             "query, in the user's Desktop, Documents, Downloads and Pictures.",
             _obj({"query": {"type": "string"}}, ["query"]),
             lambda query: search_files(query, search_roots)),
        Tool("open_path", "Open a file or folder with its default app. "
             "Use a full path, e.g. one returned by search_files.",
             _obj({"path": {"type": "string"}}, ["path"]), open_path),
        Tool("system_info", "Current date/time, CPU, RAM, battery and free disk.",
             _obj({}), system_info),
        Tool("set_volume", "Change system volume. action: up, down, mute, or "
             "set (with level 0-100).",
             _obj({"action": {"type": "string", "enum": ["up", "down", "mute", "set"]},
                   "level": {"type": "integer", "minimum": 0, "maximum": 100}},
                  ["action"]), set_volume),
        Tool("take_screenshot", "Save a screenshot image file to Pictures/Kuro. This "
             "does NOT let you see it; use scan with what=screen to see the screen.",
             _obj({}), take_screenshot),
        Tool("write_clipboard", "Put text on the clipboard.",
             _obj({"text": {"type": "string"}}, ["text"]), write_clipboard),
        Tool("lock_pc", "Lock the Windows session.", _obj({}), lock_pc),
        Tool("power", "Shut down, restart or sleep the PC, or cancel a pending "
             "shutdown.",
             _obj({"action": {"type": "string",
                              "enum": ["shutdown", "restart", "sleep", "cancel"]}},
                  ["action"]), power, risky=True,
             describe=lambda a: f"{a.get('action', '?').title()} the computer"),
        Tool("add_reminder", "Set a reminder. Give in_minutes for relative times "
             "('in 20 minutes'), or at as an ISO local datetime "
             "(YYYY-MM-DDTHH:MM) for clock times. Work out the date from the "
             "current time in the system prompt.",
             _obj({"message": {"type": "string"},
                   "in_minutes": {"type": "number"},
                   "at": {"type": "string"}}, ["message"]), add_reminder),
        Tool("list_reminders", "List pending reminders with their ids.",
             _obj({}), list_reminders),
        Tool("cancel_reminder", "Cancel a reminder by id.",
             _obj({"reminder_id": {"type": "string"}}, ["reminder_id"]),
             cancel_reminder),
    ]
    if brain is not None:
        def look_at_screen(question: str = "Describe what's on the screen.") -> str:
            try:
                jpeg = (capture or capture_screen_jpeg)()
            except Exception as e:
                raise ToolError(f"couldn't capture the screen: {e}")
            prompt = (f"{question}\nDescribe only what is actually visible. Do not try to "
                      f"identify real people from their faces. Be concise.")
            try:
                return "What I see on the screen: " + brain().vision(prompt, jpeg)
            except Exception as e:
                raise ToolError(f"couldn't look at the screen: {getattr(e, 'detail', '') or e}")

        def web_answer(question: str) -> str:
            try:
                text, sources = brain().web_answer(question)
            except Exception as e:
                raise ToolError("live web lookup isn't available right now "
                                f"({getattr(e, 'kind', '') or type(e).__name__}); "
                                "offer to open a web search instead")
            if sources:
                text += "\nSources: " + "; ".join(f"{t} <{u}>" for t, u in sources)
            return text

        tools += [
            Tool("web_answer", "Answer from a live web search: news, scores, weather, "
                 "prices, facts that change. Reply in chat with the answer.",
                 _obj({"question": {"type": "string"}}, ["question"]), web_answer),
        ]

    def seek_media(time) -> str:
        secs = parse_timestamp(time)
        stamp = f"{secs // 60}:{secs % 60:02d}"
        if seek is not None:
            try:
                if seek(secs):
                    return f"Skipped to {stamp}."
            except Exception:
                pass
        if LAST_PLAYED.get("video_id"):
            # the player won't take a seek from Windows: reopen the video there
            try:
                media("play_pause")            # pause the old tab so they don't overlap
            except Exception:
                pass
            url = f"https://www.youtube.com/watch?v={LAST_PLAYED['video_id']}&t={secs}s"
            _open_in(url, LAST_PLAYED.get("browser"))
            return f"Reopened '{LAST_PLAYED.get('title', 'the video')}' at {stamp}."
        raise ToolError("this player doesn't allow skipping from outside; "
                        "ask me to play it again from that time instead")

    tools += [
        Tool("play_media", "Play a song or video RIGHT AWAY (finds the top YouTube result "
             "and opens the video so it starts playing). Use this whenever the user says "
             "'play ...'. Pass start_at for a time like '0:10' or '1:30' to start there. "
             "Pass browser if the user prefers one.",
             _obj({"query": {"type": "string", "description": "Song, artist or video."},
                   "start_at": {"type": "string", "description": "Optional time, e.g. 1:30"},
                   "browser": {"type": "string"}}, ["query"]), play_media),
        Tool("seek_media", "Skip the song/video that's playing to a time, e.g. '0:10' "
             "or '2:15'.",
             _obj({"time": {"type": "string"}}, ["time"]), seek_media),
    ]

    if music is not None:
        def now_playing() -> str:
            track = music()
            return ("Now " + track.describe()) if track else "Nothing is playing right now."
        tools.append(Tool("now_playing", "What song or video is playing right now "
                          "(Spotify, YouTube in a browser, etc.).", _obj({}), now_playing))

    # ---- one `scan` tool: Kuro's JARVIS scan of whatever you point him at
    def scan_tool(what: str, path: str = "", text: str = "", question: str = "") -> str:
        if scan_fx:
            scan_fx(True)
        try:
            return _scan(what, path, text, question)
        finally:
            if scan_fx:
                scan_fx(False)

    def _scan(what, path, text, question) -> str:
        what = (what or "").lower().strip()
        exact = "SCAN RESULT (exact data, report it faithfully; do not add anything not here):\n"
        if what == "screen":
            if brain is None:
                raise ToolError("I can't see the screen without my brain connected")
            return look_at_screen(question or "Describe what's on the screen in detail.")
        if what == "file":
            if not path:
                raise ToolError("which file? Ask for its name, or use search_files first")
            p = _resolve_file(path, search_roots)
            if p.suffix.lower() in IMAGE_EXT:
                if brain is None:
                    raise ToolError("I need my brain connected to look at images")
                jpeg = _image_to_jpeg(p)
                prompt = ((question or "Describe this image in detail.") + "\nDescribe only what "
                          "is actually visible; read any text exactly. Do not identify real "
                          "people from their faces.")
                return f"Image {p.name}: " + brain().vision(prompt, jpeg)
            body = read_file(str(p), max_chars=20000)
            return exact + _text_stats(body.split("\n", 1)[-1], p.name) + "\n" + body
        if what in ("clipboard", "text"):
            src = text if what == "text" else read_clipboard_raw()
            if not src.strip():
                raise ToolError("the clipboard is empty" if what == "clipboard" else "no text given")
            return exact + _text_stats(src, what) + "\nText:\n" + src[:20000]
        if what in ("song", "music"):
            track = music() if music else None
            if not track:
                return "Nothing is playing right now (checked Spotify, browsers and other players)."
            out = exact + "From the player: " + track.describe()
            if brain is not None:
                who = f" by {track.artist}" if getattr(track, "artist", "") else ""
                try:
                    info, sources = brain().web_answer(
                        f"The song '{track.title}'{who}: album, release year, genre, and two "
                        f"short facts about it. If unsure, say so.")
                    out += "\nFrom the web (may be imperfect): " + info
                    if sources:
                        out += "\nSources: " + "; ".join(f"{t} <{u}>" for t, u in sources[:3])
                except Exception:
                    out += "\n(Couldn't look it up on the web right now.)"
            return out
        if what in ("pc", "computer", "system"):
            if scan is None:
                raise ToolError("PC scanning isn't available")
            return exact + scan()
        raise ToolError("scan what? Use screen, file, clipboard, text, song or pc")

    tools.append(Tool(
        "scan", "Scan something and report back, JARVIS-style. what = screen (look at the "
        "screen), file (a document, PDF, Word file, code or image: give path, or a file name "
        "to find), clipboard (text the user copied), text (text they pasted in chat: pass "
        "it as text), song (what's playing now + info about it), pc (specs, apps, projects, "
        "games, file types, names only). Use whenever the user says scan/analyse/check/read "
        "one of these. Then answer from the result only.",
        _obj({"what": {"type": "string",
                       "enum": ["screen", "file", "clipboard", "text", "song", "pc"]},
              "path": {"type": "string", "description": "File path or name (what=file)"},
              "text": {"type": "string", "description": "The text to scan (what=text)"},
              "question": {"type": "string", "description": "What the user wants to know"}},
             ["what"]), scan_tool))

    if memory is not None:
        def remember(fact: str, category: str = "general") -> str:
            f, created = memory.add(fact, category, source="user")
            return f"{'Remembered' if created else 'Updated memory'} [{f.id}]: {f.text}"

        def forget(query: str) -> str:
            gone = memory.forget(query)
            if not gone:
                raise ToolError(f"nothing in memory matches '{query}'")
            return "Forgot: " + "; ".join(f.text for f in gone)

        def forget_everything() -> str:
            n = len(memory.all())
            memory.clear()
            return f"Forgot everything ({n} facts)."

        def recall(query: str) -> str:
            hits = memory.search(query)
            memory.touch([f.id for f in hits])
            return "\n".join(f"[{f.id}] {f.text}" for f in hits) or "Nothing remembered about that."

        tools.append(Tool("forget_everything", "Wipe ALL long-term memory about the user. "
                          "Only when they clearly ask to forget everything.", _obj({}),
                          forget_everything, risky=True,
                          describe=lambda a: "Forget everything Kuro has learned about you"))
        tools += [
            Tool("remember", "Save a durable fact about the user to long-term memory "
                 "(who they are, projects, preferences, people, routines). Use when they "
                 "tell you something worth knowing next time, or say 'remember'. Never "
                 "store passwords, keys, card/ID numbers or health details.",
                 _obj({"fact": {"type": "string", "description": "One short third-person sentence."},
                       "category": {"type": "string", "enum": ["profile", "preference", "project",
                                                               "people", "routine", "general"]}},
                      ["fact"]), remember),
            Tool("forget", "Remove facts from long-term memory by id or description. "
                 "Use when the user says to forget something or a fact is wrong.",
                 _obj({"query": {"type": "string"}}, ["query"]), forget),
            Tool("recall", "Search long-term memory for facts about a topic.",
                 _obj({"query": {"type": "string"}}, ["query"]), recall),
        ]
    if allow_shell:
        tools.append(Tool(
            "run_powershell",
            "Run a PowerShell command and return its output. Only when no other "
            "tool fits. The user must approve every command, so keep it to one "
            "clear, minimal command and never chain destructive steps.",
            _obj({"command": {"type": "string"}}, ["command"]),
            run_powershell, risky=True,
            describe=lambda a: f"Run this PowerShell command:\n\n{a.get('command', '')}"))
    return {t.name: t for t in tools}


def run_tool(tool: Tool, args: dict[str, Any]) -> tuple[str, bool]:
    """Execute a tool; returns (result_text, is_error). Never raises."""
    try:
        return tool.func(**args), False
    except ToolError as e:
        return f"Error: {e}", True
    except TypeError as e:  # bad/missing arguments from the model
        return f"Error: bad arguments for {tool.name}: {e}", True
    except Exception as e:  # anything else: report, don't crash the cat
        return f"Error: {type(e).__name__}: {e}", True
