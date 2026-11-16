"""Smoothness guarantees: no channel of the cat jumps between frames, every
change of look blends, and the frame rate never judders on a 60 Hz screen."""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", tempfile.mkdtemp())

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from app.ui.cat import CatWidget  # noqa: E402

DT = 1 / 60


class _Clock:
    def restart(self):
        return DT * 1000

    def elapsed(self):
        return 0


@pytest.fixture
def cat():
    c = CatWidget(1.0, can_wander=lambda: False)
    c._clock = _Clock()
    yield c
    c.close()


def _run(c, seconds, out):
    for _ in range(int(seconds / DT)):
        c._tick()
        out.append((c.pose(), c._frame.interval()))


def test_breathing_never_jumps_falling_asleep_or_waking(cat):
    frames = []
    _run(cat, 2, frames)
    cat.set_state("sleeping")
    _run(cat, 6, frames)
    cat.set_state("idle")
    _run(cat, 4, frames)
    b = [p.breath for p, _ in frames]
    assert max(abs(y - x) for x, y in zip(b, b[1:])) < 0.12


def test_only_judder_free_frame_rates(cat):
    frames = []
    for state in ("idle", "talking", "thinking", "happy", "listening", "sleeping", "idle"):
        cat.set_state(state)
        _run(cat, 2, frames)
    cat.react("love", 1.0)
    _run(cat, 2, frames)
    assert {ms for _, ms in frames} <= {16, 33, 66, 250}


def test_an_expression_fades_out_instead_of_snapping(cat):
    frames = []
    cat.react("love", 0.6)
    _run(cat, 2.5, frames)
    # while the amount is still visible, the look it belongs to is still drawn
    for p, _ in frames:
        if p.expr_amt > 0.05:
            assert p.expr == "love"
    amts = [p.expr_amt for p, _ in frames]
    assert max(abs(y - x) for x, y in zip(amts, amts[1:])) < 0.15


def test_tail_tempo_eases_between_moods(cat):
    frames = []
    _run(cat, 1, frames)
    cat.set_state("happy")
    _run(cat, 2, frames)
    ph = [p.tail_phase for p, _ in frames]
    rate = [(y - x) / DT for x, y in zip(ph, ph[1:])]
    assert max(abs(y - x) for x, y in zip(rate, rate[1:])) < 0.5     # no sudden tempo change
