"""How Kuro learns about you.

Three layers, all stored as plain JSON in %APPDATA%\\Kuro so you can read,
edit or delete them:

1. MemoryStore  - facts about you ("Drey is a 4th-year CS student",
                  "prefers short answers"). Added when you tell him to
                  remember something, or by the reflection pass below.
2. HabitLog     - every action he performs for you (apps opened, sites,
                  searches) with a timestamp. Summarised into patterns
                  ("opens Blender most evenings") and used for suggestions.
3. Reflector    - after a few exchanges, the brain's small/cheap model reads
                  the recent conversation and proposes new facts / corrections.

None of this retrains the AI model. It's context Kuro is handed at the
start of every conversation, which is how assistants "learn" in practice.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

CATEGORIES = ("profile", "preference", "project", "people", "routine", "general")
STOPWORDS = set("""a an the and or but of to in on at for with is are was were be been
i me my you your he she they it its this that these those do does did have has had
not no yes so as by from about into than then there here what which who whom""".split())


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOPWORDS}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ======================================================================= facts
@dataclass
class Fact:
    id: str
    text: str
    category: str = "general"
    source: str = "user"           # user = you asked; learned = picked up by Kuro
    created: str = ""
    updated: str = ""
    uses: int = 0


class MemoryStore:
    MAX_FACTS = 400

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._facts: list[Fact] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._facts = [Fact(**f) for f in raw.get("facts", [])]
        except (json.JSONDecodeError, OSError, TypeError):
            # never destroy a file we can't read: keep a copy for the user
            try:
                self.path.replace(self.path.with_suffix(".corrupt.json"))
            except OSError:
                pass
            self._facts = []

    def _save(self) -> None:
        _atomic_write(self.path, {"version": 1, "facts": [asdict(f) for f in self._facts]})

    # ---- api ---------------------------------------------------------
    def add(self, text: str, category: str = "general", source: str = "user") -> tuple[Fact, bool]:
        """Add a fact; if a very similar one exists, update it instead.
        Returns (fact, created)."""
        text = " ".join(text.split()).strip()
        if not text:
            raise ValueError("empty fact")
        category = category if category in CATEGORIES else "general"
        now = datetime.now().replace(microsecond=0).isoformat()
        with self._lock:
            best = max(self._facts, key=lambda f: _similarity(f.text, text), default=None)
            if best and _similarity(best.text, text) >= 0.6:
                best.text, best.updated = text, now
                if source == "user":
                    best.source = "user"
                self._save()
                return best, False
            fact = Fact(uuid.uuid4().hex[:6], text, category, source, now, now)
            self._facts.append(fact)
            if len(self._facts) > self.MAX_FACTS:   # drop the least useful learned facts
                learned = sorted((f for f in self._facts if f.source == "learned"),
                                 key=lambda f: (f.uses, f.updated))
                for victim in learned[: len(self._facts) - self.MAX_FACTS]:
                    self._facts.remove(victim)
            self._save()
            return fact, True

    def forget(self, query: str) -> list[Fact]:
        """Forget by id, or every fact that closely matches the query."""
        with self._lock:
            exact = [f for f in self._facts if f.id == query.strip()]
            if exact:
                hits = exact
            else:
                q = query.lower().strip()
                hits = [f for f in self._facts
                        if (len(q) >= 4 and q in f.text.lower())
                        or _similarity(f.text, query) >= 0.5]
            for f in hits:
                self._facts.remove(f)
            if hits:
                self._save()
            return hits

    def search(self, query: str, limit: int = 8) -> list[Fact]:
        q = _tokens(query)
        scored = []
        for f in self.all():
            overlap = len(q & _tokens(f.text + " " + f.category))
            if overlap:
                scored.append((overlap, f.uses, f))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [f for _, _, f in scored[:limit]]

    def touch(self, ids: list[str]) -> None:
        with self._lock:
            hit = False
            for f in self._facts:
                if f.id in ids:
                    f.uses += 1
                    hit = True
            if hit:
                self._save()

    def all(self) -> list[Fact]:
        with self._lock:
            return list(self._facts)

    def clear(self) -> None:
        with self._lock:
            self._facts = []
            self._save()

    def prompt_block(self, max_chars: int = 3000, query: str = "") -> str:
        """Facts for the system prompt. Ones related to what you just said come
        first (and count as used, so useful facts rise over time), then things
        you told him yourself, then the most-used."""
        words = {w for w in re.findall(r"[a-z0-9]{3,}", query.lower())} - STOP
        def related(f):
            return len(words & set(re.findall(r"[a-z0-9]{3,}", f.text.lower())))
        facts = sorted(self.all(), key=lambda f: (related(f), f.source == "user", f.uses,
                                                  f.updated), reverse=True)
        hot = [f for f in facts[:8] if words and related(f)]
        if hot:
            self.touch([f.id for f in hot])
        by_cat: dict[str, list[str]] = defaultdict(list)
        used = 0
        for f in facts:
            line = f"- [{f.id}] {f.text}"
            if used + len(line) > max_chars:
                break
            by_cat["_hot" if f in hot else f.category].append(line)
            used += len(line)
        if not by_cat:
            return "Nothing yet. You're just getting to know each other."
        parts = (["Relevant right now:\n" + "\n".join(by_cat["_hot"])] if "_hot" in by_cat else [])
        return "\n".join(parts + [f"{c.title()}:\n" + "\n".join(by_cat[c])
                                   for c in CATEGORIES if c in by_cat])


# ====================================================================== habits
TRACKED = {"open_app", "open_url", "web_search", "open_path", "search_files",
           "take_screenshot", "set_volume", "add_reminder", "system_info"}
SUGGESTABLE = {"open_app", "open_url", "open_path"}


def habit_key(tool: str, args: dict) -> str:
    if tool == "open_app":
        return str(args.get("name", "")).strip().lower()
    if tool == "open_url":
        url = str(args.get("url", ""))
        host = urlparse(url if "://" in url else "https://" + url).netloc
        return host.removeprefix("www.").lower()
    if tool in ("web_search", "search_files"):
        return str(args.get("query", "")).strip().lower()[:60]
    if tool == "open_path":
        return str(args.get("path", "")).strip()
    return ""


def part_of_day(hour: int) -> str:
    return ("late night" if hour < 5 else "morning" if hour < 12 else
            "afternoon" if hour < 17 else "evening" if hour < 22 else "night")


LABELS = {"open_app": "opens {}", "open_url": "visits {}", "web_search": "searches for '{}'",
          "open_path": "opens {}", "search_files": "looks for files named '{}'",
          "take_screenshot": "takes screenshots", "set_volume": "adjusts the volume",
          "add_reminder": "sets reminders", "system_info": "checks system status"}


@dataclass
class Action:
    ts: str
    tool: str
    key: str = ""

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.ts)


class HabitLog:
    MAX = 1500

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._items: list[Action] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines()[-self.MAX:]:
                try:
                    self._items.append(Action(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue

    def record(self, tool: str, args: dict, now: datetime | None = None) -> None:
        if tool not in TRACKED:
            return
        a = Action((now or datetime.now()).replace(microsecond=0).isoformat(), tool,
                   habit_key(tool, args))
        with self._lock:
            self._items.append(a)
            if len(self._items) > self.MAX * 1.2:       # compact occasionally
                self._items = self._items[-self.MAX:]
                self.path.write_text("".join(json.dumps(asdict(i)) + "\n"
                                             for i in self._items), encoding="utf-8")
            else:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(a)) + "\n")

    def recent(self, days: int = 30, now: datetime | None = None) -> list[Action]:
        cutoff = (now or datetime.now()) - timedelta(days=days)
        with self._lock:
            return [a for a in self._items if a.when >= cutoff]

    def summary(self, now: datetime | None = None, top: int = 8) -> str:
        items = self.recent(30, now)
        if not items:
            return "No patterns yet."
        groups: dict[tuple[str, str], list[Action]] = defaultdict(list)
        for a in items:
            groups[(a.tool, a.key)].append(a)
        ranked = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
        lines = []
        for (tool, key), acts in ranked[:top]:
            if len(acts) < 2:
                continue
            when = Counter(part_of_day(a.when.hour) for a in acts).most_common(1)[0][0]
            days = len({a.when.date() for a in acts})
            label = LABELS.get(tool, tool)
            what = label.format(key) if "{}" in label else label
            lines.append(f"- {what}: {len(acts)}x over {days} day(s), mostly in the {when}")
        return "\n".join(lines) or "No patterns yet."

    def suggestion(self, now: datetime | None = None) -> tuple[str, str] | None:
        """Something you habitually do around this time of day that you
        haven't done yet today. Returns (tool, key) or None."""
        now = now or datetime.now()
        mins = now.hour * 60 + now.minute
        today = now.date()
        days_by_key: dict[tuple[str, str], set] = defaultdict(set)
        done_today: set[tuple[str, str]] = set()
        for a in self.recent(21, now):
            if a.tool not in SUGGESTABLE or not a.key:
                continue
            k = (a.tool, a.key)
            if a.when.date() == today:
                done_today.add(k)
                continue
            m = a.when.hour * 60 + a.when.minute
            if abs(m - mins) <= 45:
                days_by_key[k].add(a.when.date())
        candidates = [(len(d), k) for k, d in days_by_key.items()
                      if len(d) >= 3 and k not in done_today]
        return max(candidates)[1] if candidates else None


