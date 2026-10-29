"""winptpd: grab the precision touchpad and replay it the Windows way.

Reads the touchpad's multitouch evdev stream, feeds frames to the Engine,
and writes the result to two virtual uinput devices (a pointer and a
keyboard for gesture shortcuts). Keyboards are watched, not grabbed, for
disable-while-typing. Stopping the daemon releases the grab, so the
touchpad falls back to plain libinput immediately.
"""

import argparse
import logging
import os
import select
import sys
import time
import tomllib

import evdev
from evdev import ecodes as e

from .engine import Config, Engine

log = logging.getLogger("winptpd")

POINTER_NAME = "winptp pointer"
PAD_NAME = "winptp touchpad"
KEYBOARD_NAME = "winptp keys"
PASSTHROUGH_PHYS = "winptp/passthrough"
VIRTUAL_VENDOR = 0x1d6b   # Linux Foundation
VIRTUAL_PRODUCT = 0x5054  # "PT"

KEYS = {
    "meta": e.KEY_LEFTMETA, "super": e.KEY_LEFTMETA, "ctrl": e.KEY_LEFTCTRL,
    "alt": e.KEY_LEFTALT, "shift": e.KEY_LEFTSHIFT, "tab": e.KEY_TAB,
    "space": e.KEY_SPACE, "left": e.KEY_LEFT, "right": e.KEY_RIGHT,
    "up": e.KEY_UP, "down": e.KEY_DOWN, "esc": e.KEY_ESC, "enter": e.KEY_ENTER,
    "pageup": e.KEY_PAGEUP, "pagedown": e.KEY_PAGEDOWN,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEYS[_c] = getattr(e, "KEY_" + _c.upper())
for _i in range(1, 13):
    KEYS["f%d" % _i] = getattr(e, "KEY_F%d" % _i)

MODIFIERS = {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT,
             e.KEY_LEFTALT, e.KEY_RIGHTALT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA,
             e.KEY_CAPSLOCK, e.KEY_FN}

MT_TOOL_PALM = 2  # linux/input.h; not exported by python-evdev

BUTTONS = {"left": e.BTN_LEFT, "right": e.BTN_RIGHT, "middle": e.BTN_MIDDLE}


def load_config(path):
    cfg = Config()
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            data = tomllib.load(f)
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, type(getattr(cfg, k))(v))
            else:
                log.warning("unknown config key %s", k)
    return cfg


def find_touchpad(name_hint=None):
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities()
        abs_codes = [c[0] if isinstance(c, tuple) else c for c in caps.get(e.EV_ABS, [])]
        is_pad = (e.ABS_MT_POSITION_X in abs_codes and
                  e.INPUT_PROP_BUTTONPAD in dev.input_props() or
                  e.ABS_MT_POSITION_X in abs_codes and "touchpad" in dev.name.lower())
        if dev.name.startswith("winptp") or (dev.phys or "").startswith("winptp"):
            is_pad = False
        if is_pad and (name_hint is None or name_hint.lower() in dev.name.lower()):
            return dev
        dev.close()
    return None


def find_keyboards():
    kbds = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        keys = dev.capabilities().get(e.EV_KEY, [])
        if e.KEY_A in keys and e.KEY_SPACE in keys and not dev.name.startswith("winptp"):
            kbds.append(dev)
        else:
            dev.close()
    return kbds


