"""Windows Precision Touchpad behaviour, as a pure state machine.

The engine knows nothing about evdev or uinput. It is fed one frame per
SYN_REPORT (touch positions in millimetres, physical button state, a
timestamp in milliseconds) and returns a list of output actions:

    ("move", dx_px, dy_px)
    ("scroll", vertical_units, horizontal_units)   # 120 units == 1 wheel notch
    ("button", "left"|"right"|"middle", pressed)
    ("combo", "meta+w")                            # press and release
    ("keydown", "alt") / ("keyup", "alt")
    ("zoom", +1 | -1)                              # ctrl+wheel step

The default values are measured from how a Windows 10 precision touchpad
behaves, so the feel matches Windows out of the box. Every value can be
changed in winptp.toml.
"""

import math
from dataclasses import dataclass

# Windows default pointer curve (the SmoothMouseXCurve / SmoothMouseYCurve values).
WIN_CURVE_X = [0.0, 0.43, 1.25, 3.86, 40.0]
WIN_CURVE_Y = [0.0, 1.07, 4.14, 18.98, 443.75]

# Palm check while typing, delays per sensitivity level:
# (tap suppressed ms, cursor move suppressed ms)
AAP_LEVELS = {0: (0, 0), 1: (500, 250), 2: (750, 500), 3: (1250, 750),
              4: (1250, 750)}


class Accelerator:
    """Windows-style ballistic pointer acceleration (4-segment curve)."""

    def __init__(self, cursor_speed=10, dpi=120.0):
        scale_y = max(dpi, 96.0) / 120.0 * cursor_speed / 10.0
        xs = [x * 3.5 for x in WIN_CURVE_X]
        ys = [y * scale_y for y in WIN_CURVE_Y]
        self.xs = xs
        self.slope = [(ys[i] - ys[i - 1]) / (xs[i] - xs[i - 1]) for i in range(1, 5)]
        self.icept = [ys[i - 1] - self.slope[i - 1] * xs[i - 1] for i in range(1, 5)]
        self.prev_seg = 0

    def gain(self, dx, dy):
        ax, ay = abs(dx), abs(dy)
        speed = max(ax, ay) + min(ax, ay) / 2.0
        if speed == 0.0:
            return 0.0
        i = 1
        while i < 4 and speed > self.xs[i]:
            i += 1
        seg = i - 1
        g = self.slope[seg] + self.icept[seg] / speed
        if seg > self.prev_seg:
            p = self.prev_seg
            g = (g + self.slope[p] + self.icept[p] / speed) / 2.0
        self.prev_seg = seg
        return max(g, 0.0)