# ================================================================= reflection
STOP = {"the", "and", "for", "you", "your", "with", "that", "this", "what", "can", "are",
        "how", "please", "kuro", "his", "her", "him", "she", "was", "have", "has", "not"}

REFLECT_PROMPT = """You maintain the long-term memory of {cat}, a desktop assistant, about its user {user}.

Read the recent conversation and the current memory. Propose changes so the memory stays accurate and useful for future conversations.

Rules:
- Only durable facts: who they are, their projects, preferences about how {cat} should behave, people they mention by role, routines. Skip one-off requests and small talk.
- Only things the user actually said or clearly confirmed. No guesses, no inferences about personality.
- Never store passwords, keys, card or ID numbers, health conditions, or anything the user asked not to be remembered.
- If a new fact contradicts an existing one, put the old id in "forget" and add the corrected fact.
- Learn from feedback: if the user corrected {cat}, was unhappy with an answer, or showed how they like things done (shorter replies, a certain app/browser, a language, a format), save that as a "preference" so {cat} does better next time.
- Merge duplicates: if two existing facts say the same thing, forget the weaker one.
- Keep each fact one short sentence in third person, e.g. "{user} is building a 3D model of their school in Blender."
- Usually there is nothing new. Then return empty lists.

Categories: profile, preference, project, people, routine, general.

Current memory:
{memory}

Recent conversation:
{transcript}

Reply with JSON only:
{{"add": [{{"text": "...", "category": "..."}}], "forget": ["id"]}}"""


