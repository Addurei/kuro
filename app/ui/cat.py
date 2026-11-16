"""Kuro on your desktop: animation brain, powers and movement physics.

Every 16 ms (~60 fps) the widget:
  1. moves the window (being thrown, teleporting)
  2. turns speed changes into follow-through (head lag, ear flop, tail drag)
  3. sets spring targets from his mood, steps every spring
  4. repaints the rig, plus sparks and teleport effects

Powers
  * Teleporting: now and then he pops to another spot on screen, in one of
    several styles (sparkle, portal, poof, glitch, zip).
  * Levitation: when he's left mid-air he floats there, gently bobbing.
  * Throw him and he slides, stops and stays where he lands.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

from .cat_rig import EYE, Pose, Spring, _accent, _heart, draw_cat

HOP_GRAVITY = 900.0     # rig units/s^2 for little hops
BLINK_TIME = 0.26       # seconds to vanish (and again to reappear)
MAX_YAW = 24.0          # he always faces you: body turns at most this far...
MAX_HEAD = 22.0         # ...and his head this much more


@dataclass
class _Note:
    x: float
    y: float
    born: float
    glyph: str
    drift: float


@dataclass
class _Spark:
    x: float
    y: float
    vx: float
    vy: float
    born: float
    life: float
    size: float
    kind: str = "spark"      # spark | puff | pixel


class CatWidget(QWidget):
    clicked = Signal()
    double_clicked = Signal()
    menu_requested = Signal(QPoint)
    moved = Signal()

    def __init__(self, scale: float = 1.0, can_wander: Callable[[], bool] = lambda: True):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        self.k = 0.82 * scale                       # rig units -> pixels
        self.setFixedSize(int(250 * scale), int(190 * scale))
        self.base = QPointF(self.width() / 2, self.height() - 14 * scale)
        self.can_wander = can_wander
        self.head_top = QPointF(self.width() / 2, 20)

        self.state = "idle"
        self.t = 0.0
        self.rest_dir = random.choice((-1, 1))

        S = Spring
        self.sp = {
            "yaw": S(14 * self.rest_dir, 55, 0.72), "head_yaw": S(0, 85, 0.62),
            "pitch": S(0, 40, 0.8),
            "stand": S(0, 30, 0.85), "gait": S(0, 18, 1.0), "run": S(0, 10, 1.0),
            "squash": S(0, 260, 0.28), "lean": S(0, 110, 0.55),
            "tilt": S(0, 90, 0.6), "air": S(0, 6, 1.0), "hover": S(0, 30, 0.9),
            "hlag_x": S(0, 150, 0.32), "hlag_y": S(0, 170, 0.32),
            "tw_l": S(0, 420, 0.22), "tw_r": S(0, 420, 0.22),
            "tail_drag": S(0, 45, 0.45),
            "eye_scale": S(1, 220, 0.45), "happy": S(0, 160, 0.9),
            # eyes: quick and snappy (like real eye darts); head: slower
            "look_x": S(0, 320, 0.78), "look_y": S(0, 320, 0.78), "ear": S(0, 200, 0.4),
            "converge": S(0, 60, 1.0), "pupil": S(1, 90, 0.7), "lid": S(0, 40, 1.0),
            "expr": S(0, 140, 0.8), "phones": S(0, 22, 0.75), "bob": S(0, 160, 0.5),
            "glow": S(0, 30, 1.0), "think": S(0, 40, 1.0),
            "listen": S(0, 40, 1.0), "sleep": S(0, 9, 1.0),
            "tail_start": S(0.12, 35, 0.8), "tail_curl": S(-0.13, 35, 0.8),
            "tail_sway": S(0.8, 15, 1.0), "tail_side": S(1.05, 25, 0.85),
            "dangle": S(0, 90, 0.55), "alpha": S(1, 120, 1.0),
            "whisk": S(0, 170, 0.16), "yawn": S(0, 32, 1.0), "sway": S(0, 8, 1.0),
            "jig": S(0, 140, 0.16),            # chubby belly wobble
            "paw": S(0, 34, 0.92), "lick": S(0, 50, 1.0),
            "gx": S(0, 48, 0.88), "gy": S(0, 48, 0.88),
            "blep": S(0, 30, 1.0),             # sleepy tongue tip
            "glasses": S(0, 70, 0.6), "pencil": S(0, 70, 0.6), "beret": S(0, 70, 0.6),
            "knead": S(0, 40, 1.0),            # typing along with you
            "purr": S(0, 30, 1.0), "sparkle": S(0, 60, 1.0),
            "mouth": S(0, 520, 0.8),           # follows his voice (lip-sync)
        }
        self._idle_in = random.uniform(12, 25)
        # affection: stroking his head, boops, tickles, greeting you back
        self.geo: dict = {}                 # where his head / nose / belly are (from the rig)
        self._pet = 0.0                     # 0..1 how petted he feels right now
        self._stroke = 0.0                  # recent stroking over his head, in pixels
        self._pet_last = -99.0
        self._pet_x = 0.0                   # which side of his head your hand is on (-1..1)
        self._hover_pos: QPointF | None = None
        self._heart_in = 0.0
        self._boop_until = -99.0
        self._giggle_until = -99.0
        self._joy_until = -99.0
        self._sparkle_until = -99.0
        self._hop_pending: tuple[float, float] | None = None
        self._night = False
        self._night_check = -99.0
        self._night_yawn_in = random.uniform(70, 160)
        self.voice_level: Callable[[], float | None] = lambda: None
        self.setCursor(Qt.OpenHandCursor)
        # expressions, cursor attention, music
        self.expr = ""
        self._expr_until = 0.0
        self._pending_dizzy = False
        self._clicks: list[float] = []
        self._cursor_prev = QPoint()
        self._cursor_speed = 0.0
        self._cursor_still = 0.0
        self._saccade = (0.0, 0.0)
        self._saccade_in = 1.0
        self.music = False
        self.scanning = False
        self._scan_t0 = 0.0
        self._scan_fade = 0.0
        self._notes: list[_Note] = []
        self._note_in = 0.5
        self._fast = True
        self._stare_dir = 1
        self._stare_len = 12.0
        self._pounce_cool = 0.0
        self._wiggle = 0.0
        self._slow_blink_t = -1.0
        self._other_side = 0.0          # how long your cursor's been behind him
        self._tp = "sparkle"            # current teleport style
        self.away = False               # hidden for do-not-disturb
        self.focus_progress: float | None = None
        self.focus_label = ""
        self._tp_dy = 0.0               # vertical offset while teleporting (portal / zip)
        self._glitch = 0.0              # 0..1 digital glitch while teleporting
        self._action = ""
        self._action_t = 0.0
        self.hop_y = 0.0
        self.hop_v = 0.0
        self.tail_phase = 0.0
        self.gait_phase = 0.0
        self._blink = -1.0
        self._blink_in = random.uniform(1.5, 4)
        self._double_blink = False
        self._twitch_in = random.uniform(3, 8)
        self._glance_in = random.uniform(4, 9)
        self._glance = 0.0
        # falling asleep / asleep
        self._doze_t0 = -99.0           # when he started nodding off
        self._bubble = 0.0              # nose bubble size 0..1
        self._bubble_life = 7.0
        self._pop_t = -99.0
        self._blep_in = random.uniform(8, 14)
        self._blep_until = 0.0
        # what you're doing (app/activity.py): a prop, and paws that type along
        self.prop = ""
        self.typing_rate: Callable[[], float] = lambda: 0.0
        self._knead_phase = 0.0

        # movement
        self.mode = "rest"                 # rest | walk | drag | fly | blink
        self.fx = self.fy = 0.0
        self.vx = self.vy = 0.0
        self._pvx = self._pvy = 0.0
        self.target = QPointF()
        self.running = False
        self._wander_in = random.uniform(8, 16)
        self._blink_t = 0.0
        self._press: QPoint | None = None
        self._grab = QPointF()
        self._drag_clock = QElapsedTimer()
        self._sparks: list[_Spark] = []

        self._clock = QElapsedTimer()
        self._clock.start()
        self._frame = QTimer(self)
        self._frame.setTimerType(Qt.PreciseTimer)
        self._frame.timeout.connect(self._tick)
        self._frame.start(16)

        self._place_default()

    # ================================================================ api
    @property
    def facing(self) -> int:
        return -1 if self.sp["yaw"].value < -6 else 1

    @property
    def airborne(self) -> bool:
        return self.fy < self._floor() - 2

    def head_anchor(self) -> QPoint:
        """Global position just above his head: where the HUD pins itself."""
        return self.mapToGlobal(self.head_top.toPoint())

    def set_state(self, state: str) -> None:
        if state == self.state:
            return
        prev, self.state = self.state, state
        if state == "sleeping":
            self._doze_t0 = self.t              # nod off, don't just shut
            self._bubble = 0.0
        if state == "happy":
            self.hop(140)
        elif state == "listening":
            self.sp["ear"].kick(6)
            self.sp["squash"].kick(0.9)
            self.sp["head_yaw"].kick(-90 * self.facing)
        elif state == "talking" and prev != "talking":
            self.sp["squash"].kick(0.6)
        elif prev == "sleeping":
            self.sp["squash"].kick(1.2)

    def react(self, expr: str, seconds: float = 2.0) -> None:
        """Show a special expression for a while: surprised, dizzy, love, annoyed."""
        self.expr = expr
        self._expr_until = self.t + seconds
        if expr == "surprised":
            self.sp["ear"].kick(8)
            self.sp["squash"].kick(1.0)
        elif expr == "love":
            self.sp["squash"].kick(0.8)
        elif expr == "annoyed":
            self.sp["ear"].kick(-6)
        elif expr == "dizzy":
            self.sp["tilt"].kick(-120)

    def set_music(self, playing: bool) -> None:
        """Headphones on while Spotify / YouTube is playing."""
        if playing != self.music:
            self.music = playing
            self.sp["squash"].kick(0.9 if playing else -0.6)

    def set_scale(self, scale: float) -> None:
        """Resize him live (Settings → Size), keeping his paws where they are."""
        scale = max(0.6, min(1.6, scale))
        foot = self.pos() + self.base.toPoint()
        self.k = 0.82 * scale
        self.setFixedSize(int(250 * scale), int(190 * scale))
        self.base = QPointF(self.width() / 2, self.height() - 14 * scale)
        self.fx = foot.x() - self.base.x()
        self.fy = foot.y() - self.base.y()
        self.move(int(self.fx), int(self.fy))
        self.sp["squash"].kick(0.8)
        self.update()
        self.moved.emit()

    def set_prop(self, prop: str) -> None:
        """glasses | pencil | beret | '' (what you're doing decides)."""
        if prop != self.prop:
            self.prop = prop
            if prop:
                self.sp["squash"].kick(0.5)
                self.sp["ear"].kick(4)

    def set_scanning(self, on: bool) -> None:
        """JARVIS mode: glowing eyes sweep the screen, radar rings at his feet."""
        if on and not self.scanning:
            self._scan_t0 = self.t
            if self.state == "sleeping":
                self.set_state("idle")
            self.sp["ear"].kick(6)
            self.sp["squash"].kick(0.8)
        self.scanning = on

    def hop(self, strength: float = 150) -> None:
        """Anticipation first: he crouches for a few frames, then springs up."""
        if self.hop_y <= 0.5 and self._hop_pending is None:
            self._hop_pending = (strength, self.t + 0.085)
            self.sp["squash"].kick(-1.5)
            self.sp["ear"].kick(-3)

    def go_to(self, x: float, y: float, run: bool | None = None) -> None:
        """He doesn't walk any more: wherever he goes, he teleports."""
        self.blink_to(x, y)

    TELEPORTS = ("sparkle", "portal", "poof", "glitch", "zip")

    def blink_to(self, x: float, y: float, style: str = "") -> None:
        """Teleport, in one of several styles (picked at random). Works
        across monitors: the spot is kept on whichever screen it's on."""
        scr = (QGuiApplication.screenAt(QPoint(int(x + self.width() / 2), int(y + self.base.y())))
               or QGuiApplication.screenAt(QPoint(int(x), int(y))))
        geo = scr.availableGeometry() if scr else self._screen_geo()
        floor = geo.bottom() - self.base.y() + 3
        self.target = QPointF(min(max(x, geo.left()), geo.right() - self.width()),
                              min(max(y, geo.top() + 40), floor))
        self.mode = "blink"
        self._blink_t = 0.0
        self._tp = style or random.choice([s_ for s_ in self.TELEPORTS if s_ != self._tp])
        self._tp_fx(start=True)

    def _tp_fx(self, start: bool) -> None:
        """Particles for leaving (start) and arriving."""
        if self._tp == "sparkle":
            self._burst(28)
        elif self._tp == "poof":
            self._burst(16, kind="puff")
        elif self._tp == "glitch":
            self._burst(22, kind="pixel")
        elif self._tp == "portal":
            self._burst(18, ring=True)
        elif self._tp == "zip" and not start:
            self._burst(10, kind="puff")

    # ============================================================ geometry
    def _screen_geo(self):
        scr = QGuiApplication.screenAt(self.geometry().center()) or self.screen() \
            or QGuiApplication.primaryScreen()
        return scr.availableGeometry()

    def set_away(self, away: bool) -> None:
        """Do-not-disturb: fade out and hide (fullscreen game / video), or come back."""
        if away == self.away:
            return
        self.away = away
        if not away:
            self.show()
            self.sp["squash"].kick(1.2)

    def set_focus(self, progress: float | None, label: str = "") -> None:
        """Focus-session ring on the floor under him (None = off)."""
        self.focus_progress, self.focus_label = progress, label

    def _paint_focus(self, p: QPainter) -> None:
        if self.focus_progress is None:
            return
        cx, cy = self.base.x(), self.base.y() - 3
        rx, ry = 44 * self.k / 0.82, 7 * self.k / 0.82
        rect = QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry)
        p.save()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_accent(50), 2.0))
        p.drawEllipse(rect)
        p.setPen(QPen(_accent(220), 2.4, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 90 * 16, -int(360 * 16 * self.focus_progress))
        if self.focus_label:
            f = QFont()
            f.setPixelSize(int(11 * self.k / 0.82))
            f.setBold(True)
            p.setFont(f)
            p.setPen(_accent(230))
            p.drawText(QRectF(cx + rx + 4, cy - 14, 60, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       self.focus_label)
        p.restore()

    def _floor(self) -> float:
        """Window y at which he stands on the taskbar."""
        return self._screen_geo().bottom() - self.base.y() + 3

    def _place_default(self) -> None:
        geo = self._screen_geo()
        self.fx = geo.right() - self.width() - 30
        self.fy = self._floor()
        self.move(int(self.fx), int(self.fy))

    # =============================================================== tick
    # how fast a spring must be moving to count as visible motion (units/s)
    MOTION = {"yaw": 6, "head_yaw": 6, "pitch": 4, "squash": 0.25, "lean": 3, "tilt": 3,
              "hlag_x": 6, "hlag_y": 6, "tw_l": 12, "tw_r": 12, "ear": 8, "tail_drag": 0.15,
              "eye_scale": 0.25, "happy": 0.3, "look_x": 0.4, "look_y": 0.4, "expr": 0.3,
              "bob": 3, "whisk": 15, "jig": 4, "tail_sway": 0.12, "tail_side": 0.12,
              "dangle": 4, "paw": 0.3, "lick": 0.3, "sleep": 0.08, "blep": 0.3, "glasses": 0.3,
              "pencil": 0.3, "beret": 0.3, "knead": 0.2, "phones": 0.3, "gx": 0.3, "gy": 0.3}

    def _moving(self) -> bool:
        """Is anything on screen visibly moving right now? (Then: 60 fps.)"""
        sp = self.sp
        return any(abs(sp[name].vel) > thr for name, thr in self.MOTION.items() if name in sp)

    def _tick(self) -> None:
        raw = min(self._clock.restart() / 1000.0, 1 / 20)
        if raw <= 0:
            return
        # Windows' timer ticks unevenly (15/16/17 ms). Easing dt toward the
        # real elapsed time keeps motion evenly spaced frame to frame, while
        # a big gap (frame rate change, busy PC) is taken in immediately.
        prev = getattr(self, "_dt", raw)
        dt = raw if abs(raw - prev) > 0.012 else prev + (raw - prev) * 0.35
        self._dt = dt
        self.t += dt
        self._move(dt)
        self._follow_through()
        self._set_targets(dt)
        self._idle_actions(dt)                 # these adjust targets, so they run
        self._attention(dt)                    # before the springs step
        self._affection(dt)
        self._drift()
        if self.scanning:
            self._scan_look()
        for s in self.sp.values():
            s.step(dt)
        self.sp["hlag_x"].clamp(-9, 9, 220)
        self.sp["hlag_y"].clamp(-7, 7, 220)
        self.sp["tw_l"].clamp(-28, 28, 900)
        self.sp["tw_r"].clamp(-28, 28, 900)
        self.sp["whisk"].clamp(-22, 22, 600)
        self.sp["jig"].clamp(-1.0, 1.0, 40)
        self._hop_step(dt)
        self._blinks(dt)
        self._slow_blinks(dt)
        self._twitches(dt)
        self._asleep(dt)
        self._type_along(dt)
        if self.expr and self.t > self._expr_until:
            self.expr = ""
        # save power: 30 fps when nothing much is moving
        self._scan_fade = max(0.0, min(1.0, self._scan_fade + (dt * 3 if self.scanning else -dt * 2)))
        self._pace()
        self._particles(dt)
        if self.away and self.sp["alpha"].value < 0.03 and self.isVisible():
            self.hide()                      # fully faded: get out of the way
        speed = {"thinking": 5.0, "happy": 7.0, "sleeping": 1.0,
                 "listening": 3.0}.get(self.state, 2.2) + 3 * self.sp["gait"].value
        self.tail_phase += dt * speed
        self.update()

    def _pace(self) -> None:
        """Frame rate by need. 60 fps only while he is really moving (dragged,
        flying, walking, hopping, being petted, a fast cursor to follow); 40
        while he talks, reacts or blinks; 25 while he just sits and breathes;
        15 asleep; 4 while hidden. Physics uses real elapsed time, so nothing
        changes speed, it only gets fewer (identical-looking) in-between frames."""
        fast = (self.mode != "rest" or self.hop_y > 0.5 or self._hop_pending is not None
                or self._cursor_speed > 400 or self._moving() or self._pet > 0.02)
        lively = (self._sparks or self._notes or self._scan_fade > 0 or self._action
                  or self.expr or self.state in ("talking", "happy", "listening", "thinking")
                  or self._blink >= 0 or self.sp["knead"].value > 0.05
                  or self.sp["mouth"].value > 0.03)
        if self.away:
            ms = 250
        elif fast:
            ms = 16
        elif lively:
            ms = 25
        elif self.state == "sleeping" and self.t - self._doze_t0 > 4:
            ms = 66
        else:
            ms = 40
        if ms != self._frame.interval():
            self._frame.setInterval(ms)

    def _follow_through(self) -> None:
        dvx, dvy = self.vx - self._pvx, self.vy - self._pvy
        self._pvx, self._pvy = self.vx, self.vy
        sp = self.sp
        sp["hlag_x"].kick(-dvx * 0.5 / self.k)
        sp["hlag_y"].kick(-dvy * 0.3 / self.k)
        flop = -dvx * 0.9 - sp["yaw"].vel * 0.35 - sp["head_yaw"].vel * 0.25
        sp["tw_l"].kick(flop)
        sp["tw_r"].kick(flop)
        sp["tail_drag"].kick(-abs(dvx) * 0.004)
        sp["whisk"].kick(-dvx * 0.35 + dvy * 0.15 - sp["head_yaw"].vel * 0.4)
        sp["jig"].kick((abs(dvx) + abs(dvy)) * 0.012 + sp["squash"].vel * 0.35)

    def _set_targets(self, dt: float) -> None:
        st, sp, t = self.state, self.sp, self.t
        sleeping = st == "sleeping"
        dangle = self.mode == "drag"
        thrown = self.mode == "fly"
        walking = self.mode == "walk"
        speed = math.hypot(self.vx, self.vy)
        air = self.airborne

        sp["air"].target = 1.0 if air and not dangle else 0.0
        sp["hover"].target = (1.5 + 1.0 * math.sin(t * 1.6)) if (air and self.mode == "rest") else 0.0
        sp["sleep"].target = 1.0 if sleeping else 0.0
        special = self.expr in ("love", "dizzy", "surprised", "annoyed")
        sp["happy"].target = 1.0 if st == "happy" and not special else 0.0
        sp["expr"].target = 1.0 if self.expr else 0.0
        vibing = self.music and st == "idle" and self.mode == "rest" and not special
        sp["phones"].target = 1.0 if self.music else 0.0
        sp["lid"].target = 0.32 if vibing else 0.0
        sp["dangle"].target = 1.0 if dangle else 0.0
        sp["gait"].target = min(1.0, speed / 35) if walking else 0.0
        sp["run"].target = 1.0 if (walking and self.running and speed > 120) else 0.0
        sp["stand"].target = 1.0 if (walking or thrown) else 0.0
        sp["eye_scale"].target = (1.13 if self.expr == "surprised" else
                                  1.1 if (st == "listening" or dangle or thrown) else 1.0)
        sp["glow"].target = 1.0 if (st in ("thinking", "listening") or self.mode == "blink"
                                    or self.scanning) else 0.0
        sp["think"].target = 1.0 if st == "thinking" else 0.0
        sp["listen"].target = 1.0 if st == "listening" else 0.0
        sp["ear"].target = (1.0 if st in ("listening", "happy") else
                            0.5 if dangle else -0.7 if (sleeping or self.running) else 0.0)
        sp["squash"].target = 0.07 if dangle else (-0.06 if sleeping else 0.0)
        sp["tail_drag"].target = -min(0.45, speed * 0.002) if walking else 0.0
        sp["alpha"].target = 0.0 if self.away else 1.0

        lx, ly = self._cursor_look()
        if walking or thrown:
            d = 1 if self.vx > 0 else -1 if self.vx < 0 else self.rest_dir
            if abs(self.vx) > 8:
                self.rest_dir = d
            yaw, head = 74.0 * self.rest_dir, 0.0
            climb = math.degrees(math.atan2(-self.vy, abs(self.vx) + 30))
            sp["pitch"].target = max(-22.0, min(22.0, climb)) if walking else 0.0
        else:
            sp["pitch"].target = 0.0
            if dangle:
                yaw, head = max(-55.0, min(55.0, self.vx * 0.05)), 0.0
            elif sleeping:
                yaw, head = 22.0 * self.rest_dir, 0.0
            elif st == "thinking":
                yaw, head = 18.0 * self.rest_dir, 16.0 * self.rest_dir
            elif st in ("listening", "talking"):
                yaw, head = 10.0 * self.rest_dir + lx * 8, lx * 14 - 10 * self.rest_dir
            else:
                self._glance_in -= dt
                if self._glance_in <= 0:
                    self._glance_in = random.uniform(5, 12)
                    self._glance = random.uniform(-22, 22) if random.random() < 0.4 else 0.0
                if lx * self.rest_dir < -0.55 and self._cursor_dist < 700:
                    self._other_side += dt
                    if self._other_side > 1.2:          # turns around to face you
                        self.rest_dir = -self.rest_dir
                        self._other_side = 0.0
                        self.sp["squash"].kick(-0.5)
                        self.sp["tail_sway"].kick(4)
                else:
                    self._other_side = 0.0
                # how interested he is in the cursor: close + recently moved = a lot;
                # far away or parked for a while = he settles back facing you
                near = max(0.0, min(1.0, (650 - self._cursor_dist) / 450))
                fresh = max(0.0, min(1.0, (6.0 - self._cursor_still) / 4.0))
                eng = 0.25 + 0.75 * max(near * 0.6 + fresh * 0.4, 0.0)
                yaw = 12.0 * self.rest_dir + lx * 12 * eng
                head = lx * 18 * eng + self._glance * (1 - abs(lx)) * 0.7
        sp["yaw"].target = yaw
        sp["head_yaw"].target = head

        if dangle:
            tail = (0.95, -0.03, 0.6, 1.05)
        elif sleeping:
            tail = (0.05, -0.03, 0.25, 1.25)
        elif walking and self.running:
            tail = (-0.22, -0.025, 1.5, 0.1)          # streams out behind him
        elif walking:
            tail = (-0.5, -0.07, 1.1, 0.18)           # up and trailing
        elif thrown:
            tail = (-0.9, -0.2, 2.2, 0.6)             # whipping around
        elif st == "happy":
            tail = (-0.35, -0.17, 1.6, 1.0)
        elif air:
            tail = (0.25, -0.16, 1.0, 1.1)            # lazy curl while hovering
        else:
            tail = (0.12, -0.13, 1.4 if st == "thinking" else 0.8, 1.05)
        if not (walking or thrown) and abs(tail[3]) > 0.5:
            tail = (*tail[:3], tail[3] * -self.rest_dir)
        (sp["tail_start"].target, sp["tail_curl"].target,
         sp["tail_sway"].target, sp["tail_side"].target) = tail

        if st == "thinking":
            tilt = 7
        elif st == "listening":
            tilt = -6
        elif sleeping:
            tilt = 9
        elif walking:
            step = math.sin(4 * math.pi * self.gait_phase)
            tilt = -sp["pitch"].value * 0.4 + 2.2 * step * (1 - sp["run"].value)
            sp["bob"].target = 1.8 * abs(math.sin(2 * math.pi * self.gait_phase)) * sp["gait"].value
            if (self.gait_phase * 2) % 1 < 0.08:      # each footfall flicks his ears
                sp["tw_l"].kick(14)
                sp["tw_r"].kick(-10)
        else:
            tilt = 2.5 * math.sin(t * 0.37) + 1.5 * math.sin(t * 0.91)
        if vibing:                                   # nod along to the music
            beat = math.sin(t * math.pi * 1.9)
            tilt += 5.5 * math.sin(t * math.pi * 0.95)
            sp["bob"].target = 2.6 * abs(beat)
        elif not walking:
            sp["bob"].target = 0.0
        if self.expr == "dizzy":
            tilt += 9 * math.sin(t * 6)
        sp["tilt"].target = tilt

        if dangle:
            sp["lean"].target = max(-30, min(30, self.vx * 0.03))
        elif thrown:
            sp["lean"].target = max(-40, min(40, self.vx * 0.04 + self.vy * 0.02 * self.rest_dir))
        else:
            # a slow, soft sway while he sits: never perfectly still
            sp["lean"].target = (1.4 * math.sin(t * 0.7) + 0.6 * math.sin(t * 1.9)
                                 if self.mode == "rest" and not sleeping else 0.0)

        if st == "thinking":
            ex, ey = 0.75 * self.rest_dir, -1.0
        elif sleeping:
            ex, ey = 0.0, 0.3
        elif walking:
            ex, ey = 0.0, max(-1.0, min(1.0, self.vy / 120))
        else:
            # eyes do most of the work (relative to where the head points),
            # plus tiny saccades when your cursor is resting
            turned = (sp["yaw"].value + sp["head_yaw"].value) / 70
            ex = max(-1.0, min(1.0, lx * 1.25 - turned)) + self._saccade[0]
            ey = max(-1.0, min(1.0, ly * 1.1)) + self._saccade[1]
        sp["look_x"].target, sp["look_y"].target = ex, ey
        near = self._cursor_dist < 150 and not (walking or sleeping)
        sp["converge"].target = max(0.0, 1 - self._cursor_dist / 150) if near else 0.0
        sp["pupil"].target = (0.62 if self.expr == "surprised" else
                              1.25 if self.expr == "love" else
                              1.18 if near or self._cursor_speed > 1800 else 1.0)

    _cursor_dist = 999.0

    def _cursor_look(self) -> tuple[float, float]:
        """Direction to the cursor from between his eyes, with a smooth
        falloff: a cursor right on him gives a small look, far away a full one."""
        c = QCursor.pos()
        eyes = self.mapToGlobal(QPoint(int(self.head_top.x()),
                                       int(self.head_top.y() + 46 * self.k)))
        dx, dy = c.x() - eyes.x(), c.y() - eyes.y()
        d = math.hypot(dx, dy)
        self._cursor_dist = d
        if d < 1:
            return 0.0, 0.0
        reach = d / (d + 70)
        return dx / d * reach, dy / d * reach

    def _attention(self, dt: float) -> None:
        """How he reacts to your cursor: perks up at fast moves, gets curious
        when it rests near him, makes little eye darts when it's still."""
        c = QCursor.pos()
        moved = (c - self._cursor_prev).manhattanLength()
        self._cursor_prev = c
        self._cursor_speed = 0.8 * self._cursor_speed + 0.2 * (moved / max(dt, 1e-3))
        idle = self.state == "idle" and self.mode == "rest"
        if moved < 2:
            self._cursor_still += dt
        else:
            if self._cursor_still > 300 and idle and not self.away:
                self._welcome_back()                  # you were gone a while: happy to see you
            elif self._cursor_still > 2 and idle and self._cursor_dist < 400:
                self.sp["ear"].kick(5)                # "oh, you're back"
            self._cursor_still = 0.0
            self._saccade = (0.0, 0.0)
        if self._cursor_still > 1.0 and idle:
            self._saccade_in -= dt
            if self._saccade_in <= 0:
                self._saccade_in = random.uniform(0.5, 1.6)
                self._saccade = (random.uniform(-0.18, 0.18), random.uniform(-0.12, 0.12))
        if idle and self._cursor_dist < 110 and self._cursor_still > 1.4 and not self.expr:
            self.sp["tilt"].target += 9 * self.rest_dir      # curious head tilt
            self.sp["tw_l" if self.rest_dir > 0 else "tw_r"].target = 10
        else:
            self.sp["tw_l"].target = self.sp["tw_r"].target = 0.0

    # ---- affection ----------------------------------------------------
    def _welcome_back(self) -> None:
        self._joy_until = self.t + 0.9
        self._sparkle_until = self.t + 2.4
        self.hop(150)
        self._hearts(2)

    def _hearts(self, n: int = 1) -> None:
        top = self.mapToGlobal(QPoint(int(self.head_top.x()), int(self.head_top.y() + 30 * self.k)))
        for _ in range(n):
            side = random.choice((-1, 1)) * random.uniform(16, 46) * self.k
            self._sparks.append(_Spark(top.x() + side, top.y() + random.uniform(-4, 10) * self.k,
                                       random.uniform(-6, 6), random.uniform(-30, -20),
                                       self.t, random.uniform(1.1, 1.6),
                                       random.uniform(4.8, 6.6) * self.k / 0.82, "heart"))

    def _is_night(self) -> bool:
        if self.t - self._night_check > 60:
            self._night_check = self.t
            import datetime
            hour = datetime.datetime.now().hour
            self._night = hour >= 23 or hour < 5
        return self._night

    def _affection(self, dt: float) -> None:
        """Stroke his head and he purrs, leans into your hand and squeezes his
        eyes shut; hearts drift up. Also: boops, tickles, his voice moving his
        mouth, sleepy eyes late at night. Runs after the mood targets are set."""
        sp, t = self.sp, self.t
        # --- petting: you have to keep stroking, like a real cat
        self._stroke = max(0.0, self._stroke - 110 * dt)
        calm = self.mode == "rest" and self.expr not in ("annoyed", "dizzy")
        stroking = calm and self._stroke > 70 and t - self._pet_last < 0.7
        self._pet = (min(1.0, self._pet + dt * 1.5) if stroking
                     else max(0.0, self._pet - dt * 0.55))
        k = self._pet
        sp["purr"].target = k
        if k > 0.12:
            sp["happy"].target = max(sp["happy"].target, min(1.0, (k - 0.12) * 1.7))
            sp["tilt"].target += 10 * k * self._pet_x          # leans into your hand
            sp["lean"].target += 3.5 * k * self._pet_x
            sp["ear"].target = min(sp["ear"].target, 0.2) - 0.35 * k
            sp["tail_sway"].target = max(sp["tail_sway"].target, 1.3)
            self._heart_in -= dt
            if k > 0.55 and self._heart_in <= 0:
                self._heart_in = random.uniform(0.5, 0.95)
                self._hearts(1)
        # --- boop: eyes cross onto his nose for a beat
        if t < self._boop_until:
            sp["converge"].target = 1.0
            sp["look_x"].target, sp["look_y"].target = 0.0, 0.55
            sp["eye_scale"].target = 1.1
        # --- tickle / happy to see you: squeezed-shut smile, then sparkly eyes
        if t < self._giggle_until:
            sp["happy"].target = 1.0
            sp["tilt"].target += 7 * math.sin(t * 26)
        if t < self._joy_until:
            sp["happy"].target = 1.0
        sp["sparkle"].target = (1.0 if (t < self._sparkle_until or 0.1 < k < 0.6
                                        or self.expr == "love") else 0.0)
        # --- late at night he's drowsy: heavy lids, the odd yawn
        if self._is_night() and self.state == "idle" and self.mode == "rest" and k < 0.1:
            sp["lid"].target = max(sp["lid"].target, 0.24)
            self._night_yawn_in -= dt
            if self._night_yawn_in <= 0 and not self._action:
                self._night_yawn_in = random.uniform(90, 200)
                self._action, self._action_t = "yawn", 0.0
        # --- mouth: follows the real loudness of his voice when it's known,
        # otherwise a syllable-like chatter while he's "talking"
        level = None
        try:
            level = self.voice_level()
        except Exception:
            pass
        if level is not None:
            sp["mouth"].target = min(1.0, level * 1.15)
        elif self.state == "talking":
            sp["mouth"].target = (abs(math.sin(t * 8.5))
                                  * (0.5 + 0.5 * math.sin(t * 2.7 + 1.3) ** 2))
        else:
            sp["mouth"].target = 0.0

    def _hop_step(self, dt: float) -> None:
        if self._hop_pending is not None and self.t >= self._hop_pending[1]:
            self.hop_v, self._hop_pending = self._hop_pending[0], None
            self.sp["squash"].kick(2.1)               # ...the stretch as he leaves the ground
            self.sp["hlag_y"].kick(30)
        if self.hop_y > 0 or self.hop_v > 0:
            self.hop_v -= HOP_GRAVITY * dt
            self.hop_y += self.hop_v * dt
            if self.hop_y <= 0:
                self.sp["squash"].kick(-abs(self.hop_v) * 0.013)
                self.sp["jig"].kick(abs(self.hop_v) * 0.03)
                self.sp["hlag_y"].kick(abs(self.hop_v) * 0.25)
                self.hop_y = self.hop_v = 0.0

    def _slow_blink_amount(self) -> float:
        """A cat's 'I love you' blink: eyelids drift down, rest, drift up.
        A fixed eased curve (not a spring), so it can never stall half-shut."""
        t = self._slow_blink_t
        if t < 0:
            return 0.0
        def ease(x):
            x = max(0.0, min(1.0, x))
            return x * x * (3 - 2 * x)
        if t < 0.45:
            return ease(t / 0.45)
        if t < 0.95:
            return 1.0
        return 1.0 - ease((t - 0.95) / 0.55)

    def _slow_blinks(self, dt: float) -> None:
        if self._slow_blink_t >= 0:
            self._slow_blink_t += dt
            if self._slow_blink_t > 1.5 or self.state not in ("idle", "happy"):
                self._slow_blink_t = -1.0
        elif (self.state == "idle" and self.mode == "rest" and self._cursor_dist < 160
              and 2.5 < self._cursor_still < 2.5 + 1 / 30):
            self._slow_blink_t = 0.0                    # an affectionate slow blink

    # ---- sleeping ---------------------------------------------------
    # nodding off: (seconds since he got sleepy, how shut his eyes are)
    DOZE = ((0.0, 0.0), (0.8, 0.38), (1.3, 0.18), (2.1, 0.42), (2.6, 0.26), (3.1, 0.4), (3.7, 1.0))

    def _doze_amount(self) -> float:
        """How shut his eyes are while falling asleep: heavy lids, a sleepy
        fight to stay awake, then he drifts off. Eased keyframes, not a spring."""
        u = self.t - self._doze_t0
        keys = self.DOZE
        if u >= keys[-1][0]:
            return 1.0
        for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
            if u <= t1:
                x = (u - t0) / (t1 - t0)
                x = x * x * (3 - 2 * x)
                return v0 + (v1 - v0) * x
        return 1.0

    def _asleep(self, dt: float) -> None:
        """Nose bubble that swells with each breath and pops, a little tongue
        blep now and then, and a dreamy ear flick."""
        sp = self.sp
        deep = self.state == "sleeping" and self.t - self._doze_t0 > 3.6
        sp["blep"].target = 0.0
        if not deep:
            self._bubble = max(0.0, self._bubble - dt * 3)
            return
        self._bubble += dt / self._bubble_life
        if self._bubble >= 1.0:                      # pop!
            self._bubble = 0.0
            self._pop_t = self.t
            self._bubble_life = random.uniform(6.5, 11.0)
            sp["tw_l" if random.random() < 0.5 else "tw_r"].kick(110)
            sp["squash"].kick(0.35)
        self._blep_in -= dt
        if self._blep_in <= 0:
            self._blep_until = self.t + random.uniform(2.0, 3.5)
            self._blep_in = random.uniform(14, 26)
        if self.t < self._blep_until:
            sp["blep"].target = 1.0

    def _type_along(self, dt: float) -> None:
        """When you type, his front paws tap along at your pace (sitting, awake,
        not busy with anything else). Props follow what you're doing."""
        sp = self.sp
        for name in ("glasses", "pencil", "beret"):
            sp[name].target = 1.0 if self.prop == name and not self.music else 0.0
        rate = 0.0
        if self.state in ("idle", "thinking") and self.mode == "rest" and not self._action:
            try:
                rate = self.typing_rate()
            except Exception:
                rate = 0.0
        sp["knead"].target = min(1.0, rate / 3) if rate >= 1.0 else 0.0
        # taps follow your speed: ~1 paw press per 2 keys, capped so it stays cute
        self._knead_phase += dt * math.pi * min(9.0, max(2.5, rate * 0.9))

    def _blinks(self, dt: float) -> None:
        if self._blink >= 0:
            self._blink += dt / 0.16
            if self._blink >= 1:
                self._blink = -1
                if self._double_blink:
                    self._double_blink = False
                    self._blink_in = 0.12
        else:
            self._blink_in -= dt
            if self._blink_in <= 0 and self.state not in ("sleeping", "happy"):
                self._blink = 0.0
                self._blink_in = random.uniform(2.2, 6.0)
                self._double_blink = random.random() < 0.18

    def _drift(self) -> None:
        """Tiny always-on motion so he never freezes: ears swivel a little
        on their own, like they're listening to the room."""
        if self.state == "sleeping" or self._action in ("stare", "look_around"):
            return
        t = self.t
        self.sp["tw_l"].target += 3.0 * math.sin(t * 0.83) + 1.5 * math.sin(t * 2.1)
        self.sp["tw_r"].target += 3.0 * math.sin(t * 0.71 + 2) + 1.5 * math.sin(t * 1.7 + 1)

    def _twitches(self, dt: float) -> None:
        self._twitch_in -= dt
        if self._twitch_in <= 0:
            self._twitch_in = random.uniform(4, 11)
            if self.state in ("idle", "talking"):
                key = random.choice(("tw_l", "tw_r"))
                self.sp[key].kick(random.choice((-1, 1)) * 140)
                self.sp["whisk"].kick(random.choice((-1, 1)) * 90)
            elif self.state == "sleeping" and random.random() < 0.6:
                self.sp[random.choice(("tw_l", "tw_r"))].kick(random.choice((-1, 1)) * 70)

    def _idle_actions(self, dt: float) -> None:
        """Little things a cat does when nothing's happening."""
        sp = self.sp
        # these only stay up while an action holds them
        sp["paw"].target = sp["lick"].target = sp["yawn"].target = 0.0
        sp["gx"].target = sp["gy"].target = 0.0
        if self.state == "talking":
            sp["whisk"].kick(math.sin(self.t * 17) * 25 * dt * 60 * 0.05)
        if self._action:
            self._action_t += dt
            a, t = self._action, self._action_t
            if a == "yawn":
                sp["yawn"].target = 1.0 if t < 1.5 else 0.0
                sp["ear"].target = -0.5 if t < 1.5 else 0.0
                sp["tilt"].target = -8 if t < 1.5 else sp["tilt"].target
                done = t > 2.2
            elif a == "stretch":
                sp["squash"].target = -0.12 if t < 0.6 else 0.09 if t < 1.1 else 0.0
                sp["tilt"].target = -6 if 0.6 <= t < 1.1 else sp["tilt"].target
                done = t > 1.6
            elif a == "stare":
                # turns three-quarters and watches something off-screen for a
                # few seconds; your cursor moving brings him straight back
                sp["yaw"].target = 52 * self._stare_dir
                sp["head_yaw"].target = 12 * self._stare_dir + 6 * math.sin(t * 0.5)
                sp["look_x"].target, sp["look_y"].target = 0.35 * self._stare_dir, -0.2
                sp["ear"].target = 0.55
                sp["tw_l"].target = sp["tw_r"].target = 4 * math.sin(t * 1.3)
                done = (t > self._stare_len or self._cursor_dist < 120
                        or (t > 1.0 and self._cursor_speed > 400))
            elif a == "look_around":
                # glances one way, the other, then back at you
                k = (-1, 1, 0)[min(2, int(t / 0.9))] * self._stare_dir
                sp["head_yaw"].target = 30 * k
                sp["look_x"].target = 0.7 * k
                sp["ear"].target = 0.35
                (sp["tw_l"] if k < 0 else sp["tw_r"]).target = 9
                done = t > 2.9
            elif a == "tail_chase":
                # notices his own tail swishing and watches it
                back = -self.rest_dir
                sp["head_yaw"].target = 32 * back
                sp["look_x"].target, sp["look_y"].target = 0.9 * back, 0.5
                sp["tail_sway"].target = 2.6
                sp["ear"].target = 0.8
                sp["pupil"].target = 1.25
                done = t > 2.6
            elif a == "sniff":
                # leans toward your cursor, nose twitching
                sp["lean"].target = 6 * self.rest_dir
                sp["hlag_x"].target = 4 * self.rest_dir
                if int(t * 8) % 2 == 0:
                    sp["whisk"].kick(4)
                done = t > 1.6
            elif a == "groom":
                # 1) raises a paw, 2) licks it in little strokes (head dips,
                # tongue flicks), 3) washes his face: paw sweeps over cheek and
                # ear, head leaning into it, 4) a satisfied shake
                up = 0.3 < t < 4.4
                sp["paw"].target = 1.0 if up else 0.0
                sp["happy"].target = 1.0 if up else sp["happy"].target
                if 0.6 < t < 2.4:                        # licking: smooth strokes
                    stroke = 0.5 - 0.5 * math.cos((t - 0.6) * 2 * math.pi * 1.9)
                    sp["lick"].target = 1.0 if stroke > 0.35 else 0.0
                    sp["hlag_y"].target = 3.0 * stroke    # head dips to the paw
                    sp["gx"].target, sp["gy"].target = -5.0, 2.5 * stroke - 1.0
                    sp["tilt"].target = -6 - 2 * stroke
                elif 2.4 <= t < 4.4:                     # face wash
                    u = (t - 2.4) * 2 * math.pi * 1.1
                    sp["gx"].target = 5 + 5 * math.cos(u)
                    sp["gy"].target = -7 - 7 * max(0.0, math.sin(u))
                    sp["tilt"].target = -14 + 4 * math.sin(u)
                    sp["tw_l"].target = sp["tw_r"].target = -12
                    sp["ear"].target = -0.3
                elif t >= 4.4:                           # little shake
                    sp["tilt"].target = 7 * math.sin((t - 4.4) * 30) * max(0.0, 1 - (t - 4.4) * 2)
                done = t > 5.0
            elif a == "pounce":
                # wiggle bum, then leap at your cursor
                if t < 0.9:
                    sp["squash"].target = -0.16
                    sp["lean"].target = 3 * math.sin(t * 28)
                    sp["ear"].target = 0.9
                    sp["pupil"].target = 1.3
                    sp["tail_sway"].target = 2.4
                    done = False
                else:
                    c = QCursor.pos()
                    tx = c.x() - self.width() / 2
                    self.hop(250)
                    self.sp["jig"].kick(6)
                    if abs(tx - self.fx) > 120:
                        self.blink_to(tx, self.fy)
                    self._pounce_cool = 25.0
                    self._action = ""
                    return
            else:                                       # tail flick
                if t < dt * 1.5:
                    sp["tail_sway"].kick(9)
                    sp["tail_drag"].kick(-3)
                done = t > 0.8
            if done or self.state != "idle" or self.mode != "rest":
                sp["yawn"].target = 0.0
                sp["paw"].target = 0.0
                sp["lick"].target = 0.0
                self._action = ""
            return
        if self.state != "idle" or self.mode != "rest":
            return
        # pounce if you wiggle the cursor close to him
        self._pounce_cool = max(0.0, self._pounce_cool - dt)
        if self._cursor_dist < 230 and self._cursor_speed > 1500:
            self._wiggle += dt
        else:
            self._wiggle = max(0.0, self._wiggle - dt * 2)
        if self._wiggle > 0.6 and self._pounce_cool <= 0 and not self.airborne:
            self._wiggle = 0.0
            self._action, self._action_t = "pounce", 0.0
            return
        self._idle_in -= dt
        if self._idle_in <= 0:
            self._idle_in = random.uniform(5, 12)
            near = self._cursor_dist < 260
            pool = ["look_around", "look_around", "groom", "groom", "tail_chase", "yawn",
                    "stretch", "flick", "flick", "stare"]
            if near:
                pool += ["sniff", "sniff"]
            self._action = random.choice(pool)
            self._action_t = 0.0
            self._stare_dir = random.choice((-1, 1))
            self._stare_len = random.uniform(3, 7)

    # ============================================================ movement
    def _move(self, dt: float) -> None:
        if self.mode == "rest":
            self._maybe_wander(dt)
            return
        if self.mode == "drag":
            return
        geo = self._screen_geo()
        floor = self._floor()
        left, right = geo.left(), geo.right() - self.width()
        top = geo.top() + 30

        if self.mode == "fly":
            # flung: a quick, snappy slide that stops fast (no floaty drift,
            # no slow fall). He stays wherever he ends up.
            drag = math.exp(-9.0 * dt)
            self.vx *= drag
            self.vy *= drag
            self.fx += self.vx * dt
            self.fy += self.vy * dt
            if self.fx < left or self.fx > right:
                self.fx = min(max(self.fx, left), right)
                self.vx *= -0.4
            self.fy = min(max(self.fy, top), floor)
            if math.hypot(self.vx, self.vy) < 40:
                self.vx = self.vy = 0.0
                self.sp["squash"].kick(-1.2)            # little "plop"
                if self._pending_dizzy:                 # flung hard: seeing stars
                    self._pending_dizzy = False
                    self.react("dizzy", 2.4)
                self._settle_rest()
        elif self.mode == "blink":
            self._blink_t += dt
            T = {"portal": 0.42, "zip": 0.3, "glitch": 0.34}.get(self._tp, BLINK_TIME)
            if self._blink_t < T:                           # leaving
                self._tp_frame(self._blink_t / T, leaving=True)
            elif self._blink_t - dt < T <= self._blink_t:   # jump to the new spot
                self.fx, self.fy = self.target.x(), self.target.y()
                self.move(int(round(self.fx)), int(round(self.fy)))
                self._tp_fx(start=False)
            else:                                           # arriving
                g = min(1.0, (self._blink_t - T) / T)
                self._tp_frame(g, leaving=False)
                if g >= 1:
                    self._tp_dy = self._glitch = 0.0
                    self.sp["alpha"].value = 1.0
                    self.sp["squash"].kick(1.4)
                    self.sp["jig"].kick(5)
                    self._settle_rest()
        self.move(int(round(self.fx)), int(round(self.fy)))
        self.moved.emit()

    def _tp_frame(self, u: float, leaving: bool) -> None:
        """One frame of the current teleport style. u goes 0 -> 1."""
        a = self.sp["alpha"]
        sq = self.sp["squash"]
        v = u if leaving else 1 - u                 # how far "gone" he is
        if self._tp == "sparkle":
            a.value, sq.value = 1 - v, -0.25 * v
        elif self._tp == "poof":                   # squash flat, vanish in a puff
            a.value = 1 - min(1.0, v * 1.4)
            sq.value = -0.45 * v
        elif self._tp == "portal":                 # sinks into a lime ring, rises out
            a.value = 1.0 if v < 0.85 else (1 - v) / 0.15
            sq.value = 0.18 * math.sin(math.pi * v)
            self._tp_dy = 75 * self.k * v * v
        elif self._tp == "glitch":                 # digital flicker + slice
            self._glitch = v
            a.value = (1 - v) * (0.55 + 0.45 * random.random())
        elif self._tp == "zip":                    # shoots up, drops back in
            a.value = 1.0 if leaving else min(1.0, 0.3 + u * 2)
            sq.value = 0.55 * v
            self._tp_dy = -260 * self.k * v * v

    def _settle_rest(self) -> None:
        self.mode = "rest"
        self._wander_in = random.uniform(45, 120)     # teleports now and then, not constantly

    def _maybe_wander(self, dt: float) -> None:
        if self.state != "idle" or not self.can_wander() or self._pet > 0.05:
            return
        self._wander_in -= dt
        if self._wander_in > 0:
            return
        screens = QGuiApplication.screens()
        if len(screens) > 1 and random.random() < 0.3:      # visit another monitor
            geo = random.choice(screens).availableGeometry()
        else:
            geo = self._screen_geo()
        floor = geo.bottom() - self.base.y() + 3
        x = random.uniform(geo.left(), geo.right() - self.width())
        # anywhere on screen, leaving room above for the HUD; often the taskbar
        y = floor if random.random() < 0.35 else random.uniform(geo.top() + 170, floor)
        if math.hypot(x - self.fx, y - self.fy) > 120:
            self.blink_to(x, y)
        else:
            self._settle_rest()

    # =========================================================== particles
    def _burst(self, n: int, ring: bool = False, kind: str = "spark") -> None:
        c = self.mapToGlobal(QPoint(int(self.base.x()), int(self.base.y() - 40 * self.k)))
        for _ in range(n):
            a = random.uniform(0, 2 * math.pi)
            if kind == "puff":                  # soft smoke balls that drift up
                sp = random.uniform(25, 70)
                self._sparks.append(_Spark(c.x() + math.cos(a) * 18, c.y() + 18 + math.sin(a) * 10,
                                           math.cos(a) * sp, math.sin(a) * sp * 0.5 - 25,
                                           self.t, random.uniform(0.5, 0.9),
                                           random.uniform(7, 13) * self.k / 0.82, "puff"))
            elif kind == "pixel":               # digital squares
                sp = random.uniform(60, 200)
                self._sparks.append(_Spark(c.x(), c.y(), math.cos(a) * sp, math.sin(a) * sp * 0.6,
                                           self.t, random.uniform(0.25, 0.55),
                                           random.uniform(1.6, 3.4), "pixel"))
            else:
                sp = random.uniform(40, 170) if not ring else 110
                self._sparks.append(_Spark(c.x(), c.y() + (36 * self.k if ring else 0),
                                           math.cos(a) * sp, math.sin(a) * sp * (0.35 if ring else 1),
                                           self.t, random.uniform(0.35, 0.8),
                                           random.uniform(1.2, 2.6)))

    def _particles(self, dt: float) -> None:
        for s in self._sparks:
            s.x += s.vx * dt
            s.y += s.vy * dt
            if s.kind == "heart":                     # hearts just float up, unhurried
                continue
            s.vx *= math.exp(-3 * dt)
            s.vy = s.vy * math.exp(-3 * dt) - 20 * dt
        self._sparks = [s for s in self._sparks if self.t - s.born < s.life]
        if self.music and self.state == "idle" and self.mode == "rest":
            self._note_in -= dt
            if self._note_in <= 0:
                self._note_in = random.uniform(0.7, 1.4)
                c = self.mapToGlobal(QPoint(int(self.head_top.x() + random.choice((-1, 1)) * 34 * self.k),
                                            int(self.head_top.y() + 40 * self.k)))
                self._notes.append(_Note(c.x(), c.y(), self.t, random.choice("♪♫♪"),
                                         random.uniform(-14, 14)))
        self._notes = [n for n in self._notes if self.t - n.born < 2.2]

    # =============================================================== mouse
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._grab = e.position()
            self._drag_clock.start()
        elif e.button() == Qt.RightButton:
            self.menu_requested.emit(self.head_anchor())

    def _dist(self, pos: QPointF, key: str) -> float:
        pt = self.geo.get(key)
        return math.hypot(pos.x() - pt.x(), pos.y() - pt.y()) if pt is not None else 1e9

    def _spot(self, pos: QPointF) -> str:
        """What you touched: 'nose', 'belly', 'head' or ''."""
        r = self.geo.get("head_r", 40 * self.k)
        if self._dist(pos, "nose") < 0.2 * r:
            return "nose"
        if self._dist(pos, "head") < 1.12 * r:
            return "head"
        if self._dist(pos, "belly") < 0.62 * r:
            return "belly"
        return ""

    def _hover_move(self, pos: QPointF) -> None:
        """No button held: moving your cursor back and forth over his head pets him."""
        prev, self._hover_pos = self._hover_pos, QPointF(pos)
        if prev is None or self._spot(pos) not in ("head", "nose"):
            return
        self._stroke = min(260.0, self._stroke + min(40.0, math.hypot(pos.x() - prev.x(),
                                                                       pos.y() - prev.y())))
        self._pet_last = self.t
        head, r = self.geo.get("head"), self.geo.get("head_r", 40 * self.k)
        if head is not None:
            self._pet_x = max(-1.0, min(1.0, (pos.x() - head.x()) / r))

    def leaveEvent(self, e) -> None:
        self._hover_pos = None
        super().leaveEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            self._hover_move(e.position())
            return
        g = e.globalPosition()
        if self.mode != "drag":
            if (g.toPoint() - self._press).manhattanLength() < 5:
                return
            self.mode = "drag"
            self.setCursor(Qt.ClosedHandCursor)
            self._pet = self._stroke = 0.0
            self.vx = self.vy = 0.0
            self.sp["squash"].kick(1.4)
            self.react("surprised", 0.9)
        dt = max(self._drag_clock.restart() / 1000.0, 1 / 240)
        nx, ny = g.x() - self._grab.x(), g.y() - self._grab.y()
        self.vx = 0.7 * self.vx + 0.3 * (nx - self.fx) / dt
        self.vy = 0.7 * self.vy + 0.3 * (ny - self.fy) / dt
        self.fx, self.fy = nx, ny
        self.move(int(nx), int(ny))
        self.moved.emit()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        if self.mode == "drag":
            self.setCursor(Qt.OpenHandCursor)
            geo = self._screen_geo()
            self.fy = min(max(self.fy, geo.top() + 30), self._floor())
            self.fx = min(max(self.fx, geo.left()), geo.right() - self.width())
            self.move(int(self.fx), int(self.fy))
            fast = self._drag_clock.elapsed() < 80 and math.hypot(self.vx, self.vy) > 900
            if fast:                                   # flicked: short slide
                self._pending_dizzy = math.hypot(self.vx, self.vy) > 1300
                self.vx = max(-1600.0, min(1600.0, self.vx))
                self.vy = max(-1600.0, min(1600.0, self.vy))
                self.mode = "fly"
            else:                                      # dropped: stays right there
                self.vx = self.vy = 0.0
                self.sp["squash"].kick(-1.4)
                self._settle_rest()
            self.moved.emit()
        else:
            self._petted(self._spot(e.position()))
            self.clicked.emit()
        self._press = None

    def _petted(self, spot: str = "head") -> None:
        """Clicks: a boop on the nose, a tickle on the belly, pats on the head.
        A few in a row: heart eyes. Way too many: grumpy."""
        now = self.t
        self._clicks = [c for c in self._clicks if now - c < 4.0] + [now]
        n = len(self._clicks)
        if n >= 7:
            self.react("annoyed", 2.6)
            self._clicks.clear()
            return
        if self.expr == "annoyed" or self.mode != "rest":
            return
        if spot == "nose":                            # boop!
            self._boop_until = now + 0.75
            self._sparkle_until = now + 2.4           # ...then sparkly eyes
            self.sp["squash"].kick(-1.1)
            self.sp["hlag_y"].kick(22)
            self.sp["whisk"].kick(random.choice((-1, 1)) * 260)
            self.sp["tw_l"].kick(160)
            self.sp["tw_r"].kick(-160)
        elif spot == "belly":                         # tickle
            self._giggle_until = now + 0.9
            self.sp["jig"].kick(30)
            self.sp["squash"].kick(1.0)
            self.hop(70)
        elif n >= 3:
            self.react("love", 2.4)
            self._hearts(2)
        else:                                         # a pat: a happy little squish
            self.sp["squash"].kick(-0.9)
            self.sp["ear"].kick(-4)
            self._stroke = min(260.0, self._stroke + 60)
            self._pet_last = now

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit()

    # =============================================================== paint
    def pose(self) -> Pose:
        sp, t = self.sp, self.t
        blink = math.sin(math.pi * self._blink) if self._blink >= 0 else 0.0
        chatter = max(0.0, min(1.0, sp["mouth"].value))
        sleep = sp["sleep"].value
        breath = math.sin(t * (1.3 if sleep > 0.5 else 2.4)) * (1 + sleep)
        # eyes: the nodding-off curve while asleep, the spring when waking up
        shut = self._doze_amount() if self.state == "sleeping" else max(0.0, min(1.0, sleep))
        pop = max(0.0, 1 - (t - self._pop_t) / 0.35)
        bubble = self._bubble * (0.82 + 0.18 * breath / 2) if self._bubble > 0 else 0.0
        gait = max(0.0, min(1.0, sp["gait"].value))
        return Pose(
            # always facing you: never more than a three-quarter turn
            t=t, yaw=max(-MAX_YAW, min(MAX_YAW, sp["yaw"].value)),
            head_yaw=max(-MAX_HEAD, min(MAX_HEAD, sp["head_yaw"].value)),
            pitch=sp["pitch"].value,
            stand=max(0.0, min(1.0, sp["stand"].value)), gait=gait,
            gait_phase=self.gait_phase, run=max(0.0, min(1.0, sp["run"].value)),
            squash=sp["squash"].value + 0.018 * breath * (1 - gait),
            lean=sp["lean"].value,
            lift=self.hop_y + max(0.0, sp["hover"].value),
            head_tilt=sp["tilt"].value, head_dx=sp["hlag_x"].value,
            head_dy=(sp["hlag_y"].value - 1.8 * chatter + sp["bob"].value
                     + (3.0 * shut * (1 - sleep) if self.state == "sleeping" else 0.0)),
            breath=breath,
            eye_open=max(0.0, (1 - blink) * (1 - shut) * (1 - 0.15 * chatter)
                         * (1 - self._slow_blink_amount())),
            eye_scale=sp["eye_scale"].value, happy=max(0.0, sp["happy"].value),
            look_x=sp["look_x"].value, look_y=sp["look_y"].value, ear=sp["ear"].value,
            twitch_l=sp["tw_l"].value, twitch_r=sp["tw_r"].value,
            glow=max(0.0, sp["glow"].value) * (0.8 + 0.2 * math.sin(t * 3)),
            think=max(0.0, sp["think"].value), listen=max(0.0, sp["listen"].value),
            sleep=max(0.0, min(1.0, sleep)),
            tail_start=sp["tail_start"].value, tail_curl=sp["tail_curl"].value,
            tail_sway=sp["tail_sway"].value, tail_phase=self.tail_phase,
            tail_side=sp["tail_side"].value, tail_drag=sp["tail_drag"].value,
            dangle=max(0.0, sp["dangle"].value),
            mouth=chatter,
            jiggle=sp["jig"].value,
            paw_raise=max(0.0, min(1.0, sp["paw"].value)),
            lick=max(0.0, min(1.0, sp["lick"].value)),
            blep=max(0.0, min(1.0, sp["blep"].value)),
            knead=max(0.0, min(1.0, sp["knead"].value)), knead_phase=self._knead_phase,
            glasses=max(0.0, min(1.0, sp["glasses"].value)),
            pencil=max(0.0, min(1.0, sp["pencil"].value)),
            beret=max(0.0, min(1.0, sp["beret"].value)),
            bubble=bubble, pop=pop,
            groom_x=sp["gx"].value, groom_y=sp["gy"].value,
            yawn=max(0.0, min(1.0, sp["yawn"].value)),
            whisker=sp["whisk"].value,
            converge=max(0.0, min(1.0, sp["converge"].value)),
            pupil=max(0.5, sp["pupil"].value),
            sparkle=max(0.0, min(1.0, sp["sparkle"].value)),
            purr=max(0.0, min(1.0, sp["purr"].value)),
            lid=max(0.0, min(0.9, sp["lid"].value)),
            expr=self.expr or ("vibe" if self.music else ""),
            expr_amt=max(0.0, min(1.0, sp["expr"].value)),
            headphones=max(0.0, min(1.0, sp["phones"].value)),
            airborne=max(0.0, min(1.0, sp["air"].value)),
        )

    def _scan_look(self) -> None:
        """Eyes and head sweep side to side like a scanner beam."""
        t = self.t - self._scan_t0
        sp = self.sp
        sweep = math.sin(t * 2.4)
        sp["look_x"].target = 0.95 * sweep
        sp["look_y"].target = -0.1 + 0.25 * math.sin(t * 1.3)
        sp["head_yaw"].target = 16 * sweep
        sp["pupil"].target = 0.72
        sp["ear"].target = 0.8

    def _paint_scan(self, p: QPainter, head_top: QPointF | None) -> None:
        a = self._scan_fade
        if a <= 0:
            return
        t = self.t - self._scan_t0
        cx, gy = self.base.x(), self.base.y()
        p.save()
        p.setBrush(Qt.NoBrush)
        # radar ripples on the floor
        for i in range(3):
            ph = (t * 0.7 + i / 3) % 1.0
            col = QColor(EYE)
            col.setAlphaF(a * 0.55 * (1 - ph) ** 1.4)
            p.setPen(QPen(col, 1.4))
            rx = (24 + 92 * ph) * self.k / 0.82
            p.drawEllipse(QPointF(cx, gy), rx, rx * 0.26)
        # spinning HUD brackets around his head
        if head_top is not None:
            hc = QPointF(head_top.x(), head_top.y() + 36 * self.k / 0.82)
            r = 40 * self.k / 0.82
            box = QRectF(hc.x() - r, hc.y() - r, 2 * r, 2 * r)
            col = QColor(EYE)
            col.setAlphaF(a * 0.75)
            p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap))
            spin = int(t * 140) % 360
            for k in range(3):
                p.drawArc(box, (spin + k * 120) * 16, 46 * 16)
            col.setAlphaF(a * 0.35)
            p.setPen(QPen(col, 1.0, Qt.DotLine))
            r2 = r + 7
            p.drawArc(QRectF(hc.x() - r2, hc.y() - r2, 2 * r2, 2 * r2), (-spin * 2) * 16, 200 * 16)
        p.restore()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_focus(p)
        alpha = max(0.0, min(1.0, self.sp["alpha"].value))
        teleporting = self.mode == "blink"
        if teleporting and self._tp == "portal":
            self._paint_portal(p)
        if alpha > 0.995 and not (teleporting and (self._tp_dy or self._glitch)):
            _paws, top = draw_cat(p, self.pose(), self.base, self.k, self.geo)
        else:   # render once to an image: fades without seams, can be sliced/clipped
            dpr = self.devicePixelRatioF()
            img = QImage(self.size() * dpr, QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.transparent)
            ip = QPainter(img)
            _paws, top = draw_cat(ip, self.pose(), self.base, self.k, self.geo)
            ip.end()
            p.save()
            if self._tp_dy > 0:                       # sinking into the portal
                p.setClipRect(QRectF(0, 0, self.width(), self.base.y() + 2))
            p.translate(0, self._tp_dy)
            p.setOpacity(alpha)
            if self._glitch > 0.01:                   # horizontal slices jump sideways
                h, y = self.height(), 0
                while y < h:
                    band = random.randint(4, 11)
                    dx = random.uniform(-1, 1) * 16 * self._glitch
                    p.drawImage(QRectF(dx, y, self.width(), band), img,
                                QRectF(0, y * dpr, self.width() * dpr, band * dpr))
                    y += band
                p.setOpacity(alpha * 0.6 * self._glitch)
                p.setPen(QPen(EYE, 1))
                for _ in range(4):
                    yy = random.uniform(top.y(), self.base.y())
                    p.drawLine(QPointF(self.base.x() - 60 * self.k, yy),
                               QPointF(self.base.x() + 60 * self.k, yy))
            else:
                p.drawImage(0, 0, img)
            p.restore()
        self.head_top = top
        self._paint_scan(p, top)
        self._paint_sparks(p)
        self._paint_notes(p)

    def _paint_portal(self, p: QPainter) -> None:
        T = 0.42
        u = self._blink_t / T
        open_ = math.sin(math.pi * min(1.0, u if u < 1 else u - 1))   # opens, closes; twice
        if open_ <= 0.01:
            return
        cx, cy = self.base.x(), self.base.y()
        rx = 52 * self.k * open_
        g = QRadialGradient(QPointF(cx, cy), rx)
        g.setColorAt(0, QColor(10, 14, 6, 230))
        g.setColorAt(0.75, QColor(20, 30, 8, 200))
        g.setColorAt(1, _accent(0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), rx, rx * 0.28)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_accent(int(220 * open_)), 2.2))
        p.drawEllipse(QPointF(cx, cy), rx, rx * 0.28)

    def _paint_notes(self, p: QPainter) -> None:
        if not self._notes:
            return
        origin = self.mapToGlobal(QPoint(0, 0))
        f = QFont()
        f.setFamilies(["Segoe UI Symbol", "DejaVu Sans"])
        f.setPixelSize(int(15 * self.k / 0.82))
        p.setFont(f)
        for n in self._notes:
            age = (self.t - n.born) / 2.2
            a = min(1.0, age * 6) * (1 - age)
            x = n.x - origin.x() + n.drift * age + 4 * math.sin(age * 9)
            y = n.y - origin.y() - 55 * age
            p.setPen(_accent(int(230 * a)))
            p.drawText(QPointF(x, y), n.glyph)

    def _paint_sparks(self, p: QPainter) -> None:
        if not self._sparks:
            return
        origin = self.mapToGlobal(QPoint(0, 0))
        p.setPen(Qt.NoPen)
        for s in self._sparks:
            a = max(0.0, 1 - (self.t - s.born) / s.life)
            x, y = s.x - origin.x(), s.y - origin.y()
            if s.kind == "heart":                    # drifts up with a little sway, then fades
                age = self.t - s.born
                grow = min(1.0, age / 0.18)
                col = QColor(255, 122, 158, int(235 * min(1.0, a * 1.6)))
                p.setBrush(col)
                p.drawPath(_heart(x + 5 * math.sin(age * 4 + s.born), y, s.size * grow))
            elif s.kind == "puff":
                p.setBrush(QColor(210, 212, 222, int(150 * a * a)))
                r = s.size * (1.5 - 0.5 * a)
                p.drawEllipse(QPointF(x, y), r, r)
            elif s.kind == "pixel":
                col = QColor(EYE) if int(s.born * 1000) % 3 else QColor(120, 230, 255)
                col.setAlphaF(a)
                p.setBrush(col)
                p.drawRect(QRectF(x - s.size, y - s.size, 2 * s.size, 2 * s.size))
            else:
                col = QColor(EYE)
                col.setAlphaF(a)
                p.setBrush(col)
                r = s.size * (0.6 + 0.4 * a)
                p.drawEllipse(QPointF(x, y), r, r)
