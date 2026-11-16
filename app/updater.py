"""Kuro keeps himself up to date.

How it works, start to finish:
1. A release is a zip of Kuro's code plus a small latest.json next to it
   (made by scripts/make_release.py and uploaded anywhere that serves files,
   e.g. a GitHub release).
2. Once a day or so, in a background thread, Kuro fetches latest.json from
   settings.update_url. A newer version there is downloaded, checked against
   its sha256, and unpacked into %APPDATA%/Kuro/update/pending — nothing that
   is running is touched, so this can never break the Kuro you're using.
3. The NEXT time Kuro starts, main.py calls apply_pending() before any of the
   app is imported: the staged files replace the old ones (the previous code
   is kept in update/backup, and restored if anything goes wrong), and Kuro
   starts as the new version.

Settings and keys live in config.json, which an update never touches.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

log = logging.getLogger("kuro")

# the only paths an update may replace — never config, voices or logs
ALLOWED = ("app/", "assets/", "server/", "main.py", "run.bat", "setup.bat",
           "requirements.txt", "README.md", "TESTING.md", "pytest.ini",
           "scripts/slim.py")


def parse(v: str) -> tuple[int, ...]:
    out = []
    for part in str(v).strip().lstrip("vV").split(".")[:4]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse(latest), parse(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def safe_member(name: str) -> bool:
    """Only plain files under the allowed paths; no absolute paths, no '..'."""
    name = name.replace("\\", "/")
    if name.startswith("/") or ".." in name.split("/") or ":" in name.split("/")[0]:
        return False
    return any(name == a or (a.endswith("/") and name.startswith(a)) for a in ALLOWED)


APP_DIR = Path(__file__).resolve().parent.parent


def installed_version(app_dir: Path = APP_DIR) -> str:
    """The version actually on disk right now (not the one this process
    imported when it started, which can be stale after files change)."""
    try:
        text = (Path(app_dir) / "app" / "version.py").read_text(encoding="utf-8")
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else "0"
    except OSError:
        return "0"


def is_dev_checkout(app_dir: Path = APP_DIR) -> bool:
    """A git checkout is where the code is written: never self-update it."""
    return (Path(app_dir) / ".git").exists()


def update_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ checking
def fetch_latest(url: str, timeout: float = 15.0) -> dict:
    """latest.json: {"version": "1.2.0", "zip": "https://...", "sha256": "...",
    "notes": "..."}. The zip URL may also be relative to latest.json."""
    import httpx
    r = httpx.get(url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    info = r.json()
    if not isinstance(info, dict) or "version" not in info or "zip" not in info:
        raise ValueError("latest.json is missing version/zip")
    if "://" not in str(info["zip"]):
        info["zip"] = url.rsplit("/", 1)[0] + "/" + str(info["zip"]).lstrip("/")
    return info


def download_and_stage(info: dict, data_dir: Path, timeout: float = 120.0) -> Path:
    """Download the release zip, verify it, unpack it into update/pending."""
    import httpx
    d = update_dir(data_dir)
    zpath = d / "kuro-download.zip"
    with httpx.stream("GET", str(info["zip"]), timeout=timeout, follow_redirects=True) as r:
        r.raise_for_status()
        digest = hashlib.sha256()
        with open(zpath, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                digest.update(chunk)
                if f.tell() > 300 * 1024 * 1024:
                    raise ValueError("update is implausibly large")
    want = str(info.get("sha256", "")).lower()
    if want and digest.hexdigest() != want:
        zpath.unlink(missing_ok=True)
        raise ValueError("update download was corrupted (sha256 mismatch)")
    return stage_zip(zpath, d, str(info["version"]))


def stage_zip(zpath: Path, d: Path, version: str) -> Path:
    pending = d / "pending"
    if pending.exists():
        shutil.rmtree(pending)
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        good = [n for n in names if safe_member(n)]
        if "main.py" not in good or not any(n.startswith("app/") for n in good):
            raise ValueError("update zip doesn't look like Kuro")
        for n in good:
            target = pending / n.replace("\\", "/")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    (pending / "UPDATE_VERSION").write_text(version, encoding="utf-8")
    zpath.unlink(missing_ok=True)
    log.info("update %s staged, applies on next start", version)
    return pending


# ------------------------------------------------------------------ applying
def apply_pending(app_dir: Path, data_dir: Path) -> str:
    """Called by main.py before anything else is imported. Replaces the code
    with the staged update; on any error the old files are put back. Returns
    the new version, or '' when there was nothing to do."""
    pending = Path(data_dir) / "update" / "pending"
    marker = pending / "UPDATE_VERSION"
    if not marker.is_file():
        return ""
    version = marker.read_text(encoding="utf-8").strip()
    # never downgrade, and never touch a developer's checkout: an update
    # staged by an older copy of the code must not overwrite newer files
    if is_dev_checkout(app_dir) or not is_newer(version, installed_version(app_dir)):
        log.info("discarding staged update %s (installed %s%s)", version,
                 installed_version(app_dir), ", dev checkout" if is_dev_checkout(app_dir) else "")
        shutil.rmtree(pending, ignore_errors=True)
        return ""
    backup = Path(data_dir) / "update" / "backup"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)
    moved: list[tuple[Path, Path]] = []
    try:
        for item in sorted(pending.iterdir()):
            if item.name == "UPDATE_VERSION":
                continue
            dst = app_dir / item.name
            if dst.exists():                      # set the old aside, restorable
                keep = backup / item.name
                shutil.move(str(dst), str(keep))
                moved.append((keep, dst))
            shutil.move(str(item), str(dst))
        shutil.rmtree(pending)
        log.info("updated to %s", version)
        return version
    except Exception as e:                        # put everything back
        log.warning("update failed, restoring previous version: %s", e)
        for keep, dst in moved:
            try:
                if dst.exists():
                    shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
                shutil.move(str(keep), str(dst))
            except OSError:
                pass
        shutil.rmtree(pending, ignore_errors=True)
        return ""


# ------------------------------------------------------------------ the loop
class Updater:
    """Background check: quiet, rare, and never interrupts. on_ready(version)
    is called (from the worker thread) when an update is staged."""

    def __init__(self, settings, data_dir: Path, on_ready=None):
        self.s = settings
        self.data_dir = Path(data_dir)
        self.on_ready = on_ready
        self._thread: threading.Thread | None = None

    @property
    def stamp(self) -> Path:
        return update_dir(self.data_dir) / "last-check"

    def due(self, every: float = 20 * 3600) -> bool:
        try:
            return time.time() - self.stamp.stat().st_mtime > every
        except OSError:
            return True

    def start(self, delay: float = 40.0) -> None:
        if not getattr(self.s, "update_url", "") or not getattr(self.s, "auto_update", True):
            return
        if is_dev_checkout():
            log.info("dev checkout: self-update off")
            return
        if not self.due() or (self._thread and self._thread.is_alive()):
            return
        def run() -> None:
            time.sleep(delay)
            try:
                self.check_now()
            except Exception as e:
                log.info("update check skipped: %s", e)
        self._thread = threading.Thread(target=run, daemon=True, name="updater")
        self._thread.start()

    def check_now(self) -> str:
        """Returns the staged version, or '' when already current."""
        VERSION = installed_version()
        self.stamp.write_text(str(time.time()), encoding="utf-8")
        info = fetch_latest(self.s.update_url)
        if not is_newer(info["version"], VERSION):
            log.info("up to date (%s)", VERSION)
            return ""
        pending = Path(self.data_dir) / "update" / "pending" / "UPDATE_VERSION"
        if pending.is_file() and pending.read_text(encoding="utf-8").strip() == str(info["version"]):
            return str(info["version"])           # already staged, waiting for a restart
        download_and_stage(info, self.data_dir)
        if self.on_ready:
            self.on_ready(str(info["version"]))
        return str(info["version"])
