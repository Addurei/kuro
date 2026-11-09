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
from PySide6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen,
                           QRadialGradient, QTransform)

FUR = QColor(10, 10, 12)
FUR_FAR = QColor(27, 27, 32)           # far-side limbs: a touch lighter = depth
RIM = QColor(255, 255, 255, 36)        # faint outline so he reads on dark wallpapers
INNER_EAR = QColor(238, 238, 242)
EYE = QColor(186, 226, 92)             # Kuro's lime green
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

    def step(self, dt: float) -> None:
        acc = self.k * (self.target - self.value) - self.d * self.vel
        self.vel += acc * dt
        self.value += self.vel * dt

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
    lid: float = 0.0           # 0..1 upper eyelids lowered (annoyed, relaxed)
    expr: str = ""             # "", "dizzy", "love", "surprised", "annoyed", "vibe"
    expr_amt: float = 0.0      # 0..1 blend of the special expression
    headphones: float = 0.0    # 0..1 slid on
    whisker: float = 0.0       # degrees of springy whisker wobble
    dangle: float = 0.0        # being carried
    jiggle: float = 0.0        # chubby jelly wobble (springy, +/-)
    paw_raise: float = 0.0     # 0..1 lifts the near front paw to his mouth (grooming)
    lick: float = 0.0          # 0..1 little tongue out
    knead: float = 0.0         # 0..1 "making biscuits" with the front paws
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
EYE_PHI, EYE_LAM = 28.0, -12.0          # big eyes, set a little below the middle
EYE_FRONT = math.cos(math.radians(EYE_LAM)) * math.cos(math.radians(EYE_PHI))
EAR_SINK = HEAD_H * math.sqrt(1 - (30 / HEAD_W) ** 2) - 22
EAR_FRONT = 0.35 + 0.65 * math.cos(math.radians(EAR_PHI * 0.6))


