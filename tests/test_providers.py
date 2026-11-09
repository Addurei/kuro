"""Tests for the Gemini brain, against a mocked Gemini server that speaks
the real generateContent format."""
import json

import httpx
import pytest

from app import providers as P
from app.brain import Brain
from app.config import Settings, load_settings
from app.reminders import ReminderStore
from app.tools import build_tools


class FakeGemini:
    """Stands in for httpx.post; records requests, replays responses."""

    def __init__(self, monkeypatch, replies):
        self.replies = list(replies)
        self.requests = []
        monkeypatch.setattr(P.httpx, "post", self.post)

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers or {}})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        status, body = r
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def answer(*parts, finish="STOP"):
    return 200, {"candidates": [{"content": {"role": "model", "parts": list(parts)},
                                 "finishReason": finish}]}


def gerr(status, state, msg):
    return status, {"error": {"code": status, "status": state, "message": msg}}


# ------------------------------------------------------------- schema
def test_schema_is_gemini_friendly(tmp_path):
    tools = build_tools(ReminderStore(tmp_path / "r.json"), [])
    decls = P.gemini_tools([t.as_api() for t in tools.values()])[0]["functionDeclarations"]
    by_name = {d["name"]: d for d in decls}
    assert "parameters" not in by_name["system_info"]          # no empty OBJECT
    vol = by_name["set_volume"]["parameters"]
    assert vol["type"] == "OBJECT"
    assert vol["properties"]["action"]["type"] == "STRING"
    assert vol["properties"]["action"]["enum"] == ["up", "down", "mute", "set"]
    assert "required" in vol and "additionalProperties" not in json.dumps(decls)


# ------------------------------------------------------------- end to end
def test_tool_loop_end_to_end_with_thought_signature(monkeypatch, tmp_path):
    srv = FakeGemini(monkeypatch, [
        answer({"functionCall": {"name": "list_reminders", "args": {}},
                "thoughtSignature": "c2lnbmF0dXJl"}),
        answer({"text": "thinking...", "thought": True},
               {"text": "You have no reminders. Mrrp!"}),
    ])
    tools = build_tools(ReminderStore(tmp_path / "r.json"), [])
    brain = Brain(Settings(gemini_key="AIza-test"), tools, lambda t, a: True)
    assert brain.ask("any reminders?") == "You have no reminders. Mrrp!"

    first, second = srv.requests
    assert first["url"].endswith("/models/gemini-3.5-flash:generateContent")
    assert first["headers"]["x-goog-api-key"] == "AIza-test"
    assert "AIza" not in first["url"]                              # key not in the URL
    assert first["json"]["systemInstruction"]["parts"][0]["text"].startswith("You are Kuro")
    assert first["json"]["contents"] == [{"role": "user", "parts": [{"text": "any reminders?"}]}]
    model_turn, tool_turn = second["json"]["contents"][1:]
    assert model_turn["parts"][0]["thoughtSignature"] == "c2lnbmF0dXJl"   # echoed back
    fr = tool_turn["parts"][0]["functionResponse"]
    assert fr["name"] == "list_reminders" and "No reminders" in fr["response"]["result"]


def test_falls_back_to_next_free_model(monkeypatch):
    srv = FakeGemini(monkeypatch, [
        gerr(429, "RESOURCE_EXHAUSTED", "Quota exceeded, limit: 0"),
        answer({"text": "OK"}),
        answer({"text": "still here"}),
    ])
    p = P.GeminiProvider("key")
    assert p.test() == "gemini-3.1-flash-lite"
    p.complete("hi")
    assert srv.requests[2]["url"].endswith("gemini-3.1-flash-lite:generateContent")  # sticks


@pytest.mark.parametrize("reply,kind", [
    (gerr(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), "auth"),
    (gerr(403, "PERMISSION_DENIED", "Method doesn't allow unregistered callers"), "auth"),
    (gerr(400, "FAILED_PRECONDITION", "User location is not supported for the API use."), "blocked"),
    (gerr(400, "INVALID_ARGUMENT", "Invalid JSON payload"), "bad_request"),
])
def test_errors_are_classified_without_retrying(monkeypatch, reply, kind):
    srv = FakeGemini(monkeypatch, [reply])
    with pytest.raises(P.ProviderError) as e:
        P.GeminiProvider("key").test()
    assert e.value.kind == kind and len(srv.requests) == 1
    assert e.value.detail                                          # Google's words kept


def test_all_models_busy_reports_rate(monkeypatch):
    FakeGemini(monkeypatch, [gerr(503, "UNAVAILABLE", "overloaded")] * 3)
    with pytest.raises(P.ProviderError) as e:
        P.GeminiProvider("key").test()
    assert e.value.kind == "rate"


def test_offline_message(monkeypatch):
    FakeGemini(monkeypatch, [httpx.ConnectError("no route")])
    brain = Brain(Settings(gemini_key="k"), {}, lambda t, a: True)
    assert "can't reach Gemini" in brain.ask("hi")


def test_blocked_prompt_and_cut_off_answers():
    with pytest.raises(P.ProviderError) as e:
        P.from_gemini({"promptFeedback": {"blockReason": "SAFETY"}})
    assert e.value.kind == "blocked"
    r = P.from_gemini({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]})
    assert "cut off" in r.content[0]["text"]


# ------------------------------------------------------------- config
def test_config_migrates_v03_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (tmp_path / "Kuro").mkdir()
    (tmp_path / "Kuro" / "config.json").write_text(json.dumps(
        {"provider": "gemini", "keys": {"gemini": "AIza-old"},
         "models": {"gemini": "gemini-3.8-flash"}, "user_name": "Drey"}))
    s = load_settings()
    assert s.gemini_key == "AIza-old" and s.model == "" and s.ready
    (tmp_path / "Kuro" / "config.json").write_text("{broken")
    assert not load_settings().ready


def test_light_calls_start_on_lite_model_and_dont_move_the_main_one(monkeypatch):
    srv = FakeGemini(monkeypatch, [answer({"text": "{}"}), answer({"text": "hi"})])
    p = P.GeminiProvider("key")
    p.complete("reflect", light=True)
    assert "flash-lite" in srv.requests[0]["url"]
    p.complete("chat")
    assert srv.requests[1]["url"].endswith("gemini-3.5-flash:generateContent")
