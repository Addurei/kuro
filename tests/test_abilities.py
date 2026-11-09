"""Tests for Kuro's newer abilities: reading files, choosing a browser,
seeing the screen and answering from the live web."""
import base64
from types import SimpleNamespace

import httpx
import pytest

from app import providers as P
from app import tools as T
from app.reminders import ReminderStore


def make_tools(tmp_path, provider=None, capture=lambda: b"\xff\xd8jpeg"):
    return T.build_tools(ReminderStore(tmp_path / "r.json"), [str(tmp_path)],
                         brain=(lambda: provider) if provider else None, capture=capture)


# ------------------------------------------------------------- files
def test_read_text_and_docx(tmp_path):
    (tmp_path / "notes.md").write_text("# Thesis\nChapter 1 is about campus navigation.")
    out = T.read_file(str(tmp_path / "notes.md"))
    assert "campus navigation" in out and out.startswith("Contents of notes.md")

    import docx
    d = docx.Document()
    d.add_paragraph("Saint Thomas campus map")
    d.save(tmp_path / "plan.docx")
    assert "Saint Thomas campus map" in T.read_file(str(tmp_path / "plan.docx"))


def test_read_file_limits_and_errors(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 20000)
    assert "truncated" in T.read_file(str(tmp_path / "big.txt"))
    (tmp_path / "pic.png").write_bytes(b"\x89PNG")
    with pytest.raises(T.ToolError):
        T.read_file(str(tmp_path / "pic.png"))
    with pytest.raises(T.ToolError):
        T.read_file(str(tmp_path / "missing.txt"))


# ------------------------------------------------------------- browsers
def test_open_in_named_browser(tmp_path, monkeypatch):
    exe = tmp_path / "Programs" / "Opera GX" / "opera.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    launched = []
    monkeypatch.setattr(T.subprocess, "Popen", lambda args, **kw: launched.append(args))
    assert T.find_browser("Opera GX") == str(exe)
    assert "in opera gx" in T.web_search("76ers score", browser="opera gx")
    assert launched[0][0] == str(exe) and "76ers+score" in launched[0][1]
    with pytest.raises(T.ToolError):
        T.open_url("example.com", browser="netscape")


# ------------------------------------------------------------- vision + web
def fake_server(monkeypatch, body):
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))
    monkeypatch.setattr(P.httpx, "post", post)
    return sent


def test_vision_sends_the_image(monkeypatch):
    sent = fake_server(monkeypatch, {"candidates": [{"content": {"parts": [
        {"text": "Blender is open with a 3D model of a school."}]}}]})
    out = P.GeminiProvider("k").vision("What's open?", b"JPEGDATA")
    parts = sent[0]["contents"][0]["parts"]
    assert parts[0]["inlineData"]["mimeType"] == "image/jpeg"
    assert base64.b64decode(parts[0]["inlineData"]["data"]) == b"JPEGDATA"
    assert "Blender" in out


def test_web_answer_uses_google_search_and_returns_sources(monkeypatch):
    sent = fake_server(monkeypatch, {"candidates": [{
        "content": {"parts": [{"text": "The 76ers won 112-104."}]},
        "groundingMetadata": {"groundingChunks": [
            {"web": {"uri": "https://nba.com/game", "title": "NBA.com"}}]}}]})
    text, sources = P.GeminiProvider("k").web_answer("76ers last game score")
    assert sent[0]["tools"] == [{"googleSearch": {}}]
    assert "112-104" in text and sources == [("NBA.com", "https://nba.com/game")]


def test_look_at_screen_tool(tmp_path):
    seen = {}
    prov = SimpleNamespace(vision=lambda prompt, jpeg: seen.update(p=prompt, j=jpeg) or "a code editor")
    tools = make_tools(tmp_path, prov)
    out, err = T.run_tool(tools["scan"], {"what": "screen", "question": "what am I doing?"})
    assert not err and "a code editor" in out
    assert seen["j"].startswith(b"\xff\xd8") and "faces" in seen["p"]


def test_web_answer_tool_failure_is_explained(tmp_path):
    def boom(q):
        raise P.ProviderError("rate", "HTTP 429")
    tools = make_tools(tmp_path, SimpleNamespace(web_answer=boom))
    out, err = T.run_tool(tools["web_answer"], {"question": "weather"})
    assert err and "offer to open a web search" in out


def test_new_tools_have_gemini_friendly_schemas(tmp_path):
    tools = make_tools(tmp_path, SimpleNamespace())
    decls = P.gemini_tools([t.as_api() for t in tools.values()])[0]["functionDeclarations"]
    names = {d["name"] for d in decls}
    assert {"scan", "web_answer", "window_action", "media",
            "type_text", "list_windows"} <= names
    assert not any("additionalProperties" in str(d) for d in decls)


