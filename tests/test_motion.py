"""Fluid motion: springs move the same at any frame rate, and stay stable."""
import math

from app.ui.cat_rig import Spring


def trace(fps, seconds=1.0, k=420, damping=0.22):
    s = Spring(0, k, damping)
    s.target = 1.0
    dt, t, out = 1 / fps, 0.0, []
    while t < seconds - 1e-9:
        s.step(dt)
        t += dt
        out.append((round(t, 4), s.value))
    return dict(out)


def test_stiff_springs_move_the_same_at_20_30_and_60_fps():
    a, b, c = trace(60), trace(30), trace(20)
    for t in (0.1, 0.2, 0.3, 0.5, 1.0):
        assert abs(a[round(t, 4)] - b[round(t, 4)]) < 0.02
        assert abs(a[round(t, 4)] - c[round(t, 4)]) < 0.02


def test_springs_never_blow_up_on_a_long_hitch():
    s = Spring(0, 420, 0.22)
    s.target = 1
    s.step(0.05)                          # a 50 ms hiccup (the tick's cap)
    for _ in range(200):
        s.step(1 / 60)
    assert math.isfinite(s.value) and abs(s.value - 1) < 1e-3
