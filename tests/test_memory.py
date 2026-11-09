"""Tests for Kuro's memory, habits, reflection and saved conversations."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.brain import Brain
from app.config import Settings
from app.memory import (HabitLog, MemoryStore, Reflector, parse_reflection,
                        transcript_from_history)
from app.reminders import ReminderStore
from app.tools import build_tools


# ------------------------------------------------------------------- facts
def test_add_dedupes_similar_facts(tmp_path):
    m = MemoryStore(tmp_path / "m.json")
    _, created = m.add("Drey is a 4th-year computer science student", "profile")
    assert created
    fact, created = m.add("Drey is a 4th-year computer science student at uni", "profile")
    assert not created and "uni" in fact.text
    assert len(m.all()) == 1


def test_memory_persists_and_forgets(tmp_path):
    path = tmp_path / "m.json"
    m = MemoryStore(path)
    f, _ = m.add("Drey prefers short answers", "preference")
    m.add("Drey is modelling Saint Thomas school in Blender", "project")
    m2 = MemoryStore(path)
    assert len(m2.all()) == 2
    assert [x.text for x in m2.forget(f.id)] == ["Drey prefers short answers"]
    assert m2.forget("blender school saint thomas modelling")  # by description
    assert MemoryStore(path).all() == []


def test_short_forget_query_does_not_wipe_everything(tmp_path):
    m = MemoryStore(tmp_path / "m.json")
    m.add("Drey likes cats")
    m.add("Drey studies at a university")
    assert m.forget("a") == []
    assert len(m.all()) == 2


def test_corrupt_memory_is_kept_aside(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{oops")
    assert MemoryStore(path).all() == []
    assert (tmp_path / "m.corrupt.json").exists()


def test_search_and_prompt_block(tmp_path):
    m = MemoryStore(tmp_path / "m.json")
    m.add("Drey's thesis is due in March", "project")
    m.add("Drey likes lime green", "preference")
    assert m.search("when is the thesis due")[0].text.startswith("Drey's thesis")
    block = m.prompt_block()
    assert "Project:" in block and "Preference:" in block


# ------------------------------------------------------------------ habits
def test_habit_summary_and_suggestion(tmp_path):
    log = HabitLog(tmp_path / "a.jsonl")
    base = datetime(2026, 11, 10, 20, 0)
    for day in range(4):                                   # 4 evenings of Blender
        log.record("open_app", {"name": "Blender"}, now=base - timedelta(days=day + 1))
    log.record("open_url", {"url": "https://www.youtube.com/x"}, now=base - timedelta(days=1))
    log.record("lock_pc", {}, now=base)                    # untracked tool: ignored

    summary = log.summary(now=base)
    assert "opens blender: 4x over 4 day(s), mostly in the evening" in summary
    assert log.suggestion(now=base + timedelta(minutes=10)) == ("open_app", "blender")
    assert log.suggestion(now=base.replace(hour=9)) is None   # wrong time of day

    log.record("open_app", {"name": "blender"}, now=base)     # already done today
    assert log.suggestion(now=base + timedelta(minutes=10)) is None
    assert len(HabitLog(tmp_path / "a.jsonl").recent(60, now=base)) == 6


# -------------------------------------------------------------- reflection
def test_parse_reflection_is_forgiving():
    assert parse_reflection("nothing here") == {"add": [], "forget": []}
    out = parse_reflection('Sure!\n{"add": [{"text": "Drey uses Unity", "category": "project"}],'
                           ' "forget": ["ab12"]}')
    assert out["add"][0]["text"] == "Drey uses Unity" and out["forget"] == ["ab12"]


def test_reflector_applies_changes(tmp_path):
    m = MemoryStore(tmp_path / "m.json")
    old, _ = m.add("Drey uses Unity 2022", "project")
    reply = ('{"add": [{"text": "Drey uses Unity 6 for the campus navigation app", '
             '"category": "project"}], "forget": ["%s"]}' % old.id)
    r = Reflector(m, "Kuro", "Drey", every_n_user_turns=2)
    assert not r.note_turn() and r.note_turn()
    learned = r.reflect(lambda prompt: reply, [{"role": "user", "content": "I switched to Unity 6"},
                                 {"role": "assistant", "content": [{"type": "text", "text": "Nice!"}]}])
    assert learned == ["Drey uses Unity 6 for the campus navigation app"]
    assert [f.source for f in m.all()] == ["learned"]


def test_reflector_never_raises(tmp_path):
    def boom(prompt):
        raise ConnectionError
    r = Reflector(MemoryStore(tmp_path / "m.json"), "Kuro", "Drey")
    assert r.reflect(boom, [{"role": "user", "content": "hi"}]) == []


def test_transcript_skips_tool_noise():
    hist = [{"role": "user", "content": "open blender"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "open_app", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Done!"}]}]
    assert transcript_from_history(hist) == "User: open blender\nAssistant: Done!"


# ------------------------------------------------------------------ brain
def _resp(stop, content):
    return SimpleNamespace(stop_reason=stop, content=content)


def test_memory_tools_and_habit_logging(tmp_path):
    mem = MemoryStore(tmp_path / "m.json")
    tools = build_tools(ReminderStore(tmp_path / "r.json"), [], memory=mem)
    actions = []
    script = [
        _resp("tool_use", [{"type": "tool_use", "id": "a", "name": "remember",
                            "input": {"fact": "Drey's cat is called Kuro", "category": "general"}}]),
        _resp("end_turn", [{"type": "text", "text": "Got it."}]),
    ]
    calls = []
    provider = SimpleNamespace(chat=lambda *a: (calls.append(a), script.pop(0))[1])
    brain = Brain(Settings(gemini_key="x"), tools, lambda t, a: True,
                  provider=provider, on_action=lambda n, a: actions.append(n))
    assert brain.ask("remember my cat is called Kuro") == "Got it."
    assert mem.all()[0].text == "Drey's cat is called Kuro"
    assert actions == ["remember"]
    persona, context = calls[0][0], calls[0][1]
    assert "long-term memory" in persona and "Current local time" in context


def test_history_save_load_cuts_broken_tail(tmp_path):
    brain = Brain(Settings(), {}, lambda t, a: True)
    brain.history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "Mrrp!"}]},
        {"role": "user", "content": "open blender"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "open_app", "input": {}}]},
    ]
    path = tmp_path / "h.json"
    brain.save(path)
    fresh = Brain(Settings(), {}, lambda t, a: True)
    fresh.load(path)
    assert len(fresh.history) == 2
    assert fresh.visible_turns() == [("you", "hi"), ("cat", "Mrrp!")]


def test_prompt_block_puts_related_facts_first_and_counts_use(tmp_path):
    from app.memory import MemoryStore
    m = MemoryStore(tmp_path / "m.json")
    m.add("Drey likes Brockhampton.", "preference", source="learned")
    m.add("Drey models the Saint Thomas campus in Blender.", "project", source="learned")
    block = m.prompt_block(query="open blender for my campus model")
    assert block.index("Blender") < block.index("Brockhampton")
    blender = next(f for f in m.all() if "Blender" in f.text)
    assert blender.uses >= 1
