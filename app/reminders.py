"""Persistent reminders.

Reminders live in reminders.json in the data folder, so they survive a
restart. The UI calls `pop_due()` on a timer and makes the cat announce
whatever comes back.
"""
import json
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class Reminder:
    id: str
    message: str
    due: str          # ISO local time, seconds precision

    @property
    def due_dt(self) -> datetime:
        return datetime.fromisoformat(self.due)


class ReminderStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._items: list[Reminder] = []
        self._load()

    # ---- persistence -------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [Reminder(**r) for r in raw]
        except (json.JSONDecodeError, OSError, TypeError):
            self._items = []

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in self._items], indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)  # atomic: a crash never leaves half a file

    # ---- api ---------------------------------------------------------
    def add(self, message: str, *, in_minutes: float | None = None,
            at: str | None = None, now: datetime | None = None) -> Reminder:
        now = now or datetime.now()
        if in_minutes is not None:
            if in_minutes <= 0:
                raise ValueError("in_minutes must be positive")
            due = now + timedelta(minutes=float(in_minutes))
        elif at:
            due = datetime.fromisoformat(at)
            if due.tzinfo is not None:  # store everything as naive local time
                due = due.astimezone().replace(tzinfo=None)
            if due <= now:
                raise ValueError(f"{due:%Y-%m-%d %H:%M} is already in the past")
        else:
            raise ValueError("give either in_minutes or at")
        r = Reminder(id=uuid.uuid4().hex[:6], message=message.strip(),
                     due=due.replace(microsecond=0).isoformat())
        with self._lock:
            self._items.append(r)
            self._items.sort(key=lambda x: x.due)
            self._save()
        return r

    def all(self) -> list[Reminder]:
        with self._lock:
            return list(self._items)

    def cancel(self, reminder_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [r for r in self._items if r.id != reminder_id]
            changed = len(self._items) != before
            if changed:
                self._save()
            return changed

    def pop_due(self, now: datetime | None = None) -> list[Reminder]:
        now = now or datetime.now()
        with self._lock:
            due = [r for r in self._items if r.due_dt <= now]
            if due:
                self._items = [r for r in self._items if r.due_dt > now]
                self._save()
            return due
