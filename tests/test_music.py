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


# ------------------------------------------------------------ previews & DJ mode
def test_hover_preview_never_reaches_kuro():
    """A muted YouTube hover preview plays for a moment: ignored."""
    w = MusicWatcher(start=False)
    seen = []
    w.changed.connect(seen.append)
    song = Track("Midnight City", "M83", "Browser", True, audible=True)
    w.offer(song, now=0)
    w.offer(song, now=3.5)                                       # confirmed
    assert [t.title for t in seen] == ["Midnight City"]
    preview = Track("How Bridges Are Built", "Docs", "Browser", True, audible=True)
    w.offer(preview, now=10)                                     # hover...
    w.offer(preview, now=11.5)
    w.offer(song, now=12)                                        # ...cursor moved on
    w.offer(preview, now=13)                                     # timer restarts
    w.offer(song, now=14)
    assert [t.title for t in seen] == ["Midnight City"]


def test_silent_preview_never_confirms_but_real_song_does():
    w = MusicWatcher(start=False)
    seen = []
    w.changed.connect(seen.append)
    muted = Track("Some Vlog", "Someone", "Browser", True, audible=False)
    for t in range(0, 12):
        w.offer(muted, now=t)
    assert seen == []
    nxt = Track("Wait", "M83", "Spotify", True, audible=None)   # can't measure: time only
    w.offer(nxt, now=20)
    w.offer(nxt, now=23.1)
    assert [t.title for t in seen] == ["Wait"]


def test_meter_misreading_silence_cant_block_a_real_song_forever():
    w = MusicWatcher(start=False)
    seen = []
    w.changed.connect(seen.append)
    song = Track("Wait", "M83", "Browser", True, audible=False)
    for t in range(0, 13):
        w.offer(song, now=t)
    assert [t.title for t in seen] == ["Wait"]


def test_same_track_pause_resume_is_instant():
    w = MusicWatcher(start=False)
    seen = []
    w.changed.connect(seen.append)
    song = Track("Wait", "M83", "Spotify", True)
    w.offer(song, now=0)
    w.offer(song, now=3.2)
    w.offer(Track("Wait", "M83", "Spotify", False), now=4)
    assert [t.playing for t in seen] == [True, False]


@pytest.mark.parametrize("track, verdict", [
    (Track("Never Gonna Give You Up (Official Music Video)", "Rick Astley", "Browser"), True),
    (Track("Good News", "Mac Miller", "Spotify"), True),
    (Track("Good News", "Mac Miller - Topic", "Browser"), True),
    (Track("NIKI - Every Summertime (Lyrics)", "Vibe Lyrics", "Browser"), True),
    (Track("Anything", "x", "Browser", album="Blonde"), True),
    (Track("Episode 12: Habits", "Huberman Lab", "Spotify"), False),
    (Track("The Deepest Hole Ever Dug | Documentary", "Real Stories", "Browser"), False),
    (Track("Skip this", "Brand", "Browser", duration=15), False),
    (Track("Building a PC: full tutorial", "Linus", "Browser", duration=1500), False),
    (Track("Blinding Lights", "The Weeknd", "Browser", duration=200), True),   # song-length
    (Track("Hev Abi - Alam Mo Ba Girl", "Hev Abi", "Browser"), True),          # Artist - Song
    (Track("I tried living in Tokyo for 30 days", "Abroad", "Browser", duration=900), False),
    (Track("Funny cat compilation", "Cats", "Browser"), False),                 # no clues: no
])
def test_classify_music(track, verdict):
    from app.music import classify
    assert classify(track) is verdict


def test_audio_meter_matches_apps_and_forgets_old_sound():
    import time as _t
    from app.music import AudioMeter
    m = AudioMeter(start=False)
    assert m.audible("Chrome") is None                          # no data yet
    m.ok = True
    m.levels = {"chrome": _t.monotonic(), "spotify": _t.monotonic() - 10}
    assert m.audible("Chrome") is True
    assert m.audible("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify") is False
    assert m.audible("Opera GX Stable") is None                 # never seen: can't tell


def test_music_thread_never_imports_com():
    """comtypes on the WinRT thread made every media read hang (STA, no pump)."""
    import inspect
    from app import music
    src = inspect.getsource(music.MusicWatcher)
    assert "comtypes" not in src and "pycaw" not in src
