"""Kuro's JARVIS-style scan: getting to know your PC.

Looks only at *names*, never inside files:
  * system specs (CPU, RAM, disk, battery, Windows version)
  * installed apps (from the Start menu)
  * project folders in your Documents / Desktop / code folders, and what
    kind they are (Blender, Unity, Python, Node, ...)
  * recently opened files (names from Windows' Recent list)
  * which windows are open, and which app you're in right now

The result is saved to scan.json in Kuro's AppData folder. A short summary
goes into his context, and a few durable facts go into his memory, where
you can review or delete them.
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

IS_WINDOWS = platform.system() == "Windows"
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "Library", "Temp", "obj",
             "bin", "build", "dist", ".git", ".idea", ".vs", "AppData", "site-packages"}
NOISE_APPS = re.compile(r"uninstall|readme|help|manual|website|license|release notes|"
                        r"documentation|support|^about |changelog|safe mode|debug", re.I)

Progress = Callable[[str], None]


# ------------------------------------------------------------------- system
def system_info() -> dict:
    info = {"os": platform.platform(terse=True), "machine": platform.node()}
    cpu = os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor()
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                cpu = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    info["cpu"] = cpu
    try:
        import psutil
        info["cores"] = psutil.cpu_count(logical=True)
        info["ram_gb"] = round(psutil.virtual_memory().total / 2 ** 30)
        du = psutil.disk_usage(str(Path.home().anchor or "/"))
        info["disk_free_gb"] = round(du.free / 2 ** 30)
        info["disk_total_gb"] = round(du.total / 2 ** 30)
        bat = psutil.sensors_battery()
        if bat:
            info["battery"] = f"{bat.percent:.0f}% ({'charging' if bat.power_plugged else 'on battery'})"
            info["laptop"] = True
    except Exception:
        pass
    return info


# --------------------------------------------------------------------- apps
def start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("PROGRAMDATA", "APPDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def installed_apps(dirs: list[Path] | None = None, limit: int = 250) -> list[str]:
    names: dict[str, None] = {}
    for d in dirs if dirs is not None else start_menu_dirs():
        if not d.is_dir():
            continue
        for lnk in d.rglob("*.lnk"):
            name = lnk.stem.strip()
            if name and not NOISE_APPS.search(name):
                names[name] = None
            if len(names) >= limit:
                break
    return sorted(names, key=str.lower)


# ------------------------------------------------------------------ projects
KINDS = [
    ("Unity", lambda fs, ds: "Assets" in ds and "ProjectSettings" in ds),
    ("Blender", lambda fs, ds: any(f.endswith(".blend") for f in fs)),
    ("Git repo", lambda fs, ds: ".git" in ds),
    ("Python", lambda fs, ds: any(f.endswith(".py") for f in fs)
     or "requirements.txt" in fs or "pyproject.toml" in fs),
    ("Node/JS", lambda fs, ds: "package.json" in fs),
    ("C#/.NET", lambda fs, ds: any(f.endswith((".sln", ".csproj")) for f in fs)),
    ("Java", lambda fs, ds: "pom.xml" in fs or "build.gradle" in fs
     or any(f.endswith(".java") for f in fs)),
    ("Web", lambda fs, ds: "index.html" in fs),
    ("Documents", lambda fs, ds: sum(f.endswith((".docx", ".pdf", ".pptx", ".xlsx"))
                                     for f in fs) >= 3),
    ("Media", lambda fs, ds: sum(f.endswith((".png", ".jpg", ".psd", ".mp4", ".fig"))
                                 for f in fs) >= 5),
]


def project_roots() -> list[Path]:
    h = Path.home()
    roots = [h / "Documents", h / "Desktop", h / "source" / "repos", h / "source",
             h / "PycharmProjects", h / "PyCharmMiscProject", h / "Projects",
             h / "Unity Projects", h / "Documents" / "GitHub"]
    od = os.environ.get("OneDrive")
    if od:
        roots += [Path(od) / "Documents", Path(od) / "Desktop"]
    seen, out = set(), []
    for r in roots:
        try:
            key = r.resolve()
        except OSError:
            continue
        if r.is_dir() and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def classify(folder: Path, depth: int = 2, max_files: int = 400) -> list[str]:
    files: set[str] = set()
    dirs: set[str] = set()
    count = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        rel_depth = len(Path(dirpath).relative_to(folder).parts)
        if rel_depth == 0:
            dirs.update(dirnames)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                       and rel_depth < depth]
        files.update(f.lower() for f in filenames)
        count += len(filenames)
        if count > max_files:
            break
    return [k for k, test in KINDS if test(files, dirs)]


def projects(roots: list[Path] | None = None, limit: int = 40) -> list[dict]:
    found = []
    for root in roots if roots is not None else project_roots():
        try:
            children = [c for c in root.iterdir() if c.is_dir() and not c.name.startswith((".", "$"))
                        and c.name not in SKIP_DIRS and not c.is_symlink()]
        except OSError:
            continue
        for c in children:
            try:
                kinds = classify(c)
                mtime = c.stat().st_mtime
            except OSError:
                continue
            if kinds:
                found.append({"name": c.name, "where": root.name, "kinds": kinds,
                              "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")})
    found.sort(key=lambda p: p["modified"], reverse=True)
    return found[:limit]


# ------------------------------------------------------------- recent files
def recent_files(limit: int = 25) -> list[str]:
    base = os.environ.get("APPDATA")
    if not base:
        return []
    d = Path(base) / "Microsoft" / "Windows" / "Recent"
    try:
        items = sorted((p for p in d.glob("*.lnk")), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    except OSError:
        return []
    return [p.stem for p in items[:limit]]


# ------------------------------------------------------------ live context
def active_window() -> tuple[str, str]:
    """(app name, window title) of the window you're using right now."""
    if not IS_WINDOWS:
        return "", ""
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = ""
        try:
            import psutil
            app = psutil.Process(pid.value).name().removesuffix(".exe")
        except Exception:
            pass
        return app, buf.value.strip()
    except Exception:
        return "", ""


