"""More things Kuro can do, on top of app/tools.py.

- calculate / convert_units: exact arithmetic, so he never does maths "in his head"
- todo: a to-do list that lives on this PC
- write_file: save text he wrote as a real file (never overwrites, never a program)
- routine: several steps saved under one name ("study mode")
- pc_health / close_app: what's slowing the PC down, and closing it
- display: dark mode and screen brightness
- organize_folder: sort loose files into folders by type, with undo

Everything that could cost you work (closing apps, moving files) asks first.
"""
from __future__ import annotations

import ast
import ctypes
import json
import math
import operator
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .tools import IS_WINDOWS, Tool, ToolError, _obj

# ---------------------------------------------------------------- calculate
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _factorial(n):
    if n != int(n) or n < 0 or n > 170:
        raise ToolError("factorial needs a whole number from 0 to 170")
    return math.factorial(int(n))


_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
          "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin,
          "acos": math.acos, "atan": math.atan, "log": math.log, "ln": math.log,
          "log10": math.log10, "log2": math.log2, "exp": math.exp, "min": min, "max": max,
          "factorial": _factorial, "radians": math.radians, "degrees": math.degrees,
          "pow": pow, "sum": lambda *a: sum(a), "avg": lambda *a: sum(a) / len(a),
          "mean": lambda *a: sum(a) / len(a)}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 400 or abs(a) > 1e12):
            raise ToolError("that power is too big to work out")
        return _BIN[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id.lower() in _NAMES:
        return _NAMES[node.id.lower()]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id.lower() in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id.lower()](*[_eval(a) for a in node.args])
    raise ToolError("I can only work out numbers, + - * / ^ %, brackets and maths functions")


def _tidy(expr: str) -> str:
    """'15% of 240', '2^10', '3 x 4', '1,250.50 + 2' -> something Python can parse."""
    s = expr.strip().lower().replace("×", "*").replace("÷", "/").replace("−", "-")
    s = re.sub(r"^(what is|what's|calculate|compute|solve)\s+", "", s).strip(" ?=")
    if not re.search(r"[a-z]\s*\(", s):                          # (commas there are arguments)
        s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)              # 1,250 -> 1250
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", s)  # 15% of 240
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)           # 240 + 15%  (as 0.15)
    s = re.sub(r"(?<=[\d)])\s*x\s*(?=[\d(])", "*", s)            # 3 x 4
    return s.replace("^", "**")


def _fmt(x) -> str:
    if isinstance(x, complex):
        raise ToolError("that has no real answer")
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            raise ToolError("that doesn't come out as a number")
        if x == int(x) and abs(x) < 1e15:
            return f"{int(x):,}"
        return f"{x:,.6g}" if abs(x) >= 1e6 or abs(x) < 1e-4 else f"{round(x, 6):,}".rstrip("0").rstrip(".")
    return f"{x:,}" if abs(x) < 10 ** 30 else f"{x:.6e}"


def calculate(expression: str) -> str:
    src = _tidy(expression)
    if not src or len(src) > 300:
        raise ToolError("give me a sum to work out, like 15% of 240 or sqrt(2) * 3")
    try:
        value = _eval(ast.parse(src, mode="eval"))
    except ToolError:
        raise
    except ZeroDivisionError:
        raise ToolError("that divides by zero")
    except (ValueError, OverflowError):
        raise ToolError("that has no real answer (or it's too big)")
    except (SyntaxError, TypeError, RecursionError):
        raise ToolError(f"I couldn't read '{expression}' as a sum")
    return f"{expression.strip()} = {_fmt(value)}"


