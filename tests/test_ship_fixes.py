"""Brain choices, honest app launching, no roleplay actions, auto-location."""
from datetime import date
from pathlib import Path

import pytest

from app import launcher as L
from app import location
from app import providers as P
from app.brain import strip_actions
from app.config import Settings


# ------------------------------------------------------------ no *actions*
@pytest.mark.parametrize("raw, clean", [
    ("*yawns* Okay, Blender is open!", "Okay, Blender is open!"),
    ("Sure thing! *wags tail*", "Sure thing!"),
    ("Done *purrs*.", "Done."),
    ("(stretches) Morning!", "Morning!"),
    ("2*3*4 = 24", "2*3*4 = 24"),                 # maths survives
    ("open my_file_name.txt", "open my_file_name.txt"),
])
def test_strip_actions(raw, clean):
    assert strip_actions(raw) == clean


def test_persona_forbids_actions():
    from app.brain import persona_prompt
    text = persona_prompt(Settings())
    assert "asterisks" in text and "mrrp" not in Settings().personality.lower()


# ------------------------------------------------------------ brains
def test_every_brain_is_offered_and_buildable():
    assert P.ORDER == ["groq", "gemini", "openrouter", "ollama", "openai", "claude"]
    assert isinstance(P.make("gemini", "k"), P.GeminiProvider)
    assert isinstance(P.make("claude", "k"), P.ClaudeProvider)
    for n in ("groq", "openrouter", "ollama", "openai"):
        assert isinstance(P.make(n, "k"), P.OpenAIProvider)


def test_settings_keys_per_brain_and_ollama_needs_none():
    s = Settings()
    s.provider = "groq"
    assert not s.ready
    s.set_key("groq", "gsk_1")
    assert s.ready and s.key_for() == "gsk_1" and s.gemini_key == ""
    s.provider = "ollama"
    assert s.ready


def test_pick_models_prefers_and_skips():
    got = P.pick_models(["whisper-large-v3", "llama-3.1-8b-instant", "llama-3.3-70b-versatile",
                         "openai/gpt-oss-120b"], P.PRESETS["groq"]["prefer"],
                        P.PRESETS["groq"]["skip"])
    assert got[0] == "openai/gpt-oss-120b" and "whisper-large-v3" not in got


def test_openai_roundtrip_with_tools(monkeypatch):
    calls = []

    def fake(method, url, headers, body=None, timeout=60, who=""):
        calls.append((method, url, body))
        if url.endswith("/models"):
            return {"data": [{"id": "llama-3.3-70b-versatile"}]}
        if len(calls) == 2:
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "list_reminders", "arguments": "{}"}}]}}]}
        return {"choices": [{"message": {"content": "<think>hm</think>No reminders!"}}]}
    monkeypatch.setattr(P, "_http", fake)
    from app.brain import Brain
    from app.tools import Tool
    tools = {"list_reminders": Tool("list_reminders", "List", {"type": "object",
                                    "properties": {}}, lambda: "No reminders set.")}
    s = Settings(provider="groq", keys={"groq": "gsk"})
    b = Brain(s, tools, lambda t, a: True)
    assert b.ask("any reminders?") == "No reminders!"
    second = calls[2][2]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": "No reminders set."}
    assert calls[1][2]["tools"][0]["function"]["parameters"]["type"] == "object"


def test_claude_uses_native_blocks(monkeypatch):
    sent = []

    def fake(method, url, headers, body=None, timeout=60, who=""):
        if url.endswith("/models?limit=100"):
            return {"data": [{"id": "claude-sonnet-4-5"}, {"id": "claude-haiku-4-5"}]}
        sent.append(body)
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Hi Drey."}]}
    monkeypatch.setattr(P, "_http", fake)
    c = P.ClaudeProvider("sk-ant")
    r = c.chat("persona", "ctx", [], [{"role": "user", "content": "hi"}], 100)
    assert r.content[0]["text"] == "Hi Drey." and sent[0]["model"] == "claude-haiku-4-5"


