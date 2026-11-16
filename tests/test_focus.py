"""The focus helper: D-Bus wire format, the Set() call KWin's script
makes, and the daemon reading the focused app back."""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from winptp import focus  # noqa: E402


class TestWire(unittest.TestCase):
    def test_round_trip(self):
        msg = focus.encode(focus.METHOD_CALL, 7,
                           {focus.F_PATH: "/", focus.F_MEMBER: "Set",
                            focus.F_INTERFACE: focus.INTERFACE, focus.F_DEST: "x.y"},
                           "su", ("pdfcraft", 4))
        self.assertEqual(focus.message_length(msg + b"junk"), len(msg))
        kind, flags, serial, fields, body = focus.decode(msg)
        self.assertEqual((kind, flags, serial), (focus.METHOD_CALL, 0, 7))
        self.assertEqual(fields[focus.F_MEMBER], "Set")
        self.assertEqual(fields[focus.F_SIG], "su")
        self.assertEqual(body, ["pdfcraft", 4])

    def test_short_header(self):
        self.assertIsNone(focus.message_length(b"l\1\0\1"))

    def test_kwin_script_reports_app_ids(self):
        s = focus.KWIN_SCRIPT
        self.assertIn("workspace.windowActivated.connect", s)
        self.assertIn("desktopFileName", s)
        self.assertIn('"Set"', s)
        self.assertIn(focus.BUS_NAME, s)

    def test_load_script_calls(self):
        calls = []

        class R:
            returncode = 0

        def run(*a):
            calls.append(a[0])
            return R()
        self.assertTrue(focus.load_kwin_script("/x.js", run))
        self.assertEqual(calls, ["unloadScript", "loadScript", "start"])


@unittest.skipUnless(shutil.which("dbus-daemon"), "needs dbus-daemon")
class TestBus(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        sock = os.path.join(self.dir, "bus")
        self.daemon = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--address=unix:path=" + sock],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if os.path.exists(sock):
                break
            time.sleep(0.02)
        self.addr = "unix:path=" + sock
        self.env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=self.addr)

    def tearDown(self):
        self.daemon.terminate()
        self.daemon.wait()
        shutil.rmtree(self.dir)

    def test_set_writes_focus_file(self):
        server = focus.Bus(os.path.join(self.dir, "bus"))
        got = server.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "RequestName", "su",
                          (focus.BUS_NAME, 4))
        self.assertEqual(got, [1])
        path = os.path.join(self.dir, "winptp-focus")
        seen = []
        self.addCleanup(server.sock.close)

        def run():
            try:
                focus.serve(server, path, seen.append)
            except OSError:
                pass            # bus closed at tearDown
        threading.Thread(target=run, daemon=True).start()

        client = focus.Bus(os.path.join(self.dir, "bus"))
        self.addCleanup(client.sock.close)
        client.call(focus.BUS_NAME, "/", focus.INTERFACE, "Set", "s",
                    ("pdfcraft_pdfcraft pdfcraft pdfcraft",))
        with open(path) as f:
            self.assertEqual(f.read(), "pdfcraft_pdfcraft pdfcraft pdfcraft")
        self.assertEqual(seen, ["pdfcraft_pdfcraft pdfcraft pdfcraft"])
        with self.assertRaises(OSError):     # unknown methods get an error
            client.call(focus.BUS_NAME, "/", focus.INTERFACE, "Nope")

        if shutil.which("dbus-send"):        # an independent client
            subprocess.run(["dbus-send", "--session", "--print-reply",
                            "--dest=" + focus.BUS_NAME, "/",
                            focus.INTERFACE + ".Set", "string:firefox_firefox firefox"],
                           env=self.env, check=True, capture_output=True, timeout=5)
            with open(path) as f:
                self.assertEqual(f.read(), "firefox_firefox firefox")


class TestDaemonReadsFocus(unittest.TestCase):
    def test_newest_session_wins(self):
        try:
            from winptp import daemon
        except ImportError:
            self.skipTest("needs python3-evdev")
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        self.assertEqual(daemon.focused_app(os.path.join(root, "missing")), "")
        for uid, app, t in (("1000", "PdfCraft_pdfcraft", 200), ("1001", "firefox", 100)):
            os.makedirs(os.path.join(root, uid))
            p = os.path.join(root, uid, "winptp-focus")
            with open(p, "w") as f:
                f.write(app + "\n")
            os.utime(p, (t, t))
        self.assertEqual(daemon.focused_app(root), "pdfcraft_pdfcraft")


if __name__ == "__main__":
    unittest.main()