# to a base unit per family; temperature is handled separately
_UNITS = {
    "length": {"mm": 0.001, "cm": 0.01, "m": 1, "km": 1000, "in": 0.0254, "inch": 0.0254,
               "ft": 0.3048, "foot": 0.3048, "feet": 0.3048, "yd": 0.9144, "yard": 0.9144,
               "mi": 1609.344, "mile": 1609.344},
    "mass": {"mg": 1e-6, "g": 0.001, "kg": 1, "t": 1000, "tonne": 1000, "oz": 0.028349523125,
             "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237, "st": 6.35029318,
             "stone": 6.35029318},
    "data": {"bit": 0.125, "b": 1, "byte": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12,
             "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4},
    "time": {"ms": 0.001, "s": 1, "sec": 1, "second": 1, "min": 60, "minute": 60, "h": 3600,
             "hr": 3600, "hour": 3600, "day": 86400, "week": 604800, "year": 31557600},
    "speed": {"m/s": 1, "km/h": 1 / 3.6, "kph": 1 / 3.6, "mph": 0.44704, "knot": 0.514444},
    "area": {"sqm": 1, "m2": 1, "sqft": 0.09290304, "ft2": 0.09290304, "hectare": 10000,
             "ha": 10000, "acre": 4046.8564224, "sqkm": 1e6, "km2": 1e6},
    "volume": {"ml": 0.001, "l": 1, "liter": 1, "litre": 1, "gal": 3.785411784,
               "gallon": 3.785411784, "cup": 0.2365882365, "tbsp": 0.01478676478,
               "tsp": 0.00492892159, "floz": 0.0295735296},
}
_TEMP = {"c": "c", "celsius": "c", "°c": "c", "f": "f", "fahrenheit": "f", "°f": "f",
         "k": "k", "kelvin": "k"}


def _unit(name: str) -> str:
    u = name.strip().lower().replace(" ", "").replace("per", "/").replace("²", "2")
    for fam in _UNITS.values():
        if u in fam:
            return u
    if u in _TEMP:
        return u
    if u.endswith("es") and u[:-2] in {k for f in _UNITS.values() for k in f}:
        return u[:-2]
    if u.endswith("s") and u[:-1] in {k for f in _UNITS.values() for k in f}:
        return u[:-1]
    return u


def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    try:
        value = float(str(value).replace(",", ""))
    except ValueError:
        raise ToolError("the value has to be a number")
    a, b = _unit(from_unit), _unit(to_unit)
    if a in _TEMP and b in _TEMP:
        k = {"c": value + 273.15, "f": (value - 32) * 5 / 9 + 273.15, "k": value}[_TEMP[a]]
        out = {"c": k - 273.15, "f": (k - 273.15) * 9 / 5 + 32, "k": k}[_TEMP[b]]
        return f"{_fmt(float(value))} {from_unit} = {_fmt(round(out, 2))} {to_unit}"
    for fam in _UNITS.values():
        if a in fam and b in fam:
            out = value * fam[a] / fam[b]
            return f"{_fmt(float(value))} {from_unit} = {_fmt(float(f'{out:.6g}'))} {to_unit}"
    raise ToolError(f"I can't convert {from_unit} to {to_unit} (money changes daily: "
                    "use web_answer for currencies)")


# ---------------------------------------------------------------- small JSON stores
class _Store:
    def __init__(self, path: Path, default):
        self.path, self._default = Path(path), default

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, type(self._default)) else type(self._default)()
        except (OSError, ValueError):
            return type(self._default)()

    def save(self, data) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


# ---------------------------------------------------------------- to-do list
class TodoList:
    """A plain list of things to do. Items keep a small number you can say."""

    def __init__(self, path: Path):
        self.store = _Store(path, [])

    def items(self) -> list[dict]:
        return [i for i in self.store.load() if isinstance(i, dict) and i.get("text")]

    def open_count(self) -> int:
        return sum(not i.get("done") for i in self.items())

    def _find(self, items: list[dict], which: str) -> dict:
        w = str(which).strip().lower().lstrip("#")
        if w.isdigit():
            for i in items:
                if i.get("id") == int(w):
                    return i
        hits = [i for i in items if w and w in i["text"].lower()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ToolError(f"nothing on the list matches '{which}'")
        raise ToolError("more than one matches: " + "; ".join(f"#{i['id']} {i['text']}" for i in hits[:5]))

    def run(self, action: str = "list", text: str = "", item: str = "") -> str:
        items = self.items()
        action = (action or "list").lower()
        if action == "add":
            parts = [p.strip() for p in re.split(r"\n|;", text) if p.strip()]
            if not parts:
                raise ToolError("what should I add?")
            nxt = max((i.get("id", 0) for i in items), default=0)
            for part in parts[:20]:
                nxt += 1
                items.append({"id": nxt, "text": part[:200], "done": False, "at": int(time.time())})
            self.store.save(items)
            return f"Added {len(parts[:20])}: " + "; ".join(parts[:20]) + f". {self._count(items)}"
        if action == "list":
            todo = [i for i in items if not i.get("done")]
            if not todo:
                return "The to-do list is empty."
            return "To do:\n" + "\n".join(f"#{i['id']} {i['text']}" for i in todo)
        if action in ("done", "remove"):
            target = self._find(items, item or text)
            if action == "done":
                target["done"] = True
            else:
                items.remove(target)
            self.store.save(items)
            word = "Ticked off" if action == "done" else "Removed"
            return f"{word}: {target['text']}. {self._count(items)}"
        if action == "clear":
            kept = [i for i in items if not i.get("done")] if (item or text) != "all" else []
            self.store.save(kept)
            return f"Cleared. {self._count(kept)}"
        raise ToolError("action must be add, list, done, remove or clear")

    @staticmethod
    def _count(items: list[dict]) -> str:
        n = sum(not i.get("done") for i in items)
        return f"{n} left to do." if n else "Nothing left to do."


# ---------------------------------------------------------------- write a file
NOT_PROGRAMS = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".wsf", ".scr", ".lnk",
                ".reg", ".msi", ".com", ".hta", ".jar", ".pif", ".cpl", ".sys"}
