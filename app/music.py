"""What's playing right now.

Windows keeps a list of "media sessions": Spotify, YouTube in any browser,
the Media Player app, and so on. Each one reports its title, artist,
play/pause state and album art (that's how the volume flyout shows the song).
Kuro reads those through WinRT on a background thread, every ~1.5 s.

Needs the `winrt-*` packages (see requirements.txt). On other systems,
or if they're missing, music mode simply stays off.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("kuro")

BROWSERS = ("chrome", "msedge", "opera", "firefox", "brave", "vivaldi", "arc")


@dataclass
class Track:
    title: str
    artist: str = ""
    source: str = ""               # "Spotify", "Browser", or the app id
    playing: bool = False
    art: bytes | None = None       # album / video thumbnail (JPEG or PNG bytes)
    album: str = ""                # YouTube Music / Spotify fill this; plain YouTube doesn't
    kind: int | None = None        # Windows' guess: 1 music, 2 video (often missing)
    duration: float = 0.0          # seconds, 0 = unknown
    audible: bool | None = None    # is that app actually making sound? None = can't tell
    in_window: bool | None = None  # browsers: is it the tab you're on (window title)?

    @property
    def key(self) -> tuple[str, str]:
        return self.title, self.artist

    def describe(self) -> str:
        state = "playing" if self.playing else "paused"
        who = f" by {self.artist}" if self.artist else ""
        where = f" on {self.source}" if self.source else ""
        return f"'{self.title}'{who} ({state}{where})"


# ------------------------------------------------------------ is it music?
MUSIC_APPS = ("spotify", "zunemusic", "applemusic", "itunes", "deezer", "tidal",
              "amazonmusic", "musicbee", "foobar", "aimp", "winamp", "soundcloud")
NOT_MUSIC = re.compile(
    r"\b(advert\w*|sponsored|\bad\b|documentar\w*|trailer|teaser|podcast|episode|"
    r"ep\.? ?\d+|review|tutorial|how to|explained|vlog|gameplay|walkthrough|let's play|"
    r"reaction|reacts?|news|interview|lecture|course|lesson|full movie|highlights|"
    r"unboxing|asmr|commentary|analysis|recap|stream|livestream|vod|shorts?)\b", re.I)
MUSIC_WORDS = re.compile(
    r"(official (music )?video|official audio|official lyric|lyric video|lyrics?\b|"
    r"visuali[sz]er|\bm/?v\b|\(audio\)|\[audio\]|\bft\.|\bfeat\.|\bremix\b|\bcover\b|"
    r"acoustic|live session|karaoke|instrumental|\bost\b|soundtrack|slowed|reverb|"
    r"sped up|nightcore|lo-?fi|\bbeats?\b|music video|\bsong\b|\bopm\b|\bk-?pop\b)", re.I)
MUSIC_ARTIST = re.compile(r"(- topic$|vevo|records\b|music\b|official\b|\bband\b)", re.I)


PODCAST = re.compile(r"\b(podcast|episode|ep\.? ?\d+|chapter \d+|audiobook)\b", re.I)
STRONG_MUSIC = re.compile(r"(official (music |lyric )?(video|audio)|lyric video|visuali[sz]er|"
                          r"\(audio\)|\[audio\]|music video)", re.I)
STRONG_ARTIST = re.compile(r"(- topic$|vevo)", re.I)


# a social site's own page title ("TikTok - Make Your Day"), not a song
SITE_PAGE = re.compile(r"^\s*(\(\d+\+?\)\s*)?(tiktok|instagram|facebook|reels|twitch|reddit|"
                       r"netflix)\b\s*([-–|•:·]|$)"
                       r"|[|•·-]\s*(tiktok|instagram|facebook|reddit|twitch)\s*$", re.I)


def classify(t: Track, app_id: str = "") -> bool | None:
    """True = a song / music video, False = anything else (ads, documentaries,
    talk, games...), None = can't tell from the metadata alone."""
    title, artist = t.title or "", t.artist or ""
    if t.duration and t.duration < 45:            # ads, Shorts, sound bites
        return False
    if (STRONG_MUSIC.search(title) or STRONG_ARTIST.search(artist)
            or t.album.strip() or t.kind == 1):
        return True
    low = (app_id or getattr(t, "app_id", "") or t.source).lower()
    if any(m in low for m in MUSIC_APPS):          # Spotify etc.: music unless a podcast
        return not PODCAST.search(f"{title} {artist}")
    if NOT_MUSIC.search(title) or SITE_PAGE.search(title):
        return False
    if t.duration and t.duration > 20 * 60 and not MUSIC_WORDS.search(title):
        return False                              # long talks / movies (mixes say "mix")
    if MUSIC_WORDS.search(title) or MUSIC_ARTIST.search(artist):
        return True
    if " - " in title or " – " in title:          # "Hev Abi - Song": the usual music upload
        return True
    if t.duration:                                # songs are 1-9 minutes; most talk isn't
        return 75 <= t.duration <= 9 * 60
    return False


