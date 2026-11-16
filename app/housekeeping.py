"""Keeps Kuro's footprint small. Runs once, in the background, a few seconds
after he starts. Only ever removes things Kuro made himself or can fetch again:

- voices you aren't using (each Piper voice is ~60 MB; the chosen one stays,
  and switching back downloads it again)
- the offline speech model and Mac/Linux tools bundled with SpeechRecognition
  (~43 MB; Kuro uses Whisper/Google online and only needs the Windows part)
- stray __pycache__ folders in his own app folder (Python keeps those in
  %LOCALAPPDATA%\\Kuro\\pycache now)
- the Google sign-in token from versions that had Calendar + Gmail (that
  integration is gone; the token shouldn't linger)
"""
from __future__ import annotations

import logging
import shutil
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("kuro")

APP_DIR = Path(__file__).resolve().parent.parent


def _size(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def _remove(p: Path) -> int:
    n = _size(p)
    try:
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        else:
            return 0
    except OSError:
        return 0
    return n


def unused_voices(voice_dir: Path, keep: str) -> list[Path]:
    if not voice_dir.is_dir():
        return []
    return [f for f in voice_dir.iterdir()
            if f.is_file() and f.name.endswith((".onnx", ".onnx.json", ".part"))
            and f.name.split(".")[0] != keep]


def speech_extras(site: Path, platform: str = sys.platform) -> list[Path]:
    sr = site / "speech_recognition"
    if not sr.is_dir():
        return []
    out = [sr / "pocketsphinx-data"]
    for flac in ("flac-mac", "flac-linux-x86", "flac-linux-x86_64", "flac-win32.exe"):
        mine = (platform == "win32" and flac.endswith(".exe")) or \
               (platform == "darwin" and flac == "flac-mac") or \
               (platform.startswith("linux") and flac.startswith("flac-linux"))
        if not mine:
            out.append(sr / flac)
    return [p for p in out if p.exists()]


def stray_caches(root: Path = APP_DIR) -> list[Path]:
    return [d for sub in ("app", "app/ui", "tests", "scripts", "server")
            for d in [root / sub / "__pycache__"] if d.is_dir()]


def retired_files() -> list[Path]:
    """Files only older versions used."""
    try:
        from .config import data_dir
        return [p for p in (data_dir() / "google.token",) if p.exists()]
    except Exception:
        return []


def tidy(settings, voice_dir: Path | None = None, site: Path | None = None) -> int:
    """Remove what isn't needed; returns bytes freed."""
    from .voice import VOICES, voice_files
    if voice_dir is None:
        voice_dir = voice_files(next(iter(VOICES)))[0].parent
    if site is None:
        import importlib.util
        spec = importlib.util.find_spec("speech_recognition")
        site = Path(spec.origin).resolve().parent.parent if spec and spec.origin else Path("/nonexistent")
    keep = getattr(settings, "voice", "")
    targets = (unused_voices(voice_dir, keep) + speech_extras(site) + stray_caches()
               + retired_files())
    freed = sum(_remove(p) for p in targets)
    if freed:
        log.info("housekeeping freed %.0f MB", freed / 1e6)
    return freed


def start(settings, delay: float = 20.0) -> None:
    def run() -> None:
        time.sleep(delay)                      # never compete with startup
        try:
            tidy(settings)
        except Exception as e:                 # never matters enough to bother you
            log.info("housekeeping skipped: %s", e)
    threading.Thread(target=run, daemon=True, name="housekeeping").start()
