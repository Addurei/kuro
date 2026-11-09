"""Focus companion: pomodoro-style focus / break cycles.

Pure logic (no Qt) so it's easy to test. The controller ticks it once a
second and reacts to the events it returns.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class FocusEvent:
    kind: str            # "break" | "focus" | "done"
    session: int         # sessions finished so far (in this run)


class FocusTimer:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.phase = "idle"            # idle | focus | break
        self.work_s = 25 * 60
        self.break_s = 5 * 60
        self.cycles = 4
        self.done_sessions = 0
        self._ends = 0.0
        self._started = 0.0
        self._stats = self._load()

    # ------------------------------------------------------------ control
    def start(self, minutes: float = 25, break_minutes: float = 5, cycles: int = 4,
              now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.work_s = max(60, int(minutes * 60))
        self.break_s = max(60, int(break_minutes * 60))
        self.cycles = max(1, int(cycles))
        self.done_sessions = 0
        self._begin("focus", now)

    def stop(self, now: float | None = None) -> int:
        """Stops early. Returns whole minutes focused in the unfinished session."""
        now = time.time() if now is None else now
        partial = 0
        if self.phase == "focus":
            partial = int((now - self._started) // 60)
            self._add_minutes(partial, sessions=0)
        self.phase = "idle"
        return partial

    def tick(self, now: float | None = None) -> FocusEvent | None:
        now = time.time() if now is None else now
        if self.phase == "idle" or now < self._ends:
            return None
        if self.phase == "focus":
            self.done_sessions += 1
            self._add_minutes(self.work_s // 60, sessions=1)
            if self.done_sessions >= self.cycles:
                self.phase = "idle"
                return FocusEvent("done", self.done_sessions)
            self._begin("break", now)
            return FocusEvent("break", self.done_sessions)
        self._begin("focus", now)
        return FocusEvent("focus", self.done_sessions)

    # ------------------------------------------------------------ status
    @property
    def active(self) -> bool:
        return self.phase != "idle"

    def remaining(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return max(0, int(round(self._ends - now))) if self.active else 0

    def progress(self, now: float | None = None) -> float:
        """0..1 through the current phase."""
        if not self.active:
            return 0.0
        now = time.time() if now is None else now
        total = self.work_s if self.phase == "focus" else self.break_s
        return max(0.0, min(1.0, 1 - (self._ends - now) / total))

    def describe(self, now: float | None = None) -> str:
        today_min, today_sessions = self.today()
        if not self.active:
            return (f"No focus session running. Today: {today_sessions} sessions, "
                    f"{today_min} minutes focused.")
        r = self.remaining(now)
        what = "Focus" if self.phase == "focus" else "Break"
        return (f"{what}: {r // 60}:{r % 60:02d} left (session {self.done_sessions + 1} of "
                f"{self.cycles}). Today: {today_sessions} sessions, {today_min} minutes.")

    def today(self) -> tuple[int, int]:
        return self.day(date.today())

    def day(self, d: date) -> tuple[int, int]:
        rec = self._stats.get(d.isoformat(), {})
        return int(rec.get("minutes", 0)), int(rec.get("sessions", 0))

    # ------------------------------------------------------------ internals
    def _begin(self, phase: str, now: float) -> None:
        self.phase = phase
        self._started = now
        self._ends = now + (self.work_s if phase == "focus" else self.break_s)

    def _add_minutes(self, minutes: int, sessions: int) -> None:
        if minutes <= 0 and sessions <= 0:
            return
        key = date.today().isoformat()
        d = self._stats.setdefault(key, {"minutes": 0, "sessions": 0})
        d["minutes"] += minutes
        d["sessions"] += sessions
        for k in sorted(self._stats)[:-30]:          # keep a month
            del self._stats[k]
        self._save()

    def _load(self) -> dict:
        if not self.path:
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        if self.path:
            try:
                self.path.write_text(json.dumps(self._stats, separators=(",", ":")),
                                     encoding="utf-8")
            except OSError:
                pass