def is_music(t: Track, app_id: str = "") -> bool:
    """Decided on this PC from the title, channel, app and length: instant,
    and it never spends the brain's (small, free) daily quota."""
    return bool(classify(t, app_id))


class AudioMeter:
    """Which apps are making sound right now, from Windows' audio meters
    (pycaw). Runs on its OWN thread: COM must never be touched on the WinRT
    media thread, or WinRT's replies can't get through and it hangs forever."""

    def __init__(self, interval: float = 0.5, start: bool = True):
        self.interval = interval
        self.levels: dict[str, float] = {}       # process name -> last time it was loud
        self.ok = False
        self._stop = threading.Event()
        if start:
            threading.Thread(target=self._run, daemon=True, name="audio-meter").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        except Exception as e:
            log.info("audio meter off: %s", e)
            return
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                for sess in AudioUtilities.GetAllSessions():
                    proc = sess.Process
                    if proc is None:
                        continue
                    name = proc.name().lower().removesuffix(".exe")
                    self.levels.setdefault(name, 0.0)
                    meter = sess._ctl.QueryInterface(IAudioMeterInformation)
                    if meter.GetPeakValue() > 0.0005:
                        self.levels[name] = now
                self.ok = True
            except Exception as e:
                if self.ok:
                    log.info("audio meter read failed: %s", e)
                self.ok = False
            self._stop.wait(self.interval)

    def audible(self, app_id: str, within: float = 1.5) -> bool | None:
        """Has that app made sound in the last moment? None = can't tell."""
        if not self.ok:
            return None
        want = (app_id or "").lower().removesuffix(".exe")
        if "!" in want:                          # Store apps: "...!Spotify"
            want = want.split("!")[-1].split(".")[-1]
        if not want:
            return None
        now = time.monotonic()
        hits = [t for name, t in list(self.levels.items()) if name in want or want in name]
        if not hits:
            return None
        return any(now - t <= within for t in hits)


def _norm_title(s: str) -> str:
    s = re.sub(r"^\(\d+\)\s*", "", s or "")          # "(3) Hate Me - YouTube"
    return re.sub(r"\s+", " ", s).strip().lower()


def window_titles() -> list[str]:
    """Titles of visible top-level windows. Plain user32 calls: no COM,
    so it's safe on the WinRT thread."""
    import ctypes
    import platform
    if platform.system() != "Windows":
        return []
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    out: list[str] = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append(buf.value)
        return True
    user32.EnumWindows(proto(cb), 0)
    return out


def title_in_windows(title: str, titles: list[str]) -> bool:
    """A browser window is titled after its current tab ("Hate Me - YouTube").
    YouTube's hover previews never change it, so a match means it's the
    video you're actually on."""
    t = _norm_title(title)
    return bool(t) and any(_norm_title(w).startswith(t) or f" {t} " in f" {_norm_title(w)} "
                           for w in titles)