# ---------------------------------------------------------------- body
def draw_cat(p: QPainter, pose: Pose, origin: QPointF, scale: float
             ) -> tuple[list[Paw], QPointF]:
    """Paint Kuro. Returns his paws and the top of his head, in widget
    coordinates (for paw prints and for pinning the HUD above him)."""
    yaw = math.radians(max(-88.0, min(88.0, pose.yaw)))
    psi = math.radians(max(-88.0, min(88.0, pose.yaw + pose.head_yaw)))
    st, sleep, b = pose.stand, pose.sleep, pose.breath
    run = pose.run * pose.gait
    phase = pose.gait_phase
    side_sign = -1 if pose.yaw + pose.head_yaw < -6 else 1

    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(origin)
    p.scale(scale, scale)

    # ---- ground shadow, or a levitation glow when he's standing on air
    k = max(0.3, 1 - pose.lift / 70)
    span = 30 + 18 * abs(math.sin(yaw)) * st
    ground = 1 - pose.airborne
    if ground > 0.02:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, int(80 * k * ground)))
        p.drawEllipse(QPointF(0, 1.0), span * k, 4.2 * k)
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
    body_depth = project((hf + cf) / 2, 0, yaw)[1]

    # ---- legs (IK) -----------------------------------------------------
    gp = _gait_params(pose.run)
    legs = []
    groom_paw = None              # a paw raised to his face is drawn in front of it
    contours = []                 # (centre, radius, start_deg, span_deg) for near haunch/shoulder
    paws: list[Paw] = []
    for front in (1, 0):
        for side in (-1, 1):
            if front:
                root_f, root_y = cf + 2, cy + rc * 0.35
                base_f = cf + 2
                s_off = side * lerp(8.5, 8.0, st)
                a, bl = lerp(16.0, 11.5, st), lerp(16.0, 11.5, st)
                radii = (7.4, 6.8, 6.4)
            else:
                root_f, root_y = hf + 1, hy + rh * 0.3
                base_f = lerp(hf + 11, hf + 1, st)
                s_off = side * lerp(13.5, 8.5, st)
                a, bl = lerp(16.0, 11.5, st), lerp(17.0, 12.0, st)
                radii = (lerp(12.0, 9.5, st), 7.2, 6.6)
            dfo, lift, planted = _foot_cycle(phase + gp["off"][(front, side)],
                                             gp["duty"], gp["stride"], gp["lift"])
            foot_f = base_f + dfo * pose.gait
            foot_y = -3.2 - lift * pose.gait
            foot_f, foot_y = tilt(foot_f, foot_y)    # paws walk the same slope
            near_side = -1 if math.sin(yaw) > 0.05 else 1 if math.sin(yaw) < -0.05 else -1
            if front and pose.knead > 0:             # alternate little paw presses
                press = max(0.0, math.sin(pose.t * 6.5 + (0 if side < 0 else math.pi)))
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
            _ellipse(shape, px, tgt[1] + 0.2,
                     math.hypot(8.0 * sy, 6.8 * cyaw), 5.0)       # round beans
            near = (-s_off * math.sin(yaw) + (tgt[0] - (hf + cf) / 2) * math.cos(yaw) * 0.6
                    ) > body_depth * 0.0 - 0.01
            if front and side == near_side and (pose.paw_raise > 0.3 or pose.bat > 0.3):
                groom_paw = shape
            else:
                legs.append((near, shape))
            if near and sy > 0.45 and st > 0.3:
                rx_, _ = project(root_f, s_off, yaw)
                if front:                               # shoulder / elbow line
                    contours.append((QPointF(rx_ - 2 * math.copysign(1, math.sin(yaw)), root_y + 2),
                                     7.0, -75, 120))
                else:                                   # front edge of the round haunch
                    contours.append((QPointF(rx_ - 3 * math.copysign(1, math.sin(yaw)), root_y),
                                     11.0, -80, 150))
            paws.append(Paw(QPointF(px, tgt[1] + 2.5), planted and pose.gait > 0.2, near))

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
        prev = cur
    _ellipse(tail, tbx, tby, 6.2, 6.2)

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
    head = head_tf.map(head_local)

    ears_outer, ears_inner = [], []
    turn_e = max(0.0, min(1.0, (abs(math.sin(psi)) - 0.8) / 0.17))   # only near side-on
    m_e = turn_e * turn_e * (3 - 2 * turn_e)
    d_e = 1.0 if psi >= 0 else -1.0
    ear_order = []
    for side in (-1, 1):
        phi = side * EAR_PHI
        ex, _, _ = on_head(phi, EAR_LAM, psi)
        far_e = math.cos(psi + math.radians(phi * 0.6)) < 0
        ex -= d_e * m_e * (11.0 + (13.0 if far_e else 0.0))   # side-on: on the crown, not the face
        u = min(0.98, abs(ex) / head_rx)
        ey = -HEAD_H * math.sqrt(1 - u * u) + EAR_SINK
        front = math.cos(psi + math.radians(phi * 0.6))
        width = min(1.08, (0.35 + 0.65 * abs(front)) / EAR_FRONT)
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
        local.addEllipse(t.map(QPointF(2.0 * (1 if side < 0 else -1) * width, 13.0)),
                         15.0 * width, 9.0)              # soft fillet where ear meets head
        outer = head_tf.map(local)
        ear_order.append((front, outer))
        vis = max(0.0, min(1.0, front * 3))
        if vis > 0.02:
            ears_inner.append((head_tf.map(t.map(_ear_path(m_e, mf, d_e, yk, False))), outer, vis))
    ear_order.sort(key=lambda e_: e_[0])          # far ear painted first
    ears_outer = [o for _f, o in ear_order]
    # ears and head are ONE silhouette: one outline, no seams at the base
    head_full = head
    for o in ears_outer:
        o.setFillRule(Qt.WindingFill)
        head_full = head_full.united(o.simplified())

    # ---- paint: rims first (outer silhouette only), then fills back-to-front
    far_legs = [s for nr, s in legs if not nr]
    near_legs = [s for nr, s in legs if nr]
    fills = ([(s, FUR_FAR) for s in far_legs] + [(tail, FUR), (torso, FUR)]
             + [(s, FUR) for s in near_legs])
    # faint rim on the big shapes only (legs are thin and tucked under him;
    # outlining their many little blobs was most of the frame cost)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(RIM, 2.0))
    for shape in (tail, torso, head_full):
        p.drawPath(shape)
    p.setPen(Qt.NoPen)
    for shape, col in fills:
        p.setBrush(col)
        p.drawPath(shape)
    p.setBrush(FUR)
    p.drawPath(head_full)
    # inner ears, then the skull again on top so they only show above the
    # head line (cheaper than subtracting paths every frame)
    if ears_inner:
        for inner, _outer, vis in ears_inner:
            col = QColor(INNER_EAR)
            col.setAlphaF(vis)
            p.setBrush(col)
            p.drawPath(inner)
        p.setBrush(FUR)
        p.drawPath(head)

    # faint contour lines for the near thigh and shoulder: a cartoon trick
    # so a black body still reads as having legs and haunches side-on
    if contours:                    # soft light instead of lines: haunch/shoulder volume
        a_ = int(18 * min(1.0, (sy - 0.45) * 3) * st)
        for c_, r_, _start, _span in contours:
            _sheen(p, torso, c_, r_ * 1.4, a_)
    # key light on the back and crown, so the black reads as a volume
    bx0, _ = project((hf + cf) / 2, 0, yaw)
    _sheen(p, torso, QPointF(bx0 - 6, min(hy, cy) - rc * 0.8), 34, 16)
    sx, sy_, _ = on_head(-25, 50, psi)
    _sheen(p, head_full, head_tf.map(QPointF(sx, sy_)), 48, 24)


    p.save()
    p.setTransform(head_tf, True)
    p.setClipPath(head_local)
    _face(p, pose, psi)
    _eyes(p, pose, psi)
    p.restore()

    if groom_paw is not None:                     # paw up (grooming / batting)
        p.setPen(QPen(RIM, 1.6))
        p.setBrush(FUR)
        p.drawPath(groom_paw.simplified())         # one clean outline, no seams
        p.setPen(Qt.NoPen)

    p.save()
    p.setTransform(head_tf, True)
    _whiskers(p, pose, psi, head_local)
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
    p.restore()
    return [Paw(world.map(w.pos), w.planted, w.near) for w in paws], top


