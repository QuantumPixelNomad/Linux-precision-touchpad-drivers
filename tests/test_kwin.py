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


class KwinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        add_dev(self.root, "event12", "SYN3000:00 06CB:7F28 Touchpad",
                "i2c-SYN3000:00", "06cb", "7f28")
        add_dev(self.root, "event15", "winptp touchpad", "py-evdev-uinput",
                "1d6b", "5056")
        add_dev(self.root, "event16", "winptp pointer", "py-evdev-uinput",
                "1d6b", "5054")

    def tearDown(self):
        self.tmp.cleanup()

    def test_gestures_copy_is_switched_on_under_the_pads_own_ids(self):
        add_dev(self.root, "event18", "SYN3000:00 06CB:7F28 Touchpad",
                "winptp/passthrough", "06cb", "7f28")
        ran = []
        pads = kwin.enable(want_passthrough=True, sysfs=self.root, run=ran.append)
        self.assertEqual([p[0] for p in pads], ["event15", "event18"])
        self.assertIn(["kwriteconfig6", "--file", "kcminputrc", "--group", "Libinput",
                       "--group", "1739", "--group", "32552", "--group",
                       "SYN3000:00 06CB:7F28 Touchpad", "--key", "Enabled", "true"], ran)
        self.assertIn(["busctl", "--user", "set-property", "org.kde.KWin",
                       "/org/kde/KWin/InputDevice/event18",
                       "org.kde.KWin.InputDevice", "enabled", "b", "true"], ran)

    def test_real_pad_and_pointer_are_left_alone(self):
        ran = []
        kwin.enable(sysfs=self.root, run=ran.append)
        flat = [" ".join(c) for c in ran]
        self.assertTrue(any("winptp touchpad" in c for c in flat))
        self.assertFalse(any("event12" in c or "32552" in c or "pointer" in c
                             for c in flat))

    def test_missing_tools_do_not_raise(self):
        def run(c):
            raise FileNotFoundError(c[0])
        kwin.enable(sysfs=self.root, run=run)

    def test_waits_for_the_copy_after_a_restart(self):
        ran = []
        pads = kwin.enable(wait=0.3, want_passthrough=True, sysfs=self.root,
                           run=ran.append)
        self.assertEqual(len(pads), 1)      # gave up, still did the pinch pad
        self.assertTrue(ran)


if __name__ == "__main__":
    unittest.main()
