"""Kuro's bigger features, mixed into the Controller to keep it readable:

  * Focus companion (pomodoro)          * Study mode (quiz / flashcards)
  * Daily briefing                      * Google Calendar + Gmail (read-only)
  * Select-anywhere quick actions       * "Hey Kuro" wake word
  * Do-not-disturb during fullscreen    (multi-monitor lives in the cat widget)
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta

from PySide6.QtCore import QTimer

from . import briefing, location, quick, study, winutil
from .focus import FocusTimer
from .google import Google, GoogleError, fmt_email, fmt_event
from .tools import Tool, ToolError, _obj, _resolve_file, read_file

log = logging.getLogger("kuro")


class FeaturesMixin:
    # ================================================================ setup
    def _init_features_model(self, d) -> None:
        """Before the brain exists: state + tools."""
        self.focus = FocusTimer(d / "focus.json")
        self.google = Google(d / "google.token", self.s.google_client_id,
                             self.s.google_client_secret)
        self.dnd = False
        self._held: list[str] = []            # messages kept back during do-not-disturb
        self._quick_hwnd = 0
        self._briefing_busy = False
        self.wake = None
        for t in self._feature_tools():
            self.tools[t.name] = t

    def _init_features_view(self) -> None:
        """After the widgets exist: timers, panels, hotkeys, wake word."""
        from .ui.quick import QuickPanel
        self.quick = QuickPanel()
        self.quick.action.connect(self._quick_action)
        self.quick.copy_clicked.connect(self._quick_copy)
        self.quick.replace_clicked.connect(self._quick_replace)
        self.sig.quick_text.connect(self._quick_open)
        self.sig.quick_result.connect(lambda t: self.quick.set_result(t))
        self.sig.hotkey_quick.connect(self._quick_hotkey)
        self.sig.study_ready.connect(self._study_open)
        self.sig.briefing.connect(self._briefing_show)
        self.sig.google_status.connect(self.hud.settings.set_google_status)
        self.sig.wake.connect(self._on_wake)
        self.hud.settings.google_clicked.connect(self._google_toggle)
        self._google_status()

        self._focus_timer = QTimer(timeout=self._focus_tick)
        self._focus_timer.start(1000)
        self._dnd_timer = QTimer(timeout=self._dnd_check)
        self._dnd_timer.start(1500)
        self._start_wake()

    def _feature_hotkeys(self, keyboard) -> None:
        keyboard.add_hotkey(self.s.hotkey_quick, self.sig.hotkey_quick.emit)

    # ================================================================ tools
    def _feature_tools(self) -> list[Tool]:
        def study_tool(path: str = "", text: str = "", mode: str = "quiz", count: int = 8,
                       focus: str = "") -> str:
            mode = "cards" if mode.startswith(("flash", "card")) else "quiz"
            if path:
                p = _resolve_file(path, self.s.search_roots)
                source = read_file(str(p), max_chars=study.MAX_SOURCE).split("\n", 1)[-1]
                title = p.stem
            elif text:
                source, title = text, "Your notes"
            else:
                raise ToolError("what should I make it from? Give a file or paste the text")
            provider = self.brain.provider
            try:
                items = study.make(lambda pr: provider.complete(pr, max_tokens=6000),
                                   source, mode, count, focus)
            except study.StudyError as e:
                raise ToolError(str(e))
            self.sig.study_ready.emit((items, mode, title))
            kind = "question quiz" if mode == "quiz" else "flashcards"
            return (f"Opened {len(items)} {kind} from '{title}' in the Study panel. "
                    f"Tell the user it's ready; don't list the questions.")

        def focus_tool(action: str = "start", minutes: int = 0, break_minutes: int = 0) -> str:
            if action == "start":
                self.focus.start(minutes or self.s.focus_minutes,
                                 break_minutes or self.s.break_minutes)
                return "Focus session started. " + self.focus.describe()
            if action == "stop":
                mins = self.focus.stop()
                return f"Focus stopped ({mins} min in this session). " + self.focus.describe()
            return self.focus.describe()

        def briefing_tool() -> str:
            return self._build_briefing()

        def calendar_tool(days: int = 1, start_date: str = "") -> str:
            start = None
            if start_date:
                try:
                    start = datetime.fromisoformat(start_date).astimezone()
                except ValueError:
                    raise ToolError("start_date must look like 2026-11-09")
            try:
                evs = self.google.events(days, start)
            except GoogleError as e:
                raise ToolError(str(e))
            if not evs:
                return "No events in that range."
            return "Events:\n" + "\n".join(fmt_event(e) for e in evs)

        def add_event_tool(title: str, start: str, minutes: int = 60) -> str:
            try:
                when = datetime.fromisoformat(start)
            except ValueError:
                raise ToolError("start must be a local date-time like 2026-11-09T15:00")
            try:
                self.google.add_event(title, when, minutes)
            except GoogleError as e:
                raise ToolError(str(e))
            return f"Added '{title}' on {when.strftime('%a %b %d, %I:%M %p')} ({minutes} min)."

        def email_tool(query: str = "", count: int = 5) -> str:
            try:
                mails = self.google.emails(query or "is:unread in:inbox", count)
            except GoogleError as e:
                raise ToolError(str(e))
            if not mails:
                return "No matching emails."
            return "Emails (newest first):\n" + "\n".join(
                f"- {fmt_email(m)} — {m['snippet'][:140]}" for m in mails)

        tools = [
            Tool("study", "Make a quiz or flashcards from the user's notes, a PDF/Word file or "
                 "pasted text, and open them in the Study panel. mode: quiz or flashcards.",
                 _obj({"path": {"type": "string", "description": "File path or file name"},
                       "text": {"type": "string", "description": "Pasted notes"},
                       "mode": {"type": "string", "enum": ["quiz", "flashcards"]},
                       "count": {"type": "integer", "minimum": 3, "maximum": 20},
                       "focus": {"type": "string", "description": "Topic to focus on"}}),
                 study_tool),
            Tool("focus", "Focus companion (pomodoro). action start (optional minutes, "
                 "break_minutes), stop, or status.",
                 _obj({"action": {"type": "string", "enum": ["start", "stop", "status"]},
                       "minutes": {"type": "integer"}, "break_minutes": {"type": "integer"}},
                      ["action"]), focus_tool),
            Tool("daily_briefing", "Today's briefing: weather, calendar, reminders, unread "
                 "email. Use when asked 'brief me', 'what's my day', 'good morning'.",
                 _obj({}), briefing_tool),
        ]
        tools += [
            Tool("calendar_events", "List Google Calendar events (needs Google connected). "
                 "days = how many days from start_date (default today).",
                 _obj({"days": {"type": "integer", "minimum": 1, "maximum": 31},
                       "start_date": {"type": "string", "description": "YYYY-MM-DD"}}),
                 calendar_tool),
            Tool("add_calendar_event", "Add an event to Google Calendar. start is a local "
                 "date-time YYYY-MM-DDTHH:MM (work it out from the current time).",
                 _obj({"title": {"type": "string"}, "start": {"type": "string"},
                       "minutes": {"type": "integer", "minimum": 5}}, ["title", "start"]),
                 add_event_tool, risky=True,
                 describe=lambda a: f"Add “{a.get('title', '?')}” to your Google Calendar "
                                    f"at {a.get('start', '?')}"),
            Tool("check_email", "Read the user's Gmail (read-only): unread by default, or a "
                 "Gmail search query like 'from:prof' or 'subject:thesis'.",
                 _obj({"query": {"type": "string"},
                       "count": {"type": "integer", "minimum": 1, "maximum": 15}}),
                 email_tool),
        ]
        return tools

    # ================================================================ focus
    def _focus_tick(self) -> None:
        ev = self.focus.tick()
        if self.focus.active:
            r = self.focus.remaining()
            self.cat.set_focus(self.focus.progress(), f"{r // 60}:{r % 60:02d}")
            if (self.focus.phase == "focus" and self.s.focus_nap and not self.busy
                    and self.cat.state == "idle" and not self.hud.is_open()):
                self.cat.set_state("sleeping")       # quiet nap while you work
        else:
            if self.cat.focus_progress is not None:
                self.cat.set_focus(None)
        if not ev:
            return
        if ev.kind == "break":
            self._wake_cat()
            self.cat.react("surprised", 0.8)
            self.cat._action, self.cat._action_t = "stretch", 0.0
            self.say(f"Break time! {self.focus.break_s // 60} minutes. Stretch with me, "
                     f"drink some water.", state="happy")
            self.tray.showMessage(self.s.cat_name, "Break time!")
        elif ev.kind == "focus":
            self.say("Back to it! I'll keep quiet.", state="happy")
        elif ev.kind == "done":
            self._wake_cat()
            mins, sessions = self.focus.today()
            self.cat.react("love", 2.5)
            self.cat.hop(220)
            self.cat._burst(36)
            self.say(f"All {ev.session} sessions done! {mins} minutes of focus today. "
                     f"I'm proud of you.", state="happy")

    def toggle_focus(self) -> None:
        self._touch()
        if self.focus.active:
            mins = self.focus.stop()
            self.cat.set_focus(None)
            self.say(f"Focus stopped. {mins} minutes in. Good effort!", state="idle")
        else:
            self.focus.start(self.s.focus_minutes, self.s.break_minutes)
            self.say(f"Focus mode: {self.s.focus_minutes} minutes. I'll nap; "
                     f"you've got this.", state="happy")
            if self.s.focus_nap:                      # curl up on the taskbar
                geo = self.cat._screen_geo()
                self.cat.blink_to(geo.right() - self.cat.width() - 30,
                                  geo.bottom() - self.cat.base.y() + 3)

    def _wake_cat(self) -> None:
        if self.cat.state == "sleeping":
            self.cat.set_state("idle")

    # ================================================================ study
    def _study_open(self, payload) -> None:
        items, mode, title = payload
        self.hud.study.load(items, mode, title)
        self.hud.open("study")

    # ================================================================ briefing
    def _build_briefing(self) -> str:
        now = datetime.now()
        weather, events, unread = "", None, None
        try:
            weather = location.weather(self.s)          # no city to type, any brain
        except Exception as e:
            log.info("briefing weather skipped: %s", e)
        if self.google.connected:
            try:
                events = [fmt_event(e) for e in self.google.events(1)]
            except GoogleError as e:
                log.info("briefing calendar skipped: %s", e)
            try:
                unread = [fmt_email(m) for m in self.google.emails(count=5)]
            except GoogleError as e:
                log.info("briefing email skipped: %s", e)
        today = now.date()
        reminders = [f"{r.message} at {r.due_dt.strftime('%I:%M %p').lstrip('0')}"
                     for r in self.reminders.all() if r.due_dt.date() == today]
        return briefing.compose(self.s.user_name, now, weather, reminders, events, unread,
                                self.focus.day(today - timedelta(days=1)))

    def maybe_briefing(self) -> None:
        """Once a day, the first time he sees you (after 4 am)."""
        today = date.today().isoformat()
        if (not self.s.morning_briefing or self.s.last_briefing == today
                or datetime.now().hour < 4 or self._briefing_busy or self.dnd):
            return
        self._briefing_busy = True
        self.s.last_briefing = today
        try:
            self.s.save()
        except OSError:
            pass

        def work():
            try:
                self.sig.briefing.emit(self._build_briefing())
            finally:
                self._briefing_busy = False
        threading.Thread(target=work, daemon=True, name="briefing").start()

    def _briefing_show(self, text: str) -> None:
        if "\n" not in text:
            return                            # nothing beyond "Morning!": skip it
        self.hud.chat.add("cat", text)
        self.say(text, state="happy")

    # ================================================================ google
    def _google_status(self) -> None:
        g = self.google
        if g.connected:
            self.sig.google_status.emit(True, f"Connected as {g.email or 'your account'}.")
        elif g.configured:
            self.sig.google_status.emit(False, "Not connected.")
        else:
            self.sig.google_status.emit(False, "Add a client ID first (see README).")

    def _google_toggle(self) -> None:
        if self.google.connected:
            self.google.disconnect()
            self._google_status()
            return
        self.google.client_id = self.s.google_client_id
        self.google.client_secret = self.s.google_client_secret
        try:
            self.s.save()
        except OSError:
            pass

        def work():
            try:
                who = self.google.connect()
                self.sig.google_status.emit(True, f"Connected as {who}.")
            except GoogleError as e:
                self.sig.google_status.emit(False, str(e))
            except Exception as e:
                log.exception("google connect")
                self.sig.google_status.emit(False, f"Sign-in failed: {e}")
        threading.Thread(target=work, daemon=True, name="google").start()

    # ================================================================ quick actions
    def _quick_hotkey(self) -> None:
        if not self.s.ready:
            self.say("I need a brain first (Settings → AI & Voice).", state="idle")
            return
        self._quick_hwnd = winutil.foreground_window()

        def work():
            try:
                self.sig.quick_text.emit(quick.grab_selection())
            except Exception as e:
                log.info("quick grab failed: %s", e)
                self.sig.quick_text.emit("")
        threading.Thread(target=work, daemon=True, name="quick").start()

    def _quick_open(self, text: str) -> None:
        if not text.strip():
            self.say(f"Select some text first, then press {self.s.hotkey_quick}.", state="idle")
            return
        self._quick_src = text
        self.quick.open_for(text)

    def _quick_action(self, action: str) -> None:
        src = getattr(self, "_quick_src", "")

        def work():
            try:
                out = self.brain.provider.complete(
                    quick.prompt_for(action, src, self.s.translate_to), max_tokens=2048)
                self.sig.quick_result.emit(out.strip() or "(nothing came back, try again)")
            except Exception as e:
                self.sig.quick_result.emit(f"Couldn't do that: {getattr(e, 'detail', '') or e}")
        threading.Thread(target=work, daemon=True, name="quick-ai").start()

    def _quick_copy(self) -> None:
        try:
            import pyperclip
            pyperclip.copy(self.quick.result_text)
            self.quick.set_result(self.quick.result_text + "\n\n✓ Copied")
        except Exception:
            pass

    def _quick_replace(self) -> None:
        text = self.quick.result_text
        self.quick.hide()
        hwnd = self._quick_hwnd

        def work():
            winutil.focus_window(hwnd)
            time.sleep(0.2)
            quick.paste_text(text)
        threading.Thread(target=work, daemon=True, name="quick-paste").start()

    # ================================================================ wake word
    def _start_wake(self) -> None:
        if not (self.s.wake_word and self.s.voice_enabled):
            return
        try:
            from .voice import WakeWord
        except Exception:
            return
        self.wake = WakeWord(on_wake=self.sig.wake.emit)
        if not self.wake.start():
            log.info("wake word off: %s", self.wake.error)
            self.wake = None

    def _resume_wake(self, delay: float = 1.0) -> None:
        if self.wake:
            QTimer.singleShot(int(delay * 1000), lambda: setattr(self.wake, "paused", False))

    def _on_wake(self, command: str) -> None:
        self._touch()
        self.cat.react("surprised", 0.6)
        if self.busy:
            self._resume_wake(3)
            return
        if command:
            self.send(command)
        else:
            self.start_listening()

    def apply_wake_setting(self) -> None:
        if self.s.wake_word and not self.wake:
            self._start_wake()
        elif not self.s.wake_word and self.wake:
            self.wake.stop()
            self.wake = None

    # ================================================================ do-not-disturb
    def _dnd_check(self) -> None:
        fs = self.s.dnd_fullscreen and winutil.fullscreen_app()
        if fs == self.dnd:
            return
        self.dnd = fs
        self.cat.set_away(fs)
        if fs:
            if self.hud.page == "bubble":
                self.hud.close_panel()
            self.player.hide()
        else:
            if self.music.current and self.music.current.playing:
                self._on_music(self.music.current)      # only re-shows real music
            held, self._held = self._held, []
            if held:
                QTimer.singleShot(900, lambda: self.say(
                    "While you were busy: " + " · ".join(held), state="happy"))
