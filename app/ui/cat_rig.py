"""Kuro's body in 2.5D, built like a real (cartoon) cat.

Skeleton, in the cat's own side-on plane (f = forward, y = up is negative):

    hips H ──── spine (bends, stretches) ──── chest C ──── head
     │                                          │
    hip → knee → paw   (knee bends forward)    shoulder → elbow → paw
                                                (elbow bends back)

Legs are solved with two-bone IK, so paws plant on the ground (or on
thin air) and the joints bend naturally. Flesh is drawn as tapered tubes
over the bones. Every frame the skeleton is projected for the current
yaw (0 = facing you, ±90 = side-on), so the same rig works from any angle.

Coordinates: 1 unit ~ 1 px at scale 1. Origin is the floor under the
cat's centre.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient, QTransform)


FUR = QColor(10, 10, 12)
FUR_FAR = QColor(27, 27, 32)           # (kept for callers) far-side limbs
# Kuro is lit like a vinyl figure: one soft key light up and to his left, a
# cool rim light from behind on the right. Black fur can't get darker, so
# the lit side lifts toward a blue-charcoal and the shadows stay true black.
LIT = QColor(94, 98, 124)              # where the key light lands
MID = QColor(42, 43, 56)
SHADE = QColor(16, 16, 21)
DEEP = QColor(3, 3, 5)
RIM_COOL = QColor(126, 142, 200)       # the rim light
RIM_FUR = QColor(92, 104, 150)         # black fur where that rim light grazes it
LIGHT_DIR = (-0.42, -0.52)             # toward the key light (screen space)
RIM = QColor(255, 255, 255, 36)        # faint outline so he reads on dark wallpapers
INNER_EAR = QColor(238, 238, 242)
EYE = QColor(186, 226, 92)             # Kuro's lime green
IRIS_LIGHT = QColor(232, 250, 150)     # where light pools at the bottom of the iris
IRIS_DEEP = QColor(128, 180, 52)       # toward the rim
IRIS_RIM = QColor(62, 104, 26)         # the dark ring that makes eyes look round
IRIS_SHADOW = QColor(52, 92, 22)       # the top of the iris, in the shadow of his brow
PUPIL = QColor(3, 3, 5)

HEAD_W, HEAD_H, HEAD_D = 46.0, 36.0, 40.0   # head ellipsoid radii (side, up, forward)

# gaits: per-leg phase offsets (fraction of a cycle), share of the cycle the
# paw is on the ground, stride length and paw lift
WALK = {"off": {(1, -1): 0.25, (1, 1): 0.75, (0, -1): 0.0, (0, 1): 0.5},
        "duty": 0.58, "stride": 12.0, "lift": 6.5}
RUN = {"off": {(1, -1): 0.42, (1, 1): 0.52, (0, -1): 0.0, (0, 1): 0.08},
       "duty": 0.42, "stride": 17.0, "lift": 7.0}
HEAD_SCALE = 0.94


class Spring:
    """Damped spring: `value` chases `target` with a little overshoot.
    Nearly every moving part of the cat is one of these."""

    __slots__ = ("value", "vel", "target", "k", "d")

    def __init__(self, value: float = 0.0, k: float = 170.0, damping: float = 0.55):
        self.value = self.target = float(value)
        self.vel = 0.0
        self.k = k
        self.d = 2 * math.sqrt(k) * damping   # damping < 1 = bouncy, 1 = no overshoot

    MAX_STEP = 1 / 240               # physics sub-step: same motion at 20, 30 or 60 fps

    def step(self, dt: float) -> None:
        n = 1 if dt <= self.MAX_STEP else int(dt / self.MAX_STEP) + 1
        h = dt / n
        k, d, target = self.k, self.d, self.target
        v, x = self.vel, self.value
        for _ in range(n):
            v += (k * (target - x) - d * v) * h
            x += v * h
        self.vel, self.value = v, x

    def kick(self, velocity: float) -> None:
        self.vel += velocity

    def clamp(self, lo: float, hi: float, vmax: float | None = None) -> None:
        self.value = min(max(self.value, lo), hi)
        if vmax is not None:
            self.vel = min(max(self.vel, -vmax), vmax)


@dataclass
class Pose:
    t: float = 0.0
    yaw: float = 0.0           # degrees, body (+ = facing screen-right)
    head_yaw: float = 0.0      # degrees, extra head turn
    pitch: float = 0.0         # degrees, body tilt (climbing / descending air)
    stand: float = 0.0         # 0 sitting .. 1 standing on four legs
    gait: float = 0.0          # 0..1 how much the legs are moving
    gait_phase: float = 0.0    # cycles; advances with distance travelled
    run: float = 0.0           # 0 walk .. 1 gallop
    squash: float = 0.0        # +stretch / -squash, anchored at the paws
    lean: float = 0.0          # degrees, whole body
    lift: float = 0.0          # whole body raised (hops, hover, being held)
    head_tilt: float = 0.0
    head_dx: float = 0.0       # head lag (follow-through)
    head_dy: float = 0.0
    breath: float = 0.0        # -1..1
    eye_open: float = 1.0
    eye_scale: float = 1.0
    happy: float = 0.0
    look_x: float = 0.0
    look_y: float = 0.0
    ear: float = 0.0           # -1 flat .. 1 perked
    twitch_l: float = 0.0
    twitch_r: float = 0.0
    glow: float = 0.0
    think: float = 0.0
    listen: float = 0.0
    sleep: float = 0.0
    tail_start: float = 0.12
    tail_curl: float = -0.13
    tail_sway: float = 0.8
    tail_phase: float = 0.0
    tail_side: float = 1.05
    tail_drag: float = 0.0
    mouth: float = 0.0         # 0 closed .. 1 open (talking)
    yawn: float = 0.0          # 0..1 big yawn
    converge: float = 0.0      # 0..1 eyes cross slightly toward a near cursor
    pupil: float = 1.0         # pupil size (0.6 tiny/surprised .. 1.3 big/smitten)
    sparkle: float = 0.0       # 0..1 twinkling star glints (delighted, being petted)
    purr: float = 0.0          # 0..1 purring: a fine, fast tremble through his body
    lid: float = 0.0           # 0..1 upper eyelids lowered (annoyed, relaxed)
    expr: str = ""             # "", "dizzy", "love", "surprised", "annoyed", "vibe"
    expr_amt: float = 0.0      # 0..1 blend of the special expression
    headphones: float = 0.0    # 0..1 slid on
    whisker: float = 0.0       # degrees of springy whisker wobble
    dangle: float = 0.0        # being carried
    jiggle: float = 0.0        # chubby jelly wobble (springy, +/-)
    paw_raise: float = 0.0     # 0..1 lifts the near front paw to his mouth (grooming)
    lick: float = 0.0          # 0..1 little tongue out
    blep: float = 0.0          # 0..1 sleepy tongue tip poking out
    bubble: float = 0.0        # 0..1 sleeping nose bubble
    pop: float = 0.0           # 1..0 right after the bubble pops
    knead: float = 0.0         # 0..1 "making biscuits" with the front paws
    knead_phase: float = -1.0  # >= 0: paw taps follow this (typing along with you)
    glasses: float = 0.0       # 0..1 coding / studying
    pencil: float = 0.0        # 0..1 writing (tucked behind his ear)
    beret: float = 0.0         # 0..1 designing / 3D
    bat: float = 0.0           # 0..1 near front paw reaches out (batting at your cursor)
    groom_x: float = 0.0       # where the grooming paw goes (licking / washing his face)
    groom_y: float = 0.0
    airborne: float = 0.0      # 0 on the taskbar .. 1 standing on thin air


@dataclass
class Paw:
    pos: QPointF               # widget coordinates
    planted: bool              # on the ground (or air) this frame
    near: bool


# ---------------------------------------------------------------- maths
def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def project(f: float, s: float, yaw: float) -> tuple[float, float]:
    """Local (forward, side) -> (screen x, depth toward the viewer)."""
    sy, cy = math.sin(yaw), math.cos(yaw)
    return f * sy + s * cy, f * cy - s * sy


def on_head(phi_deg: float, lam_deg: float, yaw: float) -> tuple[float, float, float]:
    phi, lam = math.radians(phi_deg), math.radians(lam_deg)
    f = HEAD_D * math.cos(lam) * math.cos(phi)
    s = HEAD_W * math.cos(lam) * math.sin(phi)
    x, _ = project(f, s, yaw)
    return x, -HEAD_H * math.sin(lam), math.cos(lam) * math.cos(phi + yaw)


def two_bone(root: tuple[float, float], target: tuple[float, float], a: float, b: float,
             knee_forward: bool) -> tuple[tuple[float, float], tuple[float, float]]:
    """IK: returns (knee, reached_target) in the leg's (f, y) plane."""
    rf, ry = root
    dx, dy = target[0] - rf, target[1] - ry
    d = math.hypot(dx, dy)
    d_cl = min(max(d, abs(a - b) + 0.01), a + b - 0.01)
    ux, uy = (dx / d, dy / d) if d > 1e-6 else (0.0, 1.0)
    tgt = (rf + ux * d_cl, ry + uy * d_cl)
    ang = math.acos(max(-1.0, min(1.0, (a * a + d_cl * d_cl - b * b) / (2 * a * d_cl))))
    best = None
    for sgn in (1, -1):
        c, s = math.cos(sgn * ang), math.sin(sgn * ang)
        k = (rf + (ux * c - uy * s) * a, ry + (ux * s + uy * c) * a)
        mid_f = (rf + tgt[0]) / 2
        score = (k[0] - mid_f) * (1 if knee_forward else -1)
        if best is None or score > best[0]:
            best = (score, k)
    return best[1], tgt


def _ellipse(path: QPainterPath, cx, cy, rx, ry) -> None:
    path.addEllipse(QPointF(cx, cy), max(rx, 0.05), max(ry, 0.05))


def _gait_params(run: float) -> dict:
    off = {k: lerp(WALK["off"][k], RUN["off"][k], run) for k in WALK["off"]}
    return {"off": off, "duty": lerp(WALK["duty"], RUN["duty"], run),
            "stride": lerp(WALK["stride"], RUN["stride"], run),
            "lift": lerp(WALK["lift"], RUN["lift"], run)}


def _foot_cycle(p: float, duty: float, stride: float, lift: float) -> tuple[float, float, bool]:
    """Paw offset along the body and height for a leg at cycle phase p."""
    p %= 1.0
    if p < duty:                                   # planted: slides back under the body
        u = p / duty
        return stride * (0.5 - u), 0.0, True
    u = (p - duty) / (1 - duty)                    # swing: arcs forward
    e = u * u * (3 - 2 * u)
    return stride * (-0.5 + e), math.sin(math.pi * u) * lift, False


