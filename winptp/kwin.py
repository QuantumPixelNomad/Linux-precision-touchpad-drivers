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

    python3 -m winptp.kwin [--wait SECONDS] [--passthrough]
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


def config_command(pad):
    ev, name, vendor, product, _ = pad
    return ["kwriteconfig6", "--file", "kcminputrc", "--group", "Libinput",
            "--group", str(vendor), "--group", str(product),
            "--group", name, "--key", "Enabled", "true"]


def _prop(ev, *args):
    return ["busctl", "--user", *args, "org.kde.KWin",
            "/org/kde/KWin/InputDevice/" + ev, "org.kde.KWin.InputDevice", "enabled"]


def _run(c):
    r = subprocess.run(c, capture_output=True, text=True, timeout=10)
    return r.returncode, r.stdout


def enable(wait=0.0, want_passthrough=False, settle=10.0, sysfs=SYSFS, run=None,
           sleep=time.sleep, clock=time.monotonic):
    """Switch winptp's pads on. Waits up to `wait` s for them to appear
    (the service may be restarting), and for the gestures-mode copy too
    when want_passthrough. Then keeps switching each one on until KWin
    reports it enabled, for up to `settle` s: a pad that has only just
    appeared is not in KWin yet, and KWin applies the saved (old) setting
    when it adds it. Returns {event: enabled in KWin}."""
    run = run or _run
    end = clock() + wait
    while True:
        pads = winptp_touchpads(sysfs)
        ready = pads and (not want_passthrough or any(p[4] for p in pads))
        if ready or clock() >= end:
            break
        sleep(0.2)

    def call(c):
        try:
            return run(c)
        except (OSError, subprocess.SubprocessError):
            return 1, ""    # no Plasma, or KWin not running

    for pad in pads:
        call(config_command(pad))     # for the next time KWin adds it
    state = {p[0]: False for p in pads}
    end = clock() + settle
    while True:
        for ev in [e for e, on in state.items() if not on]:
            rc, out = call(_prop(ev, "get-property"))
            if rc == 0 and out.split()[-1:] == ["true"]:
                state[ev] = True
            else:
                call(_prop(ev, "set-property") + ["b", "true"])
        if all(state.values()) or clock() >= end:
            return state
        sleep(0.3)


def enable_later(want_passthrough=False):
    """Run enable() in the background (the settings app must not freeze
    while the service restarts)."""
    cmd = [sys.executable, "-m", "winptp.kwin", "--wait", "8"]
    if want_passthrough:
        cmd.append("--passthrough")
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    wait = float(argv[argv.index("--wait") + 1]) if "--wait" in argv else 0.0
    state = enable(wait, want_passthrough="--passthrough" in argv)
    for ev, on in sorted(state.items()):
        print("winptp: %s %s in KWin" % (ev, "enabled" if on else "NOT enabled"))
    return 0 if all(state.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
