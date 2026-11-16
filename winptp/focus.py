"""Tell the winptp daemon which app has focus (KWin, Plasma 6).

Some apps never get touchpad pinch gestures on Linux: egui/winit apps
such as PdfCraft, because winit only reads pinch gestures on macOS and
iOS. They do zoom with Ctrl+wheel, so for the apps in pinch_wheel_apps
winptp turns a pinch into Ctrl+wheel instead of a native pinch.

The daemon runs as root and cannot ask KWin which window is active, so
this helper runs in the desktop session (autostart): it loads a small
KWin script that reports every window activation over D-Bus, and writes
the window's app id to $XDG_RUNTIME_DIR/winptp-focus, where the daemon
reads it when a pinch starts.

    python3 -m winptp.focus

It speaks just enough of the D-Bus wire protocol to receive those calls,
so it needs nothing beyond the Python standard library.
"""

import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

BUS_NAME = "io.github.quantumpixelnomad.winptp.Focus"
INTERFACE = BUS_NAME
PLUGIN = "winptp-focus"
FOCUS_FILE = "winptp-focus"

KWIN_SCRIPT = """\
function winptpReport(w) {
    var id = w ? [w.desktopFileName, w.resourceClass, w.resourceName].join(" ") : "";
    callDBus("%(name)s", "/", "%(iface)s", "Set", id);
}
workspace.windowActivated.connect(winptpReport);
winptpReport(workspace.activeWindow);
""" % {"name": BUS_NAME, "iface": INTERFACE}


# ------------------------------------------------------------ wire format

def _pad(n, align):
    return (-n) % align


class _Writer:
    def __init__(self):
        self.b = bytearray()

    def align(self, n):
        self.b += b"\0" * _pad(len(self.b), n)

    def byte(self, v):
        self.b.append(v)

    def u32(self, v):
        self.align(4)
        self.b += struct.pack("<I", v)

    def string(self, s):                     # 's' and 'o'
        data = s.encode()
        self.u32(len(data))
        self.b += data + b"\0"

    def sig(self, s):                        # 'g'
        data = s.encode()
        self.byte(len(data))
        self.b += data + b"\0"

    def value(self, sig, v):
        if sig in "so":
            self.string(v)
        elif sig == "g":
            self.sig(v)
        elif sig == "u":
            self.u32(v)
        else:
            raise ValueError(sig)


METHOD_CALL, METHOD_RETURN, ERROR, SIGNAL = 1, 2, 3, 4
NO_REPLY_EXPECTED = 1
# header field code -> signature
FIELDS = {1: "o", 2: "s", 3: "s", 4: "s", 5: "u", 6: "s", 7: "s", 8: "g", 9: "u"}
F_PATH, F_INTERFACE, F_MEMBER, F_ERROR, F_REPLY_SERIAL, F_DEST, F_SENDER, F_SIG = range(1, 9)


def encode(kind, serial, fields, body_sig="", body=()):
    """One little-endian message. fields: {code: value}; body: values of
    body_sig (only s, o, g, u)."""
    bw = _Writer()
    for sig, v in zip(body_sig, body):
        bw.value(sig, v)
    fields = dict(fields)
    if body_sig:
        fields[F_SIG] = body_sig
    hw = _Writer()
    for code, v in sorted(fields.items()):
        hw.align(8)
        hw.byte(code)
        hw.sig(FIELDS[code])
        hw.value(FIELDS[code], v)
    head = struct.pack("<cBBBIII", b"l", kind, 0, 1, len(bw.b), serial, len(hw.b))
    msg = bytearray(head + hw.b)
    msg += b"\0" * _pad(len(msg), 8)
    return bytes(msg + bw.b)


