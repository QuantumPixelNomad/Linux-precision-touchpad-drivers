"""Keep winptp's virtual touchpads switched on in KWin (Plasma).

KWin remembers per device whether a touchpad is enabled
(~/.config/kcminputrc, [Libinput][vendor][product][name] Enabled=...).
The touchpad on/off key turns every touchpad off, and KWin saves that.
In Windows mode nothing seems wrong, because winptp grabs the real pad,
but:

* gestures mode replays the pad on a copy with the pad's own name and
  IDs (so Plasma's touchpad settings apply to it), so a saved
  Enabled=false for the real pad leaves the pointer dead while winptp's
  own 3/4-finger gestures keep working;
* pinch zoom goes through "winptp touchpad", which the key switches off
  too.

This runs as the desktop user (settings app after Apply, login autostart,
install.sh): it writes Enabled=true for those devices and switches them
on in the running session over D-Bus.

    python3 -m winptp.kwin [--wait SECONDS]
"""

import os
import subprocess
import sys
import time

SYSFS = "/sys/class/input"
PINCH_NAME = "winptp touchpad"
PASSTHROUGH_PHYS = "winptp/passthrough"


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def winptp_touchpads(sysfs=SYSFS):
    """[(event, name, vendor, product, is_passthrough)] of winptp's pads."""
    found = []
    try:
        entries = sorted(os.listdir(sysfs))
    except OSError:
        return found
    for ev in entries:
        if not ev.startswith("event"):
            continue
        dev = os.path.join(sysfs, ev, "device")
        name, phys = _read(os.path.join(dev, "name")), _read(os.path.join(dev, "phys"))
        passthrough = phys == PASSTHROUGH_PHYS
        if not passthrough and name != PINCH_NAME:
            continue
        try:
            vendor = int(_read(os.path.join(dev, "id", "vendor")), 16)
            product = int(_read(os.path.join(dev, "id", "product")), 16)
        except ValueError:
            continue
        found.append((ev, name, vendor, product, passthrough))
    return found


def commands(pads):
    """The kwriteconfig6 and busctl commands that switch pads on."""
    cmds = []
    for ev, name, vendor, product, _ in pads:
        cmds.append(["kwriteconfig6", "--file", "kcminputrc", "--group", "Libinput",
                     "--group", str(vendor), "--group", str(product),
                     "--group", name, "--key", "Enabled", "true"])
        cmds.append(["busctl", "--user", "set-property", "org.kde.KWin",
                     "/org/kde/KWin/InputDevice/" + ev, "org.kde.KWin.InputDevice",
                     "enabled", "b", "true"])
    return cmds


def enable(wait=0.0, want_passthrough=False, sysfs=SYSFS, run=None):
    """Switch winptp's pads on. Waits up to `wait` s for them to appear
    (the service may be restarting), and for the gestures-mode copy too
    when want_passthrough. Returns the pads it found."""
    run = run or (lambda c: subprocess.run(c, capture_output=True, timeout=10))
    end = time.monotonic() + wait
    while True:
        pads = winptp_touchpads(sysfs)
        ready = pads and (not want_passthrough or any(p[4] for p in pads))
        if ready or time.monotonic() >= end:
            break
        time.sleep(0.2)
    for c in commands(pads):
        try:
            run(c)
        except (OSError, subprocess.SubprocessError):
            pass        # no Plasma, or KWin not running: nothing to do
    return pads


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    wait = float(argv[argv.index("--wait") + 1]) if "--wait" in argv else 0.0
    for ev, name, *_ in enable(wait):
        print("winptp: %s (%s) enabled in KWin" % (name, ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