class PinchPad:
    """A virtual clone of the touchpad that only ever sees pinches.

    libinput turns the replayed contacts into native pinch gestures, which
    Wayland apps (browsers, image viewers) use for smooth zoom.
    """

    def __init__(self, pad, reader):
        self.reader = reader
        absinfo = dict(pad.capabilities(absinfo=True)[e.EV_ABS])
        axes = [e.ABS_X, e.ABS_Y, e.ABS_MT_SLOT, e.ABS_MT_POSITION_X,
                e.ABS_MT_POSITION_Y, e.ABS_MT_TRACKING_ID]
        self.dev = evdev.UInput(
            {e.EV_KEY: [e.BTN_LEFT, e.BTN_TOUCH, e.BTN_TOOL_FINGER,
                        e.BTN_TOOL_DOUBLETAP],
             e.EV_ABS: [(a, absinfo[a]) for a in axes if a in absinfo]},
            name=PAD_NAME, vendor=VIRTUAL_VENDOR, product=VIRTUAL_PRODUCT + 2,
            version=1, bustype=e.BUS_VIRTUAL,
            input_props=[e.INPUT_PROP_POINTER, e.INPUT_PROP_BUTTONPAD])
        self.active = {}
        self.next_id = 1

    def frame(self, touches):
        r = self.reader
        w = self.dev.write
        if not self.active:
            w(e.EV_KEY, e.BTN_TOUCH, 1)
            w(e.EV_KEY, e.BTN_TOOL_DOUBLETAP, 1)
        for i, (slot, (x, y)) in enumerate(sorted(touches.items())[:2]):
            w(e.EV_ABS, e.ABS_MT_SLOT, i)
            if i not in self.active:
                self.active[i] = self.next_id
                self.next_id = (self.next_id + 1) % 65535
                w(e.EV_ABS, e.ABS_MT_TRACKING_ID, self.active[i])
            ux = int(x * r.rx + r.ax.min)
            uy = int(y * r.ry + r.ay.min)
            w(e.EV_ABS, e.ABS_MT_POSITION_X, ux)
            w(e.EV_ABS, e.ABS_MT_POSITION_Y, uy)
            if i == 0:
                w(e.EV_ABS, e.ABS_X, ux)
                w(e.EV_ABS, e.ABS_Y, uy)
        self.dev.syn()

    def end(self):
        if not self.active:
            return
        for i in list(self.active):
            self.dev.write(e.EV_ABS, e.ABS_MT_SLOT, i)
            self.dev.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
        self.active.clear()
        self.dev.write(e.EV_KEY, e.BTN_TOUCH, 0)
        self.dev.write(e.EV_KEY, e.BTN_TOOL_DOUBLETAP, 0)
        self.dev.syn()

    def close(self):
        self.end()
        self.dev.close()


