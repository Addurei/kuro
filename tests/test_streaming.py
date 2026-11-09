"""Streaming replies: every brain, tool calls mid-stream, fallbacks, and what the user sees."""
import json

import pytest

from app import providers as P
from app.brain import Brain, live_text
from app.config import Settings
from app.tools import Tool


class FakeStream:
    """Stands in for httpx.stream: replays SSE lines, records the request."""

    def __init__(self, scripts):
        self.scripts, self.calls = list(scripts), []

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        status, lines = self.scripts.pop(0)
        outer = self

        class R:
            status_code = status
            text = lines if isinstance(lines, str) else ""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

            def iter_lines(self):
                yield from (lines if isinstance(lines, list) else [])
        return R()


def sse(*objs):
    return [f"data: {json.dumps(o)}" for o in objs] + ["data: [DONE]"]


def tools():
    return {"list_reminders": Tool("list_reminders", "List reminders",
                                   {"type": "object", "properties": {}},
                                   lambda: "No reminders set.")}


def run(brain, text="hi"):
    seen = []
    out = brain.ask(text, on_text=seen.append)
    return out, seen


def test_openai_compatible_streams_words_and_tools(monkeypatch):
    fake = FakeStream([
        (200, sse({"choices": [{"delta": {"content": "Let me "}}]},
                  {"choices": [{"delta": {"content": "check."}}]},
                  {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                      "name": "list_reminders", "arguments": ""}}]}}]},
                  {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
                      "arguments": "{}"}}]}}]})),
        (200, sse({"choices": [{"delta": {"content": "<think>hmm</think>You have "}}]},
                  {"choices": [{"delta": {"content": "no reminders! *wags tail*"}}]})),
    ])
    monkeypatch.setattr(P.httpx, "stream", fake)
    s = Settings(provider="groq", keys={"groq": "gsk"}, model="llama-3.3-70b-versatile")
    out, seen = run(Brain(s, tools(), lambda t, a: True))
    assert out == "Let me check. You have no reminders!"
    assert seen[0] == "Let me" and seen[-1] == out          # grows word by word, ends clean
    assert all("think" not in x and "*" not in x for x in seen)
    assert fake.calls[0]["json"]["stream"] is True
    assert fake.calls[1]["json"]["messages"][-1] == {
        "role": "tool", "tool_call_id": "c1", "content": "No reminders set."}


def test_gemini_streams_and_keeps_signatures(monkeypatch):
    part = lambda **k: {"candidates": [{"content": {"parts": [k]}}]}
    fake = FakeStream([
        (200, sse(part(functionCall={"name": "list_reminders", "args": {}},
                       thoughtSignature="c2ln"))),
        (200, sse(part(text="Nothing "), part(text="planned today."))),
    ])
    monkeypatch.setattr(P.httpx, "stream", fake)
    s = Settings(provider="gemini", gemini_key="AIza")
    out, seen = run(Brain(s, tools(), lambda t, a: True))
    assert out == "Nothing planned today." and seen == ["Nothing", "Nothing planned today."]
    assert ":streamGenerateContent?alt=sse" in fake.calls[0]["url"]
    model_turn = fake.calls[1]["json"]["contents"][1]
    assert model_turn["parts"][0]["thoughtSignature"] == "c2ln"     # echoed back


def test_claude_streams(monkeypatch):
    monkeypatch.setattr(P.ClaudeProvider, "models", property(lambda self: ["claude-haiku-4-5"]))
    fake = FakeStream([(200, sse(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Drey!"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}))])
    monkeypatch.setattr(P.httpx, "stream", fake)
    out, seen = run(Brain(Settings(provider="claude", keys={"claude": "sk"}), {}, lambda t, a: True))
    assert out == "Hi Drey!" and seen[-1] == "Hi Drey!"


def test_out_of_quota_before_any_words_falls_back(monkeypatch):
    fake = FakeStream([
        (429, '{"error":{"message":"quota","status":"RESOURCE_EXHAUSTED"}}'),   # gemini m1
        (429, '{"error":{"message":"quota","status":"RESOURCE_EXHAUSTED"}}'),   # gemini m2
        (429, '{"error":{"message":"quota","status":"RESOURCE_EXHAUSTED"}}'),   # gemini m3
        (200, sse({"choices": [{"delta": {"content": "Groq here!"}}]})),
    ])
    monkeypatch.setattr(P.httpx, "stream", fake)
    switched = []
    s = Settings(provider="gemini", gemini_key="AIza", keys={"groq": "gsk"})
    b = Brain(s, {}, lambda t, a: True, on_switch=lambda a, c: switched.append(c))
    monkeypatch.setattr(P.OpenAIProvider, "models", property(lambda self: ["llama"]))
    out, _ = run(b)
    assert out == "Groq here!" and switched == ["Groq"]


def test_live_text_never_shows_half_actions():
    assert live_text("Sure! *wag") == "Sure!"
    assert live_text("Sure! *wags tail* Opening") == "Sure! Opening"
    assert live_text("Okay (stre") == "Okay"
    assert live_text("2*3 = 6") == "2"          # odd '*' is held until it resolves
    assert live_text("2*3*4 = 24") == "2*3*4 = 24"


# ------------------------------------------------------------ voice in
def test_whisper_engines_follow_your_keys_and_fall_back(monkeypatch):
    from app import voice as V
    s = Settings(provider="gemini", gemini_key="AIza", keys={"groq": "gsk", "openai": "sk"})
    L = V.Listener.__new__(V.Listener)
    L.s, L.available, L.last_engine = s, True, ""
    assert [e[0] for e in L.engines()] == ["Groq Whisper", "OpenAI Whisper"]

    class Audio:
        def get_wav_data(self, **k):
            return b"RIFFwav"
    calls = []

    def fake(wav, base, key, model, language="", timeout=30):
        calls.append((base, model, language))
        if "groq" in base:
            raise RuntimeError("HTTP 429: quota")
        return "buksan mo ang Spotify"
    monkeypatch.setattr(V, "transcribe_whisper", fake)
    s.language = "Filipino"
    assert L.transcribe(Audio()) == "buksan mo ang Spotify"
    assert L.last_engine == "OpenAI Whisper" and calls[0][2] == "tl"


def test_whisper_request_and_phantom_filter(monkeypatch):
    from app import voice as V
    sent = {}

    class R:
        status_code = 200

        def __init__(self, text):
            self._t = text

        def json(self):
            return {"text": self._t}
    texts = iter(["Open Blender please.", "Thank you."])

    def post(url, **k):
        sent.update(url=url, **k)
        return R(next(texts))
    import httpx
    monkeypatch.setattr(httpx, "post", post)
    out = V.transcribe_whisper(b"wav", "https://api.groq.com/openai/v1", "gsk",
                               "whisper-large-v3-turbo")
    assert out == "Open Blender please."
    assert sent["url"].endswith("/audio/transcriptions")
    assert sent["data"]["model"] == "whisper-large-v3-turbo" and "Kuro" in sent["data"]["prompt"]
    assert V.transcribe_whisper(b"wav", "https://x", "k", "m") == ""   # phantom dropped
