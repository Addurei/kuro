"""Knowing what you're doing (from app + window title only) and reacting politely."""
import pytest

from app.activity import PROPS, Activity, Tracker, Typing, detect


@pytest.mark.parametrize("app, title, kind, subject, project", [
    ("Code", "controller.py - Kuro - Visual Studio Code", "coding", "controller", "Kuro"),
    ("pycharm64", "Kuro – main.py", "coding", "main", "Kuro"),
    ("WINWORD", "Thesis Chapter 2.docx - Word", "writing", "Thesis Chapter 2", ""),
    ("blender", r"Blender [C:\Users\user\StThomas3.blend]", "designing", "StThomas3", ""),
    ("chrome", "Thesis - Google Docs - Google Chrome", "writing", "Thesis", ""),
    ("chrome", "Two Sum - LeetCode - Google Chrome", "coding", "Two Sum", ""),
    ("opera", "(3) Hate Me - YouTube - Opera", "watching", "Hate Me", ""),
    ("Discord", "#general | Server - Discord", "chatting", "#general | Server", ""),
    ("chrome", "New Tab - Google Chrome", "browsing", "", ""),
    ("explorer", "Downloads", "other", "", ""),
])
def test_detect(app, title, kind, subject, project):
    a = detect(app, title)
    assert (a.kind, a.subject, a.project) == (kind, subject, project)


def test_props_follow_the_activity():
    assert PROPS["coding"] == "glasses" and PROPS["writing"] == "pencil"
    assert PROPS["designing"] == "beret" and "watching" not in PROPS


def test_tracker_waits_greets_once_and_never_nags():
    tr = Tracker(settle=20, gap=15 * 60)
    code = Activity("coding", "VS Code", "brain", "Kuro")
    assert tr.update(code, now=0) == ""              # just switched: wait
    assert tr.update(code, now=10) == ""
    line = tr.update(code, now=21)                   # held it: one greeting
    assert "Kuro" in line or "Glasses" in line or "duck" in line
    assert tr.update(code, now=30) == ""
    # a quick alt-tab to Discord and back doesn't count as a new activity
    tr.update(Activity("chatting", "Discord"), now=40)
    assert tr.update(code, now=45) == "" and tr.current.kind == "coding"
    # switching files in the same project isn't a new greeting either
    assert tr.update(Activity("coding", "VS Code", "tools", "Kuro"), now=50) == ""
    # writing, held: but it's within 15 minutes of the last line, so quiet
    doc = Activity("writing", "Word", "Thesis")
    tr.update(doc, now=60)
    assert tr.update(doc, now=90) == "" and tr.current.kind == "writing"


def test_long_session_check_in_once():
    tr = Tracker(settle=0, gap=0)
    code = Activity("coding", "VS Code", "", "Kuro")
    tr.update(code, now=0)
    tr.update(code, now=1)
    tr._since = 0
    assert "45 minutes" in tr.update(code, now=45 * 60 + 2)
    assert tr.update(code, now=46 * 60) == ""


def test_typing_is_counted_not_read():
    t = Typing()
    for i in range(6):
        t.times.append(100 + i * 0.2)
    assert t.rate(window=1.5, now=101.2) == pytest.approx(5 / 1.5, rel=0.3)
    assert t.rate(now=200) == 0
    assert not hasattr(t, "keys")                    # only timestamps are kept
