"""winptp's virtual touchpads are switched back on in KWin.

Regression: the touchpad on/off key leaves Enabled=false in kcminputrc
for the real pad; gestures mode's copy of the pad shares its name and
IDs, so after switching to Plasma default the pointer was dead while
winptp's gestures still worked.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from winptp import kwin  # noqa: E402


def add_dev(root, ev, name, phys, vendor, product):
    d = os.path.join(root, ev, "device")
    os.makedirs(os.path.join(d, "id"))
    for path, text in (("name", name), ("phys", phys),
                       ("id/vendor", vendor), ("id/product", product)):
        with open(os.path.join(d, path), "w") as f:
            f.write(text + "\n")


class FakeKWin:
    """busctl/kwriteconfig6 stand-in. A device KWin has not added yet
    (`late` get-property calls) answers with an error, like the real
    one right after the service restarted."""

    def __init__(self, late=0, enabled=False):
        self.late = late
        self.enabled = enabled
        self.ran = []

    def __call__(self, c):
        self.ran.append(c)
        if c[0] != "busctl":
            return 0, ""
        if self.late:
            if c[2] == "get-property":
                self.late -= 1
            return 1, ""
        if c[2] == "set-property":
            self.enabled = True
            return 0, ""
        return 0, "b %s\n" % ("true" if self.enabled else "false")


class KwinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        add_dev(self.root, "event8", "SYN3000:00 06CB:7F28 Touchpad",
                "i2c-SYN3000:00", "06cb", "7f28")
        add_dev(self.root, "event15", "winptp touchpad", "py-evdev-uinput",
                "1d6b", "5056")
        add_dev(self.root, "event14", "winptp pointer", "py-evdev-uinput",
                "1d6b", "5054")
        self.t = 0.0

    def tearDown(self):
        self.tmp.cleanup()

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def enable(self, kw, **kv):
        return kwin.enable(sysfs=self.root, run=kw, sleep=self.sleep,
                           clock=self.clock, **kv)

    def add_copy(self):
        add_dev(self.root, "event13", "SYN3000:00 06CB:7F28 Touchpad",
                "winptp/passthrough", "06cb", "7f28")

    def test_gestures_copy_is_switched_on_under_the_pads_own_ids(self):
        self.add_copy()
        kw = FakeKWin()
        state = self.enable(kw, want_passthrough=True)
        self.assertEqual(state, {"event13": True, "event15": True})
        self.assertIn(["kwriteconfig6", "--file", "kcminputrc", "--group", "Libinput",
                       "--group", "1739", "--group", "32552", "--group",
                       "SYN3000:00 06CB:7F28 Touchpad", "--key", "Enabled", "true"],
                      kw.ran)
        self.assertIn(["busctl", "--user", "set-property", "org.kde.KWin",
                       "/org/kde/KWin/InputDevice/event13",
                       "org.kde.KWin.InputDevice", "enabled", "b", "true"], kw.ran)

    def test_retries_until_kwin_has_added_the_copy(self):
        # regression (0.4.4 on the laptop): the copy was in sysfs but not
        # yet in KWin, the one set-property failed, and KWin then added it
        # with the old saved Enabled=false: dead pointer in Plasma mode
        self.add_copy()
        kw = FakeKWin(late=5)
        state = self.enable(kw, want_passthrough=True)
        self.assertTrue(state["event13"])
        self.assertTrue(kw.enabled)

    def test_already_enabled_is_left_alone(self):
        kw = FakeKWin(enabled=True)
        self.assertEqual(self.enable(kw), {"event15": True})
        self.assertFalse(any(c[2] == "set-property" for c in kw.ran
                             if c[0] == "busctl"))

    def test_gives_up_and_reports_when_kwin_never_answers(self):
        kw = FakeKWin(late=10 ** 6)
        self.assertEqual(self.enable(kw, settle=3.0), {"event15": False})

    def test_real_pad_and_pointer_are_left_alone(self):
        kw = FakeKWin()
        self.enable(kw)
        flat = [" ".join(c) for c in kw.ran]
        self.assertTrue(any("winptp touchpad" in c for c in flat))
        self.assertFalse(any("event8" in c or "32552" in c or "pointer" in c
                             for c in flat))

    def test_missing_tools_do_not_raise(self):
        def run(c):
            raise FileNotFoundError(c[0])
        self.assertEqual(self.enable(run, settle=1.0), {"event15": False})

    def test_waits_for_the_copy_after_a_restart(self):
        kw = FakeKWin()
        state = self.enable(kw, wait=0.5, want_passthrough=True)
        self.assertEqual(state, {"event15": True})   # gave up on the copy


if __name__ == "__main__":
    unittest.main()