def source_name(app_id: str) -> str:
    low = (app_id or "").lower()
    if "spotify" in low:
        return "Spotify"
    if any(b in low for b in BROWSERS):
        return "Browser"
    return app_id.split("!")[-1].removesuffix(".exe") if app_id else ""


class MusicWatcher(QObject):
    """Emits `changed(Track | None)` when the song, artist or play state changes."""

    changed = Signal(object)

    def __init__(self, interval: float = 1.5, start: bool = True):
        super().__init__()
        self.interval = interval
        self.current: Track | None = None
        self.available = False
        self._pending: dict | None = None
        self._stop = threading.Event()
        self.meter = AudioMeter(start=start)
        if start:
            threading.Thread(target=self._run, daemon=True, name="music").start()

    def stop(self) -> None:
        self._stop.set()
        self.meter.stop()

    def seek(self, seconds: float, timeout: float = 4.0) -> bool:
        """Ask the playing app to jump to `seconds`. True if it accepted.
        (Spotify and most browsers support this; some players refuse.)"""
        loop, mgr = getattr(self, "_loop_obj", None), getattr(self, "_mgr", None)
        if loop is None or mgr is None:
            return False
        fut = asyncio.run_coroutine_threadsafe(self._seek(mgr, seconds), loop)
        try:
            return bool(fut.result(timeout))
        except Exception:
            return False

    @staticmethod
    async def _seek(mgr, seconds: float) -> bool:
        sess = None
        for s_ in mgr.get_sessions():
            st = s_.get_playback_info().playback_status
            if getattr(st, "name", "") == "PLAYING" or int(st) == 4:
                sess = s_
                break
        sess = sess or mgr.get_current_session()
        if sess is None:
            return False
        try:
            if not sess.get_playback_info().controls.is_playback_position_enabled:
                return False
        except Exception:
            pass
        # WinRT positions are in 100-nanosecond ticks
        return bool(await sess.try_change_playback_position_async(int(seconds * 10_000_000)))

    # A new song/video must keep playing (and, where we can measure it, be
    # audible) for this long before Kuro reacts. Hovering a thumbnail on
    # YouTube plays a muted preview for a few seconds: that never counts.
    CONFIRM = 3.0
    CONFIRM_SILENT = 12.0          # never heard it (muted, or the meter can't tell): wait longer
    # a browser video that isn't the tab on screen (background tab, or a hover
    # preview inside the page): only believed after this long
    CONFIRM_BACKGROUND = 15.0

    def offer(self, track: Track | None, now: float | None = None) -> None:
        """A raw reading from Windows. Passes it on only once it's clearly
        what you're actually playing."""
        now = time.monotonic() if now is None else now
        cur = self.current
        if track is None:
            self._pending = None
            self.feed(None)
            return
        if cur is not None and track.key == cur.key:
            self._pending = None
            self.feed(track)                            # same thing: pause/resume/art
            return
        if not track.playing:
            if cur is None:                             # paused at startup: remember quietly
                self.feed(track)
            elif not cur.playing and getattr(self, "_pending", None) is None:
                self.feed(track)                        # both paused: follow Windows
            return
        if track.in_window:                             # the tab you're on: no doubt
            self._pending = None
            self.feed(track)
            return
        p = getattr(self, "_pending", None)
        if p is None or p["key"] != track.key:
            self._pending = {"key": track.key, "since": now, "heard": track.audible}
            return
        if track.audible:
            p["heard"] = True
        waited = now - p["since"]
        if track.in_window is False:                    # browser, but not the tab on screen
            ok = waited >= self.CONFIRM_BACKGROUND
        else:
            ok = ((waited >= self.CONFIRM and p["heard"] is not False)
                  or waited >= self.CONFIRM_SILENT)
        if ok:
            self._pending = None
            self.feed(track)

    # Used by tests and by the thread below.
    def feed(self, track: Track | None) -> None:
        old = self.current
        if track and old and track.key == old.key and track.art is None:
            track.art = old.art                      # keep art we already fetched
        if (track is None) != (old is None) or (track and old and (
                track.key != old.key or track.playing != old.playing or
                (track.art is not None and old.art is None))):
            self.current = track
            self.changed.emit(track)
        else:
            self.current = track

    # ------------------------------------------------------------ windows
    def _run(self) -> None:
        try:
            from winrt.windows.media.control import (  # noqa: F401
                GlobalSystemMediaTransportControlsSessionManager as Manager)
        except Exception as e:
            log.info("music mode off (WinRT media control unavailable: %s)", e)
            return
        self.available = True
        log.info("music watcher on")
        try:
            asyncio.run(self._loop(Manager))
        except Exception:
            log.exception("music watcher stopped")

    async def _loop(self, Manager) -> None:
        mgr = await Manager.request_async()
        self._mgr = mgr
        self._loop_obj = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                # a stuck WinRT call must never freeze music mode silently
                self.offer(await asyncio.wait_for(self._read(mgr), 8))
            except Exception as e:                   # a session closed mid-read, etc.
                msg = f"{type(e).__name__}: {e}"
                if msg != getattr(self, "_last_err", ""):
                    self._last_err = msg
                    log.info("music read failed: %s", msg)
            await asyncio.sleep(self.interval)

    async def _read(self, mgr) -> Track | None:
        sessions = list(mgr.get_sessions())
        if not sessions:
            return None

        def is_playing(sess) -> bool:
            st = sess.get_playback_info().playback_status
            return getattr(st, "name", "") == "PLAYING" or int(st) == 4

        playing = [s for s in sessions if is_playing(s)]
        cur = self.current
        sess, props = None, None
        # stick with what we're already showing while it's still playing, so
        # another tab or a hover preview can't steal the miniplayer
        if cur is not None and len(playing) > 1:
            for s_ in playing:
                pr = await s_.try_get_media_properties_async()
                if (pr.title or "").strip() == cur.title:
                    sess, props = s_, pr
                    break
        if sess is None:
            sess = playing[0] if playing else mgr.get_current_session()
        if sess is None:
            return None
        if props is None:
            props = await sess.try_get_media_properties_async()
        title = (props.title or "").strip()
        if not title:
            return None
        app_id = sess.source_app_user_model_id or ""
        track = Track(title=title, artist=(props.artist or "").strip(),
                      source=source_name(app_id), playing=is_playing(sess),
                      album=(getattr(props, "album_title", "") or "").strip())
        track.app_id = app_id  # type: ignore[attr-defined]
        try:
            kind = props.playback_type
            track.kind = int(getattr(kind, "value", kind)) if kind is not None else None
        except Exception:
            pass
        try:
            tl = sess.get_timeline_properties()
            span = tl.end_time - tl.start_time
            track.duration = max(0.0, span.total_seconds() if hasattr(span, "total_seconds")
                                 else float(span) / 10_000_000)
        except Exception:
            pass
        if track.playing:
            track.audible = self.meter.audible(app_id)
        if track.source == "Browser":
            try:
                track.in_window = title_in_windows(title, window_titles())
            except Exception:
                track.in_window = None
        if props.thumbnail and not (cur and cur.key == track.key and cur.art):
            track.art = await self._thumbnail(props.thumbnail)
        return track

    @staticmethod
    async def _thumbnail(ref) -> bytes | None:
        try:
            from winrt.windows.storage.streams import (Buffer, DataReader,
                                                       InputStreamOptions)
            stream = await ref.open_read_async()
            size = int(stream.size)
            if not size:
                return None
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            try:
                return bytes(buf)                    # newer pywinrt: buffer protocol
            except TypeError:
                reader = DataReader.from_buffer(buf)
                data = bytearray(buf.length)
                reader.read_bytes(data)
                return bytes(data)
        except Exception as e:
            log.debug("album art unavailable: %s", e)
            return None