@dataclass
class Config:
    width_mm: float = 102.0
    height_mm: float = 67.0
    # pointer
    cursor_speed: int = 9              # Windows CursorSpeed 1-20
    screen_dpi: float = 120.0          # Windows display DPI (96 x scale)
    mickeys_per_mm: float = 320 / 25.4 # engine units fed to the curve (tuned)
    smoothing_frames: int = 3          # average deltas (stable-contact filtering)
    # taps
    tap_to_click: bool = True          # TapsEnabled
    two_finger_tap: bool = True        # TwoFingerTapEnabled
    tap_and_drag: bool = True          # TapAndDrag
    tap_max_ms: float = 300.0          # tap timeout
    tap_max_move_mm: float = 3.0       # movement allowed during a tap
    tap_drag_window_ms: float = 250.0  # Windows uses 500; shorter avoids accidental drags
    tap_drag_max_mm: float = 8.0       # second touch must land near the tap
    two_finger_tap_max_mm: float = 50.0  # max finger spread for a two-finger tap
    tap_three: str = "middle"          # Windows option: middle mouse button
    tap_four: str = "meta+alt+n"       # Windows: Action Center -> notifications
    # clicks
    right_click_zone: bool = True      # RightClickZoneEnabled
    button_zone_mm: float = 12.0       # resting/click strip height
    resting_release_mm: float = 10.0   # resting contact radius
    click_warp_mm: float = 2.7         # click warp-back radius
    # scrolling
    natural_scroll: bool = True        # reverse ("natural") scroll direction
    scroll_mm_per_notch: tuple = (10.0, 8.0, 4.0)   # by speed tier
    scroll_speed_tiers: tuple = (100.0, 300.0)      # tier boundaries (mm/s)
    pan_max_separation_mm: float = 40.0  # two-finger pan: max finger spread
    pan_cos: float = 0.776             # cos of max direction difference (~39 deg)
    scroll_rail_ratio: float = 2.0
    inertia: bool = True
    inertia_tau_ms: float = 325.0      # tuned by hand
    inertia_min_speed: float = 0.15    # wheel units per ms
    # pinch
    pinch_zoom: bool = True            # ZoomEnabled
    pinch_native: bool = True          # real touchpad pinch (smooth zoom in apps)
    pinch_mm_per_step: float = 10.0    # finger travel per zoom step
    # 3/4-finger gestures
    gesture_start_mm: float = 5.0      # swipe start threshold
    gesture4_vertical_mm: float = 2.5  # 4-finger up/down start threshold
    alt_tab_step_mm: float = 10.0
    swipe3_up: str = "meta+w"          # Task view -> Overview
    swipe3_down: str = "meta+d"        # Show desktop
    swipe3_horizontal: str = "alttab"  # app switcher, like Windows
    swipe4_up: str = "meta+w"
    swipe4_down: str = "meta+d"
    swipe4_left: str = "meta+ctrl+right"   # content follows fingers: left = next
    swipe4_right: str = "meta+ctrl+left"
    close_overview: str = "esc"        # 3/4-finger down while Task view is open
    # rejection
    aap_threshold: int = 1             # AAPThreshold; Windows default 2, 1 = shorter
    typing_tap_block_ms: float = -1.0  # custom palm check delays; -1 = use aap_threshold
    typing_move_block_ms: float = -1.0
    edge_reject_mm: float = 2.0


@dataclass
class Touch:
    x: float
    y: float
    palm: bool = False
    x0: float = 0.0
    y0: float = 0.0
    t0: float = 0.0
    max_move: float = 0.0
    resting: bool = False


@dataclass
class Session:
    """One contact session: from the first finger down to the last lift."""
    t0: float
    max_fingers: int = 0
    moved: bool = False
    clicked: bool = False
    tap_blocked: bool = False
    move_blocked: bool = False
    max_spread: float = 0.0
    pos: tuple = (0.0, 0.0)