def test_ollama_down_says_so(monkeypatch):
    def fake(*a, **k):
        raise P.ProviderError("connection", "can't connect", "refused")
    monkeypatch.setattr(P, "_http", fake)
    o = P.OpenAIProvider("ollama", model="qwen3:8b")
    with pytest.raises(P.ProviderError) as e:
        o.complete("hi")
    assert "Ollama isn't running" in e.value.detail


# ------------------------------------------------------------ launching apps
def test_resolve_finds_start_menu_shortcut_fuzzily(tmp_path):
    lnks = [("Unity Hub", tmp_path / "Unity Hub.lnk"), ("Blender 4.2", tmp_path / "Blender 4.2.lnk"),
            ("Uninstall Blender", tmp_path / "x.lnk")]
    cands, words = L.resolve("blender", lnks=lnks, store=[], which=lambda _n: None)
    assert cands[0].name == "Blender 4.2" and cands[0].kind == "lnk"
    assert "blender" in words
    cands, _ = L.resolve("unity", lnks=lnks, store=[], which=lambda _n: None)
    assert cands[0].name == "Unity Hub"


def test_resolve_store_app_and_alias():
    cands, words = L.resolve("calculator", lnks=[], store=[("Calculator", "Microsoft.Calc!App")],
                             which=lambda n: r"C:\Windows\System32\calc.exe" if n == "calc.exe" else None)
    assert cands[0].kind == "path" and "calculator" in words
    cands, _ = L.resolve("whatsapp", lnks=[], store=[("WhatsApp", "5319275A.WhatsApp!App")],
                         which=lambda _n: None)
    assert cands[0].kind == "appid"


def test_unknown_app_is_an_error_not_a_fake_success(monkeypatch):
    monkeypatch.setattr(L, "shortcuts", lambda dirs=None: [("Spotify", Path("s.lnk"))])
    monkeypatch.setattr(L, "start_apps", lambda: [])
    monkeypatch.setattr(L, "visible_windows", lambda: [])
    monkeypatch.setattr(L.shutil, "which", lambda _n: None)
    with pytest.raises(L.LaunchError) as e:
        L.open_app("zzqx nonsense app")
    assert "Nothing was opened" in str(e.value)


def test_no_window_means_kuro_says_so(monkeypatch):
    monkeypatch.setattr(L, "IS_WINDOWS", True)
    monkeypatch.setattr(L, "shortcuts", lambda dirs=None: [("Blender", Path("b.lnk"))])
    monkeypatch.setattr(L, "start_apps", lambda: [])
    monkeypatch.setattr(L, "visible_windows", lambda: [])
    monkeypatch.setattr(L, "app_paths", lambda _n: None)
    monkeypatch.setattr(L.shutil, "which", lambda _n: None)
    monkeypatch.setattr(L, "_start", lambda c: None)
    out = L.open_app("blender", wait=0.5)
    assert "no window has appeared" in out and "don't say it's open" in out


def test_window_appearing_confirms_open(monkeypatch):
    monkeypatch.setattr(L, "IS_WINDOWS", True)
    n = {"calls": 0}

    def windows():                       # nothing open until after the launch
        n["calls"] += 1
        return [] if n["calls"] <= 2 else [(7, "Blender", "blender")]
    monkeypatch.setattr(L, "shortcuts", lambda dirs=None: [("Blender", Path("b.lnk"))])
    monkeypatch.setattr(L, "start_apps", lambda: [])
    monkeypatch.setattr(L, "visible_windows", windows)
    monkeypatch.setattr(L, "app_paths", lambda _n: None)
    monkeypatch.setattr(L.shutil, "which", lambda _n: None)
    monkeypatch.setattr(L, "_start", lambda c: None)
    monkeypatch.setattr(L, "focus", lambda h: None)
    out = L.open_app("blender", wait=2)
    assert out.startswith("Opened Blender")


