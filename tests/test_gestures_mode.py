"""Gestures mode: passthrough of 1-2 fingers, winptp keeps 3/4-finger input."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from evdev import ecodes as e
    from winptp import daemon
except ImportError:  # python3-evdev missing
    daemon = None

from winptp.engine import Config, Engine  # noqa: E402


@unittest.skipIf(daemon is None, "python3-evdev not installed")
class PassthroughTest(unittest.TestCase):
    def setUp(self):
        self.out = []
        self.p = daemon.Passthrough(None, write=lambda *ev: self.out.append(ev))

    def down(self, slot, tid, x, y):
        return [(e.EV_ABS, e.ABS_MT_SLOT, slot), (e.EV_ABS, e.ABS_MT_TRACKING_ID, tid),
                (e.EV_ABS, e.ABS_MT_POSITION_X, x), (e.EV_ABS, e.ABS_MT_POSITION_Y, y),
                (e.EV_KEY, e.BTN_TOUCH, 1)]

    def test_two_fingers_forwarded_verbatim(self):
        raw = self.down(0, 5, 100, 200)
        self.p.frame(raw, 0, {0: {"ux": 100, "uy": 200}}, 1)
        self.assertEqual(self.out[0], (e.EV_ABS, e.ABS_MT_SLOT, 0))
        self.assertEqual(self.out[1:6], raw)
        self.assertEqual(self.out[-1], (e.EV_SYN, e.SYN_REPORT, 0))

    def test_third_finger_lifts_and_blocks_until_empty(self):
        self.p.frame(self.down(0, 5, 100, 200), 0, {0: {}}, 1)
        self.out.clear()
        live = {"id": 1}
        self.p.frame(self.down(2, 7, 300, 200), 0, {0: live, 1: live, 2: live}, 3)
        self.assertIn((e.EV_ABS, e.ABS_MT_TRACKING_ID, -1), self.out)
        self.assertIn((e.EV_KEY, e.BTN_TOUCH, 0), self.out)
        self.assertNotIn((e.EV_ABS, e.ABS_MT_TRACKING_ID, 7), self.out)
        self.out.clear()
        self.p.frame([(e.EV_ABS, e.ABS_MT_POSITION_X, 310)], 2, {0: live, 2: live}, 2)
        self.assertEqual(self.out, [])          # still blocked: fingers on pad
        self.p.frame([], 2, {}, 0)              # all lifted
        self.p.frame(self.down(0, 9, 50, 60), 2, {0: {"ux": 50, "uy": 60}}, 1)
        self.assertEqual(self.out[0], (e.EV_ABS, e.ABS_MT_SLOT, 2))  # slot restated
        self.assertIn((e.EV_ABS, e.ABS_MT_TRACKING_ID, 9), self.out)

    def test_unblocks_with_stale_empty_slots(self):
        # the reader leaves {} entries behind for slots that were lifted
        self.p.frame([], 0, {0: {"id": 1}}, 3)
        self.p.frame([], 0, {0: {}, 1: {}, 2: {}}, 0)
        self.out.clear()
        self.p.frame(self.down(0, 9, 50, 60), 0, {0: {"id": 9, "ux": 50, "uy": 60}}, 1)
        self.assertIn((e.EV_ABS, e.ABS_MT_TRACKING_ID, 9), self.out)

    def test_staggered_three_finger_tap_never_reaches_libinput(self):
        p = daemon.Passthrough(None, write=lambda *ev: self.out.append(ev), hold_ms=80)
        one, two = {0: {"id": 1}}, {0: {"id": 1}, 1: {"id": 2}}
        three = {**two, 2: {"id": 3}}
        p.frame(self.down(0, 1, 100, 200), 0, one, 1, t=0)
        p.frame(self.down(1, 2, 300, 200), 0, two, 2, t=25)
        p.frame(self.down(2, 3, 500, 200), 1, three, 3, t=50)
        p.frame([], 2, {}, 0, t=120)
        p.tick(200)
        self.assertEqual(self.out, [])

    def test_quick_tap_is_replayed_down_then_up(self):
        p = daemon.Passthrough(None, write=lambda *ev: self.out.append(ev), hold_ms=80)
        p.frame(self.down(0, 1, 100, 200), 0, {0: {"id": 1, "ux": 100, "uy": 200}}, 1, t=0)
        self.assertEqual(self.out, [])
        p.frame([(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1), (e.EV_KEY, e.BTN_TOUCH, 0)],
                0, {0: {}}, 0, t=60)
        syns = [i for i, ev in enumerate(self.out) if ev == (e.EV_SYN, e.SYN_REPORT, 0)]
        self.assertEqual(len(syns), 2)          # touch-down frame, then lift frame
        self.assertIn((e.EV_ABS, e.ABS_MT_TRACKING_ID, 1), self.out[:syns[0]])
        self.assertIn((e.EV_ABS, e.ABS_MT_TRACKING_ID, -1), self.out[syns[0]:])

    def test_held_touch_starts_after_hold(self):
        p = daemon.Passthrough(None, write=lambda *ev: self.out.append(ev), hold_ms=80)
        p.frame(self.down(0, 1, 100, 200), 0, {0: {"id": 1}}, 1, t=0)
        p.frame([(e.EV_ABS, e.ABS_MT_POSITION_X, 110)], 0, {0: {"id": 1}}, 1, t=40)
        self.assertEqual(self.out, [])
        self.assertEqual(p.next_timeout(40), 40)
        p.tick(80)                              # finger resting still: timer flushes
        self.assertIn((e.EV_ABS, e.ABS_MT_TRACKING_ID, 1), self.out)
        self.assertIn((e.EV_ABS, e.ABS_MT_POSITION_X, 110), self.out)
        self.out.clear()
        p.frame([(e.EV_ABS, e.ABS_MT_POSITION_X, 120)], 0, {0: {"id": 1}}, 1, t=90)
        self.assertIn((e.EV_ABS, e.ABS_MT_POSITION_X, 120), self.out)   # live now

    def test_new_contact_position_restated(self):
        raw = [(e.EV_ABS, e.ABS_MT_TRACKING_ID, 3)]   # kernel dropped equal X/Y
        self.p.frame(raw, 1, {1: {"ux": 11, "uy": 22}}, 1)
        self.assertIn((e.EV_ABS, e.ABS_MT_POSITION_X, 11), self.out)
        self.assertIn((e.EV_ABS, e.ABS_MT_POSITION_Y, 22), self.out)


@unittest.skipIf(daemon is None, "python3-evdev not installed")
class GateTest(unittest.TestCase):
    def run_pad(self, frames):
        cfg = Config(mode="gestures")
        daemon.gestures_only(cfg)
        eng, gate, t, out = Engine(cfg), daemon.GestureGate(), 0.0, []
        for touches in frames:
            t += 8
            acts = eng.frame(t, touches, False)
            out += gate.frame(acts, eng.finger_count(), touches)
            out += gate.tick(eng.tick(t))
        return out

    def stroke(self, starts, d, n):
        return [{i: (x + d[0] * k, y + d[1] * k, False) for i, (x, y) in enumerate(starts)}
                for k in range(n)] + [{}]

    def test_one_and_two_fingers_do_nothing(self):
        self.assertEqual(self.run_pad(self.stroke([(50, 30)], (2, 0), 10)), [])
        self.assertEqual(self.run_pad(self.stroke([(40, 30), (60, 30)], (0, 2), 10)), [])
        self.assertEqual(self.run_pad(self.stroke([(50, 30)], (0, 0), 3)), [])  # tap

    def test_three_finger_swipe_up_still_works(self):
        out = self.run_pad(self.stroke([(30, 40), (50, 40), (70, 40)], (0, -2), 10))
        self.assertIn(("combo", "meta+w"), out)

    def test_three_finger_tap_still_clicks_middle(self):
        out = self.run_pad(self.stroke([(30, 40), (50, 40), (70, 40)], (0, 0), 3))
        self.assertEqual(out, [("button", "middle", True), ("button", "middle", False)])

    def test_four_finger_sideways(self):
        out = self.run_pad(self.stroke([(20, 40), (40, 40), (60, 40), (80, 40)],
                                       (-2, 0), 10))
        self.assertIn(("combo", "meta+ctrl+right"), out)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(daemon is None, "python3-evdev not installed")
class ReaderPassthroughTest(unittest.TestCase):
    """Real TouchpadReader + Engine + Passthrough on a scripted kernel stream."""

    class Abs:
        def __init__(self, hi):
            self.min, self.max, self.resolution = 0, hi, 12

    class Ev:
        def __init__(self, t, c, v):
            self.type, self.code, self.value = t, c, v

    class Dev:
        def __init__(self):
            self.queue = []

        def absinfo(self, code):
            return ReaderPassthroughTest.Abs(1224 if code == e.ABS_MT_POSITION_X else 804)

        def read(self):
            q, self.queue = self.queue, []
            return q

    def setUp(self):
        self.dev = self.Dev()
        self.reader = daemon.TouchpadReader(self.dev)
        cfg = Config(mode="gestures")
        daemon.gestures_only(cfg)
        self.engine = Engine(cfg)
        self.out = []
        self.p = daemon.Passthrough(None, write=lambda *ev: self.out.append(ev),
                                    hold_ms=80)
        self.gate = daemon.GestureGate()
        self.actions = []
        self.t = 0.0

    def push(self, *events):
        E = self.Ev
        self.dev.queue = [E(*ev) for ev in events] + [E(e.EV_SYN, e.SYN_REPORT, 0)]
        for touches, button in self.reader.events():
            self.t += 8
            acts = self.engine.frame(self.t, touches, button)
            n = self.engine.finger_count()
            self.p.frame(self.reader.raw, self.reader.raw_slot, self.reader.slots,
                         n, self.t)
            self.actions += self.gate.frame(acts, n, touches)
        self.p.tick(self.t)

    def fingers(self, n, tid0, x):
        evs = []
        for i in range(n):
            evs += [(e.EV_ABS, e.ABS_MT_SLOT, i), (e.EV_ABS, e.ABS_MT_TRACKING_ID, tid0 + i),
                    (e.EV_ABS, e.ABS_MT_POSITION_X, x + 200 * i),
                    (e.EV_ABS, e.ABS_MT_POSITION_Y, 400)]
        return evs + [(e.EV_ABS, e.ABS_X, x), (e.EV_ABS, e.ABS_Y, 400)]

    def lift(self, n):
        evs = []
        for i in range(n):
            evs += [(e.EV_ABS, e.ABS_MT_SLOT, i), (e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)]
        return evs

    def test_pointer_works_after_a_three_finger_swipe(self):
        self.push(*self.fingers(3, 10, 100))
        for k in range(5):
            self.push((e.EV_ABS, e.ABS_MT_SLOT, 0),
                      (e.EV_ABS, e.ABS_MT_POSITION_Y, 380 - 20 * k))
        self.push(*self.lift(3))
        self.assertTrue(self.p.blocked is False)
        self.out.clear()
        self.push(*self.fingers(1, 20, 500))
        self.t += 100
        self.p.tick(self.t)                     # hold over
        self.assertIn((e.EV_ABS, e.ABS_MT_TRACKING_ID, 20), self.out)
        self.push((e.EV_ABS, e.ABS_MT_POSITION_X, 520))
        self.assertIn((e.EV_ABS, e.ABS_MT_POSITION_X, 520), self.out)

    def test_staggered_three_finger_tap_is_a_middle_click_only(self):
        E = e
        for i, x in enumerate((100, 300, 500)):     # fingers land 16 ms apart
            self.push((E.EV_ABS, E.ABS_MT_SLOT, i), (E.EV_ABS, E.ABS_MT_TRACKING_ID, 30 + i),
                      (E.EV_ABS, E.ABS_MT_POSITION_X, x), (E.EV_ABS, E.ABS_MT_POSITION_Y, 400))
        self.push(*self.lift(3))
        self.t += 300
        self.p.tick(self.t)
        self.assertEqual(self.out, [])              # libinput saw nothing: no right click
        self.assertEqual(self.actions, [("button", "middle", True),
                                        ("button", "middle", False)])