def _sheen(p: QPainter, clip: QPainterPath, centre: QPointF, radius: float,
           alpha: int) -> None:
    p.save()
    p.setClipPath(clip)
    g = QRadialGradient(centre, radius)
    g.setColorAt(0, QColor(255, 255, 255, alpha))
    g.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawPath(clip)
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
                                   int((48 + 40 * pose.happy + 80 * pose.expr_amt * (pose.expr == 'love')) * min(1, f * 1.5))))
            g.setColorAt(1, QColor(BLUSH.red(), BLUSH.green(), BLUSH.blue(), 0))
            p.setBrush(g)
            p.drawEllipse(QPointF(bx, by), 9 * min(1.0, f + 0.3), 5.5)
    nx, ny, f = on_head(0, -40, psi)
    if f < 0.2:
        return
    w = min(1.0, f * 1.3)
    p.setBrush(NOSE)
    nose = QPainterPath(QPointF(nx - 2.6 * w, ny - 1.4))
    nose.quadTo(QPointF(nx, ny - 2.6), QPointF(nx + 2.6 * w, ny - 1.4))
    nose.quadTo(QPointF(nx, ny + 2.4), QPointF(nx - 2.6 * w, ny - 1.4))
    p.drawPath(nose)
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
            p.setBrush(EYE)
            p.save()
            p.translate(c)
            p.scale(cf * beat, beat)
            p.drawPath(_heart(0, 1, 13 * sz))
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
            sag = 2.5 + 2.5 * close
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
            p.save()
            p.setClipPath(eye_shape, Qt.IntersectClip)
            p.setPen(Qt.NoPen)
            p.setBrush(EYE)
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
            py = ey + ly * opened
            p.setBrush(PUPIL)
            p.drawEllipse(QPointF(px, py), prx, pry)
            if opened > 0.55:
                # highlights stay where the light is: the eye looks glossy
                # as the pupil moves under them
                p.setBrush(QColor(255, 255, 255, 220))
                p.drawEllipse(QPointF(ex - 5.2 * cf, ey - 7.5 * opened), 2.9 * cf, 2.9 * opened)
                p.setBrush(QColor(255, 255, 255, 95))
                p.drawEllipse(QPointF(ex + 4.0 * cf, ey + 6.0 * opened), 1.2 * cf, 1.2)
            p.restore()
            if lid > 0.02:                                   # soft dark lid edge
                edge = QPainterPath(QPointF(ex - rx, lid_y - slant))
                edge.quadTo(QPointF(ex, lid_y + sag), QPointF(ex + rx, lid_y + slant))
                p.save()
                clip = QPainterPath()
                clip.addEllipse(c, rx + 0.5, ry + 0.5)
                p.setClipPath(clip, Qt.IntersectClip)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(0, 0, 0, 150), 2.2, Qt.SolidLine, Qt.RoundCap))
                p.drawPath(edge)
                p.restore()
                if close > 0.6:                              # becomes the closed-eye line
                    a = min(1.0, (close - 0.6) / 0.33)
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