class Passthrough:
    """Gestures mode: a virtual copy of the touchpad that libinput drives.

    One- and two-finger input is replayed unchanged, so pointer, taps,
    scrolling and pinch behave exactly as stock libinput / Plasma settings
    say. When a third finger lands, the copy sees every finger lift and
    stays quiet until the real pad is empty again, so libinput never sees
    a 3/4-finger gesture and winptp's swipes are the only ones.

    The copy keeps the touchpad's name and IDs, so Plasma's saved Touchpad
    settings and libinput's quirks for it still apply.
    """

    def __init__(self, pad, write=None, hold_ms=0.0):
        self.dev = None
        if write is None:
            caps = pad.capabilities(absinfo=True)
            caps.pop(e.EV_SYN, None)
            caps.pop(e.EV_FF, None)
            info = pad.info
            self.dev = evdev.UInput(caps, name=pad.name, vendor=info.vendor,
                                    product=info.product, version=info.version,
                                    bustype=info.bustype, phys=PASSTHROUGH_PHYS,
                                    input_props=pad.input_props())
            write = self.dev.write
        self.write = write
        self.hold_ms = hold_ms
        self.blocked = False
        self.contacts = {}          # slot -> True while forwarded and down
        self.keys = set()           # BTN_* currently down on the copy
        # Fingers of a three-finger tap land a few ms apart. Had libinput
        # seen the first two, lifting them when the third lands would be a
        # two-finger tap (right click). So a touch on an idle pad is held
        # back for hold_ms, until it is clear how many fingers it is.
        self.pending = None         # [raw_slot, events] while holding
        self.hold_until = 0.0
        self.slots = {}

    def frame(self, raw, raw_slot, slots, fingers, t=0.0):
        """Forward one frame. slots: the reader's slot state after it."""
        self.slots = slots
        # the reader keeps empty entries for unused slots: only a slot
        # with a tracking id is a finger
        live = any("id" in st for st in slots.values())
        if self.blocked:
            if not live:
                self.blocked = False
            return
        if fingers >= 3:
            self.pending = None     # libinput never saw these fingers
            if self.contacts or self.keys:
                self.lift()
            self.blocked = live
            return
        if self.pending is not None:
            if not live:            # a quick one/two-finger tap: replay it
                self.flush()
                self._forward(raw_slot, raw)
            else:
                self.pending[1] += raw
                if t >= self.hold_until:
                    self.flush()
            return
        if live and not self.contacts and not self.keys and self.hold_ms > 0:
            self.pending = [raw_slot, list(raw)]
            self.hold_until = t + self.hold_ms
            return
        self._forward(raw_slot, raw)

    def next_timeout(self, t):
        """Milliseconds until tick() must run, or None."""
        if self.pending is None:
            return None
        return max(0.0, self.hold_until - t)

    def tick(self, t):
        if self.pending is not None and t >= self.hold_until:
            self.flush()

    def flush(self):
        """Send the held frames as one frame: libinput sees the touch start."""
        if self.pending is not None:
            raw_slot, raw = self.pending
            self.pending = None
            self._forward(raw_slot, raw)

    def _forward(self, raw_slot, raw):
        w = self.write
        w(e.EV_ABS, e.ABS_MT_SLOT, raw_slot)
        slot, fresh = raw_slot, []
        for t, c, v in raw:
            w(t, c, v)
            if t == e.EV_ABS and c == e.ABS_MT_SLOT:
                slot = v
            elif t == e.EV_ABS and c == e.ABS_MT_TRACKING_ID:
                if v < 0:
                    self.contacts.pop(slot, None)
                    if slot in fresh:
                        fresh.remove(slot)
                else:
                    self.contacts[slot] = True
                    fresh.append(slot)
            elif t == e.EV_KEY:
                (self.keys.add if v else self.keys.discard)(c)
        # the kernel drops a position equal to the slot's last one, which
        # the copy may not have seen: restate it for new contacts
        for s in fresh:
            st = self.slots.get(s, {})
            w(e.EV_ABS, e.ABS_MT_SLOT, s)
            if "ux" in st:
                w(e.EV_ABS, e.ABS_MT_POSITION_X, st["ux"])
            if "uy" in st:
                w(e.EV_ABS, e.ABS_MT_POSITION_Y, st["uy"])
        w(e.EV_SYN, e.SYN_REPORT, 0)

    def lift(self):
        w = self.write
        for s in list(self.contacts):
            w(e.EV_ABS, e.ABS_MT_SLOT, s)
            w(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
        self.contacts.clear()
        for k in list(self.keys):
            w(e.EV_KEY, k, 0)
        self.keys.clear()
        w(e.EV_SYN, e.SYN_REPORT, 0)

    def close(self):
        self.lift()
        if self.dev:
            self.dev.close()


class GestureGate:
    """Gestures mode: let through only what 3/4-finger sessions produce.

    A session counts from the frame a third finger lands until the pad is
    empty; the lift frame itself still passes (taps fire on lift). Key
    releases always pass so Alt can never stick.
    """

    ALLOWED = ("combo", "keydown", "keyup", "button")

    def __init__(self):
        self.multi = False

    def frame(self, actions, fingers, touches):
        was = self.multi
        if fingers >= 3:
            self.multi = True
        elif not touches:
            self.multi = False
        return self._filter(actions, was or self.multi)

    def tick(self, actions):
        return self._filter(actions, self.multi)

    def _filter(self, actions, multi):
        return [a for a in actions
                if a[0] == "keyup" or multi and a[0] in self.ALLOWED]


def gestures_only(cfg):
    """Turn off what libinput does itself in gestures mode.

    tap_to_click stays on for the engine: 3/4-finger taps are winptp's, and
    one/two-finger taps are dropped by the action filter in run().
    """
    cfg.tap_to_click = True
    for k in ("two_finger_tap", "tap_and_drag", "inertia", "pinch_zoom",
              "pinch_native"):
        setattr(cfg, k, False)


class Output:
    def __init__(self, pinchpad=None):
        self.pinchpad = pinchpad
        self.pointer = evdev.UInput(
            {e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
             e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL,
                        e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES]},
            name=POINTER_NAME, vendor=VIRTUAL_VENDOR, product=VIRTUAL_PRODUCT,
            version=1, bustype=e.BUS_VIRTUAL)
        self.keys = evdev.UInput(
            {e.EV_KEY: sorted(set(KEYS.values()))},
            name=KEYBOARD_NAME, vendor=VIRTUAL_VENDOR, product=VIRTUAL_PRODUCT + 1,
            version=1, bustype=e.BUS_VIRTUAL)
        self.rem = [0.0, 0.0]
        self.wheel = [0.0, 0.0]      # hi-res remainder
        self.notch = [0.0, 0.0]      # accumulated hi-res for legacy wheel
        self.held = set()

    def close(self):
        for k in list(self.held):
            self.keys.write(e.EV_KEY, k, 0)
        self.keys.syn()
        self.pointer.close()
        self.keys.close()
        if self.pinchpad:
            self.pinchpad.close()

    def apply(self, actions):
        p_dirty = k_dirty = False
        for a in actions:
            kind = a[0]
            if kind == "move":
                self.rem[0] += a[1]
                self.rem[1] += a[2]
                ix, iy = int(self.rem[0]), int(self.rem[1])
                self.rem[0] -= ix
                self.rem[1] -= iy
                if ix:
                    self.pointer.write(e.EV_REL, e.REL_X, ix)
                if iy:
                    self.pointer.write(e.EV_REL, e.REL_Y, iy)
                p_dirty = p_dirty or bool(ix or iy)
            elif kind == "scroll":
                p_dirty |= self._wheel(0, a[1], e.REL_WHEEL_HI_RES, e.REL_WHEEL)
                p_dirty |= self._wheel(1, a[2], e.REL_HWHEEL_HI_RES, e.REL_HWHEEL)
            elif kind == "button":
                if p_dirty:
                    self.pointer.syn()
                    p_dirty = False
                self.pointer.write(e.EV_KEY, BUTTONS[a[1]], 1 if a[2] else 0)
                self.pointer.syn()
            elif kind == "zoom":
                self._key(e.KEY_LEFTCTRL, 1)
                self.keys.syn()
                self.pointer.write(e.EV_REL, e.REL_WHEEL_HI_RES, 120 * a[1])
                self.pointer.write(e.EV_REL, e.REL_WHEEL, a[1])
                self.pointer.syn()
                self._key(e.KEY_LEFTCTRL, 0)
                self.keys.syn()
            elif kind == "pinch":
                if self.pinchpad:
                    self.pinchpad.frame(a[1])
            elif kind == "pinch_end":
                if self.pinchpad:
                    self.pinchpad.end()
            elif kind == "combo":
                self.combo(a[1])
            elif kind in ("keydown", "keyup"):
                self._key(KEYS[a[1]], 1 if kind == "keydown" else 0)
                k_dirty = True
        if p_dirty:
            self.pointer.syn()
        if k_dirty:
            self.keys.syn()

    def _wheel(self, axis, units, hi_code, lo_code):
        if not units:
            return False
        self.wheel[axis] += units
        hi = int(self.wheel[axis])
        self.wheel[axis] -= hi
        if not hi:
            return False
        self.pointer.write(e.EV_REL, hi_code, hi)
        self.notch[axis] += hi
        notches = int(self.notch[axis] / 120)
        if notches:
            self.notch[axis] -= notches * 120
            self.pointer.write(e.EV_REL, lo_code, notches)
        return True

    def _key(self, code, value):
        self.keys.write(e.EV_KEY, code, value)
        if value:
            self.held.add(code)
        else:
            self.held.discard(code)

    def combo(self, spec):
        codes = [KEYS[p.strip()] for p in spec.lower().split("+")]
        for c in codes:
            if c not in self.held:
                self.keys.write(e.EV_KEY, c, 1)
        self.keys.syn()
        for c in reversed(codes):
            if c not in self.held:
                self.keys.write(e.EV_KEY, c, 0)
        self.keys.syn()