def test_launch_error_from_windows_is_reported(monkeypatch):
    monkeypatch.setattr(L, "IS_WINDOWS", True)
    monkeypatch.setattr(L, "shortcuts", lambda dirs=None: [("Steam", Path("s.lnk"))])
    monkeypatch.setattr(L, "start_apps", lambda: [])
    monkeypatch.setattr(L, "visible_windows", lambda: [])
    monkeypatch.setattr(L, "app_paths", lambda _n: None)
    monkeypatch.setattr(L.shutil, "which", lambda _n: None)

    def boom(c):
        raise OSError(2, "The system cannot find the file specified")
    monkeypatch.setattr(L, "_start", boom)
    monkeypatch.setitem(L.ALIASES, "steam", ("steam:", ("steam",)))
    with pytest.raises(L.LaunchError) as e:
        L.open_app("steam")
    assert "couldn't open" in str(e.value)


# ------------------------------------------------------------ location
def test_location_cached_and_weather_sentence(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    looked = []
    monkeypatch.setattr(location, "from_ip", lambda: looked.append(1) or ("Manila", 14.6, 121.0))
    s = Settings()
    assert location.locate(s, date(2026, 11, 9)) == ("Manila", 14.6, 121.0)
    assert location.locate(s, date(2026, 11, 12))[0] == "Manila" and len(looked) == 1
    txt = location.describe({"current": {"temperature_2m": 30.6, "weather_code": 2},
                             "daily": {"temperature_2m_max": [33.1], "temperature_2m_min": [25.9],
                                       "precipitation_probability_max": [40]}})
    assert txt == "31°C and partly cloudy, high 33°, low 26°, 40% chance of rain"


# ------------------------------------------------------------ backup brain
def test_out_of_quota_falls_back_and_rests_the_tired_brain():
    switched = []

    class Fake:
        def __init__(self, name, fail=None):
            self.name, self.label, self.model, self.fail, self.calls = name, name.title(), "m", fail, 0

        def complete(self, prompt, **k):
            self.calls += 1
            if self.fail:
                raise P.ProviderError(self.fail, "x", "quota")
            return f"{self.name}: ok"

    gem, groq = Fake("gemini", "rate"), Fake("groq")
    fb = P.FallbackProvider([gem, groq], on_switch=lambda a, b: switched.append((a, b)))
    assert fb.complete("hi") == "groq: ok" and switched == [("Gemini", "Groq")]
    assert fb.complete("again") == "groq: ok" and gem.calls == 1      # Gemini rests
    assert fb.label == "Groq"


def test_bad_key_is_not_hidden_by_fallback():
    class Bad:
        name = label = "x"
        model = "m"

        def complete(self, *a, **k):
            raise P.ProviderError("auth", "bad key")
    with pytest.raises(P.ProviderError):
        P.FallbackProvider([Bad(), Bad()]).complete("hi")


def test_backups_only_brains_with_keys():
    s = Settings(provider="gemini", gemini_key="AIza", keys={"groq": "gsk", "openai": ""})
    assert P.backups(s) == ["groq"]
    assert isinstance(P.make_provider(s), P.FallbackProvider)
    s.auto_fallback = False
    assert isinstance(P.make_provider(s), P.GeminiProvider)


def test_rate_limit_without_backup_suggests_groq():
    from app.brain import Brain
    s = Settings(provider="gemini", gemini_key="k")

    class Tired:
        def chat(self, *a):
            raise P.ProviderError("rate", "quota")
    reply = Brain(s, {}, lambda t, a: True, provider=Tired()).ask("hi")
    assert "Groq" in reply and "Settings" in reply


def test_existing_setups_skip_the_welcome(tmp_path, monkeypatch):
    import json
    from app.config import load_settings
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert load_settings().onboarded is False                       # brand new
    (tmp_path / "Kuro").mkdir(exist_ok=True)
    (tmp_path / "Kuro" / "config.json").write_text(json.dumps({"gemini_key": "AIza"}))
    assert load_settings().onboarded is True                        # already set up
