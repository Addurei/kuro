"""Focus, study, briefing, quick actions and wake word."""
import json
from datetime import datetime

import pytest

from app import briefing, quick, study
from app.focus import FocusTimer
from app.voice import find_wake


# ------------------------------------------------------------- focus
def test_focus_cycles_and_stats(tmp_path):
    f = FocusTimer(tmp_path / "focus.json")
    f.start(25, 5, cycles=2, now=0)
    assert f.phase == "focus" and f.remaining(now=60) == 24 * 60
    assert f.tick(now=100) is None
    ev = f.tick(now=25 * 60)
    assert ev.kind == "break" and f.phase == "break"
    assert 0.49 < f.progress(now=25 * 60 + 150) < 0.51
    assert f.tick(now=30 * 60).kind == "focus"
    ev = f.tick(now=55 * 60)
    assert ev.kind == "done" and ev.session == 2 and not f.active
    assert f.today() == (50, 2)
    assert FocusTimer(tmp_path / "focus.json").today() == (50, 2)     # persisted


def test_focus_stop_counts_partial_minutes(tmp_path):
    f = FocusTimer(tmp_path / "focus.json")
    f.start(25, 5, now=0)
    assert f.stop(now=10 * 60 + 30) == 10 and not f.active
    assert f.today() == (10, 0)
    assert "No focus session" in f.describe()


# ------------------------------------------------------------- study
NOTES = ("Photosynthesis happens in the chloroplasts of plant cells. It uses sunlight, water "
         "and carbon dioxide to make glucose and oxygen. Chlorophyll is the green pigment that "
         "absorbs light. The light reactions happen in the thylakoids and the Calvin cycle "
         "happens in the stroma.")


def test_quiz_is_validated_and_shuffled():
    good = [{"q": f"Question {i}?", "options": ["A", "B", "C", "D"], "answer": 1,
             "why": "Because."} for i in range(5)]
    bad = [{"q": "Two options?", "options": ["A", "B"], "answer": 0},
           {"q": "Dupes?", "options": ["A", "A", "B", "C"], "answer": 0},
           {"q": "Bad answer?", "options": ["A", "B", "C", "D"], "answer": 7},
           {"q": "Question 0?", "options": ["A", "B", "C", "D"], "answer": 0}]
    raw = "Here you go:\n" + json.dumps(good + bad)
    items = study.make(lambda p: raw, NOTES, "quiz", 5)
    assert len(items) == 5
    for it in items:
        assert it["options"][it["answer"]] == "B"        # still the right answer after shuffle


def test_cards_and_errors():
    cards = json.dumps([{"front": f"Term {i}", "back": "Meaning"} for i in range(4)])
    assert len(study.make(lambda p: cards, NOTES, "cards", 4)) == 4
    with pytest.raises(study.StudyError):
        study.make(lambda p: cards, "too short", "cards")
    with pytest.raises(study.StudyError):
        study.make(lambda p: "sorry, no", NOTES, "quiz")


def test_study_retries_once_when_too_few():
    calls = []
    good = json.dumps([{"q": f"Q{i}?", "options": ["w", "x", "y", "z"], "answer": 2}
                       for i in range(6)])

    def complete(p):
        calls.append(p)
        return "[]" if len(calls) == 1 else good
    assert len(study.make(complete, NOTES, "quiz", 6)) == 6 and len(calls) == 2


# ------------------------------------------------------------- briefing
def test_briefing_compose_skips_missing_parts():
    now = datetime(2026, 11, 9, 8, 30)
    text = briefing.compose("Drey", now, weather="27°C, cloudy.", reminders=["Thesis at 3 PM"],
                            focus_yesterday=(75, 3))
    assert text.startswith("Morning, Drey! It's Monday, November 9.")
    for bit in ("27°C", "Thesis at 3 PM", "75 min over 3"):
        assert bit in text
    assert briefing.compose("Drey", datetime(2026, 11, 9, 20)).startswith("Evening")
    assert "\n" not in briefing.compose("Drey", now)


# ------------------------------------------------------------- quick + wake
def test_quick_prompts():
    p = quick.prompt_for("translate", "Kumusta ka?", "Japanese")
    assert "Japanese" in p and "Kumusta ka?" in p
    assert quick.REPLACEABLE <= set(quick.ACTIONS)


@pytest.mark.parametrize("heard,hit,rest", [
    ("hey Kuro open Spotify", True, "open Spotify"),
    ("Kuro", True, ""),
    ("hey kurro, what's the weather", True, "what's the weather"),
    ("I like curry", False, ""),
    ("the weather today", False, "")])
def test_wake_word_matching(heard, hit, rest):
    assert find_wake(heard) == (hit, rest)


def test_fullscreen_check_is_safe_off_windows():
    from app import winutil
    assert winutil.fullscreen_app() in (True, False)


def test_google_integration_is_gone():
    """Calendar + Gmail needed every user to make a Google Cloud project; removed."""
    import importlib.util
    from app.config import Settings
    assert importlib.util.find_spec("app.google") is None
    assert not any(f.startswith("google_") for f in Settings.__dataclass_fields__)


def test_old_configs_with_google_fields_still_load(tmp_path, monkeypatch):
    import app.config as C
    monkeypatch.setattr(C, "data_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(
        {"user_name": "Drey", "google_client_id": "x.apps.googleusercontent.com",
         "google_client_secret": "s"}))
    s = C.load_settings()
    assert s.user_name == "Drey" and not hasattr(s, "google_client_id")


def test_the_old_google_token_is_cleaned_up(tmp_path, monkeypatch):
    import app.config as C
    from app import housekeeping
    monkeypatch.setattr(C, "data_dir", lambda: tmp_path)
    (tmp_path / "google.token").write_bytes(b"secret")
    assert housekeeping.retired_files() == [tmp_path / "google.token"]
