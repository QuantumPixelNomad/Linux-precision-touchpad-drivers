"""Tests for the settings app's TOML handling (no Qt needed)."""

import os
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from winptp import settings  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "packaging", "winptp.toml")


class RenderTest(unittest.TestCase):
    def setUp(self):
        with open(SAMPLE, encoding="utf-8") as f:
            self.text = f.read()

    def test_unchanged_round_trip(self):
        values, text = settings.read_config(SAMPLE)
        out = settings.render_config(text, settings.changed_keys(values, text))
        self.assertEqual(out, self.text)

    def test_edit_keeps_comments(self):
        out = settings.render_config(self.text, {"cursor_speed": 14,
                                                 "tap_four": "ctrl+alt+t",
                                                 "natural_scroll": False})
        data = tomllib.loads(out)
        self.assertEqual(data["cursor_speed"], 14)
        self.assertEqual(data["tap_four"], "ctrl+alt+t")
        self.assertIs(data["natural_scroll"], False)
        line = next(l for l in out.splitlines() if l.startswith("cursor_speed"))
        self.assertIn("# Windows CursorSpeed", line)
        self.assertEqual(line.index("#"), 29)
        self.assertEqual(len(out.splitlines()), len(self.text.splitlines()))

    def test_new_keys_appended(self):
        out = settings.render_config("# hi\ncursor_speed = 9\n", {"inertia": False})
        self.assertEqual(out, "# hi\ncursor_speed = 9\n\ninertia = false\n")

    def test_strings_quoted(self):
        out = settings.render_config("", {"tap_four": 'a"b\\c'})
        self.assertEqual(tomllib.loads(out)["tap_four"], 'a"b\\c')

    def test_changed_keys_skips_defaults(self):
        values = settings.default_config()
        values["inertia"] = False
        self.assertEqual(settings.changed_keys(values, ""), {"inertia": False})

    def test_read_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            values, text = settings.read_config(os.path.join(d, "none.toml"))
        self.assertEqual(values, settings.default_config())
        self.assertEqual(text, "")


class ActionTest(unittest.TestCase):
    def test_valid(self):
        for spec in ("meta+w", "Meta+Ctrl+Left", "none", "middle", "f5", "ctrl+alt+t"):
            self.assertTrue(settings.valid_action(spec, taps=True), spec)
        self.assertTrue(settings.valid_action("alttab"))

    def test_invalid(self):
        for spec in ("", "meta+", "hyper+x", "ctrl+1", "alttab"):
            self.assertFalse(settings.valid_action(spec, taps=True), spec)

    def test_key_names_match_daemon(self):
        try:
            from winptp import daemon
        except ImportError:
            self.skipTest("python3-evdev not installed")
        self.assertEqual(settings.KEY_NAMES, set(daemon.KEYS))


if __name__ == "__main__":
    unittest.main()