class TouchpadReader:
    """Turns the kernel MT protocol B stream into per-frame touch dicts."""

    def __init__(self, dev):
        self.dev = dev
        ax = dev.absinfo(e.ABS_MT_POSITION_X)
        ay = dev.absinfo(e.ABS_MT_POSITION_Y)
        self.ax, self.ay = ax, ay
        self.rx = ax.resolution or (ax.max - ax.min) / 102.0
        self.ry = ay.resolution or (ay.max - ay.min) / 67.0
        self.width_mm = (ax.max - ax.min) / self.rx
        self.height_mm = (ay.max - ay.min) / self.ry
        self.slot = 0
        self.slots = {}
        self.button = False
        self.raw = []               # this frame's events, for Passthrough
        self.raw_slot = 0           # slot selected when the frame began

    def events(self):
        """Yield (touches, button) for every SYN_REPORT.

        self.raw / self.raw_slot hold the frame's raw events meanwhile.
        """
        for ev in self.dev.read():
            if ev.type != e.EV_SYN:
                self.raw.append((ev.type, ev.code, ev.value))
            if ev.type == e.EV_ABS:
                s = self.slots.setdefault(self.slot, {})
                if ev.code == e.ABS_MT_SLOT:
                    self.slot = ev.value
                elif ev.code == e.ABS_MT_TRACKING_ID:
                    if ev.value < 0:
                        self.slots.pop(self.slot, None)
                    else:
                        self.slots[self.slot] = {"id": ev.value}
                elif ev.code == e.ABS_MT_POSITION_X:
                    s["x"] = (ev.value - self.ax.min) / self.rx
                    s["ux"] = ev.value
                elif ev.code == e.ABS_MT_POSITION_Y:
                    s["y"] = (ev.value - self.ay.min) / self.ry
                    s["uy"] = ev.value
                elif ev.code == e.ABS_MT_TOOL_TYPE:
                    s["palm"] = ev.value == MT_TOOL_PALM
            elif ev.type == e.EV_KEY and ev.code == e.BTN_LEFT:
                self.button = bool(ev.value)
            elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT:
                touches = {k: (v["x"], v["y"], v.get("palm", False))
                           for k, v in self.slots.items()
                           if "id" in v and "x" in v and "y" in v}
                yield touches, self.button
                self.raw = []
                self.raw_slot = self.slot
            elif ev.type == e.EV_SYN and ev.code == e.SYN_DROPPED:
                self.slots.clear()
                self.raw = []