class Engine:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.accel = Accelerator(self.cfg.cursor_speed, self.cfg.screen_dpi)
        self.touches = {}
        self.session = None
        self.mode = "idle"
        self.last_t = None
        self.button_down = False
        self.pressed_button = None
        self.press_pos = None
        self.last_key = -1e9
        self.out = []
        # pointer: motion is held back until the contact is clearly not a
        # tap (Windows moves, then warps back; holding back is equivalent)
        self.pending = [0.0, 0.0]
        self.history = []
        # scroll
        self.scroll_axis = None
        self.scroll_v = (0.0, 0.0)
        self.inertia_v = None
        self.inertia_t = 0.0
        self.two_start = {}
        # pinch
        self.pinch_ref = 0.0
        # swipe
        self.swipe_acc = [0.0, 0.0]
        self.swipe_done = False
        self.alt_tab = False
        self.alt_tab_ref = 0.0
        # tap-and-drag
        self.drag_until = None
        self.drag_candidate = False
        self.dragging = False
        self.prev = {}
        self.prev_count = 0
        self.history = []
        self.overview_open = False
        self.tap_pos = (0.0, 0.0)

    # ------------------------------------------------------------------ input

    def key_activity(self, t):
        """A non-modifier key was pressed on a keyboard."""
        self.last_key = t

    def frame(self, t, touches, button):
        """touches: {slot: (x_mm, y_mm, palm)}; button: physical click."""
        self.out = []
        dt = 8.0 if self.last_t is None else max(1.0, min(50.0, t - self.last_t))
        self.last_t = t
        self._update_touches(t, touches)
        fingers = self._fingers()
        n = len(fingers)

        if n and self.inertia_v is not None:
            self.inertia_v = None          # a touch stops kinetic scrolling

        if n and self.session is None:
            self._begin_session(t)
        if self.session:
            self.session.max_fingers = max(self.session.max_fingers, n)
            if n >= 2:
                self.session.max_spread = max(self.session.max_spread, self._spread())

        self._handle_button(button)

        if n != self.prev_count:
            self._count_changed(t, n)
        elif n:
            self._motion(t, dt, fingers)

        self.prev = {s: (tc.x, tc.y) for s, tc in self.touches.items()}
        self.prev_count = n
        if n == 0 and not self.touches and self.session is not None:
            self._end_session(t)
        return self.out

    def tick(self, t):
        """Run timers (tap-drag window, inertia). Returns actions."""
        self.out = []
        if self.drag_until is not None and t >= self.drag_until and \
                not self.drag_candidate:
            self.drag_until = None
        if self.inertia_v is not None:
            dt = max(1.0, t - self.inertia_t)
            self.inertia_t = t
            decay = math.exp(-dt / self.cfg.inertia_tau_ms)
            v, h = self.inertia_v
            v, h = v * decay, h * decay
            if math.hypot(v, h) < 0.02:
                self.inertia_v = None
            else:
                self.inertia_v = (v, h)
                self.out.append(("scroll", v * dt, h * dt))
        return self.out

    def next_timeout(self, t):
        """Milliseconds until tick() needs to run, or None."""
        if self.inertia_v is not None:
            return 8.0
        if self.drag_until is not None:
            return max(0.0, self.drag_until - t)
        return None

    # --------------------------------------------------------------- helpers

    def _update_touches(self, t, touches):
        cfg = self.cfg
        for slot in list(self.touches):
            if slot not in touches:
                del self.touches[slot]
        for slot, (x, y, palm) in touches.items():
            tc = self.touches.get(slot)
            if tc is None:
                edge = x < cfg.edge_reject_mm or x > cfg.width_mm - cfg.edge_reject_mm
                # AAP curtain: a contact landing right after typing is ignored
                curtain = t - self.last_key < self._aap()[1] \
                    and self.session is None
                tc = Touch(x=x, y=y, palm=palm or edge or curtain, x0=x, y0=y, t0=t,
                           resting=y > cfg.height_mm - cfg.button_zone_mm)
                self.touches[slot] = tc
            else:
                tc.x, tc.y = x, y
                tc.palm = tc.palm or palm
                tc.max_move = max(tc.max_move, math.hypot(x - tc.x0, y - tc.y0))
                if tc.resting and tc.max_move > cfg.resting_release_mm:
                    tc.resting = False

    def _aap(self):
        """(tap, move) suppression after typing, in ms."""
        cfg = self.cfg
        tap_ms, move_ms = AAP_LEVELS[cfg.aap_threshold]
        if cfg.typing_tap_block_ms >= 0:
            tap_ms = cfg.typing_tap_block_ms
        if cfg.typing_move_block_ms >= 0:
            move_ms = cfg.typing_move_block_ms
        return tap_ms, move_ms

    def _fingers(self):
        """Contacts that count as fingers (no palms, no resting thumbs)."""
        live = {s: t for s, t in self.touches.items() if not t.palm}
        if len(live) > 1 or self.button_down:
            moving = {s: t for s, t in live.items() if not t.resting}
            if moving:
                return moving
        return live

    def _begin_session(self, t):
        cfg = self.cfg
        tap_ms, move_ms = self._aap()
        f = next(iter(self._fingers().values()), None)
        self.session = Session(t0=t, tap_blocked=t - self.last_key < tap_ms,
                               move_blocked=t - self.last_key < move_ms,
                               pos=(f.x, f.y) if f else (0.0, 0.0))
        self.pending = [0.0, 0.0]
        self.history = []
        if self.drag_until is not None and t < self.drag_until and cfg.tap_and_drag:
            f = next(iter(self._fingers().values()), None)
            if f and math.hypot(f.x - self.tap_pos[0], f.y - self.tap_pos[1]) \
                    <= cfg.tap_drag_max_mm:
                self.drag_candidate = True

    def _end_session(self, t):
        cfg = self.cfg
        s = self.session
        self.session = None
        self._finish_mode(t)
        if self.dragging:
            self.dragging = False
            self.drag_candidate = False
            self.drag_until = None
            self.out.append(("button", "left", False))
            return
        self.drag_candidate = False
        is_tap = (cfg.tap_to_click and not s.clicked and not s.tap_blocked
                  and not s.moved and t - s.t0 <= cfg.tap_max_ms
                  and s.max_fingers > 0)
        if is_tap:
            self._tap(t, s)

    def _tap(self, t, s):
        cfg = self.cfg
        self.overview_open = False
        n = s.max_fingers
        if n == 1:
            self._click("left")
            self.drag_until = t + cfg.tap_drag_window_ms
            self.tap_pos = s.pos
        elif n == 2:
            if cfg.two_finger_tap and s.max_spread <= cfg.two_finger_tap_max_mm:
                self._click("right")
        elif n == 3:
            self._action(cfg.tap_three)
        else:
            self._action(cfg.tap_four)

    def _click(self, name):
        self.out.append(("button", name, True))
        self.out.append(("button", name, False))

    def _action(self, action):
        if action in ("left", "right", "middle"):
            self._click(action)
        elif action and action != "none":
            self.out.append(("combo", action))

    def _handle_button(self, button):
        if button and not self.button_down:
            self.button_down = True
            self.overview_open = False
            if self.session:
                self.session.clicked = True
            self.pressed_button = self._which_button()
            live = [tc for tc in self.touches.values() if not tc.palm]
            self.press_pos = {id(tc): (tc.x, tc.y) for tc in live}
            self.pending = [0.0, 0.0]
            self.history = []
            self.out.append(("button", self.pressed_button, True))
        elif not button and self.button_down:
            self.button_down = False
            self.press_pos = None
            self.out.append(("button", self.pressed_button, False))
            self.pressed_button = None

    def _which_button(self):
        cfg = self.cfg
        live = [t for t in self.touches.values() if not t.palm]
        moving = [t for t in live if not t.resting]
        if len(moving) == 2 or (len(live) == 2 and not moving):
            return "right"
        if len(moving) >= 3:
            return "middle"
        if cfg.right_click_zone and live:
            # IsRCZClick: current and touch-down point both in the zone
            presser = max(live, key=lambda tc: tc.y)
            zone_y = cfg.height_mm - cfg.button_zone_mm
            if presser.y > zone_y and presser.y0 > zone_y and \
                    presser.x > cfg.width_mm / 2 and presser.x0 > cfg.width_mm / 2:
                return "right"
        return "left"

    def _count_changed(self, t, n):
        self._finish_mode(t)
        self.pending = [0.0, 0.0]
        self.history = []
        if n == 0:
            self.mode = "idle"
        elif self.mode in ("idle", "pointer") and n == 1:
            self.mode = "pointer"
        elif n == 1:
            # fingers lifted out of a multi-finger gesture: no pointer jump
            self.mode = "lockout"
        elif n == 2:
            self.mode = "two"
            self.scroll_axis = None
            self.scroll_v = (0.0, 0.0)
            self.pinch_ref = self._spread()
            self.two_start = {s: (tc.x, tc.y) for s, tc in self._fingers().items()}
        else:
            self.mode = "swipe"
            self.swipe_acc = [0.0, 0.0]
            self.swipe_done = False

    def _finish_mode(self, t):
        if self.mode == "pinch" and self.cfg.pinch_native:
            self.out.append(("pinch_end",))
        if self.mode == "scroll" and self.cfg.inertia:
            v, h = self.scroll_v
            if math.hypot(v, h) >= self.cfg.inertia_min_speed:
                self.inertia_v = (v, h)
                self.inertia_t = t
        if self.alt_tab:
            self.alt_tab = False
            self.out.append(("keyup", "alt"))

    def _deltas(self, fingers):
        ds = []
        for s, tc in fingers.items():
            if s in self.prev:
                px, py = self.prev[s]
                ds.append((tc.x - px, tc.y - py))
        if not ds:
            return 0.0, 0.0
        return (sum(d[0] for d in ds) / len(ds), sum(d[1] for d in ds) / len(ds))

    def _spread(self):
        f = list(self._fingers().values())
        if len(f) < 2:
            return 0.0
        return math.hypot(f[0].x - f[1].x, f[0].y - f[1].y)

    def _motion(self, t, dt, fingers):
        cfg = self.cfg
        dx, dy = self._deltas(fingers)
        s = self.session
        if s and any(tc.max_move > cfg.tap_max_move_mm for tc in fingers.values()):
            s.moved = True

        if self.drag_candidate and not self.dragging and s and s.moved:
            # tap, then touch again within the window and move: drag
            self.dragging = True
            self.drag_candidate = False
            self.drag_until = None
            self.out.append(("button", "left", True))

        if self.mode == "pointer":
            if s and s.move_blocked and not self.dragging:
                # AAP holds the cursor only until the delay after the last
                # key has passed, not for the whole contact
                if t - self.last_key < self._aap()[1]:
                    return
                s.move_blocked = False
            self._pointer(t, dx, dy, fingers)
        elif self.mode in ("two", "scroll", "pinch"):
            self._two_finger(t, dt, dx, dy, fingers)
        elif self.mode == "swipe":
            self._swipe(dx, dy, len(fingers))

    def _pointer(self, t, dx, dy, fingers):
        cfg = self.cfg
        if dx == 0.0 and dy == 0.0:
            return
        # FilterMoveForStableContact: sensor noise makes straight lines wavy,
        # so average the last few deltas before applying the curve
        self.history.append((dx, dy))
        if len(self.history) > max(1, cfg.smoothing_frames):
            self.history.pop(0)
        n = len(self.history)
        dx = sum(h[0] for h in self.history) / n
        dy = sum(h[1] for h in self.history) / n
        mx, my = dx * cfg.mickeys_per_mm, dy * cfg.mickeys_per_mm
        g = self.accel.gain(mx, my)
        ox, oy = mx * g, my * g
        tc = next(iter(fingers.values()))
        if self.button_down and self.press_pos is not None:
            # click warp-back: ignore slide during a physical press until
            # it leaves the warp ellipse
            x0, y0 = self.press_pos.get(id(tc), (tc.x0, tc.y0))
            if math.hypot(tc.x - x0, tc.y - y0) < cfg.click_warp_mm:
                return
        s = self.session
        held = s is not None and not s.moved and not self.dragging and \
            t - tc.t0 <= cfg.tap_max_ms and not self.button_down
        if held:
            self.pending[0] += ox
            self.pending[1] += oy
            return
        if self.pending != [0.0, 0.0]:
            ox += self.pending[0]
            oy += self.pending[1]
            self.pending = [0.0, 0.0]
        self.out.append(("move", ox, oy))

    def _two_finger(self, t, dt, dx, dy, fingers):
        cfg = self.cfg
        if self.mode == "two":
            vecs = [(tc.x - self.two_start[s][0], tc.y - self.two_start[s][1])
                    for s, tc in fingers.items() if s in self.two_start]
            if len(vecs) < 2:
                return
            (ax, ay), (bx, by) = vecs[:2]
            la, lb = math.hypot(ax, ay), math.hypot(bx, by)
            if max(la, lb) < 1.5:      # 1.5 mm "moved at all" ellipse
                return
            spread_change = abs(self._spread() - self.pinch_ref)
            cos = (ax * bx + ay * by) / (la * lb) if la and lb else 1.0
            if cfg.pinch_zoom and (cos <= -cfg.pan_cos or
                                   spread_change > max(la, lb) * 0.8):
                self.mode = "pinch"
            elif cos >= cfg.pan_cos and self._spread() <= cfg.pan_max_separation_mm:
                self.mode = "scroll"
                mx, my = (ax + bx) / 2, (ay + by) / 2
                if abs(my) > abs(mx) * cfg.scroll_rail_ratio:
                    self.scroll_axis = "v"
                elif abs(mx) > abs(my) * cfg.scroll_rail_ratio:
                    self.scroll_axis = "h"
                else:
                    self.scroll_axis = None
                dx, dy = mx, my
            elif la > 6 and lb > 6:
                self.mode = "scroll"   # wide-apart fingers: still a scroll
                dx, dy = (ax + bx) / 2, (ay + by) / 2
            else:
                return
            if self.session:
                self.session.moved = True
        if self.mode == "pinch" and cfg.pinch_native:
            # replay the two contacts on a virtual touchpad so apps get a
            # real pinch gesture (Windows sends DirectManipulation zoom)
            self.out.append(("pinch", {s: (tc.x, tc.y) for s, tc in fingers.items()}))
            return
        if self.mode == "pinch":
            spread = self._spread()
            diff = spread - self.pinch_ref
            while abs(diff) >= cfg.pinch_mm_per_step:
                step = 1 if diff > 0 else -1
                self.out.append(("zoom", step))
                self.pinch_ref += step * cfg.pinch_mm_per_step
                diff = spread - self.pinch_ref
            return
        if self.scroll_axis == "v":
            dx = 0.0
        elif self.scroll_axis == "h":
            dy = 0.0
        speed = math.hypot(dx, dy) / (dt / 1000.0)   # mm/s
        t1, t2 = cfg.scroll_speed_tiers
        mm = cfg.scroll_mm_per_notch
        per_notch = mm[0] if speed < t1 else mm[1] if speed < t2 else mm[2]
        units = 120.0 / per_notch
        sign = 1.0 if cfg.natural_scroll else -1.0
        # wheel "up" is positive: natural scrolling moves content with fingers
        v = sign * dy * units
        h = -sign * dx * units
        if v or h:
            self.out.append(("scroll", v, h))
        a = 0.4
        pv, ph = self.scroll_v
        self.scroll_v = (pv * (1 - a) + a * v / dt, ph * (1 - a) + a * h / dt)

    def _swipe(self, dx, dy, n):
        cfg = self.cfg
        self.swipe_acc[0] += dx
        self.swipe_acc[1] += dy
        if self.alt_tab:
            moved = self.swipe_acc[0] - self.alt_tab_ref
            while abs(moved) >= cfg.alt_tab_step_mm:
                step = 1 if moved > 0 else -1
                self.out.append(("combo", "tab" if step > 0 else "shift+tab"))
                self.alt_tab_ref += step * cfg.alt_tab_step_mm
                moved = self.swipe_acc[0] - self.alt_tab_ref
            return
        if self.swipe_done:
            return
        ax, ay = self.swipe_acc
        vertical = abs(ay) > abs(ax)
        need = cfg.gesture4_vertical_mm if (n >= 4 and vertical) else cfg.gesture_start_mm
        if max(abs(ax), abs(ay)) < need:
            return
        self.swipe_done = True
        if self.session:
            self.session.moved = True
        if vertical:
            direction = "up" if ay < 0 else "down"
        else:
            direction = "left" if ax < 0 else "right"
        if n == 3:
            if direction in ("left", "right"):
                action = cfg.swipe3_horizontal
            else:
                action = getattr(cfg, "swipe3_" + direction)
                if direction == "down" and self.overview_open:
                    action = cfg.close_overview
        else:
            action = getattr(cfg, "swipe4_" + direction)
            if direction == "down" and self.overview_open:
                action = cfg.close_overview
        if action == "alttab":
            self.alt_tab = True
            self.alt_tab_ref = ax
            self.out.append(("keydown", "alt"))
            self.out.append(("combo", "tab" if direction == "right" else "shift+tab"))
        else:
            self.overview_open = direction == "up" and action == cfg.swipe3_up
            self._action(action)
