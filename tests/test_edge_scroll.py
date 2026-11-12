"""Edge auto-scroll: a drag held at the pad's edge scrolls the page."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from test_engine import Pad  # noqa: E402

try:
    from winptp import daemon
except ImportError:  # python3-evdev missing
    daemon = None

H = 67.0   # default pad height (mm)


def moves(actions):
    """Scrolling while held (the 1 px wiggles alone don't count)."""
    return [a for a in actions if a[0] == "edge_scroll"]


def net_move(actions):
    return (sum(a[1] for a in actions if a[0] in ("move", "edge_move")),
            sum(a[2] for a in actions if a[0] in ("move", "edge_move")))


class EdgeScroll(unittest.TestCase):
    def tap_drag_to_bottom(self, **cfg):
        """Tap, touch again, drag down to the bottom edge and hold there."""
        p = Pad(**cfg)
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.frame({}, dt=60)
        p.stroke([(50, 30)], (0, H - 1.0 - 30), 20)   # 1 mm from the edge
        self.assertIn(("button", "left", True), p.actions)
        return p

    def held_moves(self, p, ms):
        p.actions = []
        p.wait(ms)
        return moves(p.actions)

    def test_held_at_bottom_scrolls_down_at_once(self):
        p = self.tap_drag_to_bottom()
        m = self.held_moves(p, 500)
        self.assertGreater(len(m), 30)
        # wheel down (negative), nothing sideways, from the first tick on
        self.assertTrue(all(v < 0 and h == 0 for _, v, h in m))
        self.assertIn(p.actions[0][0], ("edge_scroll", "edge_move"))
        # 5 notches/s at the edge, 1 mm short of it is a little less
        notches = -sum(v for _, v, _ in m) / 120
        self.assertTrue(1.8 < notches < 3.0, notches)
        self.assertIsNotNone(p.e.next_timeout(p.t))
        # the pointer itself stays put: only +-1 px wiggles
        self.assertIn(net_move(p.actions)[1], (0.0, 1.0))

    def test_scroll_ignores_natural_scroll(self):
        p = self.tap_drag_to_bottom(natural_scroll=False)
        self.assertTrue(all(v < 0 for _, v, _ in self.held_moves(p, 200)))

    def test_speeds_up_while_held(self):
        p = self.tap_drag_to_bottom()
        first = -sum(v for _, v, _ in self.held_moves(p, 500))
        self.held_moves(p, 1500)
        later = -sum(v for _, v, _ in self.held_moves(p, 500))
        self.assertGreater(later, first * 1.6)

    def test_wiggle_is_undone_on_stop(self):
        p = self.tap_drag_to_bottom()
        p.actions = []
        for ms in (60, 70, 130):
            p.wait(ms)
            p.stroke([(50, H - 1.0)], (0, -15), 3)   # off the edge
            p.stroke([(50, H - 16.0)], (0, 15), 3)   # and back
        p.stroke([(50, H - 1.0)], (0, -15), 3)
        wiggles = [a for a in p.actions if a[0] == "edge_move"
                   and abs(a[2]) == 1.0]
        self.assertTrue(wiggles)
        self.assertEqual(sum(a[2] for a in wiggles), 0.0)

    def test_pulling_back_slightly_stops(self):
        p = self.tap_drag_to_bottom()
        p.stroke([(50, H - 1.0)], (0, -1.5), 3)     # still inside the zone
        self.assertEqual(self.held_moves(p, 300), [])
        # pushing down again resumes
        p.stroke([(50, H - 2.5)], (0, 2.0), 3)
        self.assertTrue(self.held_moves(p, 200))

    def test_leaving_the_zone_stops(self):
        p = self.tap_drag_to_bottom()
        p.stroke([(50, H - 1.0)], (0, -15), 10)
        self.assertEqual(self.held_moves(p, 300), [])

    def test_lift_ends_drag_and_scroll(self):
        p = self.tap_drag_to_bottom()
        p.lift()
        p.wait(300)
        self.assertIn(("button", "left", False), p.actions)
        self.assertEqual(self.held_moves(p, 300), [])
        self.assertIsNone(p.e.next_timeout(p.t))

    def test_top_left_corner(self):
        p = Pad()
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.frame({}, dt=60)
        p.stroke([(50, 30)], (-49.5, -29.5), 25)
        m = self.held_moves(p, 300)
        self.assertTrue(m)
        # wheel up, and left on the horizontal wheel
        self.assertTrue(all(v > 0 and h < 0 for _, v, h in m))

    def test_click_and_drag(self):
        p = Pad()
        p.stroke([(50, 30)], (0, 0), 3, button=True)
        p.stroke([(50, 30)], (0, H - 1.0 - 30), 20, button=True)
        m = self.held_moves(p, 300)
        self.assertTrue(m and all(v < 0 for _, v, _ in m))
        p.stroke([(50, H - 1.0)], (0, 0), 1, button=False)   # release
        self.assertEqual(self.held_moves(p, 300), [])

    def test_plain_pointer_motion_does_not_scroll(self):
        p = Pad()
        p.stroke([(50, 30)], (0, H - 1.0 - 30), 20)
        self.assertEqual(self.held_moves(p, 300), [])

    def test_drag_that_starts_at_the_edge_waits(self):
        p = Pad()
        p.stroke([(50, H - 3)], (0, 0), 3, button=True)
        p.stroke([(50, H - 3)], (5, 0), 5, button=True)   # sideways along it
        self.assertEqual(self.held_moves(p, 300), [])

    def test_two_fingers_do_not_scroll(self):
        p = self.tap_drag_to_bottom()
        p.frame({0: (50, H - 1.0, False), 1: (70, 40, False)})
        self.assertEqual(self.held_moves(p, 300), [])

    def test_can_be_turned_off(self):
        p = self.tap_drag_to_bottom(edge_scroll=False)
        self.assertEqual(self.held_moves(p, 300), [])

    def gestures_pad(self):
        """A Pad configured like the daemon does in gestures mode."""
        if daemon is None:
            self.skipTest("python3-evdev not installed")
        p = Pad(mode="gestures")
        daemon.gestures_only(p.e.cfg)
        return p, daemon.GestureGate()

    def test_gestures_mode_libinput_tap_drag(self):
        p, gate = self.gestures_pad()
        out = []
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.frame({}, dt=100)        # within libinput's 180 ms
        p.stroke([(20, 30)], (0, H - 1.0 - 30), 20)   # lands elsewhere: fine
        out += gate.frame(p.actions, p.e.finger_count(), {0: 1})
        # libinput does the clicking and dragging: winptp sends no buttons
        self.assertEqual([a for a in p.actions if a[0] == "button"], [])
        p.actions = []
        p.wait(300)
        out += gate.tick(p.actions)
        self.assertTrue(moves(out))
        self.assertTrue(all(v < 0 for _, v, _ in moves(out)))

    def test_gestures_mode_click_drag(self):
        p, gate = self.gestures_pad()
        p.stroke([(50, 30)], (0, 0), 3, button=True)
        p.stroke([(50, 30)], (0, H - 1.0 - 30), 20, button=True)
        p.actions = []
        p.wait(300)
        self.assertTrue(moves(gate.tick(p.actions)))

    def test_gestures_mode_slow_second_touch_is_not_a_drag(self):
        p, gate = self.gestures_pad()
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.frame({}, dt=230)        # libinput's tap timeout has passed
        p.stroke([(50, 30)], (0, H - 1.0 - 30), 20)
        self.assertEqual(self.held_moves(p, 300), [])

    def test_gestures_mode_one_finger_taps_send_nothing(self):
        p, gate = self.gestures_pad()
        for _ in range(2):
            p.stroke([(50, 30)], (0, 0), 3)
            p.lift()
            p.frame({}, dt=60)
        p.wait(400)
        self.assertEqual([a for a in p.actions if a[0] == "button"], [])

if __name__ == "__main__":
    unittest.main()
