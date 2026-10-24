"""Synthetic-touch tests for the Windows PTP engine."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from winptp.engine import Accelerator, Config, Engine  # noqa: E402


class Pad:
    """Drives an Engine with timed frames; collects all actions."""

    def __init__(self, **cfg):
        self.e = Engine(Config(**cfg))
        self.t = 0.0
        self.actions = []

    def frame(self, touches, button=False, dt=8.0):
        self.t += dt
        self.actions += self.e.tick(self.t)
        self.actions += self.e.frame(self.t, touches, button)

    def wait(self, ms):
        end = self.t + ms
        while self.t < end:
            self.t += 8.0
            self.actions += self.e.tick(self.t)

    def stroke(self, starts, delta, steps, button=False):
        """Move fingers (list of (x, y)) by delta over steps frames."""
        for i in range(steps + 1):
            f = i / steps
            self.frame({n: (x + delta[0] * f, y + delta[1] * f, False)
                        for n, (x, y) in enumerate(starts)}, button)

    def lift(self):
        self.frame({})

    def kinds(self, kind):
        return [a for a in self.actions if a[0] == kind]


class TestCurve(unittest.TestCase):
    def test_gain_increases_with_speed(self):
        g = lambda v: Accelerator(10, 96).gain(v, 0)
        self.assertLess(g(2.0), g(8.0))
        self.assertLess(g(8.0), g(30.0))

    def test_cursor_speed_scales_output(self):
        self.assertAlmostEqual(Accelerator(20, 120).gain(5, 0),
                               2 * Accelerator(10, 120).gain(5, 0))


class TestTaps(unittest.TestCase):
    def test_single_tap_is_left_click(self):
        p = Pad()
        p.stroke([(50, 30)], (0.5, 0), 5)
        p.lift()
        p.wait(300)
        self.assertEqual(p.kinds("button"),
                         [("button", "left", True), ("button", "left", False)])

    def test_two_finger_tap_is_right_click(self):
        p = Pad()
        p.stroke([(40, 30), (60, 30)], (0.3, 0), 5)
        p.lift()
        p.wait(300)
        self.assertEqual(p.kinds("button"),
                         [("button", "right", True), ("button", "right", False)])

    def test_three_finger_tap_opens_search(self):
        p = Pad()
        p.stroke([(30, 30), (50, 30), (70, 30)], (0, 0), 5)
        p.lift()
        self.assertEqual(p.kinds("button"),
                         [("button", "middle", True), ("button", "middle", False)])

    def test_long_press_is_not_a_tap(self):
        p = Pad()
        p.stroke([(50, 30)], (0, 0), 40)
        p.lift()
        p.wait(300)
        self.assertEqual(p.kinds("button"), [])

    def test_tap_and_drag(self):
        p = Pad()
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.frame({}, dt=60)
        p.stroke([(50, 30)], (20, 0), 20)
        self.assertEqual(p.kinds("button"), [("button", "left", True),
                                             ("button", "left", False),
                                             ("button", "left", True)])
        self.assertTrue(p.kinds("move"))
        p.lift()
        self.assertEqual(p.kinds("button")[-1], ("button", "left", False))

    def test_typing_suppresses_tap(self):
        p = Pad()
        p.e.key_activity(p.t)
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.wait(300)
        self.assertEqual(p.kinds("button"), [])

    def test_custom_typing_delay(self):
        p = Pad(typing_tap_block_ms=0.0)
        p.e.key_activity(p.t)
        p.frame({}, dt=300)  # past the move block, so no curtain
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.wait(300)
        self.assertEqual(len(p.kinds("button")), 2)
        p = Pad(typing_tap_block_ms=2000.0)
        p.e.key_activity(p.t)
        p.frame({}, dt=1500)
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.wait(300)
        self.assertEqual(p.kinds("button"), [])

    def test_palm_is_ignored(self):
        p = Pad()
        for _ in range(5):
            p.frame({0: (50, 30, True)})
        p.lift()
        p.wait(300)
        self.assertEqual(p.actions, [])


class TestTapExtras(unittest.TestCase):
    def test_double_tap(self):
        p = Pad()
        for _ in range(2):
            p.stroke([(50, 30)], (0, 0), 5)
            p.lift()
            p.frame({}, dt=60)
        self.assertEqual(len(p.kinds("button")), 4)

    def test_tap_does_not_move_pointer(self):
        p = Pad()
        p.stroke([(50, 30)], (2, 0), 5)
        p.lift()
        self.assertEqual(p.kinds("move"), [])

    def test_wide_two_finger_tap_is_ignored(self):
        p = Pad()
        p.stroke([(10, 30), (90, 30)], (0, 0), 5)
        p.lift()
        self.assertEqual(p.kinds("button"), [])

    def test_touch_right_after_typing_is_a_curtain(self):
        p = Pad()
        p.e.key_activity(p.t)
        p.stroke([(50, 30)], (20, 0), 10)
        self.assertEqual(p.kinds("move"), [])


class TestTapDragLimits(unittest.TestCase):
    def test_late_second_touch_is_not_a_drag(self):
        p = Pad()
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.frame({}, dt=300)
        p.stroke([(50, 30)], (20, 0), 20)
        p.lift()
        self.assertEqual(len(p.kinds("button")), 2)

    def test_far_second_touch_is_not_a_drag(self):
        p = Pad()
        p.stroke([(20, 30)], (0, 0), 5)
        p.lift()
        p.frame({}, dt=60)
        p.stroke([(70, 30)], (20, 0), 20)
        p.lift()
        self.assertEqual(len(p.kinds("button")), 2)


class TestClicks(unittest.TestCase):
    def test_bottom_right_click_is_right(self):
        p = Pad()
        p.frame({0: (90, 62, False)})
        p.frame({0: (90, 62, False)}, button=True)
        p.frame({0: (90, 62, False)}, button=False)
        p.lift()
        self.assertEqual(p.kinds("button"),
                         [("button", "right", True), ("button", "right", False)])

    def test_center_click_is_left(self):
        p = Pad()
        p.frame({0: (50, 30, False)})
        p.frame({0: (50, 30, False)}, button=True)
        p.frame({0: (50, 30, False)})
        p.lift()
        p.wait(300)
        self.assertEqual(p.kinds("button"),
                         [("button", "left", True), ("button", "left", False)])

    def test_two_finger_click_is_right(self):
        p = Pad()
        two = {0: (40, 30, False), 1: (60, 30, False)}
        p.frame(two)
        p.frame(two, button=True)
        p.frame(two)
        p.lift()
        self.assertEqual(p.kinds("button")[0], ("button", "right", True))

    def test_thumb_on_button_strip_does_not_block_pointer(self):
        p = Pad()
        p.frame({0: (30, 62, False)})
        p.frame({0: (30, 62, False)}, button=True)
        for i in range(10):
            p.frame({0: (30, 62, False), 1: (50 + i, 30, False)}, button=True)
        self.assertEqual(p.kinds("button")[0], ("button", "left", True))
        self.assertTrue(p.kinds("move"))


class TestPointer(unittest.TestCase):
    def test_jitter_is_smoothed(self):
        p = Pad()
        for i in range(60):
            wobble = 0.15 if i % 2 else -0.15
            p.frame({0: (20 + i * 0.5, 30 + wobble, False)})
        ys = [a[2] for a in p.kinds("move")][5:]
        xs = [a[1] for a in p.kinds("move")][5:]
        self.assertLess(max(abs(y) for y in ys), 0.25 * min(xs))

    def test_faster_moves_further(self):
        slow, fast = Pad(), Pad()
        slow.stroke([(20, 30)], (20, 0), 100)
        fast.stroke([(20, 30)], (20, 0), 10)
        dist = lambda p: sum(a[1] for a in p.kinds("move"))
        self.assertGreater(dist(fast), dist(slow) * 1.2)

    def test_no_jump_after_two_finger_scroll(self):
        p = Pad()
        p.stroke([(40, 20), (60, 20)], (0, 20), 10)
        moves_before = len(p.kinds("move"))
        for i in range(5):
            p.frame({0: (40, 40 + i, False)})
        self.assertEqual(len(p.kinds("move")), moves_before)


class TestScroll(unittest.TestCase):
    def test_natural_two_finger_scroll(self):
        p = Pad(inertia=False)
        p.stroke([(40, 20), (60, 20)], (0, 20), 20)
        v = sum(a[1] for a in p.kinds("scroll"))
        # fingers moved down 20 mm; natural scrolling: content follows
        self.assertGreater(v, 0)
        # 125 mm/s is the middle Windows speed tier: 8 mm per notch
        self.assertAlmostEqual(v, 20 / 8.0 * 120, delta=60)
        self.assertEqual(sum(a[2] for a in p.kinds("scroll")), 0)

    def test_traditional_scroll_direction(self):
        p = Pad(inertia=False, natural_scroll=False)
        p.stroke([(40, 20), (60, 20)], (0, 20), 20)
        self.assertLess(sum(a[1] for a in p.kinds("scroll")), 0)

    def test_inertia_continues_after_lift(self):
        p = Pad()
        p.stroke([(40, 10), (60, 10)], (0, 30), 6)
        p.lift()
        before = len(p.kinds("scroll"))
        p.wait(500)
        self.assertGreater(len(p.kinds("scroll")), before + 5)

    def test_touch_stops_inertia(self):
        p = Pad()
        p.stroke([(40, 10), (60, 10)], (0, 30), 6)
        p.lift()
        p.wait(50)
        p.frame({0: (50, 30, False)})
        n = len(p.kinds("scroll"))
        p.wait(200)
        self.assertEqual(len(p.kinds("scroll")), n)


class TestPinch(unittest.TestCase):
    def test_native_pinch_replays_contacts(self):
        p = Pad()
        for i in range(20):
            p.frame({0: (45 - i, 30, False), 1: (55 + i, 30, False)})
        p.lift()
        self.assertTrue(p.kinds("pinch"))
        self.assertEqual(p.kinds("pinch_end"), [("pinch_end",)])
        self.assertEqual(p.kinds("scroll"), [])

    def test_spread_zooms_in(self):
        p = Pad(pinch_native=False)
        for i in range(20):
            p.frame({0: (45 - i, 30, False), 1: (55 + i, 30, False)})
        zooms = p.kinds("zoom")
        self.assertTrue(zooms)
        self.assertTrue(all(z[1] == 1 for z in zooms))
        self.assertEqual(p.kinds("scroll"), [])

    def test_pinch_zooms_out(self):
        p = Pad(pinch_native=False)
        for i in range(20):
            p.frame({0: (25 + i, 30, False), 1: (75 - i, 30, False)})
        self.assertTrue(all(z[1] == -1 for z in p.kinds("zoom")))


class TestSwipes(unittest.TestCase):
    three = [(30, 40), (50, 40), (70, 40)]
    four = [(20, 40), (40, 40), (60, 40), (80, 40)]

    def test_three_up_is_task_view(self):
        p = Pad()
        p.stroke(self.three, (0, -20), 10)
        p.lift()
        self.assertEqual(p.kinds("combo"), [("combo", "meta+w")])
        self.assertEqual(p.kinds("button"), [])

    def test_three_down_shows_desktop(self):
        p = Pad()
        p.stroke(self.three, (0, 20), 10)
        self.assertEqual(p.kinds("combo"), [("combo", "meta+d")])

    def test_three_horizontal_is_alt_tab(self):
        p = Pad()
        p.stroke(self.three, (30, 0), 30)
        p.lift()
        self.assertEqual(p.actions[0], ("keydown", "alt"))
        tabs = [a for a in p.actions if a == ("combo", "tab")]
        self.assertGreaterEqual(len(tabs), 2)
        self.assertEqual(p.kinds("keyup"), [("keyup", "alt")])

    def test_four_left_switches_desktop(self):
        p = Pad()
        p.stroke(self.four, (-20, 0), 10)
        self.assertEqual(p.kinds("combo"), [("combo", "meta+ctrl+right")])

    def test_three_down_closes_overview(self):
        p = Pad()
        p.stroke(self.three, (0, -20), 10)
        p.lift()
        p.stroke(self.three, (0, 20), 10)
        p.lift()
        p.stroke(self.three, (0, 20), 10)
        p.lift()
        self.assertEqual(p.kinds("combo"), [("combo", "meta+w"), ("combo", "esc"),
                                            ("combo", "meta+d")])


if __name__ == "__main__":
    unittest.main()