class _Reader:
    def __init__(self, data, pos=0, little=True):
        self.d, self.p = data, pos
        self.e = "<" if little else ">"

    def align(self, n):
        self.p += _pad(self.p, n)

    def byte(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def u32(self):
        self.align(4)
        v = struct.unpack_from(self.e + "I", self.d, self.p)[0]
        self.p += 4
        return v

    def string(self):
        n = self.u32()
        v = self.d[self.p:self.p + n].decode(errors="replace")
        self.p += n + 1
        return v

    def sig(self):
        n = self.byte()
        v = self.d[self.p:self.p + n].decode(errors="replace")
        self.p += n + 1
        return v

    def value(self, sig):
        if sig in "so":
            return self.string()
        if sig == "g":
            return self.sig()
        if sig in "ub":
            return self.u32()
        if sig == "i":
            return struct.unpack(self.e + "i", struct.pack(self.e + "I", self.u32()))[0]
        raise ValueError(sig)


def message_length(buf):
    """Bytes in the first message of buf, or None if the header is short."""
    if len(buf) < 16:
        return None
    e = "<" if buf[0:1] == b"l" else ">"
    body_len, = struct.unpack_from(e + "I", buf, 4)
    fields_len, = struct.unpack_from(e + "I", buf, 12)
    return 16 + fields_len + _pad(16 + fields_len, 8) + body_len


def decode(msg):
    """(kind, flags, serial, {code: value}, [body values]); body values only
    for simple signatures, else []."""
    little = msg[0:1] == b"l"
    r = _Reader(msg, 0, little)
    r.p = 1
    kind, flags, _ = r.byte(), r.byte(), r.byte()
    body_len = r.u32()
    serial = r.u32()
    fields_len = r.u32()
    end = r.p + fields_len
    fields = {}
    while r.p < end:
        r.align(8)
        code = r.byte()
        sig = r.sig()
        try:
            fields[code] = r.value(sig)
        except ValueError:
            return kind, flags, serial, fields, []   # unknown field type
    r.p = end + _pad(end, 8)
    body = []
    sig = fields.get(F_SIG, "")
    try:
        for c in sig:
            body.append(r.value(c))
    except ValueError:
        body = []
    return kind, flags, serial, fields, body


# ------------------------------------------------------------- connection

def bus_address():
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    for part in addr.split(";"):
        if not part.startswith("unix:"):
            continue
        kv = dict(x.split("=", 1) for x in part[5:].split(",") if "=" in x)
        if "path" in kv:
            return kv["path"]
        if "abstract" in kv:
            return "\0" + kv["abstract"]
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    return os.path.join(runtime, "bus")


class Bus:
    def __init__(self, address=None):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(address or bus_address())
        self.buf = b""
        self.serial = 0
        self._auth()
        self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                  "org.freedesktop.DBus", "Hello")

    def _auth(self):
        uid = str(os.getuid()).encode().hex().encode()
        self.sock.sendall(b"\0AUTH EXTERNAL " + uid + b"\r\n")
        line = b""
        while not line.endswith(b"\r\n"):
            chunk = self.sock.recv(256)
            if not chunk:
                raise OSError("bus closed during auth")
            line += chunk
        if not line.startswith(b"OK"):
            raise OSError("bus refused auth: %r" % line)
        self.sock.sendall(b"BEGIN\r\n")

    def send(self, data):
        self.sock.sendall(data)

    def call(self, dest, path, iface, member, sig="", args=()):
        """Call and wait for the reply; returns its body. Other messages
        that arrive meanwhile are dropped (only used at start-up)."""
        self.serial += 1
        serial = self.serial
        self.send(encode(METHOD_CALL, serial, {F_PATH: path, F_INTERFACE: iface,
                                               F_MEMBER: member, F_DEST: dest},
                         sig, args))
        while True:
            kind, _, _, fields, body = self.recv()
            if kind in (METHOD_RETURN, ERROR) and fields.get(F_REPLY_SERIAL) == serial:
                if kind == ERROR:
                    raise OSError("%s: %s" % (fields.get(F_ERROR), body))
                return body

    def recv(self):
        while True:
            n = message_length(self.buf)
            if n is not None and len(self.buf) >= n:
                msg, self.buf = self.buf[:n], self.buf[n:]
                return decode(msg)
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("bus closed")
            self.buf += chunk

    def reply(self, serial, fields, error=None):
        self.serial += 1
        f = {F_REPLY_SERIAL: serial}
        if F_SENDER in fields:
            f[F_DEST] = fields[F_SENDER]
        if error:
            f[F_ERROR] = error
            self.send(encode(ERROR, self.serial, f))
        else:
            self.send(encode(METHOD_RETURN, self.serial, f))


# ----------------------------------------------------------------- helper

def focus_path(runtime=None):
    runtime = runtime or os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    return os.path.join(runtime, FOCUS_FILE)


def write_focus(app, path):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".winptp-focus.", dir=d)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(app[:512])
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def serve(bus, path, on_set=None):
    """Answer Set(s) calls until the bus closes."""
    while True:
        kind, flags, serial, fields, body = bus.recv()
        if kind != METHOD_CALL:
            continue
        if fields.get(F_INTERFACE) in (INTERFACE, None) and fields.get(F_MEMBER) == "Set" \
                and body and isinstance(body[0], str):
            app = body[0].strip()
            write_focus(app, path)
            if on_set:
                on_set(app)
            if not flags & NO_REPLY_EXPECTED:
                bus.reply(serial, fields)
        elif not flags & NO_REPLY_EXPECTED:
            bus.reply(serial, fields, error="org.freedesktop.DBus.Error.UnknownMethod")


def _busctl(*args):
    return subprocess.run(["busctl", "--user", "call", "org.kde.KWin", "/Scripting",
                           "org.kde.kwin.Scripting", *args],
                          capture_output=True, text=True, timeout=10)


def load_kwin_script(script_path, run=_busctl):
    """(Re)load the reporting script into KWin. True on success."""
    run("unloadScript", "s", PLUGIN)
    r = run("loadScript", "ss", script_path, PLUGIN)
    if r.returncode:
        return False
    run("start")
    return True


def main(argv=None):
    path = focus_path()
    try:
        bus = Bus()
        got = bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                       "org.freedesktop.DBus", "RequestName", "su", (BUS_NAME, 4))
    except OSError as exc:
        print("winptp-focus: no session bus (%s)" % exc, file=sys.stderr)
        return 1
    if got and got[0] not in (1, 4):      # primary owner / already owner
        print("winptp-focus: already running", file=sys.stderr)
        return 0

    def stop(*_):
        try:
            os.unlink(path)
        except OSError:
            pass
        _busctl("unloadScript", "s", PLUGIN)
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    script = os.path.join(os.path.dirname(path), "winptp-focus.js")
    with open(script, "w", encoding="utf-8") as f:
        f.write(KWIN_SCRIPT)
    # KWin may still be starting at login
    for _ in range(30):
        try:
            if load_kwin_script(script):
                break
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(1)
    else:
        print("winptp-focus: could not load the KWin script", file=sys.stderr)
    try:
        serve(bus, path)
    except OSError:
        pass
    stop()


if __name__ == "__main__":
    sys.exit(main())
