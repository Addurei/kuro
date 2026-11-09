"""Focus, study, briefing, Google, quick actions and wake word."""
import json
from datetime import date, datetime, timedelta

import httpx
import pytest

from app import briefing, google as G, quick, study
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
                            events=["9:00 AM Class"], unread=["Prof: Defense schedule"],
                            focus_yesterday=(75, 3))
    assert text.startswith("Morning, Drey! It's Monday, November 9.")
    for bit in ("27°C", "Thesis at 3 PM", "9:00 AM Class", "1 unread", "75 min over 3"):
        assert bit in text
    assert briefing.compose("Drey", datetime(2026, 11, 9, 20)).startswith("Evening")
    assert "\n" not in briefing.compose("Drey", now)


# ------------------------------------------------------------- google
def test_token_storage_roundtrip(tmp_path):
    g = G.Google(tmp_path / "t", "cid", "sec")
    g._tok = {"refresh_token": "r1", "access_token": "a1", "expires_at": 9e12, "email": "d@x.com"}
    g._save()
    assert b"r1" not in (tmp_path / "t").read_bytes()
    g2 = G.Google(tmp_path / "t", "cid", "sec")
    assert g2.connected and g2.email == "d@x.com"


def fake_google(monkeypatch, routes):
    seen = []

    def request(method, url, headers=None, timeout=None, **kw):
        seen.append((method, url, kw))
        for part, body in routes.items():
            if part in url:
                return httpx.Response(200, json=body, request=httpx.Request(method, url))
        return httpx.Response(404, json={"error": {"message": "nope"}},
                              request=httpx.Request(method, url))
    monkeypatch.setattr(G.httpx, "request", request)
    return seen


def connected(tmp_path):
    g = G.Google(tmp_path / "t", "cid", "sec")
    g._tok = {"refresh_token": "r", "access_token": "a", "expires_at": 9e12}
    return g


def test_calendar_events_and_add(monkeypatch, tmp_path):
    seen = fake_google(monkeypatch, {"/events": {"items": [
        {"summary": "Thesis defense", "start": {"dateTime": "2026-11-09T15:00:00+08:00"},
         "location": "Room 3"},
        {"summary": "Holiday", "start": {"date": "2026-11-09"}}]}})
    g = connected(tmp_path)
    evs = g.events(1, datetime(2026, 11, 9).astimezone())
    assert [e["title"] for e in evs] == ["Thesis defense", "Holiday"]
    assert "Thesis defense @ Room 3" in G.fmt_event(evs[0]) and "(all day)" in G.fmt_event(evs[1])
    g.add_event("Study", datetime(2026, 11, 10, 14, 0), 45)
    body = seen[-1][2]["json"]
    start = datetime.fromisoformat(body["start"]["dateTime"])
    end = datetime.fromisoformat(body["end"]["dateTime"])
    assert end - start == timedelta(minutes=45) and body["summary"] == "Study"


def test_gmail_is_read_only_listing(monkeypatch, tmp_path):
    fake_google(monkeypatch, {
        "/messages/m1": {"snippet": "Your defense is on Friday", "payload": {"headers": [
            {"name": "From", "value": "\"Prof Cruz\" <cruz@ust.edu>"},
            {"name": "Subject", "value": "Defense schedule"}]}},
        "/messages": {"messages": [{"id": "m1"}]}})
    mails = connected(tmp_path).emails(count=3)
    assert G.fmt_email(mails[0]) == "Prof Cruz: Defense schedule"
    assert all("send" not in s for s in G.SCOPES) and "gmail.readonly" in G.SCOPES[1]


def test_google_errors_are_friendly(monkeypatch, tmp_path):
    with pytest.raises(G.GoogleError, match="isn't connected"):
        G.Google(tmp_path / "none", "cid").events()
    with pytest.raises(G.GoogleError, match="client ID"):
        G.Google(tmp_path / "none").connect()


def test_oauth_flow_with_local_redirect(monkeypatch, tmp_path):
    import urllib.parse
    import urllib.request

    def open_browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"] and "gmail.readonly" in q["scope"][0]
        back = f"{q['redirect_uri'][0]}/?code=abc&state={q['state'][0]}"
        import threading
        threading.Thread(target=lambda: urllib.request.urlopen(back).read(), daemon=True).start()

    payload = __import__("base64").urlsafe_b64encode(b'{"email":"drey@gmail.com"}').decode()

    def post(url, data=None, timeout=None):
        assert data["code"] == "abc" and data["code_verifier"]
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r",
                                         "expires_in": 3600, "id_token": f"x.{payload}.y"},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(G.httpx, "post", post)
    g = G.Google(tmp_path / "tok", "cid", "sec")
    assert g.connect(timeout=10, open_browser=open_browser) == "drey@gmail.com"
    assert G.Google(tmp_path / "tok", "cid", "sec").connected


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