def transcript_from_history(history: list[dict], max_chars: int = 6000) -> str:
    lines = []
    for m in history:
        if isinstance(m["content"], str):
            who = "User" if m["role"] == "user" else "Assistant"
            lines.append(f"{who}: {m['content']}")
        elif m["role"] == "assistant":
            text = " ".join(b.get("text", "") for b in m["content"] if b.get("type") == "text")
            if text.strip():
                lines.append(f"Assistant: {text.strip()}")
    out = "\n".join(lines)
    return out[-max_chars:]


def parse_reflection(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"add": [], "forget": []}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"add": [], "forget": []}
    adds = [a for a in data.get("add", []) if isinstance(a, dict) and a.get("text")]
    forgets = [str(f) for f in data.get("forget", []) if f]
    return {"add": adds[:8], "forget": forgets[:8]}


@dataclass
class Reflector:
    memory: MemoryStore
    cat_name: str
    user_name: str
    every_n_user_turns: int = 3
    _since: int = field(default=0, init=False)
    _running: bool = field(default=False, init=False)

    def note_turn(self) -> bool:
        """Call once per user message. True when it's time to reflect."""
        self._since += 1
        return self._since >= self.every_n_user_turns and not self._running

    def reflect(self, complete, history: list[dict]) -> list[str]:
        """Blocking; run it on a worker thread. `complete(prompt) -> text` is
        the provider's one-shot call. Returns the new facts' texts."""
        self._running = True
        self._since = 0
        try:
            transcript = transcript_from_history(history[-24:])
            if not transcript.strip():
                return []
            prompt = REFLECT_PROMPT.format(cat=self.cat_name, user=self.user_name,
                                           memory=self.memory.prompt_block(),
                                           transcript=transcript)
            plan = parse_reflection(complete(prompt))
            for fid in plan["forget"]:
                self.memory.forget(fid)
            added = []
            for a in plan["add"]:
                fact, created = self.memory.add(a["text"], a.get("category", "general"),
                                                source="learned")
                if created:
                    added.append(fact.text)
            return added
        except Exception:
            return []          # learning is best-effort; never disturb the chat
        finally:
            self._running = False