# ---------------------------------------------------------------- ears
def _ear_shapes() -> tuple[QPainterPath, QPainterPath]:
    outer = QPainterPath(QPointF(-15, 22))
    outer.quadTo(QPointF(-23, -6), QPointF(-19, -26))
    outer.quadTo(QPointF(-17.5, -32), QPointF(-12, -30))
    outer.quadTo(QPointF(5, -22), QPointF(21, -13))
    outer.lineTo(QPointF(10, 14))
    outer.closeSubpath()
    inner = QPainterPath(QPointF(-13.5, 12))
    inner.quadTo(QPointF(-18, -8), QPointF(-15.5, -23))
    inner.quadTo(QPointF(-6, -14), QPointF(0, -7))
    inner.quadTo(QPointF(-5, 2), QPointF(-13.5, 12))
    inner.closeSubpath()
    return outer, inner


EAR_OUTER, EAR_INNER = _ear_shapes()

# The same ear seen side-on (facing +x). Points correspond one-to-one with
# the front template above, so the ear morphs smoothly as he turns.
EAR_FRONT_PTS = [(-15, 22), (-23, -6), (-19, -26), (-17.5, -32), (-12, -30), (9, -19), (18, -3), (10, 16)]
EAR_SIDE_PTS = [(-16, 20), (-19, -4), (-10, -25), (-6.5, -31), (-2, -26), (9, -10), (15, 8), (4, 20)]
INNER_FRONT_PTS = [(-13.5, 12), (-18, -8), (-15.5, -23), (-6, -14), (0, -7), (-5, 2)]
INNER_SIDE_PTS = [(10, 12), (7, -6), (-4.5, -22), (0.5, -9), (4, 7), (8, 10)]


def _ear_path(m: float, mf: float, mp: float, yk: float, outer: bool) -> QPainterPath:
    a, b = (EAR_FRONT_PTS, EAR_SIDE_PTS) if outer else (INNER_FRONT_PTS, INNER_SIDE_PTS)
    pts = [QPointF(lerp(fx * mf, sx * mp, m), lerp(fy, sy, m) * yk)
           for (fx, fy), (sx, sy) in zip(a, b)]
    path = QPainterPath(pts[0])
    if outer:
        path.quadTo(pts[1], pts[2])
        path.quadTo(pts[3], pts[4])
        path.quadTo(pts[5], pts[6])
        path.lineTo(pts[7])
    else:
        path.quadTo(pts[1], pts[2])
        path.quadTo(pts[3], pts[4])
        path.quadTo(pts[5], pts[0])
    path.closeSubpath()
    return path
EAR_PHI, EAR_LAM = 55.0, 37.7
MAX_TURN = 18.0                         # degrees his body may turn from facing you
MAX_HEAD_TURN = 26.0                    # ...and his head
EYE_PHI, EYE_LAM = 28.0, -12.0          # big eyes, set a little below the middle
EYE_FRONT = math.cos(math.radians(EYE_LAM)) * math.cos(math.radians(EYE_PHI))
EAR_SINK = HEAD_H * math.sqrt(1 - (30 / HEAD_W) ** 2) - 22
EAR_FRONT = 0.35 + 0.65 * math.cos(math.radians(EAR_PHI * 0.6))


