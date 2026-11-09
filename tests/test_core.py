"""Tests for the parts that don't need Windows or a real API key.

Run:  python -m pytest
"""
import copy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.brain import Brain
from app.providers import ProviderError
from app.config import Settings
from app.reminders import ReminderStore
from app.tools import Tool, build_tools, run_tool, search_files


# ---------------------------------------------------------------- reminders
def test_reminder_add_and_pop(tmp_path):
    store = ReminderStore(tmp_path / "r.json")
    now = datetime(2026, 11, 7, 9, 0)
    store.add("stretch", in_minutes=10, now=now)
    store.add("submit thesis", at="2026-11-07T12:00", now=now)
    assert [r.message for r in store.all()] == ["stretch", "submit thesis"]

    assert store.pop_due(now + timedelta(minutes=5)) == []
    due = store.pop_due(now + timedelta(minutes=11))
    assert [r.message for r in due] == ["stretch"]
    assert len(store.all()) == 1


def test_reminders_survive_restart(tmp_path):
    path = tmp_path / "r.json"
    ReminderStore(path).add("water the plant", in_minutes=30)
    assert ReminderStore(path).all()[0].message == "water the plant"


def test_reminder_rejects_past_and_bad_input(tmp_path):
    store = ReminderStore(tmp_path / "r.json")
    now = datetime(2026, 11, 7, 9, 0)
    with pytest.raises(ValueError):
        store.add("x", at="2026-11-06T09:00", now=now)
    with pytest.raises(ValueError):
        store.add("x", in_minutes=-5, now=now)
    with pytest.raises(ValueError):
        store.add("x", now=now)


def test_cancel_reminder(tmp_path):
    store = ReminderStore(tmp_path / "r.json")
    r = store.add("x", in_minutes=5)
    assert store.cancel(r.id)
    assert not store.cancel(r.id)


def test_corrupt_reminder_file_does_not_crash(tmp_path):
    path = tmp_path / "r.json"
    path.write_text("{not json")
    assert ReminderStore(path).all() == []


# -------------------------------------------------------------------- tools
def test_search_files(tmp_path):
    (tmp_path / "thesis").mkdir()
    (tmp_path / "thesis" / "Chapter1_draft.docx").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "chapter_junk.js").write_text("")
    out = search_files("chapter", [str(tmp_path)])
    assert "Chapter1_draft.docx" in out
    assert "node_modules" not in out


def test_run_tool_never_raises():
    boom = Tool("boom", "", {}, lambda: 1 / 0)
    out, err = run_tool(boom, {})
    assert err and "ZeroDivisionError" in out
    out, err = run_tool(boom, {"unexpected": 1})
    assert err and "bad arguments" in out


def test_risky_tools_are_marked(tmp_path):
    tools = build_tools(ReminderStore(tmp_path / "r.json"), [], allow_shell=True)
    assert tools["run_powershell"].risky
    assert tools["power"].risky
    assert not tools["open_app"].risky
    assert "run_powershell" not in build_tools(
        ReminderStore(tmp_path / "r2.json"), [], allow_shell=False)


def test_tool_schemas_are_valid_for_api(tmp_path):
    for t in build_tools(ReminderStore(tmp_path / "r.json"), []).values():
        api = t.as_api()
        assert api["input_schema"]["type"] == "object"
        assert set(api["input_schema"]["required"]) <= set(api["input_schema"]["properties"])


# -------------------------------------------------------------------- brain
class FakeProvider:
    """Plays back scripted model responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, persona, context, tools, messages, max_tokens):
        self.calls.append(copy.deepcopy({"messages": messages, "tools": tools}))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def text_resp(t):
    return SimpleNamespace(stop_reason="end_turn",
                           content=[{"type": "text", "text": t}])


def tool_resp(name, args, tid="tu_1"):
    return SimpleNamespace(stop_reason="tool_use", content=[
        {"type": "text", "text": "On it."},
        {"type": "tool_use", "id": tid, "name": name, "input": args}])


def make_brain(tmp_path, responses, confirm=lambda t, a: True):
    tools = build_tools(ReminderStore(tmp_path / "r.json"), [str(tmp_path)])
    client = FakeProvider(responses)
    return Brain(Settings(gemini_key="x"), tools, confirm, provider=client), client


def test_plain_reply(tmp_path):
    brain, _ = make_brain(tmp_path, [text_resp("Mrrp! Hi.")])
    assert brain.ask("hello") == "Mrrp! Hi."


def test_tool_loop_runs_tool_and_returns_result(tmp_path):
    brain, client = make_brain(tmp_path, [
        tool_resp("add_reminder", {"message": "drink water", "in_minutes": 15}),
        text_resp("Done, I'll remind you."),
    ])
    assert brain.ask("remind me to drink water in 15 min") == "Done, I'll remind you."
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and not result["is_error"]
    assert brain.tools["list_reminders"].func().count("drink water") == 1


def test_risky_tool_needs_confirmation(tmp_path):
    asked = []
    brain, client = make_brain(
        tmp_path,
        [tool_resp("run_powershell", {"command": "Remove-Item C:\\x"}),
         text_resp("Okay, I won't.")],
        confirm=lambda t, a: asked.append(a) or False)
    brain.ask("delete x")
    assert asked == [{"command": "Remove-Item C:\\x"}]
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "declined" in result["content"]


def test_api_error_rolls_back_history(tmp_path):
    brain, _ = make_brain(tmp_path, [text_resp("hi"),
                                     ProviderError("connection", "down", "gemini")])
    brain.ask("one")
    n = len(brain.history)
    reply = brain.ask("two")
    assert "internet" in reply
    assert len(brain.history) == n


def test_history_trim_keeps_tool_pairs(tmp_path):
    responses = []
    for i in range(5):
        responses += [tool_resp("system_info", {}, tid=f"t{i}"), text_resp("ok")]
    brain, _ = make_brain(tmp_path, responses)
    brain.s.history_turns = 2
    for i in range(5):
        brain.ask(f"msg {i}")
    first = brain.history[0]
    assert first["role"] == "user" and isinstance(first["content"], str)
    assert first["content"] == "msg 3"


def test_missing_api_key_message(tmp_path):
    tools = build_tools(ReminderStore(tmp_path / "r.json"), [])
    brain = Brain(Settings(), tools, lambda t, a: True)   # gemini, no key yet
    assert "Settings" in brain.ask("hi")
