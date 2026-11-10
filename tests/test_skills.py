"""New abilities: sums, to-do list, saving files, routines, tidying folders,
battery warnings; and the rebuilt brain picker."""
import os
import time
from types import SimpleNamespace

import pytest

from app import skills
from app.controller import battery_alert
from app.skills import (Organizer, Routines, TodoList, build_skills, calculate, convert_units,
                        write_file)
from app.tools import ToolError, run_tool


# ---- sums ----------------------------------------------------------------
@pytest.mark.parametrize("expr, answer", [
    ("2 + 2", "4"), ("15% of 240", "36"), ("2^10", "1,024"), ("3 x 4", "12"),
    ("1,250.50 + 2", "1,252.5"), ("sqrt(16) * 3", "12"), ("(1 + 2) * 3 - 4 / 2", "7"),
    ("0.1 + 0.2", "0.3"), ("max(3, 400)", "400"), ("factorial(5)", "120"),
    ("what is 7 * 6?", "42"), ("10 / 4", "2.5"), ("round(pi, 2)", "3.14"),
])
def test_calculate(expr, answer):
    assert calculate(expr).endswith("= " + answer)


@pytest.mark.parametrize("expr", [
    "__import__('os').system('dir')", "open('x')", "a.b", "1/0", "9**9**9", "sqrt(-1)",
    "", "lambda: 1", "[1,2]", "'a'*5", "x + 1",
])
def test_calculate_refuses_anything_that_is_not_a_sum(expr):
    with pytest.raises(ToolError):
        calculate(expr)


def test_convert_units():
    assert convert_units(5, "ft", "cm").endswith("= 152.4 cm")
    assert convert_units(100, "C", "F").endswith("= 212 F")
    assert convert_units(32, "fahrenheit", "celsius").endswith("= 0 celsius")
    assert convert_units(3, "GB", "MB").endswith("= 3,000 MB")
    assert convert_units(2, "hours", "minutes").endswith("= 120 minutes")
    assert convert_units("10", "miles", "km").endswith("= 16.0934 km")
    with pytest.raises(ToolError):
        convert_units(1, "kg", "km")
    with pytest.raises(ToolError):
        convert_units(1, "usd", "php")


# ---- to-do ---------------------------------------------------------------
def test_todo_list_round_trip(tmp_path):
    todo = TodoList(tmp_path / "todo.json")
    assert "empty" in todo.run("list")
    assert "2 left" in todo.run("add", "finish thesis chapter; buy cat food")
    assert todo.open_count() == 2
    listing = TodoList(tmp_path / "todo.json").run("list")          # survives a restart
    assert "#1 finish thesis chapter" in listing and "#2 buy cat food" in listing
    assert "Ticked off: buy cat food" in todo.run("done", item="cat food")
    assert "cat food" not in todo.run("list")
    assert "Removed: finish thesis chapter" in todo.run("remove", item="1")
    assert "empty" in todo.run("list")
    with pytest.raises(ToolError):
        todo.run("done", item="nothing like this")


def test_todo_asks_which_when_two_match(tmp_path):
    todo = TodoList(tmp_path / "todo.json")
    todo.run("add", "email Sam; email the dean")
    with pytest.raises(ToolError, match="more than one"):
        todo.run("done", item="email")


def test_todo_survives_a_broken_file(tmp_path):
    (tmp_path / "todo.json").write_text("{not json")
    todo = TodoList(tmp_path / "todo.json")
    assert "empty" in todo.run("list")
    assert "1 left" in todo.run("add", "x")