PLACES = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads"}


def _place(folder: str, home: Path) -> Path:
    """A folder inside the user's home: 'desktop', 'documents', or a path under it."""
    key = (folder or "documents").strip().strip('"').lower()
    if key in PLACES:
        for base in (home, home / "OneDrive"):
            if (base / PLACES[key]).is_dir():
                return base / PLACES[key]
        return home / PLACES[key]
    p = Path(os.path.expandvars(os.path.expanduser(folder.strip().strip('"'))))
    if not p.is_absolute():
        p = home / p
    p = p.resolve()
    try:
        rel = p.relative_to(home.resolve())
    except ValueError:
        raise ToolError("I only save and tidy inside your own folders (Desktop, Documents, "
                        "Downloads...)")
    if rel.parts and rel.parts[0].lower() in ("appdata", ".ssh", ".config", ".gnupg"):
        raise ToolError("that folder holds settings and keys; I'll leave it alone")
    return p


def _free_name(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        cand = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not cand.exists():
            return cand
    raise ToolError("too many files with that name already")


def write_file(name: str, content: str, folder: str = "documents",
               home: Path | None = None) -> str:
    home = home or Path.home()
    name = Path(name.strip().strip('"')).name            # no sneaking in a path
    if not name or name in (".", ".."):
        raise ToolError("what should the file be called?")
    if "." not in name:
        name += ".txt"
    if Path(name).suffix.lower() in NOT_PROGRAMS:
        raise ToolError(f"I don't create {Path(name).suffix} files (programs and scripts "
                        "Windows can run by itself)")
    if len(content) > 400_000:
        raise ToolError("that's too much text for one file")
    where = _place(folder, home)
    where.mkdir(parents=True, exist_ok=True)
    path = _free_name(where / name)
    path.write_text(content, encoding="utf-8")
    return f"Saved {path} ({len(content):,} characters). Use open_path to open it."


# ---------------------------------------------------------------- routines
class Routines:
    """Several steps saved under one name. Running one hands the steps back to
    the brain, which carries them out with its ordinary tools."""

    def __init__(self, path: Path):
        self.store = _Store(path, {})

    def names(self) -> list[str]:
        return sorted(self.store.load())

    def run(self, action: str = "list", name: str = "", steps: str = "") -> str:
        data = self.store.load()
        key = re.sub(r"\s+", " ", name.strip().lower())
        action = (action or "list").lower()
        if action == "save":
            if not key or not steps.strip():
                raise ToolError("a routine needs a name and its steps")
            if len(data) >= 30 and key not in data:
                raise ToolError("that's a lot of routines; delete one first")
            data[key] = steps.strip()[:1500]
            self.store.save(data)
            return f"Saved the routine '{key}'. Say its name any time to run it."
        if action == "list":
            if not data:
                return "No routines saved yet."
            return "Routines:\n" + "\n".join(f"- {k}: {v}" for k, v in sorted(data.items()))
        match = key if key in data else next((k for k in data if key and (key in k or k in key)), "")
        if not match:
            raise ToolError(f"there's no routine called '{name}'"
                            + (f". Saved: {', '.join(sorted(data))}" if data else ""))
        if action == "delete":
            del data[match]
            self.store.save(data)
            return f"Deleted the routine '{match}'."
        if action == "run":
            return (f"Routine '{match}'. Carry out these steps now, in order, with your tools, "
                    f"then tell the user in one short sentence what you did:\n{data[match]}")
        raise ToolError("action must be save, run, list or delete")


# ---------------------------------------------------------------- PC health
def pc_health(top: int = 5) -> str:
    try:
        import psutil
    except ImportError:
        raise ToolError("psutil isn't installed")
    groups: dict[str, list[float]] = {}
    procs = list(psutil.process_iter(["name", "memory_info"]))
    psutil.cpu_percent(None)                          # prime; read after a short wait
    for p in procs:
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(0.4)
    cores = psutil.cpu_count() or 1
    for p in procs:
        try:
            name = (p.info.get("name") or "?").removesuffix(".exe")
            mem = p.info["memory_info"].rss if p.info.get("memory_info") else 0
            cpu = p.cpu_percent(None) / cores
        except Exception:
            continue
        if name.lower() in ("system idle process", "idle"):
            continue
        g = groups.setdefault(name, [0.0, 0.0, 0])
        g[0] += mem
        g[1] += cpu
        g[2] += 1
    vm = psutil.virtual_memory()
    lines = [f"CPU {psutil.cpu_percent(interval=None):.0f}% busy. "
             f"Memory {vm.percent:.0f}% used ({vm.used / 2**30:.1f} of {vm.total / 2**30:.1f} GB)."]
    try:
        du = psutil.disk_usage(str(Path.home().anchor or "/"))
        lines.append(f"Disk {du.percent:.0f}% full ({du.free / 2**30:.0f} GB free).")
        bat = psutil.sensors_battery()
        if bat:
            lines.append(f"Battery {bat.percent:.0f}% ({'charging' if bat.power_plugged else 'on battery'}).")
    except Exception:
        pass
    top = max(1, min(10, int(top)))
    by_mem = sorted(groups.items(), key=lambda kv: -kv[1][0])[:top]
    lines.append("Using the most memory: " + ", ".join(
        f"{n} {g[0] / 2**20:,.0f} MB" + (f" ({g[2]} processes)" if g[2] > 3 else "")
        for n, g in by_mem))
    by_cpu = [(n, g) for n, g in sorted(groups.items(), key=lambda kv: -kv[1][1])[:top] if g[1] >= 2]
    lines.append("Using the most CPU: " + (", ".join(f"{n} {g[1]:.0f}%" for n, g in by_cpu)
                                           if by_cpu else "nothing much right now"))
    return "\n".join(lines)


PROTECTED = {"explorer", "system", "csrss", "winlogon", "services", "lsass", "svchost", "dwm",
             "smss", "wininit", "python", "pythonw", "kuro", "registry", "fontdrvhost",
             "taskmgr", "searchhost", "startmenuexperiencehost", "shellexperiencehost"}


APP_EXES = {"powerpoint": "powerpnt", "word": "winword", "vscode": "code",
            "visualstudiocode": "code", "calculator": "calculatorapp", "edge": "msedge",
            "operagx": "opera", "fileexplorer": "explorer", "taskmanager": "taskmgr"}


def _matching(name: str):
    import psutil
    q = name.strip().lower().removesuffix(".exe").replace(" ", "")
    q = APP_EXES.get(q, q)
    if len(q) < 2:
        raise ToolError("which app?")
    hits = []
    for p in psutil.process_iter(["name", "pid"]):
        n = (p.info.get("name") or "").lower().removesuffix(".exe").replace(" ", "")
        if n and n not in PROTECTED and (q in n or (len(n) >= 4 and n in q)):
            hits.append(p)
    return hits


def close_app(name: str, force: bool = False) -> str:
    """Ask the app's windows to close (it can still offer to save). `force`
    ends it outright, for something frozen."""
    try:
        hits = _matching(name)
    except ImportError:
        raise ToolError("psutil isn't installed")
    if not hits:
        raise ToolError(f"nothing called '{name}' is running (or it's part of Windows, "
                        "which I won't close)")
    label = (hits[0].info.get("name") or name).removesuffix(".exe")
    if force:
        for p in hits:
            try:
                p.terminate()
            except Exception:
                pass
        return f"Ended {label} ({len(hits)} process{'es' if len(hits) != 1 else ''})."
    if not IS_WINDOWS:
        raise ToolError("closing windows is only supported on Windows")
    pids = {p.info["pid"] for p in hits}
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    closed = 0
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        nonlocal closed
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)          # WM_CLOSE
            closed += 1
        return True
    user32.EnumWindows(proto(cb), 0)
    if not closed:
        return (f"{label} is running but has no window to close (it may be in the tray). "
                "Call close_app again with force=true only if the user wants it ended.")
    return (f"Asked {label} to close ({closed} window{'s' if closed != 1 else ''}). "
            "If it had unsaved work it will ask the user first.")


