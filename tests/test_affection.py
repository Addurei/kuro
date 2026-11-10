"""Petting, boops, tickles, hop anticipation, lip-sync, chat starters, menu."""
import io
import math
import struct
import wave

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from app import i18n
from app.music import Track, is_music
from app.voice import ENV_STEP, Speaker, envelope


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Clock:
    def restart(self):
        return 1000 / 60


@pytest.fixture
def cat(qapp):
    from app.ui.cat import CatWidget
    c = CatWidget()
    c.can_wander = lambda: False
    c._is_night = lambda: False
    c._clock = _Clock()
    c.show()
    run(c, 60)
    c.grab()                               # one paint so he knows where his head is
    yield c
    c.close()


def run(c, frames):
    for _ in range(frames):
        c._tick()


def stroke(c, frames):
    for i in range(frames):
        c._tick()
        h, r = c.geo["head"], c.geo["head_r"]
        c._hover_move(QPointF(h.x() + r * 0.5 * math.sin(i * 0.35), h.y() - r * 0.3))


def test_rig_reports_where_his_head_nose_and_belly_are(cat):
    assert {"head", "head_r", "nose", "belly"} <= set(cat.geo)
    assert cat._spot(cat.geo["nose"]) == "nose"
    assert cat._spot(cat.geo["belly"]) == "belly"
    h = cat.geo["head"]
    assert cat._spot(QPointF(h.x(), h.y() - cat.geo["head_r"] * 0.7)) == "head"
    assert cat._spot(QPointF(2, 2)) == ""


def test_stroking_his_head_makes_him_purr_and_hearts_appear(cat):
    stroke(cat, 200)
    assert cat._pet > 0.9
    assert cat.sp["purr"].value > 0.5
    assert cat.sp["happy"].value > 0.5              # eyes squeezed shut, smiling
    assert any(s.kind == "heart" for s in cat._sparks)
    assert cat.pose().purr > 0.5


def test_he_calms_down_when_you_stop(cat):
    stroke(cat, 200)
    run(cat, 300)
    assert cat._pet == 0.0
    assert cat.sp["purr"].value < 0.05
    assert cat.sp["happy"].value < 0.1


def test_just_resting_the_cursor_on_him_is_not_petting(cat):
    h = cat.geo["head"]
    for _ in range(200):
        cat._tick()
        cat._hover_move(QPointF(h.x(), h.y()))
    assert cat._pet == 0.0


def test_petting_while_asleep_does_not_wake_him(cat):
    cat.set_state("sleeping")
    run(cat, 60)
    stroke(cat, 150)
    assert cat.state == "sleeping"
    assert cat.sp["purr"].value > 0.5


def test_boop_crosses_his_eyes_then_they_sparkle(cat):
    cat._petted("nose")
    run(cat, 20)
    assert cat.sp["converge"].target == 1.0
    run(cat, 60)
    assert cat.sp["converge"].target < 1.0
    assert cat.sp["sparkle"].value > 0.5


def test_belly_tickle_gives_a_giggle_and_a_little_hop(cat):
    cat._petted("belly")
    run(cat, 12)
    assert cat.sp["happy"].target == 1.0
    assert cat.hop_y > 0 or cat.hop_v > 0


def test_hop_crouches_before_it_launches(cat):
    cat.hop(150)
    assert cat.hop_v == 0 and cat._hop_pending is not None
    run(cat, 2)
    assert cat.hop_y == 0                          # still winding up
    run(cat, 8)
    assert cat.hop_y > 0 and cat._hop_pending is None


def test_too_many_clicks_still_annoy_him(cat):
    for _ in range(7):
        cat._petted("head")
    assert cat.expr == "annoyed"


def test_mouth_follows_his_voice(cat):
    cat.voice_level = lambda: 0.9
    run(cat, 20)
    assert cat.pose().mouth > 0.6
    cat.voice_level = lambda: 0.0
    run(cat, 30)
    assert cat.pose().mouth < 0.1