# ---- saving files --------------------------------------------------------
def test_write_file_saves_and_never_overwrites(tmp_path):
    (tmp_path / "Documents").mkdir()
    out = write_file("notes", "hello", home=tmp_path)
    assert (tmp_path / "Documents" / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert "notes.txt" in out
    write_file("notes.txt", "second", home=tmp_path)
    assert (tmp_path / "Documents" / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert (tmp_path / "Documents" / "notes (2).txt").read_text(encoding="utf-8") == "second"


def test_write_file_stays_in_your_folders_and_makes_no_programs(tmp_path):
    with pytest.raises(ToolError):
        write_file("run.bat", "del *", home=tmp_path)
    with pytest.raises(ToolError):
        write_file("x.txt", "x", folder="/etc", home=tmp_path)
    with pytest.raises(ToolError):
        write_file("x.txt", "x", folder="../outside", home=tmp_path)
    with pytest.raises(ToolError):
        write_file("x.txt", "x", folder="AppData/Roaming", home=tmp_path)
    out = write_file("../../sneaky.md", "ok", folder="desktop", home=tmp_path)   # name only
    assert (tmp_path / "Desktop" / "sneaky.md").is_file() and "sneaky.md" in out


# ---- routines ------------------------------------------------------------
def test_routines(tmp_path):
    r = Routines(tmp_path / "r.json")
    assert "No routines" in r.run("list")
    r.run("save", "Study Mode", "open VS Code, play lofi, start a 25 minute focus")
    assert r.names() == ["study mode"]
    ran = Routines(tmp_path / "r.json").run("run", "study")          # loose match, after restart
    assert "open VS Code" in ran and "Carry out these steps" in ran
    with pytest.raises(ToolError, match="study mode"):
        r.run("run", "bedtime")
    assert "Deleted" in r.run("delete", "study mode")
    assert r.names() == []


# ---- tidying a folder ----------------------------------------------------
def _old(path, text="x"):
    path.write_text(text)
    t = time.time() - 3600
    os.utime(path, (t, t))
    return path


@pytest.fixture
def messy(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    for name in ("cat.png", "notes.pdf", "song.mp3", "setup.exe", "movie.mp4", "data.xyz",
                 "half.crdownload", "Game.lnk", "main.py"):
        _old(d / name)
    (d / "fresh.pdf").write_text("just saved")            # still being written: left alone
    (d / "Stuff").mkdir()
    return d


def test_organize_sorts_by_type_and_undoes(tmp_path, messy):
    org = Organizer(tmp_path / "undo.json", home=tmp_path)
    said = org.describe({"folder": "downloads"})
    assert "6 loose files" in said and "Nothing is deleted" in said
    out = org.run("downloads")
    assert "Sorted 6 files" in out
    assert (messy / "Images" / "cat.png").is_file()
    assert (messy / "Documents" / "notes.pdf").is_file()
    assert (messy / "Installers" / "setup.exe").is_file()
    assert (messy / "Code" / "main.py").is_file()
    for left in ("data.xyz", "half.crdownload", "Game.lnk", "fresh.pdf"):
        assert (messy / left).is_file()                   # unknown / unfinished / shortcuts stay
    assert "Put 6 of 6 files back" in org.run("downloads", undo=True)
    assert (messy / "cat.png").is_file() and not (messy / "Images").exists()
    with pytest.raises(ToolError):
        org.undo("downloads")


def test_organize_never_overwrites(tmp_path, messy):
    (messy / "Images").mkdir()
    _old(messy / "Images" / "cat.png", "the old one")
    Organizer(tmp_path / "undo.json", home=tmp_path).run("downloads")
    assert (messy / "Images" / "cat.png").read_text() == "the old one"
    assert (messy / "Images" / "cat (2).png").is_file()


def test_organize_only_inside_your_home(tmp_path):
    org = Organizer(tmp_path / "undo.json", home=tmp_path)
    for bad in ("/", "/etc", "..", str(tmp_path)):
        with pytest.raises(ToolError):
            org.run(bad)


# ---- the registry --------------------------------------------------------
def test_skills_are_real_tools_and_the_risky_ones_ask(tmp_path):
    tools, todo, routines = build_skills(tmp_path, home=tmp_path)
    by = {t.name: t for t in tools}
    assert {"calculate", "convert_units", "todo", "write_file", "routine", "pc_health",
            "close_app", "display", "organize_folder"} <= set(by)
    assert by["close_app"].risky and by["organize_folder"].risky
    assert not by["calculate"].risky and not by["todo"].risky
    assert "Force-quit chrome" in by["close_app"].describe({"name": "chrome", "force": True})
    assert run_tool(by["calculate"], {"expression": "6*7"}) == ("6*7 = 42", False)
    text, err = run_tool(by["calculate"], {"expression": "import os"})
    assert err and text.startswith("Error:")
    assert run_tool(by["todo"], {"action": "add", "text": "ship Kuro"})[1] is False
    assert todo.open_count() == 1
    for t in tools:                                       # schemas the brains accept
        assert t.as_api()["input_schema"]["type"] == "object" and t.description


def test_close_app_wont_touch_windows_itself(monkeypatch):
    import psutil
    fake = [SimpleNamespace(info={"name": n, "pid": i}) for i, n in
            enumerate(["explorer.exe", "svchost.exe", "chrome.exe", "chrome.exe", "Code.exe"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: fake)
    assert [p.info["name"] for p in skills._matching("Chrome")] == ["chrome.exe", "chrome.exe"]
    assert [p.info["name"] for p in skills._matching("vs code")] == ["Code.exe"]
    assert skills._matching("explorer") == [] and skills._matching("file explorer") == []
    with pytest.raises(ToolError):
        skills.close_app("explorer")


def test_pc_health_reads(monkeypatch):
    monkeypatch.setattr(skills.time, "sleep", lambda s: None)
    out = skills.pc_health(3)
    assert "Memory" in out and "Using the most memory" in out


# ---- battery -------------------------------------------------------------
def test_battery_warns_once_per_level():
    def bat(pct, plugged=False):
        return SimpleNamespace(percent=pct, power_plugged=plugged)
    assert battery_alert(None, 100) is None               # a desktop PC
    assert battery_alert(bat(60), 100) is None
    assert battery_alert(bat(19), 100) == 20
    assert battery_alert(bat(17), 20) is None             # already said
    assert battery_alert(bat(9), 20) == 10
    assert battery_alert(bat(5), 10) is None
    assert battery_alert(bat(5, plugged=True), 100) is None


# ---- settings: the brain picker -----------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_brain_picker_is_one_plain_list(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.config import Settings
    from app.ui.hud import BrainOption, SettingsPage
    s = Settings(provider="gemini", gemini_key="AIza-test", keys={"groq": "gsk_test"})
    monkeypatch.setattr(Settings, "save", lambda self: None)
    page = SettingsPage(s, lambda *a: "model")
    rows = page.brain_rows
    assert all(isinstance(r, BrainOption) for r in rows.values())
    assert rows["gemini"].selected and not rows["groq"].selected
    assert rows["groq"].note == "backup" and rows["claude"].note == ""
    assert page.key_label.text() == "Gemini key" and page.key.text() == "AIza-test"
    rows["groq"].picked.emit("groq")
    assert s.provider == "groq" and rows["groq"].selected and not rows["gemini"].selected
    assert page.key_label.text() == "Groq key" and page.key.text() == "gsk_test"
    assert rows["gemini"].note == "backup"
    rows["ollama"].picked.emit("ollama")
    assert page.key_label.text() == "No key needed" and page.key.isHidden()
    assert not hasattr(page, "backup_note")


# ---- through the brain, the way a real request runs ----------------------
def test_brain_uses_the_new_abilities(tmp_path):
    import copy
    from app.brain import Brain, persona_prompt
    from app.config import Settings
    from app.reminders import ReminderStore
    from app.tools import build_tools

    class Fake:
        def __init__(self, responses):
            self.responses, self.calls = list(responses), []

        def chat(self, persona, context, tools, messages, max_tokens):
            self.calls.append(copy.deepcopy(messages))
            return self.responses.pop(0)

    def use(name, args, tid):
        return SimpleNamespace(stop_reason="tool_use", content=[
            {"type": "tool_use", "id": tid, "name": name, "input": args}])

    def say(text):
        return SimpleNamespace(stop_reason="end_turn", content=[{"type": "text", "text": text}])

    tools = build_tools(ReminderStore(tmp_path / "r.json"), [str(tmp_path)])
    more, todo, routines = build_skills(tmp_path, home=tmp_path)
    tools.update({t.name: t for t in more})
    asked = []
    fake = Fake([use("routine", {"action": "save", "name": "study mode",
                                 "steps": "add 'review notes' to my to-do list"}, "a"),
                 say("Saved study mode."),
                 use("routine", {"action": "run", "name": "study mode"}, "b"),
                 use("todo", {"action": "add", "text": "review notes"}, "c"),
                 say("Study mode is on."),
                 use("organize_folder", {"folder": "downloads"}, "d"),
                 say("Okay, I left it alone.")])
    brain = Brain(Settings(gemini_key="x"), tools,
                  confirm=lambda t, a: asked.append(t.name) or False, provider=fake)
    assert brain.ask("when I say study mode, add review notes to my list") == "Saved study mode."
    assert brain.ask("study mode") == "Study mode is on."
    assert todo.open_count() == 1 and routines.names() == ["study mode"]
    steps = fake.calls[3][-1]["content"][0]["content"]         # what 'run' handed back
    assert "review notes" in steps and "Carry out these steps" in steps
    (tmp_path / "Downloads").mkdir()
    assert brain.ask("tidy my downloads") == "Okay, I left it alone."
    assert asked == ["organize_folder"]                        # it asked; you said no
    assert "calculate" in persona_prompt(Settings())