# ---------------------------------------------------------------- display
def display(setting: str, value: str = "") -> str:
    setting, value = (setting or "").lower().strip(), str(value).lower().strip()
    if not IS_WINDOWS:
        raise ToolError("display settings are only supported on Windows")
    if setting in ("dark_mode", "dark", "theme"):
        dark = value not in ("off", "light", "false", "0", "no")
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                             0, winreg.KEY_SET_VALUE)
        with key:
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if dark else 1)
        try:                                           # tell open apps the theme changed
            ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
                0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 200, None)
        except Exception:
            pass
        return f"Windows is now in {'dark' if dark else 'light'} mode."
    if setting == "brightness":
        try:
            level = max(0, min(100, int(float(value.rstrip("%")))))
        except ValueError:
            raise ToolError("brightness needs a number from 0 to 100")
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; $m = Get-WmiObject -Namespace root/WMI "
             f"-Class WmiMonitorBrightnessMethods; $m.WmiSetBrightness(1,{level}) | Out-Null"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise ToolError("this screen doesn't let Windows set its brightness (that works "
                            "on laptop screens; desktop monitors use their own buttons)")
        return f"Brightness set to {level}%."
    raise ToolError("setting must be dark_mode (on/off) or brightness (0-100)")


# ---------------------------------------------------------------- organize a folder
KINDS = {
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic", ".tiff", ".ico"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".m4v"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".ppt", ".pptx",
                  ".xls", ".xlsx", ".csv", ".epub"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".iso"},
    "Installers": {".exe", ".msi", ".msix", ".appx"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".java", ".c", ".cpp", ".cs",
             ".ipynb", ".sql", ".sh", ".php", ".go", ".rs", ".kt", ".xml", ".yml", ".yaml"},
}
UNFINISHED = {".crdownload", ".part", ".tmp", ".download", ".partial"}
LEAVE = {".lnk", ".url", ".ini", ".db"}