def test_mouth_mimes_when_the_loudness_is_unknown(cat):
    cat.voice_level = lambda: None
    cat.set_state("talking")
    peak = 0.0
    for _ in range(90):
        cat._tick()
        peak = max(peak, cat.pose().mouth)
    assert peak > 0.3
    cat.set_state("idle")
    run(cat, 40)
    assert cat.pose().mouth < 0.05


# ---- voice loudness ----------------------------------------------------
def _wav(samples, rate=22050):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def test_envelope_tracks_loud_and_quiet_parts():
    rate = 22050
    loud = [int(20000 * math.sin(i * 0.2)) for i in range(rate // 2)]
    env, secs = envelope(_wav(loud + [0] * (rate // 2), rate))
    assert abs(secs - 1.0) < 0.01
    assert abs(len(env) - 1.0 / ENV_STEP) <= 2
    assert min(env[2:12]) > 0.8 and max(env[-10:]) == 0.0


def test_envelope_of_silence_or_rubbish_is_safe():
    assert envelope(b"not a wav") == ([], 0.0)
    env, _ = envelope(_wav([0] * 4000))
    assert set(env) == {0.0}


def test_speaker_level_is_unknown_when_he_is_not_speaking():
    sp = Speaker.__new__(Speaker)              # no audio devices in tests
    sp._pending, sp._natural, sp._env, sp._env_t0 = 0, None, [], 0.0
    assert sp.busy is False and sp.level() is None
    sp._pending, sp._natural = 1, object()
    assert sp.level() == 0.0                   # making the next sentence: mouth closed
    import time
    sp._env, sp._env_t0 = [0.7] * 30, time.monotonic()
    assert sp.level() == 0.7
    sp._env_t0 -= 5                            # past the end of the clip
    assert sp.level() == 0.0


# ---- chat + menu -------------------------------------------------------
class _S:
    language = "Taglish"


def test_chat_starters_come_in_every_language():
    for name in i18n.LANGS:
        _S.language = name
        hint, chips = i18n.chat_starters(_S)
        assert hint and len(chips) == 3 and all(label and prompt for label, prompt in chips)


def test_tapping_a_suggestion_sends_it(qapp):
    from app.ui.hud import ChatPage
    page = ChatPage("Kuro", ("Try these", [("Open Spotify", "open Spotify")]))
    sent = []
    page.submitted.connect(sent.append)
    page.chips[0].click()
    assert sent == ["open Spotify"]
    page.set_busy(True)
    page.chips[0].click()
    assert sent == ["open Spotify"]            # not while he's busy
    page.set_starters("Subukan", [("A", "a"), ("B", "b")])
    assert [c.text() for c in page.chips] == ["A", "B"] and page.hint.text() == "Subukan"


def test_menu_folds_away_then_runs_what_you_picked(qapp):
    from app.ui.hud import RadialMenu
    ran = []
    menu = RadialMenu([("chat", "CHAT", lambda: ran.append("chat")),
                       ("power", "QUIT", lambda: ran.append("quit"))])
    for _ in range(80):
        menu._step()
    assert all(s.value > 0.95 for s in menu.springs)
    menu.hover = 0
    for _ in range(20):
        menu._step()
    assert menu.lift[0].value > 0.8 and menu.lift[1].value < 0.1 and menu.tip.value > 0.8
    menu._picked = menu.items[0][2]
    for _ in range(40):
        menu._step()
        qapp.processEvents()
    assert ran == ["chat"] and menu._picked is None


def test_a_sites_page_title_is_not_a_song():
    assert not is_music(Track("TikTok - Make Your Day", "", "chrome.exe", True))
    assert not is_music(Track("(3) Reels | Facebook", "", "chrome.exe", True))
    assert is_music(Track("Hev Abi - Alam Mo Ba Girl", "", "chrome.exe", True, duration=200))
