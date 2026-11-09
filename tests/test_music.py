"""Tests for music mode: change detection, the now_playing tool and the HUD."""
import os

import pytest

from app.music import MusicWatcher, Track, source_name
from app.reminders import ReminderStore
from app.tools import build_tools


def test_source_names():
    assert source_name("Spotify.exe") == "Spotify"
    assert source_name("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify") == "Spotify"
    assert source_name("Chrome") == "Browser"
    assert source_name("Opera GX Stable") == "Browser"
    assert source_name("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic") == "Microsoft.ZuneMusic"


def test_watcher_only_emits_on_real_changes():
    w = MusicWatcher(start=False)
    seen = []
    w.changed.connect(seen.append)
    song = Track("Midnight City", "M83", "Spotify", True, b"art")
    w.feed(song)
    w.feed(Track("Midnight City", "M83", "Spotify", True))       # same song, no art re-read
    assert len(seen) == 1 and w.current.art == b"art"             # art kept
    w.feed(Track("Midnight City", "M83", "Spotify", False))      # paused
    w.feed(Track("Wait", "M83", "Spotify", False))               # next song
    w.feed(None)                                                  # stopped
    assert [t.title if t else None for t in seen] == \
        ["Midnight City", "Midnight City", "Wait", None]
    assert seen[1].playing is False


def test_now_playing_tool(tmp_path):
    w = MusicWatcher(start=False)
    tools = build_tools(ReminderStore(tmp_path / "r.json"), [], music=lambda: w.current)
    assert "Nothing" in tools["now_playing"].func()
    w.feed(Track("Never Gonna Give You Up", "Rick Astley", "Browser", True))
    assert tools["now_playing"].func() == \
        "Now 'Never Gonna Give You Up' by Rick Astley (playing on Browser)"


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_now_playing_hud_spins_and_hides(qapp, tmp_path):
    import io
    from PIL import Image
    from PySide6.QtCore import QPoint
    from app.ui.nowplaying import NowPlaying

    class FakeCat:
        k = 0.82
        def head_anchor(self):
            return QPoint(300, 300)

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 50, 90)).save(buf, "JPEG")
    toggled = []
    hud = NowPlaying(FakeCat(), on_toggle=lambda: toggled.append(1), on_next=lambda: None)
    hud.set_track(Track("Song", "Artist", "Spotify", True, buf.getvalue()))
    assert hud.art is not None and hud.spin.target > 0 and hud.isVisible()
    hud.set_track(Track("Song", "Artist", "Spotify", False, buf.getvalue()))
    assert hud.spin.target == 0                                   # disc slows to a stop
    assert hud._linger.isActive() and hud.alpha.target == 1       # ...for a moment
    hud._fade_out()
    assert hud.alpha.target == 0                                  # then disappears
    hud.set_track(Track("Song", "Artist", "Spotify", True, buf.getvalue()))
    assert hud.alpha.target == 1 and not hud._linger.isActive()   # back when playing
    hud.set_track(None)                                           # player closed
    hud._fade_out()
    assert hud.alpha.target == 0
    hud.set_track(Track("Song", "Artist", "Spotify", True, buf.getvalue()))
    hud.set_hud_open(True)
    assert hud.alpha.target == 0                                  # steps aside for the chat
    hud.grab()                                                    # paints without errors

    paused_hud = NowPlaying(FakeCat(), on_toggle=lambda: None, on_next=lambda: None)
    paused_hud.set_track(Track("Old", "Song", "Spotify", False))  # paused at startup
    assert paused_hud.alpha.target == 0 and not paused_hud.isVisible()
