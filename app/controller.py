"""Wires everything together: Kuro, his HUD, brain, tools, memory, voice.

Threading rule: Qt widgets may only be touched on the main thread.
Slow work (brain calls, listening) runs on worker threads and reports
back through Qt signals, which Qt delivers safely to the main thread.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .brain import Brain, context_prompt, strip_actions
from . import activity, scan
from .i18n import chat_starters, t
from .config import Settings, app_root, data_dir
from .features import FeaturesMixin
from .memory import HabitLog, MemoryStore, Reflector
from .music import MusicWatcher, is_music as _music_like
from . import providers
from .reminders import ReminderStore
from .skills import build_skills
from .tools import Tool, build_tools
from .ui import theme
from .ui.cat import CatWidget
from .ui.hud import Hud, RadialMenu
from .ui.nowplaying import NowPlaying

log = logging.getLogger("kuro")

TOOL_STATUS = {
    "open_app": "Opening that…", "open_url": "Opening the page…",
    "web_search": "Searching…", "search_files": "Sniffing through your files…",
    "take_screenshot": "Snap!", "set_volume": "Adjusting volume…",
    "add_reminder": "Noted!", "run_powershell": "Running it…",
    "remember": "I'll remember that.", "forget": "Forgotten.",
    "look_at_screen": "Let me look…", "web_answer": "Checking the web…",
    "read_file": "Reading it…", "media": "♪", "now_playing": "♪ Let me listen…",
    "play_media": "♪ Putting it on…", "seek_media": "Skipping…",
    "scan": "Scanning…",
}


class Signals(QObject):
    reply = Signal(str)
    stream = Signal(str)               # the reply so far, while it streams in
    tool_used = Signal(str)
    heard = Signal(str)
    voice_error = Signal(str)
    hotkey_chat = Signal()
    hotkey_voice = Signal()
    confirm_request = Signal(object)   # _ConfirmJob
    learned = Signal(list)
    scan_fx = Signal(bool)             # glowing-eyes scan effect on/off
    hotkey_quick = Signal()
    quick_text = Signal(str)
    quick_result = Signal(str)
    study_ready = Signal(object)
    briefing = Signal(str)
    google_status = Signal(bool, str)
    wake = Signal(str)
    brain_switched = Signal(str, str)


def battery_alert(bat, warned: int) -> int | None:
    """Which warning level (20 or 10) to give now, or None. `warned` is the
    lowest level already given since the charger was last in."""
    if bat is None or bat.power_plugged:
        return None
    for level in (10, 20):
        if bat.percent <= level:
            return level if warned > level else None
    return None


class _ConfirmJob:
    def __init__(self, tool: Tool, args: dict):
        self.tool, self.args = tool, args
        self.allowed = False
        self.done = threading.Event()


def test_key(provider: str, key: str, model: str = "") -> str:
    """Used by Settings → Test: one tiny call to the chosen brain."""
    return providers.make(provider, key, model).test()


class Controller(FeaturesMixin):
    def __init__(self, app: QApplication, settings: Settings):
        self.app = app
        self.s = settings
        self.sig = Signals()
        self.busy = False
        self.last_activity = time.monotonic()
        self._job: _ConfirmJob | None = None

        # --- model side ---
        d = data_dir()
        self.history_path = d / "history.json"
        self.reminders = ReminderStore(d / "reminders.json")
        self.memory = MemoryStore(d / "memory.json")
        self.habits = HabitLog(d / "actions.jsonl")
        # learning pass: every 6 messages, on the light model, to spare the
        # main model's small free daily quota
        self.reflector = Reflector(self.memory, settings.cat_name, settings.user_name,
                                   every_n_user_turns=4)
        self._last_query = ""
        self.music = MusicWatcher()
        self.scan_path = d / "scan.json"
        self.scan = scan.load(self.scan_path) or {}
        self.active = ("", "")
        self.typing = activity.Typing()
        self.tracker = activity.Tracker()
        for old in ("usage.json",):                  # no longer used: free the space
            try:
                (d / old).unlink()
            except OSError:
                pass
        self.tools = build_tools(self.reminders, settings.search_roots,
                                 settings.allow_shell, memory=self.memory,
                                 brain=lambda: self.brain.provider,
                                 music=lambda: self.music.current,
                                 seek=self.music.seek,
                                 scan=self._scan_pc, scan_fx=self.sig.scan_fx.emit)
        more, self.todo, self.routines = build_skills(d)
        for tool in more:
            self.tools[tool.name] = tool
        self._battery_warned = 100               # lowest level already mentioned
        self._init_features_model(d)
        self.brain = Brain(settings, self.tools, confirm=self._confirm_from_worker,
                           notify=self.sig.tool_used.emit, context=self._context,
                           on_action=self.habits.record,
                           on_switch=self.sig.brain_switched.emit)
        self.brain.load(self.history_path)
        self.pending_suggestion = ""
        self._suggested: set[tuple[date, str]] = set()
        self._last_suggest = 0.0

        # --- view side ---
        icon = QIcon(str(app_root() / "assets" / "icon.png"))
        app.setWindowIcon(icon)
        self.cat = CatWidget(settings.scale, can_wander=self._can_wander)
        self.cat.typing_rate = lambda: (self.typing.rate() if self.s.aware
                                        and self.s.react_activity else 0.0)
        self.hud = Hud(self.cat, settings, self.memory, self._test_key)
        self.player = NowPlaying(self.cat, on_toggle=lambda: self._media("play_pause"),
                                 on_next=lambda: self._media("next"))
        for who, text in self.brain.visible_turns()[-12:]:
            self.hud.chat.add(who, text, animate=False)

        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip(settings.cat_name)
        tray_menu = QMenu()
        tray_menu.setStyleSheet(TRAY_CSS)
        tray_menu.addAction("Chat", self.toggle_chat)
        tray_menu.addAction("Settings", lambda: self.hud.open("settings"))
        tray_menu.addSeparator()
        tray_menu.addAction("Quit", self.quit)
        self._tray_menu = tray_menu
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(
            lambda r: self.toggle_chat() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        # --- wiring ---
        self.cat.clicked.connect(self._poke)
        self.cat.double_clicked.connect(self.toggle_chat)
        self.cat.menu_requested.connect(self._radial)
        self.cat.moved.connect(self.hud.follow)
        self.cat.moved.connect(self.player.follow)
        self.music.changed.connect(self._on_music)
        self._music_verdicts: dict[tuple[str, str], bool] = {}
        self._hud_was_open = False
        self._hud_watch = QTimer(timeout=self._watch_hud)
        self._hud_watch.start(200)
        self.hud.submitted.connect(self.send)
        self.hud.mic_clicked.connect(self.start_listening)
        self.hud.clear_clicked.connect(self.reset_chat)
        self.hud.settings_saved.connect(self._settings_saved)
        self.hud.hotkey_capture.connect(lambda capturing: self._bind_hotkeys(not capturing))
        self.hud.confirm_answered.connect(self._confirm_answered)
        self.sig.reply.connect(self._on_reply)
        self.sig.stream.connect(self._on_stream)
        self._streamed = ""
        self._spoken = 0
        self.sig.tool_used.connect(self._on_tool)
        self.sig.heard.connect(self._on_heard)
        self.sig.voice_error.connect(self._on_voice_error)
        self.sig.confirm_request.connect(self._show_confirm)
        self.sig.learned.connect(self._on_learned)
        self.sig.scan_fx.connect(lambda on: self.cat.set_scanning(on))
        self.sig.hotkey_chat.connect(self.toggle_chat)
        self.sig.hotkey_voice.connect(self.start_listening)
        self.sig.brain_switched.connect(self._brain_switched)

        self._init_features_view()
        self._tick = QTimer(timeout=self._heartbeat)
        self._tick.start(5000)

        self.speaker = self.listener = None
        self._settle_waits = 0
        self._init_voice()
        self._init_hotkeys()

        self.cat.show()
        self.hud.chat.set_status("online" if settings.ready else "offline")
        if settings.onboarded:
            QTimer.singleShot(600, self._greet)
        else:
            QTimer.singleShot(900, self._welcome)
        # find the city quietly in the background (for weather), nothing to type
        threading.Thread(target=self._locate, daemon=True, name="locate").start()

    def _locate(self) -> None:
        try:
            from . import location
            location.locate(self.s)
        except Exception as e:
            log.info("auto-location skipped: %s", e)

    # ------------------------------------------------------------------
    # chat
    # ------------------------------------------------------------------
    def send(self, text: str) -> None:
        if self.busy:
            return
        self._touch()
        self.busy = True
        self._last_query = text
        self.hud.chat.add("you", text)
        self.hud.set_busy(True)
        self.cat.set_state("thinking")
        reflect = self.s.learn_enabled and self.reflector.note_turn()

        self._streamed, self._spoken = "", 0

        def work():
            reply = self.brain.ask(text, on_text=self.sig.stream.emit)
            try:
                self.brain.save(self.history_path)
            except OSError:
                pass
            self.sig.reply.emit(reply)
            if reflect and self.s.ready:
                try:
                    provider = self.brain.provider
                except Exception:
                    return

                def complete(prompt):
                    return provider.complete(prompt, light=True)
                learned = self.reflector.reflect(complete, list(self.brain.history))
                if learned:
                    self.sig.learned.emit(learned)
        threading.Thread(target=work, daemon=True, name="brain").start()

    def _on_stream(self, text: str) -> None:
        """Words arriving: grow the chat bubble (and the one above his head),
        and start speaking as soon as the first sentence is complete."""
        if not self.busy or not text:
            return
        if not self._streamed:
            self.cat.set_state("talking")
        self._streamed = text
        self.hud.chat.stream(text)
        if self.dnd:
            return                                 # fullscreen: just the chat, quietly
        if self.hud.page != "chat":
            self.hud.stream_bubble(text)
        self._speak_ready(text, final=False)

    def _speak_ready(self, text: str, final: bool) -> None:
        """Speak every sentence that's complete and not spoken yet."""
        if not (self.speaker and self.s.speak_replies):
            self._spoken = len(text)
            return
        rest = text[self._spoken:]
        if final:
            chunk = rest
        else:
            ends = [m.end() for m in re.finditer(r"[.!?…]+[\"')\]]*\s+", rest)]
            chunk = rest[:ends[-1]] if ends else ""
        if chunk.strip():
            self.speaker.say(chunk.strip())
        self._spoken += len(chunk)

    def _on_reply(self, text: str) -> None:
        self.busy = False
        self.pending_suggestion = ""
        self.hud.set_busy(False)
        if not self.s.ready:
            self.hud.chat.set_status("offline")
        streamed, self._streamed = self._streamed, ""
        if not (streamed and self.hud.chat.end_stream(text)):
            self.hud.chat.add("cat", text)
            self.say(text)                           # not streamed: the classic way
        elif self.dnd:
            self._held.append(text if len(text) < 140 else text[:137] + "…")
        else:
            if self.hud.page != "chat":
                self.hud.stream_bubble(text)
            if text.startswith(streamed[: self._spoken]):
                self._speak_ready(text, final=True)  # say whatever's left
            # (if the final text changed shape, don't repeat what was already said)
            self._touch()
            duration = min(max(2.0, len(text) / 14), 12)
            QTimer.singleShot(int(duration * 1000), self._settle)
        self._resume_wake(2 + len(text) / 14)

    def _test_key(self, provider: str, key: str, model: str = "") -> str:
        base = self.s.cloud_url if provider == "cloud" else ""
        return providers.make(provider, key, model, base=base).test()

    def _brain_switched(self, frm: str, to: str) -> None:
        log.info("brain switched %s -> %s", frm, to)
        self.hud.chat.add("cat", t(self.s, "switched", frm=frm, to=to),
                          animate=False)

    def _on_learned(self, facts: list) -> None:
        log.info("learned: %s", facts)        # quietly; see Memory to review

    def _context(self) -> str:
        extra = []
        u = self.s.user_name
        if self.pending_suggestion:
            extra.append(f"You just offered (unprompted) to {self.pending_suggestion} because "
                         f"{u} usually does that around now. If they say yes, do it.")
        where = self.s.city.strip() or self.s.auto_city
        if where:
            extra.append(f"{u} is in or near {where} (use it for weather and local questions).")
        if self.s.aware:
            app, title = self.active
            if app:
                extra.append(f"Right now {u} is using {app}" + (f": \"{title[:90]}\"." if title else "."))
            cur = self.tracker.current
            if cur.kind not in ("other", "browsing"):
                what = cur.project or cur.subject
                extra.append(f"{u} has been {cur.kind} for about {self.tracker.minutes} min"
                             + (f" ({what})." if what else ".")
                             + " Tailor help to that when it fits.")
        names = self.routines.names()
        if names:
            extra.append("Saved routines (when the user says one of these names, run it with "
                         f"the routine tool): {', '.join(names)}.")
        left = self.todo.open_count()
        if left:
            extra.append(f"{u}'s to-do list has {left} thing{'s' if left != 1 else ''} on it "
                         "(use the todo tool to read it).")
        return context_prompt(self.s, self.memory.prompt_block(query=self._last_query),
                              self.habits.summary(),
                              "\n\n".join(extra))

    # ------------------------------------------------------------------
    # PC scan (only when you ask for it in chat: the `scan` tool, what=pc)
    # ------------------------------------------------------------------
    def _scan_pc(self) -> str:
        """Runs on the brain's worker thread."""
        result = scan.run_scan(lambda _m: None, previous=self.scan or None)
        scan.save(result, self.scan_path)
        self.scan = result
        text = scan.summary(result, max_chars=3000)
        if self.s.ready and self.s.learn_enabled:
            try:
                provider = self.brain.provider
                for f in scan.facts_from_scan(lambda p: provider.complete(p, light=True),
                                              text, self.s.cat_name, self.s.user_name):
                    self.memory.add(f["text"], f.get("category", "general"), source="scan")
            except Exception as e:
                log.info("scan facts skipped: %s", e)
        return f"Scanned in {result['seconds']}s (names only, nothing opened). {text}"

    def _on_tool(self, name: str) -> None:
        if name in TOOL_STATUS:
            self.hud.say(TOOL_STATUS[name], seconds=2.5)

    def say(self, text: str, state: str = "talking") -> None:
        """Kuro says a line: HUD bubble (or the open chat), animation, voice.
        During do-not-disturb, lines are held and delivered afterwards."""
        text = strip_actions(text) or text
        if self.dnd:
            self._held.append(text if len(text) < 140 else text[:137] + "…")
            return
        self._touch()
        self.cat.set_state(state)
        self.hud.say(text)
        if self.speaker and self.s.speak_replies:
            self.speaker.say(text)
        duration = min(max(2.0, len(text) / 14), 12)
        QTimer.singleShot(int(duration * 1000), self._settle)

    def _settle(self) -> None:
        if (self.speaker and self.speaker.busy and self.s.speak_replies
                and self.cat.state == "talking" and self._settle_waits < 40):
            self._settle_waits += 1                      # still speaking: keep "talking"
            QTimer.singleShot(500, self._settle)
            return
        self._settle_waits = 0
        if not self.busy and self.cat.state in ("talking", "happy", "listening"):
            # during a focus nap, go straight from the happy face to dozing off
            # (via "idle" his eyes would pop wide open for a second first)
            napping = (self.focus.active and self.focus.phase == "focus"
                       and self.s.focus_nap and not self.hud.is_open())
            self.cat.set_state("sleeping" if napping else "idle")

    def _can_wander(self) -> bool:
        menu = getattr(self, "_menu", None)
        return (self.s.wander and not self.busy and not self.hud.is_open()
                and not self.dnd and not self.focus.active
                and not (menu is not None and menu.isVisible()))   # stay put under his menu

    def toggle_chat(self) -> None:
        self._touch()
        self.hud.toggle_chat()

    def reset_chat(self) -> None:
        self.brain.reset()
        try:
            self.brain.save(self.history_path)
        except OSError:
            pass
        self.hud.chat.clear()

    def _radial(self, head) -> None:
        self._touch()
        self.hud.close_panel()            # one thing above his head at a time
        menu = RadialMenu([
            ("chat", "CHAT", self.toggle_chat),
            ("mic", "TALK", self.start_listening),
            ("timer", "FOCUS", self.toggle_focus),
            ("gear", "SETTINGS", lambda: self.hud.open("settings")),
            ("power", "QUIT", self.quit),
        ])
        old = getattr(self, "_menu", None)
        if old is not None:
            old.close()
        self._menu = menu
        # the menu stays pinned to his head if he moves (dragged, reminder hop...)
        self.cat.moved.connect(lambda: menu.follow(self.cat.head_anchor()))
        menu.popup(head)

    def _on_music(self, track) -> None:
        """Miniplayer + DJ mode, but only for actual music. Ads, documentaries,
        vlogs and the like are left alone."""
        is_music = bool(track) and self._is_music(track)
        playing = bool(track and track.playing and is_music)
        self.cat.set_music(playing)
        self.player.set_track(track if is_music else None)
        if playing:
            self._touch()                     # music wakes him up

    def _is_music(self, track) -> bool:
        key = track.key
        if key not in self._music_verdicts:
            if len(self._music_verdicts) > 300:
                self._music_verdicts.clear()
            self._music_verdicts[key] = _music_like(track)
            log.info("media: %r by %r (%s, %ss) -> %s", track.title, track.artist,
                     track.source, int(track.duration), "music" if self._music_verdicts[key]
                     else "not music")
        return self._music_verdicts[key]

    def _media(self, action: str) -> None:
        tool = self.tools.get("media")
        if tool:
            tool.func(action)

    def _watch_hud(self) -> None:
        open_ = self.hud.is_open()
        if open_ != self._hud_was_open:
            self._hud_was_open = open_
            self.player.set_hud_open(open_)

    def _settings_saved(self) -> None:
        self.brain.refresh_provider()
        if abs(self.cat.k - 0.82 * self.s.scale) > 1e-6:
            self.cat.set_scale(self.s.scale)
        self._bind_hotkeys(True)
        if self.speaker:
            self.speaker.set_voice(self.s.voice, self.s.cute_voice)
        self.apply_wake_setting()
        self.google.client_id = self.s.google_client_id
        self.google.client_secret = self.s.google_client_secret
        self.hud.chat.set_status("online" if self.s.ready else "offline")
        self.hud.chat.set_starters(*chat_starters(self.s))       # language may have changed
        self.cat.set_state("happy")
        QTimer.singleShot(1200, self._settle)

    # ------------------------------------------------------------------
    # risky-action confirmation (worker thread -> UI thread and back)
    # ------------------------------------------------------------------
    def _confirm_from_worker(self, tool: Tool, args: dict) -> bool:
        job = _ConfirmJob(tool, args)
        self.sig.confirm_request.emit(job)
        job.done.wait()  # worker blocks here until you answer in the HUD
        return job.allowed

    def _show_confirm(self, job: _ConfirmJob) -> None:
        text = job.tool.describe(job.args) if job.tool.describe else \
            f"{job.tool.name} {job.args}"
        self._job = job
        self.cat.set_state("listening")
        self.hud.ask(text)

    def _confirm_answered(self, allowed: bool) -> None:
        job, self._job = self._job, None
        self.cat.set_state("thinking")
        if job:
            job.allowed = allowed
            job.done.set()

    # ------------------------------------------------------------------
    # voice
    # ------------------------------------------------------------------
    def _init_voice(self) -> None:
        if not self.s.voice_enabled:
            return
        try:
            from .voice import Listener, Speaker
        except Exception:
            return
        self.speaker = Speaker(self.s.tts_rate, self.s.voice, self.s.cute_voice)
        self.cat.voice_level = self.speaker.level        # his mouth follows his voice
        self.listener = Listener(self.s)

    def start_listening(self) -> None:
        if self.busy:
            return
        if not self.listener or not self.listener.available:
            why = self.listener.error if self.listener else "voice is turned off"
            self.say(f"I can't hear you: {why}.", state="idle")
            return
        self._touch()
        self.busy = True
        self.cat.set_state("listening")
        self.hud.chat.set_status("listening")
        self.hud.say("Listening…", seconds=8)

        def work():
            try:
                self.sig.heard.emit(self.listener.listen_once())
            except Exception as e:
                self.sig.voice_error.emit(str(e))
        threading.Thread(target=work, daemon=True, name="listen").start()

    def _on_heard(self, text: str) -> None:
        self.busy = False
        if not text:
            self._resume_wake()
        self.hud.chat.set_status("online")
        if not text:
            self.say(t(self.s, "missed"), state="idle")
            return
        if self.hud.page != "chat":
            self.hud.open("chat")
        self.send(text)

    def _on_voice_error(self, err: str) -> None:
        self.busy = False
        self._resume_wake(3)
        self.hud.chat.set_status("online")
        self.say(f"My ears aren't working: {err}", state="idle")

    # ------------------------------------------------------------------
    # hotkeys
    # ------------------------------------------------------------------
    def _init_hotkeys(self) -> None:
        try:
            import keyboard
            keyboard.on_press(self.typing.press)      # counts presses, never which keys
        except Exception as e:  # missing package, or no permission on Linux
            log.info("global hotkeys disabled: %s", e)
            return
        self._bind_hotkeys(True)

    def _bind_hotkeys(self, on: bool) -> None:
        """(Re)register the global shortcuts. Off while you pick a new one in
        Settings, so pressing it there doesn't also trigger it."""
        try:
            import keyboard
            for h in getattr(self, "_hotkey_handles", []):
                try:
                    keyboard.remove_hotkey(h)
                except (KeyError, ValueError):
                    pass
            self._hotkey_handles = []
            if not on:
                return
            for combo, emit in ((self.s.hotkey_chat, self.sig.hotkey_chat.emit),
                                (self.s.hotkey_voice, self.sig.hotkey_voice.emit),
                                (self.s.hotkey_quick, self.sig.hotkey_quick.emit)):
                try:
                    self._hotkey_handles.append(keyboard.add_hotkey(combo, emit))
                except (ValueError, KeyError) as e:
                    log.info("hotkey %s not usable: %s", combo, e)
        except Exception as e:
            log.info("global hotkeys disabled: %s", e)

    # ------------------------------------------------------------------
    # background: reminders, suggestions, sleeping
    # ------------------------------------------------------------------
    def _heartbeat(self) -> None:
        if self.s.aware:
            self.active = scan.active_window()
        self._react_to_activity()
        for r in self.reminders.pop_due():
            self.say(t(self.s, "reminder", msg=r.message), state="happy")
            self.cat.react("surprised", 1.0)
            self.cat.hop(170)
            self.tray.showMessage(f"{self.s.cat_name} reminds you", r.message)
        self._maybe_suggest()
        self._maybe_reflect_idle()
        self._battery_watch()
        idle_min = (time.monotonic() - self.last_activity) / 60
        if (idle_min >= self.s.sleep_after_minutes and not self.busy
                and self.cat.state == "idle" and self.cat.mode == "rest"
                and not self.hud.is_open()):
            self.cat.set_state("sleeping")

    def _battery_watch(self) -> None:
        """A laptop about to die takes your work with it: he says so once at
        20% and once more at 10%, and not again until it has been plugged in."""
        try:
            import psutil
            bat = psutil.sensors_battery()
        except Exception:
            return
        level = battery_alert(bat, self._battery_warned)
        if level is None:
            if bat is not None and (bat.power_plugged or bat.percent > 25):
                self._battery_warned = 100
            return
        self._battery_warned = level
        if self.busy or self.hud.is_open():
            return
        self.hud.say(t(self.s, "battery_low", pct=int(bat.percent)), seconds=8)
        self.cat.react("surprised", 1.2)
        self.cat.hop(120)

    def _react_to_activity(self) -> None:
        """Glasses while you code, a pencil while you write, a beret in Blender;
        now and then a short line about it (bubble only, never spoken)."""
        if not (self.s.aware and self.s.react_activity):
            self.cat.set_prop("")
            return
        act = activity.detect(*self.active)
        self.tracker.lang = self.s.language
        line = self.tracker.update(act)
        if not line:
            line = self.tracker.typing_burst(self.typing.rate(window=4.0))
        self.cat.set_prop(activity.PROPS.get(self.tracker.current.kind, ""))
        quiet = (self.busy or self.hud.is_open() or self.dnd or self.focus.active
                 or self.cat.state == "sleeping"
                 or (self.tracker.current.kind == "watching" and self.cat.music))
        if line and not quiet:
            self.hud.say(line, seconds=5)
            self.cat.set_state("happy")
            QTimer.singleShot(1500, self._settle)

    def _maybe_reflect_idle(self) -> None:
        """Learn from the latest chat once you've gone quiet for a few minutes,
        so nothing you said is missed even if it was under 4 messages."""
        r = self.reflector
        if (not self.s.learn_enabled or not self.s.ready or self.busy or r._since == 0
                or r._running or time.monotonic() - self.last_activity < 180):
            return
        r._running = True
        provider = self.brain.provider
        history = list(self.brain.history)

        def work():
            r._running = False                 # reflect() sets it again while it runs
            learned = r.reflect(lambda p: provider.complete(p, light=True), history)
            if learned:
                self.sig.learned.emit(learned)
        threading.Thread(target=work, daemon=True, name="reflect-idle").start()

    def _maybe_suggest(self) -> None:
        """Offer to do something you habitually do around this time."""
        if (not self.s.suggestions or self.busy or self.hud.is_open()
                or self.cat.state != "idle" or not self.s.ready
                or time.monotonic() - self._last_suggest < 30 * 60):
            return
        hit = self.habits.suggestion()
        if not hit:
            return
        _tool, key = hit
        if (date.today(), key) in self._suggested:
            return
        self._suggested.add((date.today(), key))
        self._last_suggest = time.monotonic()
        self.pending_suggestion = f"open {key}"
        when = datetime.now().strftime("%I:%M %p").lstrip("0")
        self.say(t(self.s, "suggest", when=when, app=key), state="happy")

    def _touch(self) -> None:
        self.last_activity = time.monotonic()
        if self.cat.state == "sleeping" and not self.focus.active:
            self.cat.set_state("idle")
        self.maybe_briefing()

    def _poke(self) -> None:
        if self.busy:
            return
        if self.focus.active:                 # don't break the focus nap: just the time
            self.hud.say(self.focus.describe(), seconds=4)
            return
        was_asleep = self.cat.state == "sleeping"
        self._touch()
        if was_asleep:
            self.say(t(self.s, "woke"), state="happy")
        else:
            self.cat.set_state("happy")
            self.cat.hop(150)
            QTimer.singleShot(1200, self._settle)

    def _welcome(self) -> None:
        """First launch: a sparkly entrance, then the welcome steps."""
        self.hud.welcome.step_changed.connect(self._welcome_step)
        self.hud.welcome.finished.connect(self._welcome_done)
        self.cat._burst(24)
        self.cat.hop(200)
        self.cat.react("love", 1.6)
        self.hud.open("welcome")

    def _welcome_step(self, i: int) -> None:
        if i == 1:
            self.cat.react("surprised", 0.8)
            self.cat.hop(140)
        elif i == 2:
            self.cat.set_state("thinking")
            QTimer.singleShot(1800, self._settle)
        elif i == 3:
            self.brain.refresh_provider()
            self.hud.chat.set_status("online" if self.s.ready else "offline")
            self.cat.react("love", 2.5)
            self.cat._burst(36)
            self.cat.hop(220)

    def _welcome_done(self) -> None:
        self.cat.set_state("happy")
        QTimer.singleShot(1500, self._settle)

    def _greet(self) -> None:
        """No spoken hello on startup. Only a silent setup hint when he has
        no brain yet, so a new user knows what to do."""
        if not self.s.ready:
            self.hud.say(t(self.s, "setup"),
                         seconds=8)

    def quit(self) -> None:
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        if self.speaker:
            self.speaker.stop()
        if self.wake:
            self.wake.stop()
        self.music.stop()
        self.tray.hide()
        self.app.quit()


TRAY_CSS = f"""
QMenu {{ background: #0d0d10; color: {theme.TEXT_HEX}; border: 1px solid #23252b;
         border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: #1a1d10; color: {theme.LIME_HEX}; }}
QMenu::separator {{ height: 1px; background: #23252b; margin: 5px 8px; }}
"""