def now_ms():
    return time.monotonic() * 1000.0


def run(cfg, name_hint=None, dry_run=False):
    pad = find_touchpad(name_hint)
    if pad is None:
        log.error("no precision touchpad found")
        return 2
    reader = TouchpadReader(pad)
    cfg.width_mm, cfg.height_mm = reader.width_mm, reader.height_mm
    log.info("touchpad %s (%s) %.0fx%.0f mm", pad.name, pad.path,
             cfg.width_mm, cfg.height_mm)
    gestures = cfg.mode == "gestures"
    if gestures:
        gestures_only(cfg)
        log.info("gestures mode: 1-2 fingers go to libinput unchanged")
    engine = Engine(cfg)
    pinchpad = passthrough = None
    if gestures and not dry_run:
        passthrough = Passthrough(pad, hold_ms=cfg.gesture_hold_ms)
    if not dry_run and cfg.pinch_native:
        try:
            pinchpad = PinchPad(pad, reader)
        except (OSError, TypeError) as err:
            log.warning("no virtual touchpad (%s); pinch falls back to ctrl+wheel", err)
            cfg.pinch_native = False
            engine.cfg.pinch_native = False
    out = None if dry_run else Output(pinchpad)
    kbds = find_keyboards()
    log.info("watching %d keyboard(s) for disable-while-typing", len(kbds))
    if not dry_run:
        pad.grab()
    gate = GestureGate() if gestures else None
    try:
        fds = {pad.fd: pad}
        for k in kbds:
            fds[k.fd] = k
        while True:
            t = now_ms()
            timeouts = [engine.next_timeout(t)]
            if passthrough:
                timeouts.append(passthrough.next_timeout(t))
            timeouts = [x for x in timeouts if x is not None]
            timeout = min(timeouts) if timeouts else None
            r, _, _ = select.select(list(fds), [], [],
                                    None if timeout is None else timeout / 1000.0)
            actions = []
            for fd in r:
                dev = fds[fd]
                if dev is pad:
                    try:
                        for touches, button in reader.events():
                            acts = engine.frame(now_ms(), touches, button)
                            if gate:
                                n = engine.finger_count()
                                if passthrough:
                                    passthrough.frame(reader.raw, reader.raw_slot,
                                                      reader.slots, n, now_ms())
                                acts = gate.frame(acts, n, touches)
                            actions += acts
                    except OSError:
                        log.warning("touchpad disappeared; exiting for restart")
                        return 1
                else:
                    try:
                        for ev in dev.read():
                            if ev.type == e.EV_KEY and ev.value == 1 and \
                                    ev.code not in MODIFIERS:
                                engine.key_activity(now_ms())
                    except OSError:
                        del fds[fd]
            if passthrough:
                passthrough.tick(now_ms())
            ticked = engine.tick(now_ms())
            actions += gate.tick(ticked) if gate else ticked
            if actions:
                if dry_run:
                    for a in actions:
                        print(a, flush=True)
                else:
                    out.apply(actions)
    finally:
        if not dry_run:
            try:
                pad.ungrab()
            except OSError:
                pass
            out.close()
            if passthrough:
                passthrough.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-c", "--config", default="/etc/winptp/winptp.toml")
    p.add_argument("--device", help="substring of the touchpad name")
    p.add_argument("--dry-run", action="store_true",
                   help="print actions instead of grabbing the touchpad")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(name)s: %(message)s")
    try:
        return run(load_config(a.config), a.device, a.dry_run)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
