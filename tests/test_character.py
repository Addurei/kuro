"""One engine, many souls: Kuro by default, or whoever CHARACTER.json says."""
import json
import subprocess
import sys
from pathlib import Path

from app.character import CHARACTERS, character_key, load_custom

ROOT = Path(__file__).resolve().parent.parent

# a fictional custom character used only by these tests
MOCHI = {
    "name": "Mochi", "folder": "Mochi", "fur_word": "cream",
    "pronouns": ["she", "her", "her"],
    "accent": [255, 170, 60], "accent_hex": "#ffaa3c",
    "ribbon": True,
    "wake_words": ["mochi", "mochee"], "wake_stem": "moch",
    "fur": {"LIT": [250, 240, 228]},
    "eyes": {"EYE": [120, 190, 255], "PUPIL_K": 0.8},
}


def test_the_character_file_decides(tmp_path):
    assert character_key(tmp_path) == "kuro"              # no file: Kuro
    (tmp_path / "CHARACTER").write_text("garfield")
    assert character_key(tmp_path) == "kuro"              # unknown: safe default


def test_custom_json_merges_over_kuro(tmp_path):
    assert load_custom(tmp_path) is None                  # no file: no custom
    (tmp_path / "CHARACTER.json").write_text(json.dumps(MOCHI))
    c = load_custom(tmp_path)
    assert c is not None
    # her own values, as tuples where the rig needs tuples
    assert c["name"] == "Mochi" and c["ribbon"] is True
    assert c["accent"] == (255, 170, 60)
    assert c["pronouns"] == ("she", "her", "her")
    assert c["fur"]["LIT"] == (250, 240, 228)
    assert c["eyes"]["EYE"] == (120, 190, 255) and c["eyes"]["PUPIL_K"] == 0.8
    # everything she didn't set falls back to Kuro's values
    assert c["fur"]["MID"] == CHARACTERS["kuro"]["fur"]["MID"]
    assert c["face"] == CHARACTERS["kuro"]["face"]
    assert set(CHARACTERS["kuro"]) <= set(c)              # no missing keys, ever


def test_broken_custom_json_is_ignored(tmp_path):
    (tmp_path / "CHARACTER.json").write_text("{not json")
    assert load_custom(tmp_path) is None
    (tmp_path / "CHARACTER.json").write_text(json.dumps({"folder": "X"}))  # no name
    assert load_custom(tmp_path) is None


def _run_fresh_app(tmp_path, setup):
    """Run the app's modules in a fresh process with a character marker set."""
    code = (
        "import os, sys\n"
        "os.environ['QT_QPA_PLATFORM']='offscreen'\n"
        f"os.environ['APPDATA']={str(tmp_path)!r}\n"
        "from app.ui import cat_rig, theme\n"
        "from app.config import Settings, data_dir\n"
        "import app.voice as V\n"
        "s = Settings()\n"
        "print(theme.LIME_HEX)\n"
        "print(cat_rig.RIBBON)\n"
        "print(s.cat_name)\n"
        "print(data_dir().name)\n"
        "print(V.WAKE_WORDS[0])\n"
    )
    marker = ROOT / "CHARACTER.json"
    had = marker.read_text() if marker.exists() else None
    try:
        if setup is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_text(json.dumps(setup))
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                             text=True, timeout=120)
    finally:
        marker.write_text(had) if had is not None else marker.unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_the_whole_app_follows_the_default_character(tmp_path):
    hex_, ribbon, name, folder, wake = _run_fresh_app(tmp_path, None)
    assert hex_ == "#bae25c" and ribbon == "False"
    assert name == "Kuro" and folder == "Kuro" and wake == "kuro"


def test_the_whole_app_follows_a_custom_character(tmp_path):
    hex_, ribbon, name, folder, wake = _run_fresh_app(tmp_path, MOCHI)
    assert hex_ == "#ffaa3c" and ribbon == "True"
    assert name == "Mochi" and folder == "Mochi" and wake == "mochi"


def test_wake_words_follow_the_character(tmp_path, monkeypatch):
    import app.voice as V
    monkeypatch.setattr(V, "WAKE_WORDS", ("mochi", "mochee"))
    monkeypatch.setattr(V, "_WAKE_STEM", "moch")
    assert V.find_wake("hey mochi, play music") == (True, "play music")
    assert V.find_wake("okay mochee what's up") == (True, "what's up")
    assert V.find_wake("hey kuro open spotify")[0] is False


def test_updates_never_overwrite_the_character_files():
    from app.updater import safe_member
    assert not safe_member("CHARACTER")
    assert not safe_member("CHARACTER.json")


def test_custom_icon_lives_in_the_character_folder(tmp_path, monkeypatch):
    """A custom character's art sits in character/, which updates never touch;
    without it the shipped icon is used."""
    import app.character as C
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "icon.png").write_bytes(b"png")
    monkeypatch.setitem(C.CURRENT, "icon", "mochi.png")
    assert C.icon_path(tmp_path) == tmp_path / "assets" / "icon.png"      # fallback
    (tmp_path / "character").mkdir()
    (tmp_path / "character" / "mochi.png").write_bytes(b"png")
    assert C.icon_path(tmp_path) == tmp_path / "character" / "mochi.png"


def test_updates_never_touch_the_character_folder():
    from app.updater import safe_member
    assert not safe_member("character/mochi.png")
    assert not safe_member("character/installer/small-55.bmp")


def test_installer_builds_any_character_from_one_script():
    iss = (ROOT / "installer" / "kuro.iss").read_text()
    for needle in ("#ifndef CatName", "AppName={#CatName}", "OutputBaseFilename={#CatName}Setup",
                   "#ifdef CatDir", 'DestDir: "{app}\\character"'):
        assert needle in iss
    for s in (55, 64, 83, 110, 138):                      # Kuro's wizard art ships in the repo
        assert (ROOT / "installer" / "art" / f"small-{s}.bmp").is_file()