# ---------------------------------------------------------------- body
def draw_cat(p: QPainter, pose: Pose, origin: QPointF, scale: float,
             geo: dict | None = None) -> tuple[list[Paw], QPointF]:
    """Paint Kuro. Returns his paws and the top of his head, in widget
    coordinates (for paw prints and for pinning the HUD above him)."""
    # he always faces you: a gentle three-quarter turn at most, never side-on
    yaw = math.radians(max(-MAX_TURN, min(MAX_TURN, pose.yaw * MAX_TURN / 88.0)))
    psi = math.radians(max(-MAX_HEAD_TURN, min(MAX_HEAD_TURN,
                                                 (pose.yaw + pose.head_yaw) * MAX_HEAD_TURN / 88.0)))
    st, sleep, b = pose.stand, pose.sleep, pose.breath
    run = pose.run * pose.gait
    phase = pose.gait_phase
    side_sign = -1 if pose.yaw + pose.head_yaw < -6 else 1

    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    outer_tf = p.worldTransform()
    dev = p.device()
    dev_scale = (scale * math.hypot(outer_tf.m11(), outer_tf.m12())
                 * (dev.devicePixelRatioF() if dev is not None else 1.0))
    p.translate(origin)
    p.scale(scale, scale)

    # ---- ground shadow, or a levitation glow when he's standing on air
    k = max(0.3, 1 - pose.lift / 70)
    span = 30 + 18 * abs(math.sin(yaw)) * st
    ground = 1 - pose.airborne
    if ground > 0.02:
        # a soft pool of shadow, darkest right under him where he touches the floor
        p.save()
        p.translate(0, 1.2)
        p.scale(1.0, 0.17)
        g = QRadialGradient(QPointF(0, 0), span * 1.25 * k)
        g.setColorAt(0.0, QColor(0, 0, 0, int(150 * k * ground)))
        g.setColorAt(0.45, QColor(0, 0, 0, int(92 * k * ground)))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), span * 1.25 * k, span * 1.25 * k)
        p.restore()
    if pose.airborne > 0.02:
        pulse = 0.75 + 0.25 * math.sin(pose.t * 3.1)
        g = QRadialGradient(QPointF(0, 1), span * 1.15)
        g.setColorAt(0, QColor(186, 226, 92, int(70 * pose.airborne * pulse)))
        g.setColorAt(1, QColor(186, 226, 92, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 1.0), span * 1.15, 6.5)
        p.setPen(QPen(QColor(186, 226, 92, int(110 * pose.airborne * pulse)), 1.1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 1.0), span * 0.8, 3.2)

    if pose.purr > 0.01:                          # purring: a fine, fast tremble
        p.translate(0.55 * pose.purr * math.sin(pose.t * 150), 0)
    p.translate(0, -pose.lift)
    p.scale(1 - pose.squash * 0.5, 1 + pose.squash)
    p.translate(0, -40)
    p.rotate(pose.lean)
    p.translate(0, 40)

    # ---- spine: hips H and chest C, sitting <-> standing, flexing with the gait
    stretch = run * math.cos(2 * math.pi * (phase - 0.3))       # +1 extended, -1 gathered
    bound = run * max(0.0, math.sin(2 * math.pi * (phase - 0.05))) * 4.0
    walk_bob = pose.gait * (1 - run) * abs(math.sin(2 * math.pi * phase * 2)) * 2.6
    hf = lerp(-5, -20, st) - 2 * stretch
    hy = lerp(-18, -30, st) - bound - walk_bob
    cf = lerp(6, 8, st) + 2 * stretch
    cy = lerp(-36, -32, st) - bound * 0.8 - walk_bob + sleep * 6
    arch = lerp(0.0, 1.5, st) - 2 * stretch * st

    # body pitch: tilt the spine when climbing / descending thin air
    # body pitch: the whole cat tilts about the ground under him, as if
    # walking up (or down) an invisible slope
    pit = math.radians(pose.pitch) * st
    pivot_f = (hf + cf) / 2
    def tilt(f, y):
        c, s = math.cos(pit), math.sin(pit)
        dy = y + 3.2
        return pivot_f + (f - pivot_f) * c + dy * s, -3.2 - (f - pivot_f) * s + dy * c
    hf, hy = tilt(hf, hy)
    cf, cy = tilt(cf, cy)

    jig = pose.jiggle
    rh = lerp(21.0, 17.5, st) * (1 + 0.03 * b) * (1 - 0.07 * jig)
    rc = lerp(17.0, 18.0, st) * (1 + 0.035 * b) * (1 - 0.07 * jig)
    wh = lerp(22.0, 18.5, st)
    wc = lerp(17.5, 18.0, st)

    def spine_at(t):
        mf, my = (hf + cf) / 2, (hy + cy) / 2 - arch
        f = (1 - t) ** 2 * hf + 2 * (1 - t) * t * mf + t * t * cf
        y = (1 - t) ** 2 * hy + 2 * (1 - t) * t * my + t * t * cy
        return f, y

    torso = QPainterPath()
    torso.setFillRule(Qt.WindingFill)
    torso_o = QPainterPath()                      # a hair bigger: his faint outline
    torso_o.setFillRule(Qt.WindingFill)
    sy, cyaw = abs(math.sin(yaw)), abs(math.cos(yaw))
    n = 8
    for i in range(n + 1):
        t = i / n
        f, y = spine_at(t)
        waist = 1 + 0.08 * math.sin(math.pi * t)            # round belly, no waist
        y += 2.6 * math.sin(math.pi * t) * st                # ...that hangs a little
        r = lerp(rh, rc, t) * waist * (1 - 0.08 * sleep)
        w = lerp(wh, wc, t) * waist
        x, _ = project(f, 0, yaw)
        _ellipse(torso, x, y, math.hypot(w * cyaw, r * sy) * (1 + 0.07 * jig), r)
        _ellipse(torso_o, x, y, math.hypot(w * cyaw, r * sy) * (1 + 0.07 * jig) + 1.0, r + 1.0)

    # ---- legs (IK) -----------------------------------------------------
    gp = _gait_params(pose.run)
    legs = []
    groom_paw = None              # a paw raised to his face is drawn in front of it
    groom_at = (0.0, 0.0)
    contours = []                 # (centre, radius, start_deg, span_deg) for near haunch/shoulder
    paws: list[Paw] = []
    toes: list[tuple[float, float, float]] = []
    for front in (1, 0):
        for side in (-1, 1):
            if front:
                root_f, root_y = cf + 2, cy + rc * 0.35
                base_f = cf + 2
                s_off = side * lerp(9.0, 8.0, st)
                a, bl = lerp(16.0, 11.5, st), lerp(16.0, 11.5, st)
                radii = (6.0, 5.2, 4.9)               # slim front legs, like the original
            else:
                root_f, root_y = hf + 1, hy + rh * 0.3
                base_f = lerp(hf + 11, hf + 1, st)
                s_off = side * lerp(15.0, 8.5, st)
                a, bl = lerp(16.0, 11.5, st), lerp(17.0, 12.0, st)
                radii = (lerp(12.0, 9.5, st), 7.2, 6.6)
            dfo, lift, planted = _foot_cycle(phase + gp["off"][(front, side)],
                                             gp["duty"], gp["stride"], gp["lift"])
            foot_f = base_f + dfo * pose.gait
            foot_y = -3.2 - lift * pose.gait
            foot_f, foot_y = tilt(foot_f, foot_y)    # paws walk the same slope
            near_side = -1 if math.sin(yaw) > 0.05 else 1 if math.sin(yaw) < -0.05 else -1
            if front and pose.knead > 0:             # alternate little paw presses
                ph = pose.knead_phase if pose.knead_phase >= 0 else pose.t * 6.5
                press = max(0.0, math.sin(ph + (0 if side < 0 else math.pi)))
                foot_y -= 3.2 * press * pose.knead
                foot_f += 1.2 * press * pose.knead
            if front and side == near_side and pose.bat > 0:
                foot_f = lerp(foot_f, root_f + 17, pose.bat)
                foot_y = lerp(foot_y, root_y - 4, pose.bat)
            if front and side == near_side and pose.paw_raise > 0:
                pr = pose.paw_raise
                # the paw travels on an arc (out, then up and in) and the
                # shoulder lifts with it, instead of sliding in a straight line
                foot_f = lerp(foot_f, root_f + 13, pr) + 6.0 * math.sin(math.pi * min(1.0, pr))
                foot_y = lerp(foot_y, root_y - 13 + pose.groom_y, pr)
                root_y -= 3.0 * pr
                s_off += side * pose.groom_x * pr     # toward mouth / cheek
            if pose.dangle > 0:                      # being carried: legs hang
                foot_f = lerp(foot_f, root_f + (2 if front else -2), pose.dangle)
                foot_y = lerp(foot_y, root_y + a + bl - 1.5, pose.dangle)
            if sleep > 0:                            # tuck the paws in
                foot_f = lerp(foot_f, foot_f + (-3 if front else 3), sleep)
            knee, tgt = two_bone((root_f, root_y), (foot_f, foot_y), a, bl,
                                 knee_forward=not front)
            # stubby legs: pull the joint back toward a straight line
            mid = ((root_f + tgt[0]) / 2, (root_y + tgt[1]) / 2)
            knee = (lerp(knee[0], mid[0], 0.75 * st), lerp(knee[1], mid[1], 0.75 * st))
            pts = [(root_f, root_y, radii[0]), (knee[0], knee[1], radii[1]),
                   (tgt[0], tgt[1], radii[2])]
            shape = QPainterPath()
            shape.setFillRule(Qt.WindingFill)
            for j in range(2):
                (f0, y0, r0), (f1, y1, r1) = pts[j], pts[j + 1]
                for q in range(4):
                    u = q / 3
                    f, y = lerp(f0, f1, u), lerp(y0, y1, u)
                    x, _ = project(f, s_off * (0.85 + 0.15 * u * j), yaw)
                    rr = lerp(r0, r1, u)
                    _ellipse(shape, x, y, rr, rr)
            px, depth = project(tgt[0] + 1.5, s_off, yaw)
            pw = 5.8 if front else 6.8
            _ellipse(shape, px, tgt[1] + 0.2,
                     math.hypot(8.0 * sy, pw * cyaw), 4.4)        # small round beans
            # he faces you, so his front legs are always in front of his body and
            # the hind legs (haunches) sit behind it, peeking out at the sides
            near = bool(front)
            if front and side == near_side and (pose.paw_raise > 0.3 or pose.bat > 0.3):
                groom_paw = shape
                groom_at = (px, tgt[1])
            else:
                rx0, _ = project(root_f, s_off, yaw)
                legs.append((near, shape, (rx0, root_y, px, tgt[1])))
            if near and sy > 0.45 and st > 0.3:
                rx_, _ = project(root_f, s_off, yaw)
                if front:                               # shoulder / elbow line
                    contours.append((QPointF(rx_ - 2 * math.copysign(1, math.sin(yaw)), root_y + 2),
                                     7.0, -75, 120))
                else:                                   # front edge of the round haunch
                    contours.append((QPointF(rx_ - 3 * math.copysign(1, math.sin(yaw)), root_y),
                                     11.0, -80, 150))
            paws.append(Paw(QPointF(px, tgt[1] + 2.5), planted and pose.gait > 0.2, near))
            if front and near and cyaw > 0.5:        # facing you: little toes on his front paws
                toes.append((px, tgt[1] + 0.2, math.hypot(8.0 * sy, 6.8 * cyaw)))

    # ---- tail: tapered, curling, travelling wave
    tb_f = hf - rh * 0.85
    tb_s = lerp(9.0, 0.0, st)
    tbx, _ = project(tb_f, tb_s, yaw)
    tby = hy - rh * 0.25
    tx = math.sin(pose.tail_side - yaw) * 1.1
    segs, seg = 14, 3.9
    x = y = 0.0
    tail = QPainterPath()
    tail.setFillRule(Qt.WindingFill)
    tail_o = QPainterPath()
    tail_o.setFillRule(Qt.WindingFill)
    tail_line = QPainterPath(QPointF(tbx, tby))        # its centre line, for the highlight
    prev = (tbx, tby)
    for i in range(segs):
        frac = (i + 1) / segs
        wave = math.sin(pose.tail_phase - i * 0.42) * pose.tail_sway * frac * 0.24
        ang = pose.tail_start + pose.tail_drag + pose.tail_curl * i + wave
        x += seg * math.cos(ang)
        y += seg * math.sin(ang)
        cur = (tbx + x * tx, tby + y)
        for q in range(2):
            u = (q + 1) / 2
            r = lerp(6.2, 4.6, (i + u) / segs)
            _ellipse(tail, lerp(prev[0], cur[0], u), lerp(prev[1], cur[1], u), r, r)
            if q:
                _ellipse(tail_o, cur[0], cur[1], r + 1.0, r + 1.0)
        if i < segs - 1:
            tail_line.lineTo(QPointF(cur[0], cur[1]))
        prev = cur
    _ellipse(tail, tbx, tby, 6.2, 6.2)
    _ellipse(tail_o, tbx, tby, 7.2, 7.2)

    # ---- head ----------------------------------------------------------
    hfh, hyh = tilt(lerp(2, pivot_f + 32, st) if st > 0 else 2, lerp(-74.0, -73.0, st))
    hfh = lerp(2, hfh, st)
    hxh, _ = project(hfh, 0, yaw)
    hyh = hyh + pose.head_dy + sleep * 9 + b * 0.7 - bound * 0.35 - walk_bob * 0.5
    head_tf = (QTransform().translate(hxh + pose.head_dx, hyh)
               .translate(0, 26).rotate(pose.head_tilt).translate(0, -26)
               .scale(HEAD_SCALE, HEAD_SCALE))
    head_rx = math.hypot(HEAD_D * math.sin(psi), HEAD_W * math.cos(psi))
    # mochi head: a sphere that's a little fuller at the cheeks
    head_local = QPainterPath()
    head_local.setFillRule(Qt.WindingFill)
    _ellipse(head_local, 0, 0, head_rx, HEAD_H)
    _ellipse(head_local, 5 * math.sin(psi), 9, head_rx * (1.05 - 0.07 * abs(math.sin(psi))),
             HEAD_H * 0.74)                      # cheeks sit toward his face
    head_o = QPainterPath()                       # a hair bigger: his faint outline
    head_o.setFillRule(Qt.WindingFill)
    _ellipse(head_o, 0, 0, head_rx + 1.1, HEAD_H + 1.1)
    _ellipse(head_o, 5 * math.sin(psi), 9, head_rx * (1.05 - 0.07 * abs(math.sin(psi))) + 1.1,
             HEAD_H * 0.74 + 1.1)
    head_o = head_tf.map(head_o)

    # ---- ears. They swivel and twitch all the time, so they are worked out
    # and painted every frame; the skull under them is cached (see below).
    turn_e = max(0.0, min(1.0, (abs(math.sin(psi)) - 0.8) / 0.17))   # only near side-on
    m_e = turn_e * turn_e * (3 - 2 * turn_e)
    d_e = 1.0 if psi >= 0 else -1.0
    ears = []                                    # (facing, outer, inner or None, how visible)
    ear_roots = []                               # where each ear meets the skull (head space)
    for side in (-1, 1):
        phi = side * EAR_PHI
        ex, _, _ = on_head(phi, EAR_LAM, psi)
        far_e = math.cos(psi + math.radians(phi * 0.6)) < 0
        ex -= d_e * m_e * (11.0 + (13.0 if far_e else 0.0))   # side-on: on the crown, not the face
        u = min(0.98, abs(ex) / head_rx)
        ey = -HEAD_H * math.sqrt(1 - u * u) + EAR_SINK
        front = math.cos(psi + math.radians(phi * 0.6))
        width = min(1.08, (0.35 + 0.65 * abs(front)) / EAR_FRONT)
        ear_roots.append((ex, ey + 17.0, 17.0 * width))
        e = pose.ear
        tw = max(-16.0, min(16.0, pose.twitch_l if side < 0 else pose.twitch_r))
        deg = (e * 8 if e >= 0 else e * 13) + tw
        mf = width * (1 if side < 0 else -1)
        yk = (1 + 0.07 * max(e, 0)) * (1 + 0.3 * min(e, 0))   # flattened ears fold down
        ey += -min(e, 0) * 5
        # rotate about a pivot down inside the skull, so the base stays
        # buried in the head however the ear swivels
        deg = max(-30.0, min(30.0, deg))
        t = (QTransform().translate(ex, ey + 15).rotate(lerp(deg * -side, deg * d_e, m_e))
             .translate(0, -15))
        local = t.map(_ear_path(m_e, mf, d_e, yk, True))
        local.setFillRule(Qt.WindingFill)
        local.addEllipse(t.map(QPointF(2.0 * (1 if side < 0 else -1) * width, 13.0)),
                         15.0 * width, 9.0)              # soft fillet where ear meets head
        outer = head_tf.map(local)
        vis = max(0.0, min(1.0, front * 3))
        inner = head_tf.map(t.map(_ear_path(m_e, mf, d_e, yk, False))) if vis > 0.02 else None
        ears.append((front, outer, inner, vis))
    ears.sort(key=lambda e_: e_[0])               # far ear painted first

    def paint_ears():
        for _front, outer, inner, vis in ears:
            box = outer.boundingRect()
            c = box.center()
            grow = (QTransform().translate(c.x(), c.y())
                    .scale(1 + 2.2 / max(1.0, box.width()), 1 + 2.2 / max(1.0, box.height()))
                    .translate(-c.x(), -c.y()))
            p.setBrush(RIM)                       # its share of his faint outline
            p.drawPath(grow.map(outer))
            p.setBrush(RIM_FUR)                   # cool rim along the edge away from the lamp
            p.drawPath(outer.translated(1.2, 0.95))
            # the back of the ear: lit toward the tip, falling into the skull's shade
            eg = QLinearGradient(QPointF(c.x() - 4, box.top()), QPointF(c.x() + 3, box.bottom()))
            eg.setColorAt(0.0, QColor(58, 60, 77))
            eg.setColorAt(0.45, QColor(25, 25, 33))
            eg.setColorAt(0.85, SHADE)
            p.setBrush(eg)
            p.drawPath(outer)
            if inner is None:
                continue
            ibox = inner.boundingRect()
            # the hollow of the ear: its far wall sits in shadow
            p.setBrush(QColor(0, 0, 0, int(150 * vis)))
            p.drawPath(inner.translated(-0.9, -0.7))
            ig = QLinearGradient(ibox.topLeft(), QPointF(ibox.left(), ibox.bottom()))
            tip, low, deep = QColor(INNER_EAR), QColor(214, 206, 228), QColor(120, 110, 148)
            for col in (tip, low, deep):
                col.setAlphaF(vis)
            ig.setColorAt(0.0, tip)                # white at the tip...
            ig.setColorAt(0.34, tip)
            ig.setColorAt(0.6, low)                # ...lilac shade deeper in the ear
            ig.setColorAt(0.9, deep)
            ig.setColorAt(1.0, QColor(58, 50, 80, int(255 * vis)))
            p.setBrush(ig)
            p.drawPath(inner)
            # shadow across the hollow, cast by the ear's own near edge
            sg = QLinearGradient(QPointF(ibox.right(), ibox.center().y()),
                                 QPointF(ibox.left(), ibox.center().y()))
            sg.setColorAt(0.0, QColor(30, 22, 52, int(78 * vis)))
            sg.setColorAt(0.45, QColor(30, 22, 52, 0))
            p.setBrush(sg)
            p.drawPath(inner)

    def skull_layer(p, tf):
        """The skull and the form of his face: everything about his head that
        only changes when he turns it or blinks. Painted through `tf`, so it
        can go straight to the screen or into the cache."""
        head = tf.map(head_local)
        hc0 = tf.map(QPointF(0, 0))
        head_r = HEAD_W * 1.12 * HEAD_SCALE
        # light wraps his real outline: painted once in cool rim light, then
        # again in place, which leaves a crisp crescent of rim along the edge
        # facing away from the lamp. This is what lifts him off the screen.
        p.setPen(Qt.NoPen)
        p.setBrush(RIM_FUR)
        p.drawPath(head.translated(1.2, 0.95))
        p.setBrush(_ball(hc0, head_r))
        p.drawPath(head)
        for rx_, ry_, rw_ in ear_roots:           # each ear shades the skull at its root
            _occlude(p, None, tf.map(QPointF(rx_ + 1.5, ry_)), rw_, 8.0, 80)
        # the underside of the head turns away from the light
        jaw = QLinearGradient(QPointF(0, hc0.y() + HEAD_H * 0.2),
                              QPointF(0, hc0.y() + HEAD_H * 0.98))
        jaw.setColorAt(0.0, QColor(0, 0, 0, 0))
        jaw.setColorAt(1.0, QColor(0, 0, 0, 150))
        p.setBrush(jaw)
        p.drawPath(head)
        _rim(p, head, hc0, head_r * 0.92, 120)
        # a soft glossy sheen across the crown, stretched the way fur lies
        gx_, gy_, _gf = on_head(-22, 50, psi)
        crown = tf.map(QPointF(gx_ - 5, gy_ + 1))
        p.save()
        p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        p.translate(crown)
        p.rotate(-24)
        p.scale(1.0, 0.5)
        g = QRadialGradient(QPointF(0, 0), 31)
        g.setColorAt(0.0, QColor(214, 222, 255, 62))
        g.setColorAt(0.45, QColor(214, 222, 255, 22))
        g.setColorAt(1.0, QColor(214, 222, 255, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), 31, 31)
        p.restore()
        p.save()
        p.setTransform(tf, True)
        p.setClipPath(head_local)
        _face_form(p, pose, psi)
        p.restore()

    # ---- paint, back to front. Every part is shaded as its own rounded form
    # and drops a soft shadow on whatever is behind it, so he reads as a little
    # solid figure instead of one flat black cut-out.
    far_legs = [(s_, at) for nr, s_, at in legs if not nr]
    near_legs = [(s_, at) for nr, s_, at in legs if nr]
    hc0 = head_tf.map(QPointF(0, 0))
    bx0, _ = project((hf + cf) / 2, 0, yaw)
    body_c = QPointF(bx0, (hy + cy) / 2 - 2)
    body_r = 30 + 12 * st * sy
    # faint outline on the big shapes so he still reads on dark wallpapers
    p.setPen(Qt.NoPen)
    p.setBrush(RIM)
    p.drawPath(tail_o)
    p.drawPath(torso_o)
    p.drawPath(head_o)

    def leg_form(shape, lift_):
        """A leg that belongs to the body: at the top it is as dark as the
        chest it grows out of (so the two melt together, like the original
        flat Kuro), and it gently comes into the light toward the paw."""
        box = shape.boundingRect()
        g = QLinearGradient(QPointF(box.left(), box.top()), QPointF(box.left(), box.bottom()))
        def mix(c, k):
            return QColor(int(SHADE.red() + (c.red() - SHADE.red()) * k),
                          int(SHADE.green() + (c.green() - SHADE.green()) * k),
                          int(SHADE.blue() + (c.blue() - SHADE.blue()) * k))
        g.setColorAt(0.0, mix(SHADE, 0.5))            # melts into the chest
        g.setColorAt(0.45, mix(MID, lift_ * 0.55))
        g.setColorAt(0.78, mix(MID, lift_ * 0.95))    # the paw catches the light
        g.setColorAt(1.0, mix(SHADE, 0.6))
        return g

    def cast(shape, strength=1.0):
        """The soft shadow a part throws down and away from the light, on
        whatever is behind it (three offset copies = a soft edge)."""
        p.setCompositionMode(QPainter.CompositionMode_SourceAtop)   # only lands on him
        p.setBrush(QColor(0, 0, 0, int(84 * strength)))
        p.drawPath(shape.translated(1.1, 2.0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    # each paw presses a small dark contact shadow into the floor
    if ground > 0.02:
        for w_ in paws:
            if w_.planted or pose.gait < 0.2:
                g = QRadialGradient(QPointF(0, 0), 9.0)
                g.setColorAt(0.0, QColor(0, 0, 0, int(120 * ground)))
                g.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.save()
                p.translate(w_.pos.x(), 0.6)
                p.scale(1.0, 0.24)
                p.setBrush(g)
                p.drawEllipse(QPointF(0, 0), 9.0, 9.0)
                p.restore()

    for shape, at in far_legs:                    # far legs sit in the body's shadow
        p.setBrush(leg_form(shape, 0.25))
        p.drawPath(shape)

    # tail: a round tube. Dark core, light along the side facing the lamp,
    # a cool rim along the other
    p.setBrush(SHADE)
    p.drawPath(tail)
    p.setBrush(Qt.NoBrush)
    hl = QColor(LIT)
    for width, alpha in ((6.4, 46), (2.6, 92)):
        hl.setAlpha(alpha)
        p.setPen(QPen(hl, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(tail_line.translated(-1.2, -1.4))
    cool = QColor(RIM_COOL)
    for width, alpha, off in ((1.5, 96, 2.8),):
        cool.setAlpha(alpha)
        p.setPen(QPen(cool, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(tail_line.translated(off * 0.75, off))
    p.setPen(Qt.NoPen)
    # ...and it goes into shadow where it tucks in behind his body
    _occlude(p, tail, QPointF(tbx, tby), 17.0, 17.0, 200)

    # torso: chest and belly as one soft ball
    p.setBrush(_ball(body_c, body_r))
    p.drawPath(torso)
    _rim(p, torso, body_c, body_r * 0.86, 110)
    # a fluffy chest: the fur there catches a little extra light
    chx, _ = project(cf + 5, 0, yaw)
    _sheen(p, torso, QPointF(chx - 3, cy + 3), 15.0, 20)
    # ...in the shadow of his big head, and darker underneath
    _occlude(p, torso, QPointF(hc0.x() + 2.5, hc0.y() + HEAD_H * HEAD_SCALE * 1.0),
             HEAD_W * 0.98, 17.0, 235)
    under = QLinearGradient(QPointF(0, -15), QPointF(0, 1))
    under.setColorAt(0.0, QColor(0, 0, 0, 0))
    under.setColorAt(1.0, QColor(0, 0, 0, 170))
    p.setBrush(under)
    p.drawPath(torso)

    for shape, at in near_legs:
        cast(shape, 0.55)                         # a soft shadow ties them to the belly...
        p.setBrush(leg_form(shape, 0.6))          # ...and no bright edge cuts them off it
        p.drawPath(shape)
        _sheen(p, shape, QPointF(at[2] - 1.6, at[3] - 1.2), 5.4, 26)      # soft puffy paw

    paint_ears()
    _paint_skull(p, pose, psi, skull_layer, head_local, (hxh + pose.head_dx, hyh), dev_scale)

    if toes and groom_paw is None:               # two tiny toe notches per front paw
        a_t = int(175 * (1 - pose.gait) * (1 - st * 0.5))
        if a_t > 8:
            p.setPen(QPen(QColor(0, 0, 0, a_t), 1.3, Qt.SolidLine, Qt.RoundCap))
            for tx, ty, tw_ in toes:
                for dx in (-0.3, 0.32):
                    p.drawLine(QPointF(tx + dx * tw_, ty + 1.2), QPointF(tx + dx * tw_ * 1.04, ty + 4.0))
            p.setPen(Qt.NoPen)

    # the near thigh and shoulder catch the light: a black body still reads
    # as having legs and haunches side-on
    if contours:
        a_ = int(34 * min(1.0, (sy - 0.45) * 3) * st)
        for c_, r_, _start, _span in contours:
            _sheen(p, torso, c_, r_ * 1.5, a_)

    p.save()
    p.setTransform(head_tf, True)
    p.setClipPath(head_local)
    _face(p, pose, psi)
    _eyes(p, pose, psi)
    p.restore()

    if groom_paw is not None:                     # paw up (grooming / batting)
        gp_ = groom_paw.simplified()               # one clean outline, no seams
        p.setPen(QPen(QColor(0, 0, 0, 120), 3.0))  # its shadow on his face / chest
        p.setBrush(Qt.NoBrush)
        p.drawPath(gp_)
        p.setPen(QPen(RIM, 1.2))
        p.setBrush(_ball(QPointF(groom_at[0], groom_at[1] - 4), 15, 0.8))
        p.drawPath(gp_)
        p.setPen(Qt.NoPen)

    p.save()
    p.setTransform(head_tf, True)
    _whiskers(p, pose, psi, head_local)
    _nose_bubble(p, pose, psi)
    _glasses(p, pose, psi)
    _pencil(p, pose, psi)
    _beret(p, pose, psi)
    _headphones(p, pose, psi, head_rx)
    p.restore()

    hc = head_tf.map(QPointF(0, 0))
    if pose.think > 0.01:
        _thinking_dots(p, pose, hc, side_sign)
    if pose.listen > 0.01:
        _sound_waves(p, pose, hc, side_sign)
    if sleep > 0.25:
        _zzz(p, pose, hc, side_sign)

    # paw positions and head top -> widget coordinates
    world = p.transform()
    top = world.map(head_tf.map(QPointF(0, -HEAD_H - 14)))
    if geo is not None:                           # where things are, for petting and boops
        nx_, ny_, _f = on_head(0, -40, psi)
        geo["head"] = world.map(hc)
        geo["head_r"] = HEAD_H * HEAD_SCALE * scale
        geo["nose"] = world.map(head_tf.map(QPointF(nx_, ny_)))
        geo["belly"] = world.map(QPointF(bx0, (hy + cy) / 2 + 4))
    p.restore()
    return [Paw(world.map(w.pos), w.planted, w.near) for w in paws], top


_HEAD_CACHE: dict = {}       # key -> (image, rect in head space)
HEAD_CACHE_MAX = 10
HEAD_CACHE_PX = 900          # bigger than this (huge previews): just paint it live


def _paint_skull(p: QPainter, pose: Pose, psi: float, layer, head_local: QPainterPath,
                 at: tuple[float, float], dev_scale: float) -> None:
    """His skull is the most expensive thing to shade and it rarely changes:
    it bobs and tilts far more often than it turns. So it is painted once into
    an image, and that image is re-used (moved and tilted) until he actually
    turns his head or blinks."""
    tilt = (QTransform().translate(at[0], at[1])
            .translate(0, 26).rotate(pose.head_tilt).translate(0, -26))
    size = QTransform().scale(HEAD_SCALE, HEAD_SCALE)
    key = (round(psi, 2), round(pose.eye_scale, 2), round(pose.eye_open, 1),
           round(pose.happy, 1), round(pose.sleep, 1), round(dev_scale, 3))
    hit = _HEAD_CACHE.pop(key, None)
    if hit is None:
        bounds = size.map(head_local).boundingRect()
        rect = bounds.adjusted(-4, -4, 5, 5)      # room for the rim that peeks out
        w, h = math.ceil(rect.width() * dev_scale), math.ceil(rect.height() * dev_scale)
        if max(w, h) > HEAD_CACHE_PX or min(w, h) < 2:
            layer(p, size * tilt)
            return
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        ip = QPainter(img)
        ip.setRenderHint(QPainter.Antialiasing)
        ip.scale(w / rect.width(), h / rect.height())
        ip.translate(-rect.left(), -rect.top())
        layer(ip, size)
        ip.end()
        if len(_HEAD_CACHE) >= HEAD_CACHE_MAX:
            _HEAD_CACHE.pop(next(iter(_HEAD_CACHE)))
        hit = (img, rect)
    _HEAD_CACHE[key] = hit                        # most recently used goes last
    img, rect = hit
    p.save()
    p.setTransform(tilt, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(rect, img)
    p.restore()


def _ball(centre: QPointF, radius: float, lift: float = 1.0) -> QRadialGradient:
    """Fur shaded as a rounded form: bright where the key light lands, falling
    off to true black on the far side. `lift` scales how bright the lit side is."""
    g = QRadialGradient(centre, radius, QPointF(centre.x() + LIGHT_DIR[0] * radius * 0.8,
                                                centre.y() + LIGHT_DIR[1] * radius * 0.8))
    def mix(c: QColor) -> QColor:
        return QColor(int(SHADE.red() + (c.red() - SHADE.red()) * lift),
                      int(SHADE.green() + (c.green() - SHADE.green()) * lift),
                      int(SHADE.blue() + (c.blue() - SHADE.blue()) * lift))
    half = QColor((LIT.red() + MID.red()) // 2, (LIT.green() + MID.green()) // 2,
                  (LIT.blue() + MID.blue()) // 2)
    g.setColorAt(0.0, mix(LIT))
    g.setColorAt(0.16, mix(half))
    g.setColorAt(0.40, mix(MID))
    g.setColorAt(0.74, SHADE)
    g.setColorAt(1.0, DEEP)
    return g


def _rim(p: QPainter, shape: QPainterPath, centre: QPointF, radius: float, alpha: int) -> None:
    """Cool rim light hugging the edge of a form on the side away from the key
    light: what separates a black shape from whatever is behind it."""
    off = QPointF(centre.x() + LIGHT_DIR[0] * radius * 0.2, centre.y() + LIGHT_DIR[1] * radius * 0.2)
    g = QRadialGradient(off, radius * 1.2)
    c0, c1 = QColor(RIM_COOL), QColor(RIM_COOL)
    c0.setAlpha(0)
    c1.setAlpha(alpha)
    g.setColorAt(0.0, c0)
    g.setColorAt(0.80, c0)
    g.setColorAt(1.0, c1)
    p.setBrush(g)
    p.drawPath(shape)


def _occlude(p: QPainter, shape: QPainterPath | None, centre: QPointF, rx: float, ry: float,
             alpha: int) -> None:
    """A soft contact shadow where one part of him sits in front of another
    and blocks the light. With a `shape` it falls on that shape only; without
    one it lands on whatever has already been painted (cheaper, for small
    shadows that stay inside him anyway)."""
    p.save()
    p.translate(centre)
    p.scale(1.0, ry / rx)
    g = QRadialGradient(QPointF(0, 0), rx)
    g.setColorAt(0.0, QColor(0, 0, 0, alpha))
    g.setColorAt(0.55, QColor(0, 0, 0, int(alpha * 0.55)))
    g.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    if shape is None:
        p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        p.drawEllipse(QPointF(0, 0), rx, rx)
    else:
        inv = QTransform().scale(1.0, rx / ry).translate(-centre.x(), -centre.y())
        p.drawPath(inv.map(shape))
    p.restore()


def _sheen(p: QPainter, clip: QPainterPath | None, centre: QPointF, radius: float,
           alpha: int) -> None:
    """A soft highlight, painted only on top of what is already there."""
    p.save()
    p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
    g = QRadialGradient(centre, radius)
    g.setColorAt(0, QColor(255, 255, 255, alpha))
    g.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawEllipse(centre, radius, radius)
    p.restore()


BLUSH = QColor(255, 132, 160)
NOSE = QColor(240, 150, 172)
MOUTH = QColor(120, 122, 132)


def _face(p: QPainter, pose: Pose, psi: float) -> None:
    """Chibi face: soft blush, a tiny nose and a little ω mouth that opens
    when he talks and widens when he's happy."""
    p.setPen(Qt.NoPen)
    for side in (-1, 1):                               # blush under the eyes
        bx, by, f = on_head(side * 41, -30, psi)
        if f > 0.15:
            g = QRadialGradient(QPointF(bx, by), 9)
            g.setColorAt(0, QColor(BLUSH.red(), BLUSH.green(), BLUSH.blue(),
                                   int((48 + 40 * pose.happy + 46 * pose.sleep
                                        + 80 * pose.expr_amt * (pose.expr == 'love')) * min(1, f * 1.5))))
            g.setColorAt(1, QColor(BLUSH.red(), BLUSH.green(), BLUSH.blue(), 0))
            p.setBrush(g)
            p.drawEllipse(QPointF(bx, by), 9 * min(1.0, f + 0.3), 5.5)
    _face_features(p, pose, psi)


def _face_form(p: QPainter, pose: Pose, psi: float) -> None:
    """The shape of his face under the features. Changes only with how his
    head is turned and how open his eyes are, so it is cached with the head."""
    p.setPen(Qt.NoPen)
    # the face has form, it isn't a flat ball: each eye sits in a soft socket
    # (shadow above and around it), and the muzzle is a small raised mound
    # with a whisker pad either side that catches the light
    for side in (-1, 1):
        ex, ey, f = on_head(side * EYE_PHI, EYE_LAM, psi)
        if f > 0.12:
            cf_ = min(1.0, f / EYE_FRONT)
            deep = cf_ * (0.3 + 0.7 * pose.eye_open * (1 - pose.happy) * (1 - 0.8 * pose.sleep))
            sr = 27 * pose.eye_scale
            sock = QRadialGradient(QPointF(0, 0), sr)
            sock.setColorAt(0.0, QColor(0, 0, 0, 0))
            sock.setColorAt(0.56, QColor(0, 0, 4, int(128 * deep)))
            sock.setColorAt(0.8, QColor(0, 0, 4, int(52 * deep)))
            sock.setColorAt(1.0, QColor(0, 0, 4, 0))
            p.save()
            p.translate(ex, ey - 2.5)
            p.scale(max(0.5, cf_), 1.0)
            p.setBrush(sock)
            p.drawEllipse(QPointF(0, 0), sr, sr)
            p.restore()
    mx_, my_, mf_ = on_head(0, -46, psi)
    if mf_ > 0.15:
        mw = min(1.0, mf_ * 1.3)
        chin = QRadialGradient(QPointF(mx_, my_ + 9.5), 12)       # under-chin shadow
        chin.setColorAt(0.0, QColor(0, 0, 0, int(120 * mw)))
        chin.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(chin)
        p.drawEllipse(QPointF(mx_, my_ + 9.5), 14 * mw, 6)
        for side in (-1, 1):                                       # whisker pads
            px_, py_, pf_ = on_head(side * 13, -47, psi)
            if pf_ > 0.15:
                pad = QRadialGradient(QPointF(px_ - 1.2, py_ - 1.4), 8.5)
                pad.setColorAt(0.0, QColor(150, 156, 190, int(92 * mw)))
                pad.setColorAt(0.6, QColor(150, 156, 190, int(22 * mw)))
                pad.setColorAt(1.0, QColor(150, 156, 190, 0))
                p.setBrush(pad)
                p.drawEllipse(QPointF(px_, py_), 8.5 * mw, 6.6)
        bridge = QRadialGradient(QPointF(mx_, my_ - 9), 9)          # bridge of the nose
        bridge.setColorAt(0.0, QColor(150, 156, 190, int(40 * mw)))
        bridge.setColorAt(1.0, QColor(150, 156, 190, 0))
        p.setBrush(bridge)
        p.drawEllipse(QPointF(mx_, my_ - 9), 5.5 * mw, 9)


def _face_features(p: QPainter, pose: Pose, psi: float) -> None:
    p.setPen(Qt.NoPen)
    nx, ny, f = on_head(0, -40, psi)
    if f < 0.2:
        return
    w = min(1.0, f * 1.3)
    nose = QPainterPath(QPointF(nx - 2.6 * w, ny - 1.4))
    nose.quadTo(QPointF(nx, ny - 2.6), QPointF(nx + 2.6 * w, ny - 1.4))
    nose.quadTo(QPointF(nx, ny + 2.4), QPointF(nx - 2.6 * w, ny - 1.4))
    p.setBrush(QColor(0, 0, 0, 110))                   # it sits proud of the muzzle
    p.drawPath(nose.translated(0.5, 0.9))
    ng = QLinearGradient(QPointF(nx, ny - 2.4), QPointF(nx, ny + 2.2))
    ng.setColorAt(0.0, QColor(255, 178, 196))
    ng.setColorAt(0.55, NOSE)
    ng.setColorAt(1.0, QColor(186, 96, 122))
    p.setBrush(ng)
    p.drawPath(nose)
    p.setBrush(QColor(255, 255, 255, 150))             # a tiny wet highlight
    p.drawEllipse(QPointF(nx - 0.9 * w, ny - 1.25), 0.85 * w, 0.5)
    my = ny + 3.2
    open_ = max(pose.mouth, pose.yawn * 2.2)
    if open_ > 0.08:                                   # talking / yawning
        p.setBrush(QColor(60, 24, 32))
        p.drawEllipse(QPointF(nx, my + 1.8 * open_), (2.8 + 1.2 * pose.yawn) * w,
                      2.4 * open_ + 0.6)
        if pose.yawn > 0.3:                            # tiny pink tongue
            p.setBrush(QColor(236, 128, 150))
            p.drawEllipse(QPointF(nx, my + 3.4 * open_), 2.2 * w, 1.3 * pose.yawn)
    if pose.lick > 0.05:                               # grooming: tongue out
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(236, 128, 150))
        p.drawEllipse(QPointF(nx + 1.2 * math.sin(psi), my + 3.2), 2.4 * w, 2.6 * pose.lick)
    if pose.sleep > 0.6:
        # asleep: a tiny soft ω, and now and then the tip of his tongue (blep)
        if pose.blep > 0.02:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(236, 128, 150))
            p.drawEllipse(QPointF(nx, my + 2.0 + 1.2 * pose.blep), 1.7 * w, 1.9 * pose.blep)
        s_ = 2.3 * w
        p.setPen(QPen(QColor(150, 152, 160), 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        m = QPainterPath(QPointF(nx - 2 * s_, my))
        m.quadTo(QPointF(nx - s_, my + 2.2), QPointF(nx, my))
        m.quadTo(QPointF(nx + s_, my + 2.2), QPointF(nx + 2 * s_, my))
        p.drawPath(m)
        return
    if pose.expr == "surprised" and pose.expr_amt > 0.4:        # little "o"
        p.setPen(QPen(MOUTH, 1.5))
        p.setBrush(QColor(60, 24, 32))
        p.drawEllipse(QPointF(nx, my + 2.6), 2.6 * w, 3.0 * pose.expr_amt)
        return
    if pose.expr in ("annoyed", "dizzy") and pose.expr_amt > 0.4:
        p.setPen(QPen(MOUTH, 1.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        wob = 1.2 * math.sin(pose.t * 9) if pose.expr == "dizzy" else 0
        m = QPainterPath(QPointF(nx - 4 * w, my + 1.5))
        m.quadTo(QPointF(nx, my + 1.5 + wob), QPointF(nx + 4 * w, my + 1.5))
        p.drawPath(m)
        return
    s = (3.3 + 1.2 * pose.happy) * w                   # the ω
    pen = QPen(MOUTH if pose.happy < 0.5 else QColor(150, 152, 160), 1.5,
               Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = QPainterPath(QPointF(nx - 2 * s, my))
    m.quadTo(QPointF(nx - s, my + 2.6 + pose.happy), QPointF(nx, my))
    m.quadTo(QPointF(nx + s, my + 2.6 + pose.happy), QPointF(nx + 2 * s, my))
    p.drawPath(m)


WHISKER = QColor(205, 208, 216, 150)


def _whiskers(p: QPainter, pose: Pose, psi: float, head: QPainterPath) -> None:
    """Three soft whiskers per cheek, aimed in 3D (out to the side and a bit
    forward), poking past the head outline. They wobble on a spring when he
    moves or talks."""
    nx, ny, nf = on_head(0, -38, psi)
    for side in (-1, 1):
        rx, ry, f = on_head(side * 34, -34, psi)        # root on the cheek
        dir_x = 0.4 * math.sin(psi) + side * 0.92 * math.cos(psi)
        if f < 0.12:
            # far cheek: only its whiskers that stick out past the muzzle show
            if abs(math.sin(psi)) < 0.45 or dir_x * math.sin(psi) <= 0 or nf < 0.05:
                continue
            rx, ry, vis = nx, ny + 1, 0.75
        else:
            vis = min(1.0, (f - 0.12) * 3)
        dx = math.copysign(max(0.75, abs(dir_x)), dir_x)   # always reach past the face
        reach = 26
        for k, base in enumerate((-14.0, 0.0, 14.0)):
            ang = math.radians(base + pose.whisker * (1 + 0.3 * k) * side
                               - 6 * pose.happy + 10 * pose.sleep)
            ux, uy = math.cos(ang) * dx, math.sin(ang) * 0.55
            start = QPointF(rx + ux * 3, ry + k * 2.2 - 2.2)
            end = QPointF(start.x() + ux * reach, start.y() + uy * reach + 1.5)
            ctrl = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2 - 2.5)
            path = QPainterPath(start)
            path.quadTo(ctrl, end)
            col = QColor(WHISKER)
            col.setAlphaF(WHISKER.alphaF() * vis)
            p.setPen(QPen(col, 1.15, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)


def _nose_bubble(p: QPainter, pose: Pose, psi: float) -> None:
    """The classic sleepy-cartoon snot bubble: grows from his nose with each
    breath, wobbles, then pops into a few tiny droplets."""
    nx, ny, f = on_head(0, -40, psi)
    if f < 0.2:
        return
    d = 1.0 if psi >= 0 else -1.0                 # bubbles out toward where he faces
    if pose.bubble > 0.04:
        r = 1.5 + 10.0 * pose.bubble
        wob = 0.06 * math.sin(pose.t * 5.3)
        c = QPointF(nx + d * (1.2 + r * 0.72), ny + 1.5 + r * 0.42)
        g = QRadialGradient(QPointF(c.x() - r * 0.3, c.y() - r * 0.35), r * 1.25)
        g.setColorAt(0.0, QColor(235, 250, 255, 70))
        g.setColorAt(0.75, QColor(190, 225, 245, 38))
        g.setColorAt(1.0, QColor(200, 235, 255, 90))
        p.setBrush(g)
        p.setPen(QPen(QColor(215, 238, 255, 140), 0.9))
        p.drawEllipse(c, r * (1 + wob), r * (1 - wob))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 200))       # the shine
        p.drawEllipse(QPointF(c.x() - r * 0.38, c.y() - r * 0.42), r * 0.2, r * 0.13)
    if pose.pop > 0.01:
        k = 1 - pose.pop
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(215, 238, 255, int(170 * pose.pop)))
        c0 = QPointF(nx + d * 8, ny + 5)
        for ang in (-50, -10, 30, 70, 120):
            a = math.radians(ang)
            p.drawEllipse(QPointF(c0.x() + d * math.cos(a) * (4 + 9 * k),
                                  c0.y() - math.sin(a) * (4 + 9 * k)), 1.2, 1.2)


def _eye_centre(side: int, psi: float) -> tuple[float, float, float]:
    """Where _eyes draws each eye (same turn-in for the chibi profile)."""
    ex, ey, facing = on_head(side * EYE_PHI, EYE_LAM, psi)
    turn = abs(math.sin(psi))
    return ex * (1 - 0.42 * turn * turn), ey, facing


FRAME = QColor(204, 209, 222)          # silver wire frames: read on black fur, don't fight the lime
FRAME_DARK = QColor(16, 17, 21)


def _glasses(p: QPainter, pose: Pose, psi: float) -> None:
    """Round wire study glasses: they drop onto his nose when you code or read.
    Thin silver frames sit clear of his eyes, with a glassy streak on each lens."""
    g = pose.glasses
    if g < 0.02:
        return
    p.save()
    p.setOpacity(p.opacity() * min(1.0, g * 1.5))
    p.translate(0, -18 * (1 - g))                 # slide down into place
    rims = []
    for side in (-1, 1):
        ex, ey, facing = _eye_centre(side, psi)
        cf = max(0.0, min(1.12, facing / EYE_FRONT))
        if cf < 0.12:
            continue
        cf = cf + (1 - cf) * 0.6 * abs(math.sin(psi)) if cf < 1 else cf
        rims.append([side, ex, ey + 1.0, 21.0 * cf, 22.0])
    rims.sort(key=lambda r_: r_[1])
    if len(rims) == 2:                            # the lenses never touch: a gap for the bridge
        gap = rims[1][1] - rims[0][1]
        room = gap / 2 - 2.8
        for r_ in rims:
            if r_[3] > room:
                r_[3] = max(10.0, room)
    if len(rims) == 2:                            # bridge over the nose
        (_, x1, y1, r1, _), (_, x2, y2, r2, _) = rims
        bridge = QPainterPath(QPointF(x1 + r1 * 0.97, y1 - 4))
        bridge.quadTo(QPointF((x1 + x2) / 2, y1 - 8), QPointF(x2 - r2 * 0.97, y2 - 4))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(FRAME_DARK, 3.6, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(bridge)
        p.setPen(QPen(FRAME, 1.7, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(bridge)
    for side, x, y, rx, ry in rims:
        lens = QLinearGradient(QPointF(x - rx, y - ry), QPointF(x + rx, y + ry))
        lens.setColorAt(0.0, QColor(210, 235, 255, 46))        # glass: cool at the top-left
        lens.setColorAt(0.55, QColor(210, 235, 255, 8))
        lens.setColorAt(1.0, QColor(210, 235, 255, 26))
        p.setBrush(lens)
        p.setPen(QPen(FRAME_DARK, 2.8))                        # dark edge: depth on any wallpaper
        p.drawEllipse(QPointF(x, y), rx, ry)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(FRAME, 1.25))
        p.drawEllipse(QPointF(x, y), rx, ry)
        p.save()                                               # glare streaks, inside the lens
        clip = QPainterPath()
        clip.addEllipse(QPointF(x, y), rx - 1.6, ry - 1.6)
        p.setClipPath(clip, Qt.IntersectClip)
        sweep = math.sin(pose.t * 0.7) * 3.0                   # the light drifts as he breathes
        p.setPen(QPen(QColor(255, 255, 255, 78), 3.2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x - rx * 0.95 + sweep, y - ry * 0.05), QPointF(x - rx * 0.1 + sweep, y - ry))
        p.setPen(QPen(QColor(255, 255, 255, 46), 1.4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x - rx * 0.7 + sweep, y + ry * 0.25), QPointF(x + rx * 0.15 + sweep, y - ry))
        p.restore()
    p.restore()


def _pencil(p: QPainter, pose: Pose, psi: float) -> None:
    """A little yellow pencil tucked behind his ear while you write."""
    k = pose.pencil
    if k < 0.02:
        return
    side = -1.0 if math.sin(psi) > 0.15 else 1.0    # the ear on the far side of his turn
    x, y, f = on_head(side * 52, 40, psi)
    if f < -0.25:
        return
    p.save()
    p.setOpacity(p.opacity() * min(1.0, k * 1.5))
    p.translate(x + side * 4, y - 6 - 14 * (1 - k))
    p.rotate(side * 58)
    L, W = 34.0, 6.0
    body = QPainterPath()
    body.addRoundedRect(QRectF(-L / 2, -W / 2, L * 0.72, W), 1.5, 1.5)
    p.setPen(QPen(QColor(30, 30, 34), 1.0))
    p.setBrush(QColor(246, 200, 72))
    p.drawPath(body)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 226, 120))               # highlight stripe
    p.drawRect(QRectF(-L / 2 + 1, -W / 2 + 1, L * 0.72 - 2, 1.4))
    p.setBrush(QColor(236, 140, 160))               # eraser
    p.drawRoundedRect(QRectF(-L / 2 - 5, -W / 2, 6, W), 2, 2)
    p.setBrush(QColor(186, 190, 200))               # ferrule
    p.drawRect(QRectF(-L / 2, -W / 2, 2.5, W))
    tip = QPainterPath(QPointF(-L / 2 + L * 0.72, -W / 2))
    tip.lineTo(QPointF(L / 2 + 3, 0))
    tip.lineTo(QPointF(-L / 2 + L * 0.72, W / 2))
    tip.closeSubpath()
    p.setBrush(QColor(235, 200, 150))               # wood
    p.drawPath(tip)
    p.setBrush(QColor(50, 50, 56))                  # graphite
    p.drawEllipse(QPointF(L / 2 + 1.5, 0), 1.6, 1.2)
    p.restore()


def _beret(p: QPainter, pose: Pose, psi: float) -> None:
    """An artist's beret, tilted between his ears, for Blender / design work."""
    k = pose.beret
    if k < 0.02:
        return
    d = 1.0 if psi >= 0 else -1.0
    p.save()
    p.setOpacity(p.opacity() * min(1.0, k * 1.5))
    cx = 8 * math.sin(psi) - 6 * d
    cy = -HEAD_H + 6 - 20 * (1 - k)
    p.translate(cx, cy)
    p.rotate(-12 * d)
    p.setPen(QPen(QColor(20, 20, 24), 1.4))
    p.setBrush(QColor(176, 44, 64))
    p.drawEllipse(QPointF(0, 0), 31, 12)            # the soft top
    p.setBrush(QColor(140, 30, 48))
    p.drawRoundedRect(QRectF(-24, 4, 48, 7), 3.5, 3.5)   # the band
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 46))
    p.drawEllipse(QPointF(-9, -4), 13, 4.5)         # sheen
    p.setPen(QPen(QColor(140, 30, 48), 3.0, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(3, -11), QPointF(6, -17))    # the little stem
    p.restore()


def _heart(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath(QPointF(cx, cy + r * 0.95))
    path.cubicTo(QPointF(cx - r * 1.5, cy - r * 0.1), QPointF(cx - r * 0.75, cy - r * 1.25),
                 QPointF(cx, cy - r * 0.45))
    path.cubicTo(QPointF(cx + r * 0.75, cy - r * 1.25), QPointF(cx + r * 1.5, cy - r * 0.1),
                 QPointF(cx, cy + r * 0.95))
    return path


LID_MEET = 0.12     # where the lids meet: fraction of the eye radius below its centre


def _closed_line(p: QPainter, ex: float, y: float, cf: float, sz: float, side: int,
                 slant: float, col: QColor) -> None:
    """A closed eye: a soft, contented curve (‿) a bit narrower than the
    open eye, its outer corner dipping slightly. `slant` tilts it (grumpy)."""
    w = 11.0 * cf * sz
    dip = 1.0 * sz                       # outer corner a hair lower: peaceful
    ly = y - 1.5 * sz - slant + (dip if side < 0 else 0.0)
    ry_ = y - 1.5 * sz + slant + (dip if side > 0 else 0.0)
    p.setPen(QPen(col, 3.2 * sz, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    line = QPainterPath(QPointF(ex - w, ly))
    line.quadTo(QPointF(ex, y + 5.0 * sz), QPointF(ex + w, ry_))
    p.drawPath(line)


def _eyes(p: QPainter, pose: Pose, psi: float) -> None:
    sz = pose.eye_scale
    happy = pose.happy
    # `opened` is the eye's SHAPE (squints when happy / yawning); blinking,
    # sleeping and slow blinks are an eyelid sliding down over it, like a
    # real cat, so there's no squashed in-between frame
    opened = (1 - happy) * (1 - min(1.0, pose.yawn * 1.4))
    close = max(0.0, min(1.0, 1 - pose.eye_open))
    special = pose.expr if pose.expr_amt > 0.5 else ""
    for side in (-1, 1):
        ex, ey, facing = on_head(side * EYE_PHI, EYE_LAM, psi)
        cf = min(1.12, facing / EYE_FRONT)
        if cf < 0.1:
            continue
        # chibi profile: don't let the eye squash to a sliver at the edge;
        # keep it roundish and pull it a little inside the face
        turn = abs(math.sin(psi))
        cf = cf + (1 - cf) * 0.6 * turn if cf < 1 else cf
        ex = ex * (1 - 0.42 * turn * turn)          # sits inside the face, not on the edge
        sz = pose.eye_scale * (1 - 0.08 * turn * turn)
        c = QPointF(ex, ey)
        rx = 16.5 * sz * cf
        if pose.glow > 0.01 and opened > 0.2 and close < 0.5:
            g = QRadialGradient(c, 30 * sz)
            g.setColorAt(0, QColor(186, 226, 92, int(95 * pose.glow * cf)))
            g.setColorAt(1, QColor(186, 226, 92, 0))
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawEllipse(c, 30 * sz, 30 * sz)

        if special == "love" and opened > 0.3 and close < 0.6:                    # heart eyes
            beat = 1 + 0.08 * math.sin(pose.t * 9)
            p.setPen(Qt.NoPen)
            p.save()
            p.translate(c)
            p.scale(cf * beat, beat)
            hg = QLinearGradient(QPointF(0, -12 * sz), QPointF(0, 13 * sz))   # puffy, not flat
            hg.setColorAt(0.0, IRIS_DEEP)
            hg.setColorAt(0.45, EYE)
            hg.setColorAt(1.0, IRIS_LIGHT)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawPath(_heart(0.6, 2.0, 13.6 * sz))
            p.setBrush(hg)
            p.drawPath(_heart(0, 1, 13 * sz))
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawEllipse(QPointF(-5.2 * sz, -4.6 * sz), 2.5 * sz, 1.9 * sz)
            p.restore()
            continue
        if special == "dizzy" and opened > 0.3 and close < 0.6:                   # spiral eyes
            p.setPen(QPen(EYE, 2.6, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            spin = pose.t * 7 * side
            path = QPainterPath()
            for i in range(60):
                ang = spin + i * 0.32
                r = 1.2 + i * 0.24
                pt = QPointF(ex + math.cos(ang) * r * cf, ey + math.sin(ang) * r)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            p.drawPath(path)
            continue

        if opened > 0.16 and happy < 0.55 and close < 0.93:
            ry = 19.0 * sz * opened
            annoyed = special == "annoyed"
            # closing: the upper lid comes down and the lower lid comes up a
            # little, so they meet a bit below the middle of the eye (where a
            # real cat's eye closes), not at the bottom over the whiskers
            lid_c = close * (1 + LID_MEET) / 2 * 1.03
            lid = max(pose.lid, 0.5 if annoyed else 0.0, lid_c)
            slant = side * 3.0 if annoyed else 0.0           # grumpy brow tilt
            lid_y = ey - ry + 2 * ry * lid
            low_y = ey + ry - close * (1 - LID_MEET) * ry * 1.03
            sag = 2.5 + 2.5 * close + 4.0 * pose.sleep * (1 - close)   # drowsy: soft, round lids
            # the eye is only drawn BETWEEN the eyelid lines, so the lids are
            # the head itself (same shading), not painted patches
            eye_shape = QPainterPath()
            eye_shape.addEllipse(c, rx, ry)
            if lid > 0.02:
                above = QPainterPath(QPointF(ex - rx - 2, ey - ry - 3))
                above.lineTo(QPointF(ex + rx + 2, ey - ry - 3))
                above.lineTo(QPointF(ex + rx + 2, lid_y + slant))
                above.quadTo(QPointF(ex, lid_y + sag), QPointF(ex - rx - 2, lid_y - slant))
                above.closeSubpath()
                eye_shape = eye_shape.subtracted(above)
            if close > 0.02:
                below = QPainterPath(QPointF(ex - rx - 2, ey + ry + 3))
                below.lineTo(QPointF(ex + rx + 2, ey + ry + 3))
                below.lineTo(QPointF(ex + rx + 2, low_y))
                below.quadTo(QPointF(ex, low_y + sag), QPointF(ex - rx - 2, low_y))
                below.closeSubpath()
                eye_shape = eye_shape.subtracted(below)
            # the last part of closing cross-fades into the closed line, so
            # there's never a thin lime "goggles" slit between open and shut
            fade = 1.0 - max(0.0, min(1.0, (close - 0.45) / 0.2))
            base_op = p.opacity()
            p.save()
            p.setOpacity(base_op * fade)
            p.setClipPath(eye_shape, Qt.IntersectClip)
            p.setPen(Qt.NoPen)
            # iris: same lime, but with depth. Brighter toward the bottom where
            # light pools, a darker ring at the rim, and the shadow his upper
            # lid casts. Still reads as Kuro's flat lime from a distance.
            # 1. top to bottom: deep green under the brow, his lime through
            #    the middle, a bright pool of light at the bottom
            iris = QLinearGradient(QPointF(ex, ey - ry), QPointF(ex, ey + ry))
            iris.setColorAt(0.0, IRIS_SHADOW)
            iris.setColorAt(0.16, IRIS_DEEP)
            iris.setColorAt(0.4, EYE)
            iris.setColorAt(0.78, IRIS_LIGHT)
            iris.setColorAt(1.0, EYE)
            p.setBrush(iris)
            p.drawEllipse(c, rx, ry)
            # 2. the eye is a ball: it darkens toward its rim all the way round
            limb = QRadialGradient(c, max(rx, ry))
            l0, l1, l2 = QColor(IRIS_RIM), QColor(IRIS_RIM), QColor(18, 40, 6)
            l0.setAlpha(0)
            l1.setAlpha(96)
            l2.setAlpha(190)
            limb.setColorAt(0.0, l0)
            limb.setColorAt(0.8, l0)
            limb.setColorAt(0.94, l1)
            limb.setColorAt(1.0, l2)
            p.save()
            p.translate(c)
            p.scale(rx / max(rx, ry), ry / max(rx, ry))
            p.translate(-c.x(), -c.y())
            p.setBrush(limb)
            p.drawEllipse(c, max(rx, ry), max(rx, ry))
            p.restore()
            # 3. the shadow his brow and upper lid cast across the top of the eye
            shade = QLinearGradient(QPointF(ex, ey - ry), QPointF(ex, ey - ry * 0.05))
            shade.setColorAt(0.0, QColor(2, 12, 0, 150))
            shade.setColorAt(0.38, QColor(2, 12, 0, 56))
            shade.setColorAt(1.0, QColor(2, 12, 0, 0))
            p.setBrush(shade)
            p.drawEllipse(c, rx, ry)
            # pupil: travels far inside the eye, converges for a close cursor,
            # dilates when he's interested
            ps = pose.pupil
            prx, pry = 11.6 * sz * cf * ps * (1 - 0.18 * turn), 14.0 * sz * opened * ps
            lx = pose.look_x * 5.2 - side * (1.6 + 2.4 * pose.converge)
            ly = pose.look_y * 4.6 - 1.8
            room_x, room_y = max(0.5, rx - prx - 1.0), max(0.5, ry - pry - 1.0)
            k = math.hypot(lx / room_x, ly / room_y)
            if k > 1:
                lx, ly = lx / k, ly / k
            px = ex + lx * cf + 1.8 * math.sin(psi)            # looks where he faces
            py = ey + ly * opened + 3.0 * pose.sleep * opened       # sleepy eyes sink
            # 4. the pupil is a hole: the iris sinks into shadow around it
            well = QRadialGradient(QPointF(px, py), max(prx, pry) * 1.15)
            well.setColorAt(0.0, QColor(10, 30, 0, 170))
            well.setColorAt(0.78, QColor(10, 30, 0, 120))
            well.setColorAt(1.0, QColor(10, 30, 0, 0))
            p.save()
            p.translate(px, py)
            p.scale(prx / max(prx, pry), pry / max(prx, pry))
            p.setBrush(well)
            p.drawEllipse(QPointF(0, 0), max(prx, pry) * 1.15, max(prx, pry) * 1.15)
            p.restore()
            p.setBrush(PUPIL)
            p.drawEllipse(QPointF(px, py), prx, pry)
            # a soft lime bounce inside the bottom of the pupil: wet, deep eyes
            bounce = QRadialGradient(QPointF(px, py + pry * 0.95), pry * 0.95)
            bounce.setColorAt(0.0, QColor(150, 205, 80, 78))
            bounce.setColorAt(1.0, QColor(150, 205, 80, 0))
            p.setBrush(bounce)
            p.drawEllipse(QPointF(px, py), prx, pry)
            # 5. the cornea is a glass dome over all of it: a faint sweep of
            #    sky across the top, a thin crescent of light along the bottom
            dome = QLinearGradient(QPointF(ex - rx, ey - ry), QPointF(ex + rx * 0.4, ey))
            dome.setColorAt(0.0, QColor(210, 228, 255, 58))
            dome.setColorAt(1.0, QColor(210, 228, 255, 0))
            p.setBrush(dome)
            p.drawEllipse(QPointF(ex - rx * 0.12, ey - ry * 0.34), rx * 0.8, ry * 0.56)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(244, 255, 200, 120), 1.5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(ex - rx * 0.82, ey - ry * 0.82, rx * 1.64, ry * 1.64),
                      int(-128 * 16), int(76 * 16))
            p.setPen(Qt.NoPen)
            if opened > 0.55:
                # highlights stay where the light is: the eye looks glossy
                # as the pupil moves under them. A big one, a small one, and
                # they twinkle a little when he's delighted
                tw = 1.0 + 0.16 * pose.sparkle * math.sin(pose.t * 11 + side)
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawEllipse(QPointF(ex - 5.4 * cf, ey - 7.6 * opened),
                              3.3 * cf * tw, 3.3 * opened * tw)
                p.setBrush(QColor(255, 255, 255, 150))
                p.drawEllipse(QPointF(ex + 4.4 * cf, ey + 6.4 * opened), 1.6 * cf, 1.6)
                if pose.sparkle > 0.05:               # delighted: a tiny star glint
                    a_ = int(230 * min(1.0, pose.sparkle))
                    gx, gy = ex + 5.6 * cf, ey - 3.0 * opened
                    r_ = (2.6 + 0.9 * math.sin(pose.t * 9 + side * 2)) * pose.sparkle
                    star = QPainterPath(QPointF(gx, gy - r_))
                    for q in range(1, 8):
                        ang = -math.pi / 2 + q * math.pi / 4
                        rr = r_ if q % 2 == 0 else r_ * 0.32
                        star.lineTo(QPointF(gx + math.cos(ang) * rr * cf, gy + math.sin(ang) * rr))
                    star.closeSubpath()
                    p.setBrush(QColor(255, 255, 255, a_))
                    p.drawPath(star)
            p.restore()
            # the rim of the lids: a dark line all round, heavier along the
            # top, so the eye sits IN his face instead of on it
            p.save()
            p.setOpacity(base_op * fade)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 190), 1.15))
            p.drawPath(eye_shape)
            if lid <= 0.02:
                p.setPen(QPen(QColor(0, 0, 0, 215), 2.1, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(QRectF(ex - rx, ey - ry, 2 * rx, 2 * ry), 22 * 16, 136 * 16)
            p.restore()
            p.setPen(Qt.NoPen)
            if lid > 0.02:                                   # soft dark lid edge
                edge = QPainterPath(QPointF(ex - rx, lid_y - slant))
                edge.quadTo(QPointF(ex, lid_y + sag), QPointF(ex + rx, lid_y + slant))
                p.save()
                p.setOpacity(base_op * fade)
                clip = QPainterPath()
                clip.addEllipse(c, rx + 0.5, ry + 0.5)
                p.setClipPath(clip, Qt.IntersectClip)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(0, 0, 0, 150), 2.2, Qt.SolidLine, Qt.RoundCap))
                p.drawPath(edge)
                p.restore()
                if close > 0.45:                             # becomes the closed-eye line
                    a = min(1.0, (close - 0.45) / 0.35)
                    _closed_line(p, ex, ey + LID_MEET * ry, cf, sz, side, slant,
                                 QColor(186, 226, 92, int(255 * a)))
                    p.setPen(Qt.NoPen)
        elif happy < 0.5:
            # fully shut (asleep / mid-blink): a soft lime crease where the
            # lids met, the same line the closing animation ends on
            ry = 19.0 * sz * max(opened, 0.5)
            _closed_line(p, ex, ey + LID_MEET * ry, cf, sz, side,
                         side * 3.0 if special == "annoyed" else 0.0, EYE)
        if happy > 0.01:
            col = QColor(EYE)
            col.setAlphaF(min(1.0, happy * 1.4))
            p.setPen(QPen(col, 3.8, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            w = 12 * cf
            path = QPainterPath(QPointF(ex - w, ey + 4))
            path.quadTo(QPointF(ex, ey - 11), QPointF(ex + w, ey + 4))
            p.drawPath(path)


PAD = QColor(22, 23, 27)


def _headphones(p: QPainter, pose: Pose, psi: float, head_rx: float) -> None:
    """Matte headphones with lime rims; they slide down onto his head."""
    h = pose.headphones
    if h < 0.02:
        return
    p.save()
    p.setOpacity(min(1.0, h * 1.6))
    p.translate(0, -26 * (1 - h))
    cups = []
    turn = max(0.0, min(1.0, (abs(math.sin(psi)) - 0.45) / 0.5))
    m = turn * turn * (3 - 2 * turn)
    d = 1.0 if psi >= 0 else -1.0
    for side in (-1, 1):
        cx, cy, f = on_head(side * 86, 4, psi)
        cx -= d * m * 10                      # side-on: over his ear, clear of the eye
        cups.append((side, cx, cy - 3 * m, f))
    l, r = cups[0], cups[1]
    if m < 0.5:
        band = QPainterPath(QPointF(l[1], l[2] - 6))              # over the crown
        band.cubicTo(QPointF(l[1] - 4, -HEAD_H - 16), QPointF(r[1] + 4, -HEAD_H - 16),
                     QPointF(r[1], r[2] - 6))
    else:                                       # side-on: from the near cup over the top
        near = max(cups, key=lambda c_: c_[3])
        cx0, cy0 = near[1], near[2] - 12
        band = QPainterPath(QPointF(cx0, cy0))
        band.cubicTo(QPointF(cx0 + d * 1, -HEAD_H * 0.9), QPointF(cx0 - d * 4, -HEAD_H - 5),
                     QPointF(cx0 - d * 16, -HEAD_H - 1))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(PAD, 6.5, Qt.SolidLine, Qt.RoundCap))
    p.drawPath(band)
    p.setPen(QPen(QColor(186, 226, 92, 150), 1.4, Qt.SolidLine, Qt.RoundCap))
    p.drawPath(band)
    beat = 0.5 + 0.5 * math.sin(pose.t * 2 * math.pi * 1.9)
    for side, cx, cy, f in cups:
        if f < -0.12:
            continue                       # behind his head
        w = 6.5 + 5.5 * max(0.0, min(1.0, abs(math.sin(psi)) if f < 0.4 else 1 - f))
        rect = QRectF(cx - w, cy - 12, 2 * w, 24)
        p.setPen(QPen(QColor(186, 226, 92, 200), 1.6))
        p.setBrush(PAD)
        p.drawRoundedRect(rect, w, 9)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(186, 226, 92, int(90 + 120 * beat)))
        p.drawEllipse(QPointF(cx, cy), 1.8 + 0.6 * beat, 1.8 + 0.6 * beat)
    p.restore()


def _thinking_dots(p: QPainter, pose: Pose, hc: QPointF, side: int) -> None:
    p.setPen(Qt.NoPen)
    for i in range(3):
        pulse = (math.sin(pose.t * 5.5 - i * 0.95) + 1) / 2
        col = QColor(EYE)
        col.setAlphaF(pose.think * (0.25 + 0.75 * pulse))
        p.setBrush(col)
        r = 2.0 + 1.3 * pulse
        p.drawEllipse(QPointF(hc.x() + side * (30 + i * 9), hc.y() - 52 - pulse * 3), r, r)


def _sound_waves(p: QPainter, pose: Pose, hc: QPointF, side: int) -> None:
    p.setBrush(Qt.NoBrush)
    cx, cy = hc.x() + side * 43, hc.y() - 22
    start = -50 if side > 0 else 130
    for k in range(3):
        ph = (pose.t * 0.9 + k / 3) % 1.0
        col = QColor(EYE)
        col.setAlphaF(pose.listen * (1 - ph) * 0.85)
        p.setPen(QPen(col, 2.0, Qt.SolidLine, Qt.RoundCap))
        r = 6 + ph * 18
        p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), start * 16, 100 * 16)


def _zzz(p: QPainter, pose: Pose, hc: QPointF, side: int) -> None:
    f = QFont()
    f.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono"])
    f.setBold(True)
    for k in range(3):
        ph = (pose.t * 0.33 + k / 3) % 1.0
        col = QColor(EYE)
        col.setAlphaF(max(0.0, (pose.sleep - 0.25) / 0.75) * (1 - ph) * 0.9 *
                      min(1.0, ph * 6))
        f.setPointSizeF(7 + ph * 6)
        p.setFont(f)
        p.setPen(col)
        x = hc.x() + side * (32 + ph * 16 + math.sin(ph * 6) * 3)
        p.drawText(QPointF(x, hc.y() - 42 - ph * 34), "z")
