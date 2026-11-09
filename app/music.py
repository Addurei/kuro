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
import threading
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

    @property
    def key(self) -> tuple[str, str]:
        return self.title, self.artist

    def describe(self) -> str:
        state = "playing" if self.playing else "paused"
        who = f" by {self.artist}" if self.artist else ""
        where = f" on {self.source}" if self.source else ""
        return f"'{self.title}'{who} ({state}{where})"


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
        self._stop = threading.Event()
        if start:
            threading.Thread(target=self._run, daemon=True, name="music").start()

    def stop(self) -> None:
        self._stop.set()

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
                self.feed(await self._read(mgr))
            except Exception as e:                   # a session closed mid-read, etc.
                log.debug("music read failed: %s", e)
            await asyncio.sleep(self.interval)

    async def _read(self, mgr) -> Track | None:
        sessions = list(mgr.get_sessions())
        if not sessions:
            return None

        def is_playing(sess) -> bool:
            st = sess.get_playback_info().playback_status
            return getattr(st, "name", "") == "PLAYING" or int(st) == 4

        playing = [s for s in sessions if is_playing(s)]
        sess = playing[0] if playing else mgr.get_current_session()
        if sess is None:
            return None
        props = await sess.try_get_media_properties_async()
        title = (props.title or "").strip()
        if not title:
            return None
        track = Track(title=title, artist=(props.artist or "").strip(),
                      source=source_name(sess.source_app_user_model_id),
                      playing=bool(playing))
        old = self.current
        if props.thumbnail and not (old and old.key == track.key and old.art):
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