def open_windows(limit: int = 15) -> list[str]:
    try:
        from .tools import _windows
        return [t for _, t in _windows()][:limit]
    except Exception:
        return []


# ------------------------------------------------------------- dev tools
DEV_TOOLS = {"git": "Git", "python": "Python", "node": "Node.js", "npm": "npm", "java": "Java",
             "javac": "JDK", "dotnet": ".NET", "code": "VS Code", "blender": "Blender",
             "unity": "Unity", "gcc": "GCC", "g++": "G++", "cargo": "Rust", "go": "Go",
             "docker": "Docker", "php": "PHP", "flutter": "Flutter", "adb": "Android tools",
             "mysql": "MySQL", "psql": "PostgreSQL", "ffmpeg": "FFmpeg", "gh": "GitHub CLI"}


def dev_tools() -> list[str]:
    """Programming tools on the PATH (presence only, nothing is run)."""
    import shutil
    return sorted({label for exe, label in DEV_TOOLS.items() if shutil.which(exe)})


# ----------------------------------------------------------------- games
def games(limit: int = 60) -> list[str]:
    """Installed game folder names from common launchers (Steam, Epic, Riot...)."""
    roots = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            roots += [Path(base) / "Steam" / "steamapps" / "common",
                      Path(base) / "Epic Games", Path(base) / "Riot Games"]
    for drive in "CDEFG":
        roots += [Path(f"{drive}:/SteamLibrary/steamapps/common")]
    names: dict[str, None] = {}
    for r in roots:
        try:
            for c in r.iterdir():
                if c.is_dir() and not c.name.lower().startswith(("steamworks", "launcher",
                                                                  "directx", "vcredist")):
                    names[c.name] = None
        except OSError:
            continue
    return sorted(names, key=str.lower)[:limit]


# ---------------------------------------------------------- file census
CENSUS = {
    "documents": (".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".md", ".odt"),
    "images": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".psd", ".svg", ".heic"),
    "videos": (".mp4", ".mov", ".mkv", ".avi", ".webm"),
    "music": (".mp3", ".wav", ".flac", ".m4a", ".ogg"),
    "code": (".py", ".js", ".ts", ".java", ".cs", ".cpp", ".c", ".html", ".css", ".kt", ".dart",
             ".php", ".go", ".rs", ".ipynb"),
    "3D": (".blend", ".fbx", ".obj", ".gltf", ".glb", ".stl", ".max", ".skp"),
    "design": (".fig", ".ai", ".xd", ".sketch", ".aep", ".prproj"),
    "archives": (".zip", ".rar", ".7z"),
}
_EXT = {e: k for k, exts in CENSUS.items() for e in exts}


def file_census(roots: list[Path] | None = None, max_files: int = 40000,
                max_seconds: float = 6.0) -> dict:
    """How many files of each kind you keep (and how much space), by
    extension only. Gives a feel for what you do: lots of .blend = 3D artist."""
    h = Path.home()
    roots = roots if roots is not None else [
        h / n for n in ("Documents", "Desktop", "Downloads", "Pictures", "Videos", "Music")]
    counts = {k: 0 for k in CENSUS}
    sizes = {k: 0 for k in CENSUS}
    seen, t0 = 0, time.monotonic()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                kind = _EXT.get(os.path.splitext(fn)[1].lower())
                if kind:
                    counts[kind] += 1
                    try:
                        sizes[kind] += os.path.getsize(os.path.join(dirpath, fn))
                    except OSError:
                        pass
            seen += len(filenames)
            if seen > max_files or time.monotonic() - t0 > max_seconds:
                break
    return {k: {"files": counts[k], "mb": round(sizes[k] / 2 ** 20)} for k in CENSUS if counts[k]}


