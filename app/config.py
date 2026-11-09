"""Settings: defaults, overridden by config.json, overridden by env vars.

The Gemini key is never stored in code. Paste it in Settings (saved to
config.json in your AppData folder) or set the GEMINI_API_KEY variable.
"""
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path


def app_root() -> Path:
    """Folder that holds assets/ - works both from source and from a PyInstaller build."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Per-user writable folder for settings, memory, reminders and logs."""
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / "Kuro"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging() -> Path:
    """Errors go to %APPDATA%\\Kuro\\kuro.log (never the API key)."""
    path = data_dir() / "kuro.log"
    logger = logging.getLogger("kuro")
    if not logger.handlers:
        h = RotatingFileHandler(path, maxBytes=200_000, backupCount=1, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
    return path


@dataclass
class Settings:
    cat_name: str = "Kuro"
    user_name: str = "Drey"
    personality: str = (
        "a cute, playful, slightly sassy black cat with small magical powers "
        "(you can walk on air and blink across the screen), who is secretly very "
        "capable. Short, warm replies. Occasional cat mannerisms (a 'mrrp', "
        "a stretch) but never more than one per reply, and never at the "
        "expense of being useful."
    )
    gemini_key: str = ""
    model: str = ""                   # "" = automatic (best free model that works)
    max_tokens: int = 1024

    # behaviour
    scale: float = 1.0
    wander: bool = True               # teleports around the screen now and then
    chat_size: str = "medium"         # small | medium | large
    dnd_fullscreen: bool = True       # hide while a fullscreen game / video is in front
    morning_briefing: bool = True     # first time you see him each day
    city: str = ""                    # for the weather in the briefing
    last_briefing: str = ""           # date of the last automatic briefing
    sleep_after_minutes: int = 5
    history_turns: int = 20           # user turns kept in context

    # hotkeys (global, via the `keyboard` package)
    hotkey_chat: str = "ctrl+alt+k"
    hotkey_voice: str = "ctrl+alt+space"
    hotkey_quick: str = "ctrl+alt+q"  # select text anywhere -> quick actions
    translate_to: str = "English"

    # voice
    voice_enabled: bool = True
    speak_replies: bool = True
    tts_rate: int = 185
    wake_word: bool = False           # "Hey Kuro" hands-free

    # focus companion
    focus_minutes: int = 25
    break_minutes: int = 5
    focus_nap: bool = True            # he naps on the taskbar while you focus

    # Google (Calendar + Gmail read-only). Client ID from Google Cloud Console.
    google_client_id: str = ""
    google_client_secret: str = ""

    # learning
    learn_enabled: bool = True        # reflect on chats and remember facts about you
    suggestions: bool = True          # "you usually open Blender around now..."
    aware: bool = True                # notice which app you're in

    # safety
    allow_shell: bool = True          # PowerShell tool exists, always asks first
    search_roots: list = field(default_factory=lambda: [
        str(Path.home() / "Desktop"),
        str(Path.home() / "Documents"),
        str(Path.home() / "Downloads"),
        str(Path.home() / "Pictures"),
    ])

    @property
    def path(self) -> Path:
        return data_dir() / "config.json"

    @property
    def ready(self) -> bool:
        return bool(self.gemini_key.strip())

    def save(self) -> None:
        data = asdict(self)
        if os.environ.get("GEMINI_API_KEY") == self.gemini_key:
            data["gemini_key"] = ""       # don't copy an env-var key onto disk
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_settings() -> Settings:
    s = Settings()
    cfg = data_dir() / "config.json"
    if cfg.exists():
        try:
            raw = json.loads(cfg.read_text(encoding="utf-8"))
            # v0.3 configs kept per-provider keys
            if not raw.get("gemini_key") and isinstance(raw.get("keys"), dict):
                raw["gemini_key"] = raw["keys"].get("gemini", "")
            if isinstance(raw.get("models"), dict):
                raw["model"] = ""         # old model overrides: go automatic
            if "sprite_scale" in raw:
                raw.setdefault("scale", raw["sprite_scale"])
            for k, v in raw.items():
                if k in Settings.__dataclass_fields__ and k != "personality":
                    setattr(s, k, v)
        except (json.JSONDecodeError, OSError, AttributeError, TypeError):
            pass  # a broken config falls back to defaults rather than crashing
    if os.environ.get("GEMINI_API_KEY") and not s.gemini_key:
        s.gemini_key = os.environ["GEMINI_API_KEY"]
    if not isinstance(s.model, str):
        s.model = ""
    if not isinstance(s.gemini_key, str):
        s.gemini_key = ""
    return s