class Organizer:
    """Sorts the loose files in one folder into sub-folders by type. Nothing is
    deleted or overwritten, files of unknown type stay where they are, and the
    last tidy of each folder can be undone."""

    def __init__(self, undo_path: Path, home: Path | None = None):
        self.store = _Store(undo_path, {})
        self.home = home or Path.home()

    def folder(self, folder: str) -> Path:
        p = _place(folder or "downloads", self.home)
        if p.resolve() == self.home.resolve():
            raise ToolError("not your whole home folder; pick one inside it, like Downloads")
        if not p.is_dir():
            raise ToolError(f"there's no folder {p}")
        return p

    def plan(self, folder: str) -> tuple[Path, dict[str, list[Path]]]:
        root = self.folder(folder)
        plan: dict[str, list[Path]] = {}
        now = time.time()
        for f in sorted(root.iterdir()):
            ext = f.suffix.lower()
            if (not f.is_file() or f.name.startswith((".", "~$")) or ext in UNFINISHED
                    or ext in LEAVE):
                continue
            try:
                if now - f.stat().st_mtime < 120:      # still being written / just saved
                    continue
            except OSError:
                continue
            kind = next((k for k, exts in KINDS.items() if ext in exts), "")
            if kind:
                plan.setdefault(kind, []).append(f)
        return root, plan

    def describe(self, args: dict) -> str:
        if args.get("undo"):
            return f"Put the files in {args.get('folder') or 'Downloads'} back where they were?"
        try:
            root, plan = self.plan(args.get("folder", ""))
        except ToolError as e:
            return f"Tidy {args.get('folder') or 'Downloads'}? ({e})"
        n = sum(len(v) for v in plan.values())
        if not n:
            return f"Tidy {root.name}? There's nothing loose to sort."
        parts = ", ".join(f"{k} {len(v)}" for k, v in plan.items())
        return (f"Tidy {root.name}: move {n} loose file{'s' if n != 1 else ''} into folders by "
                f"type ({parts}).\n\nNothing is deleted, and you can undo it.")

    def run(self, folder: str = "downloads", undo: bool = False) -> str:
        if undo:
            return self.undo(folder)
        root, plan = self.plan(folder)
        moved: list[list[str]] = []
        for kind, files in plan.items():
            dest = root / kind
            dest.mkdir(exist_ok=True)
            for f in files:
                target = _free_name(dest / f.name)
                try:
                    shutil.move(str(f), str(target))
                    moved.append([str(target), str(f)])
                except OSError:
                    continue                           # in use: leave it
        if not moved:
            return f"Nothing loose to sort in {root.name}."
        data = self.store.load()
        data[str(root)] = moved
        self.store.save(data)
        counts = ", ".join(f"{k} {len(v)}" for k, v in plan.items())
        return (f"Sorted {len(moved)} files in {root.name} into folders: {counts}. "
                "The user can ask to undo it.")

    def undo(self, folder: str = "downloads") -> str:
        root = self.folder(folder)
        data = self.store.load()
        moved = data.get(str(root))
        if not moved:
            raise ToolError(f"there's no tidy of {root.name} to undo")
        back = 0
        for now_at, was_at in moved:
            src, dst = Path(now_at), Path(was_at)
            if src.is_file() and not dst.exists():
                try:
                    shutil.move(str(src), str(dst))
                    back += 1
                except OSError:
                    pass
        for kind in KINDS:                             # remove the folders it made, if empty
            try:
                (root / kind).rmdir()
            except OSError:
                pass
        del data[str(root)]
        self.store.save(data)
        return f"Put {back} of {len(moved)} files back in {root.name}."


