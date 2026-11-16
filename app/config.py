"""Settings: defaults, overridden by config.json, overridden by env vars.

API keys are never stored in code. Paste one in Settings → AI & Voice
(saved to config.json in your AppData folder) or set GEMINI_API_KEY.
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
    from .character import CURRENT
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / CURRENT["folder"]
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
    cat_name: str = ""                # "" = this install's character; see __post_init__
    user_name: str = "friend"         # asked in the first-run welcome
    personality: str = ""             # "" = the character's own; see __post_init__
    provider: str = "gemini"          # gemini | groq | openrouter | ollama | openai | claude
    gemini_key: str = ""
    keys: dict = field(default_factory=dict)   # keys for the other brains
    model: str = ""                   # "" = automatic (best model the key can use)
    auto_fallback: bool = True        # out of quota? use another brain you have a key for
    cloud_url: str = ""               # Kuro Cloud (subscription server, see server/)
    onboarded: bool = False           # finished the first-run welcome
    auto_update: bool = True          # fetch new Kuro versions quietly
    update_url: str = ("https://raw.githubusercontent.com/Addurei/kuro/main/"
                       "releases/latest.json")   # where new versions come from ("" = off)
    max_tokens: int = 1024

    # behaviour
    scale: float = 1.0
    wander: bool = True               # teleports around the screen now and then
    chat_size: str = "medium"         # small | medium | large
    dnd_fullscreen: bool = True       # hide while a fullscreen game / video is in front
    morning_briefing: bool = True     # first time you see him each day
    city: str = ""                    # optional override; normally found automatically
    auto_city: str = ""               # found from your connection (app/location.py)
    auto_lat: float = 0.0
    auto_lon: float = 0.0
    auto_located: str = ""            # date it was last looked up
    last_briefing: str = ""           # date of the last automatic briefing
    sleep_after_minutes: int = 5
    history_turns: int = 20           # user turns kept in context

    # hotkeys (global, via the `keyboard` package)
    hotkey_chat: str = "ctrl+alt+k"
    hotkey_voice: str = "ctrl+alt+space"
    hotkey_quick: str = "ctrl+alt+q"  # select text anywhere -> quick actions
    translate_to: str = "English"
    language: str = "English"         # English | Filipino | Taglish (his replies and lines)

    # voice
    voice_enabled: bool = True
    speak_replies: bool = True
    tts_rate: int = 185
    voice: str = "amy"                # natural voice (amy | ryan | lessac) or "windows"
    cute_voice: bool = True           # a touch higher and brighter
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
    react_activity: bool = True       # props, typing along, the odd comment (app/activity.py)

    # safety
    allow_shell: bool = True          # PowerShell tool exists, always asks first (config only)
    search_roots: list = field(default_factory=lambda: [
        str(Path.home() / "Desktop"),
        str(Path.home() / "Documents"),
        str(Path.home() / "Downloads"),
        str(Path.home() / "Pictures"),
    ])

    def __post_init__(self) -> None:
        from .character import CURRENT
        if not self.cat_name:
            self.cat_name = CURRENT["name"]
        if not self.personality:
            if CURRENT["personality"]:
                self.personality = (
                    f"a cute {CURRENT['fur_word']} cat with small magical powers "
                    "(you can walk on air and blink across the screen), "
                    + CURRENT["personality"]
                    + " Short, warm, lively replies. Never act out cat actions or sounds.")
            else:
                self.personality = (
                    "a cute, playful, slightly sassy black cat with small magical powers "
                    "(you can walk on air and blink across the screen), who is secretly "
                    "very capable. Short, warm, lively replies with a bit of cat attitude "
                    "in the wording itself. Never act out cat actions or sounds.")

    @property
    def path(self) -> Path:
        return data_dir() / "config.json"

    def key_for(self, provider: str | None = None) -> str:
        p = provider or self.provider
        if p == "gemini":
            return self.gemini_key
        return str((self.keys or {}).get(p, ""))

    def set_key(self, provider: str, key: str) -> None:
        if provider == "gemini":
            self.gemini_key = key
        else:
            self.keys = {**(self.keys or {}), provider: key}

    @property
    def ready(self) -> bool:
        if self.provider == "ollama":
            return True                   # local: no key needed
        return bool(self.key_for().strip())

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
    if not isinstance(s.keys, dict):
        s.keys = {}
    if not s.onboarded and cfg.exists() and s.ready:
        s.onboarded = True                # set up before the welcome existed
    from .providers import PRESETS
    if s.provider not in PRESETS:
        s.provider = "gemini"
    return s
