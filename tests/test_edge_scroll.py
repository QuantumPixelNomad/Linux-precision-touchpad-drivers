"""Edge auto-scroll: a drag held at the pad's edge keeps the pointer going."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from test_engine import Pad  # noqa: E402

H = 67.0   # default pad height (mm)


def moves(actions):
    return [a for a in actions if a[0] == "move"]


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

    def test_held_at_bottom_keeps_moving_down(self):
        p = self.tap_drag_to_bottom()
        m = self.held_moves(p, 500)
        self.assertGreater(len(m), 30)
        self.assertTrue(all(dy > 0 and dx == 0 for _, dx, dy in m))
        # 800 px/s at the very edge, 1 mm from it is a little less
        self.assertTrue(250 < sum(dy for _, _, dy in m) < 420)
        self.assertIsNotNone(p.e.next_timeout(p.t))

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

    def test_top_and_sides(self):
        p = Pad()
        p.stroke([(50, 30)], (0, 0), 5)
        p.lift()
        p.frame({}, dt=60)
        p.stroke([(50, 30)], (-49.5, -29.5), 25)    # top-left corner
        m = self.held_moves(p, 300)
        self.assertTrue(m)
        self.assertTrue(all(dx < 0 and dy < 0 for _, dx, dy in m))

    def test_click_and_drag(self):
        p = Pad()
        p.stroke([(50, 30)], (0, 0), 3, button=True)
        p.stroke([(50, 30)], (0, H - 1.0 - 30), 20, button=True)
        m = self.held_moves(p, 300)
        self.assertTrue(m and all(dy > 0 for _, _, dy in m))
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

    def test_off_in_gestures_mode(self):
        p = Pad(mode="gestures")
        p.stroke([(50, 30)], (0, 0), 3, button=True)
        p.stroke([(50, 30)], (0, H - 1.0 - 30), 20, button=True)
        self.assertEqual(self.held_moves(p, 300), [])


if __name__ == "__main__":
    unittest.main()