# ---------------------------------------------------------------- registry
def build_skills(data_dir: Path, home: Path | None = None) -> tuple[list[Tool], TodoList, Routines]:
    todo = TodoList(Path(data_dir) / "todo.json")
    routines = Routines(Path(data_dir) / "routines.json")
    organizer = Organizer(Path(data_dir) / "organize_undo.json", home)
    # (descriptions are kept short: every brain reads all of them on every message)
    tools = [
        Tool("calculate", "Exact arithmetic. ALWAYS use for sums, percentages, formulas "
             "('15% of 240', sqrt, log, ^).",
             _obj({"expression": {"type": "string"}}, ["expression"]), calculate),
        Tool("convert_units", "Convert length, weight, temperature, data, time, speed, area, "
             "volume (5 ft to cm). Not money.",
             _obj({"value": {"type": "number"}, "from_unit": {"type": "string"},
                   "to_unit": {"type": "string"}}, ["value", "from_unit", "to_unit"]),
             convert_units),
        Tool("todo", "The user's to-do list. add (text; several split by ;), list, done or "
             "remove (item: number or words), clear.",
             _obj({"action": {"type": "string", "enum": ["add", "list", "done", "remove", "clear"]},
                   "text": {"type": "string"}, "item": {"type": "string"}}, ["action"]),
             todo.run),
        Tool("write_file", "Save text you wrote as a new file. folder: documents (default), "
             "desktop, downloads or a path in the user's home. Never overwrites.",
             _obj({"name": {"type": "string"}, "content": {"type": "string"},
                   "folder": {"type": "string"}}, ["name", "content"]),
             lambda name, content, folder="documents": write_file(name, content, folder, home)),
        Tool("routine", "Named multi-step routines ('study mode'). save (name, steps in plain "
             "words), run (name; then DO the steps returned), list, delete.",
             _obj({"action": {"type": "string", "enum": ["save", "run", "list", "delete"]},
                   "name": {"type": "string"}, "steps": {"type": "string"}}, ["action"]),
             routines.run),
        Tool("pc_health", "Why the PC is slow: CPU, memory, disk, battery and the apps using "
             "the most.", _obj({}), pc_health),
        Tool("close_app", "Close a running app by name. force=true only for a frozen app.",
             _obj({"name": {"type": "string"}, "force": {"type": "boolean"}}, ["name"]),
             close_app, risky=True,
             describe=lambda a: (f"{'Force-quit' if a.get('force') else 'Close'} "
                                 f"{a.get('name', 'this app')}?"
                                 + ("\n\nAnything unsaved in it will be lost."
                                    if a.get("force") else ""))),
        Tool("display", "dark_mode (value on/off) or brightness (value 0-100).",
             _obj({"setting": {"type": "string", "enum": ["dark_mode", "brightness"]},
                   "value": {"type": "string"}}, ["setting", "value"]), display),
        Tool("organize_folder", "Tidy a folder (default downloads): sorts loose files into "
             "folders by type, deletes nothing. undo=true reverses the last tidy.",
             _obj({"folder": {"type": "string"}, "undo": {"type": "boolean"}}),
             organizer.run, risky=True, describe=organizer.describe),
    ]
    return tools, todo, routines