# ------------------------------------------------------------- play / seek
@pytest.mark.parametrize("given,secs", [("0:10", 10), ("1:30", 90), ("1:02:03", 3723),
                                        ("90", 90), ("1m30s", 90), ("2m", 120), ("45s", 45),
                                        (None, 0), (75, 75)])
def test_parse_timestamp(given, secs):
    assert T.parse_timestamp(given) == secs


def test_parse_timestamp_rejects_nonsense():
    with pytest.raises(T.ToolError):
        T.parse_timestamp("later")


YT_HTML = ('<html>... var ytInitialData = {"contents":[{"adSlotRenderer":{"videoId":"AAAAAAAAAAA"}},'
           '{"videoRenderer":{"videoId":"dQw4w9WgXcQ","thumbnail":{},'
           '"title":{"runs":[{"text":"Hev Abi - Faded Off (Official Music Video)"}]}}}]}</html>')


def test_play_media_opens_top_video_at_time(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, text=YT_HTML, request=httpx.Request("GET", url)))
    opened = []
    monkeypatch.setattr(T, "_open_in", lambda url, browser: opened.append((url, browser)) or "")
    out = T.play_media("faded off hev abi", start_at="0:10", browser="opera gx")
    assert opened == [("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s", "opera gx")]
    assert "Faded Off" in out and "0:10" in out           # skipped the ad slot
    assert T.LAST_PLAYED["video_id"] == "dQw4w9WgXcQ"


def test_seek_uses_player_then_falls_back(monkeypatch, tmp_path):
    asked = []
    tools = T.build_tools(ReminderStore(tmp_path / "r.json"), [],
                          seek=lambda secs: asked.append(secs) or True)
    assert tools["seek_media"].func("1:30") == "Skipped to 1:30." and asked == [90]

    # player refuses: reopen the last video at that time (pausing the old one)
    T.LAST_PLAYED.update(video_id="dQw4w9WgXcQ", title="Faded Off", browser=None)
    opened, keys = [], []
    monkeypatch.setattr(T, "_open_in", lambda url, browser: opened.append(url) or "")
    monkeypatch.setattr(T, "media", lambda action: keys.append(action))
    tools = T.build_tools(ReminderStore(tmp_path / "r2.json"), [], seek=lambda s: False)
    assert "Reopened 'Faded Off' at 2:15" in tools["seek_media"].func("2:15")
    assert opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=135s"]
    assert keys == ["play_pause"]


# ------------------------------------------------------------- scan (in chat)
def test_scan_file_by_name_gives_exact_text_and_stats(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "Thesis Draft.md").write_text("Campus navigation app.\nChapter one.")
    tools = make_tools(tmp_path, SimpleNamespace())
    out, err = T.run_tool(tools["scan"], {"what": "file", "path": "thesis draft.md"})
    assert not err and "5 words" in out and "Campus navigation app." in out and "exact data" in out
    out, err = T.run_tool(tools["scan"], {"what": "file", "path": "nope.pdf"})
    assert err and "couldn't find" in out


def test_scan_image_file_uses_vision(tmp_path):
    from PySide6.QtGui import QColor, QImage
    import os
    from PySide6.QtWidgets import QApplication
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    img = QImage(40, 30, QImage.Format_RGB32)
    img.fill(QColor(200, 30, 30))
    img.save(str(tmp_path / "logo.png"))
    seen = {}
    prov = SimpleNamespace(vision=lambda prompt, jpeg: seen.update(j=jpeg) or "a red square")
    out, err = T.run_tool(make_tools(tmp_path, prov)["scan"], {"what": "file", "path": "logo.png"})
    assert not err and "a red square" in out and seen["j"].startswith(b"\xff\xd8")


def test_scan_text_and_clipboard(tmp_path, monkeypatch):
    tools = make_tools(tmp_path, SimpleNamespace())
    out, err = T.run_tool(tools["scan"], {"what": "text", "text": "one two three"})
    assert not err and "3 words" in out and "one two three" in out
    monkeypatch.setattr(T, "read_clipboard_raw", lambda: "")
    out, err = T.run_tool(tools["scan"], {"what": "clipboard"})
    assert err and "empty" in out


def test_scan_song_combines_player_and_web(tmp_path):
    track = SimpleNamespace(title="SUMMER", artist="BROCKHAMPTON",
                            describe=lambda: "playing 'SUMMER' by BROCKHAMPTON on Spotify")
    prov = SimpleNamespace(web_answer=lambda q: ("From SATURATION III (2017).", [("Wiki", "https://w")]))
    tools = T.build_tools(ReminderStore(tmp_path / "r.json"), [], brain=lambda: prov,
                          music=lambda: track)
    out, err = T.run_tool(tools["scan"], {"what": "song"})
    assert not err and "From the player: playing 'SUMMER'" in out and "SATURATION III" in out
    tools = T.build_tools(ReminderStore(tmp_path / "r2.json"), [], music=lambda: None)
    assert "Nothing is playing" in T.run_tool(tools["scan"], {"what": "song"})[0]