# ---------------------------------------------------------- what changed
def changes(old: dict | None, new: dict) -> dict:
    """New apps / projects / games since the last scan."""
    if not old:
        return {}
    out = {}
    for key, getname in (("apps", lambda x: x), ("games", lambda x: x),
                         ("projects", lambda x: x["name"])):
        before = {getname(x) for x in old.get(key, [])}
        added = [getname(x) for x in new.get(key, []) if getname(x) not in before]
        if added:
            out[key] = added[:15]
    return out


# ---------------------------------------------------------------- the scan
def run_scan(progress: Progress = lambda _m: None, previous: dict | None = None) -> dict:
    """Runs every probe in parallel (they're all disk-bound), reporting
    progress as each one lands."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    t0 = time.monotonic()
    progress("Initiating scan…")
    result = {"scanned": datetime.now().strftime("%Y-%m-%d %H:%M")}
    jobs = {"system": (system_info, "System specs read"),
            "apps": (installed_apps, "{n} apps indexed"),
            "projects": (projects, "{n} projects mapped"),
            "recent": (recent_files, "Recent activity read"),
            "tools": (dev_tools, "{n} dev tools detected"),
            "games": (games, "{n} games found"),
            "files": (file_census, "File library catalogued"),
            "windows": (open_windows, "Open windows checked")}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="scan") as pool:
        futs = {pool.submit(fn): (key, msg) for key, (fn, msg) in jobs.items()}
        for fut in as_completed(futs):
            key, msg = futs[fut]
            try:
                result[key] = fut.result()
            except Exception:
                result[key] = {} if key in ("system", "files") else []
            val = result[key]
            progress(msg.format(n=len(val) if hasattr(val, "__len__") else 0))
    result["changes"] = changes(previous, result)
    result["seconds"] = round(time.monotonic() - t0, 1)
    return result


def summary(scan: dict, max_chars: int = 1500) -> str:
    """Compact text for Kuro's context and for reporting back."""
    s = scan.get("system", {})
    parts = []
    spec = ", ".join(str(x) for x in (s.get("os"), s.get("cpu"),
                                       f"{s.get('ram_gb')} GB RAM" if s.get("ram_gb") else None,
                                       f"{s.get('disk_free_gb')}/{s.get('disk_total_gb')} GB disk free"
                                       if s.get("disk_total_gb") else None) if x)
    if spec:
        parts.append(f"System: {spec}.")
    if scan.get("projects"):
        parts.append("Projects: " + "; ".join(
            f"{p['name']} ({', '.join(p['kinds'])}, {p['modified']})" for p in scan["projects"][:12]) + ".")
    if scan.get("apps"):
        parts.append(f"Apps ({len(scan['apps'])}): " + ", ".join(scan["apps"][:45]) + ".")
    if scan.get("tools"):
        parts.append("Dev tools: " + ", ".join(scan["tools"]) + ".")
    if scan.get("games"):
        parts.append("Games: " + ", ".join(scan["games"][:15]) + ".")
    if scan.get("files"):
        top = sorted(scan["files"].items(), key=lambda kv: kv[1]["files"], reverse=True)
        parts.append("Files: " + ", ".join(f"{v['files']} {k}" for k, v in top) + ".")
    ch = scan.get("changes") or {}
    if ch:
        parts.append("New since last scan: " + "; ".join(
            f"{k}: {', '.join(v[:6])}" for k, v in ch.items()) + ".")
    if scan.get("recent"):
        parts.append("Recent files: " + ", ".join(scan["recent"][:12]) + ".")
    text = " ".join(parts)
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


FACTS_PROMPT = """You help {cat}, a desktop assistant, get to know its user {user}.
Below is a scan of {user}'s PC: names of apps, games, dev tools, project folders, counts of
file types and recent file names (no contents).

Write 4 to 8 short, durable facts about {user} that would help {cat} be a better assistant:
the tools they use, what they work on, their projects, hobbies suggested by their apps.
Rules: one short third-person sentence each; only what the scan clearly supports; no file
paths; nothing sensitive (health, finances, relationships); no guesses about personality.

Scan:
{scan}

Reply with JSON only: [{{"text": "...", "category": "profile|project|routine|preference|general"}}]"""


def facts_from_scan(complete: Callable[[str], str], scan_text: str, cat: str, user: str) -> list[dict]:
    raw = complete(FACTS_PROMPT.format(cat=cat, user=user, scan=scan_text))
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        items = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [i for i in items if isinstance(i, dict) and i.get("text")][:8]


def save(scan: dict, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(scan, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
