"""winptp-settings: a small Qt window for /etc/winptp/winptp.toml.

Run as your normal user. Apply hands the new file to the winptp-apply
helper through pkexec (one admin prompt), which installs it and restarts
winptp.service. Comments and unknown keys in the file are kept.

The TOML helpers at the top need no Qt, so they can be tested headless.
"""

import dataclasses
import os
import re
import subprocess
import sys
import tomllib

from .engine import Config

CONFIG_PATH = "/etc/winptp/winptp.toml"
APPLY_HELPER = "/opt/winptp/winptp-apply"
SERVICE = "winptp.service"

# Names the daemon's combo() understands (daemon.KEYS, without evdev).
KEY_NAMES = ({"meta", "super", "ctrl", "alt", "shift", "tab", "space", "left",
              "right", "up", "down", "esc", "enter", "pageup", "pagedown"}
             | set("abcdefghijklmnopqrstuvwxyz")
             | {"f%d" % i for i in range(1, 13)})

TAP_PRESETS = [
    ("middle", "Middle click"),
    ("right", "Right click"),
    ("meta+alt+n", "Notifications (Meta+Alt+N)"),
    ("meta+w", "Overview"),
    ("none", "Nothing"),
]
SWIPE_PRESETS = [
    ("meta+w", "Overview"),
    ("meta+d", "Show desktop"),
    ("alttab", "App switcher (Alt+Tab)"),
    ("meta+ctrl+right", "Next desktop"),
    ("meta+ctrl+left", "Previous desktop"),
    ("meta+alt+n", "Notifications"),
    ("none", "Nothing"),
]

DPI_SCALES = [100, 125, 150, 175, 200]


def default_config():
    return dataclasses.asdict(Config())


def read_config(path=CONFIG_PATH):
    """Defaults overlaid with the file's values; plus the raw file text."""
    values = default_config()
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for k, v in tomllib.loads(text).items():
            if k in values:
                values[k] = v
    return values, text


def valid_action(spec, taps=False):
    """True if the daemon can perform spec without crashing."""
    spec = spec.strip().lower()
    if spec == "alttab":
        return not taps            # only swipes can hold Alt for the switcher
    if spec in ("none", "left", "right", "middle"):
        return True                # mouse buttons (engine._action)
    return bool(spec) and all(p.strip() in KEY_NAMES for p in spec.split("+"))


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (tuple, list)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % s


_LINE = re.compile(r'^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)("(?:[^"\\]|\\.)*"|[^#]*?)(\s*#.*)?$')


def render_config(text, changes):
    """Return text with each key in changes set, keeping comments and order.

    Keys already in the file are rewritten in place (trailing comment kept);
    the rest are appended at the end.
    """
    pending = dict(changes)
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m and m.group(2) in pending:
            indent, key, eq, _, comment = m.groups()
            value = toml_value(pending.pop(key))
            line = indent + key + eq + value
            if comment:
                # keep the comment's column when there is room
                col = m.start(5) + len(comment) - len(comment.lstrip())
                line += " " * max(1, col - len(line)) + comment.lstrip()
        out.append(line)
    if pending:
        if out and out[-1].strip():
            out.append("")
        out.extend("%s = %s" % (k, toml_value(v)) for k, v in pending.items())
    result = "\n".join(out) + "\n"
    tomllib.loads(result)  # never hand the daemon a broken file
    return result


def changed_keys(values, text):
    """Settings worth writing: already in the file, or not at their default."""
    in_file = tomllib.loads(text) if text else {}
    defaults = default_config()
    return {k: v for k, v in values.items()
            if k in in_file or v != defaults[k]}


def apply_config(new_text):
    """Install new_text as the config and restart the service.

    Returns (ok, message).
    """
    cmd = [APPLY_HELPER] if os.geteuid() == 0 else ["pkexec", APPLY_HELPER]
    try:
        r = subprocess.run(cmd, input=new_text.encode(), capture_output=True,
                           timeout=120)
    except FileNotFoundError as exc:
        return False, "Could not run %s: %s" % (cmd[0], exc)
    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for the admin prompt."
    if r.returncode == 126 or r.returncode == 127:
        return False, "Not authorised, settings were not changed."
    if r.returncode:
        err = (r.stderr or r.stdout).decode(errors="replace").strip()
        return False, err or "winptp-apply failed (exit %d)." % r.returncode
    return True, "Saved and restarted winptp."


def service_state():
    try:
        r = subprocess.run(["systemctl", "is-active", SERVICE],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


# ---------------------------------------------------------------- Qt window

def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PyQt6 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def build_window(path=CONFIG_PATH):
    QtCore, QtGui, QtWidgets = _qt()
    Qt = QtCore.Qt

    class ActionBox(QtWidgets.QComboBox):
        """Editable combo: pick a preset or type a key combo like ctrl+alt+t."""

        def __init__(self, presets, taps=False):
            super().__init__()
            self.taps = taps
            self.setEditable(True)
            self.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
            for value, label in presets:
                self.addItem("%s  (%s)" % (label, value), value)
            self.lineEdit().textChanged.connect(self._check)

        def set_value(self, value):
            i = self.findData(value)
            if i >= 0:
                self.setCurrentIndex(i)
            else:
                self.setEditText(value)

        def value(self):
            i = self.findText(self.currentText())
            if i >= 0:
                return self.itemData(i)
            return self.currentText().strip().lower()

        def ok(self):
            return valid_action(self.value(), self.taps)

        def _check(self):
            self.setStyleSheet("" if self.ok() else "QComboBox { color: #c0392b; }")
            self.setToolTip("" if self.ok() else
                            "Not a key combo winptp knows. Use names like "
                            "meta, ctrl, alt, shift, a-z, f1-f12, left, esc "
                            "joined with +.")

    class Window(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Touchpad (winptp)")
            self.setWindowIcon(QtGui.QIcon.fromTheme("input-touchpad"))
            self.fields = {}
            self.values = {}
            self.text = ""

            tabs = QtWidgets.QTabWidget()
            tabs.addTab(self._pointer_tab(), "Pointer")
            tabs.addTab(self._taps_tab(), "Taps")
            tabs.addTab(self._scroll_tab(), "Scroll && zoom")
            tabs.addTab(self._gestures_tab(), "Gestures")

            self.status = QtWidgets.QLabel()
            self.status.setWordWrap(True)
            defaults = QtWidgets.QPushButton(QtGui.QIcon.fromTheme("edit-undo"),
                                             "Defaults")
            defaults.setToolTip("Fill in the winptp defaults (not saved until Apply)")
            defaults.clicked.connect(lambda: self._fill(default_config()))
            reload_ = QtWidgets.QPushButton(QtGui.QIcon.fromTheme("view-refresh"),
                                            "Reload")
            reload_.clicked.connect(self.load)
            self.apply = QtWidgets.QPushButton(QtGui.QIcon.fromTheme("dialog-ok-apply"),
                                               "Apply")
            self.apply.setDefault(True)
            self.apply.clicked.connect(self.save)

            buttons = QtWidgets.QHBoxLayout()
            buttons.addWidget(defaults)
            buttons.addWidget(reload_)
            buttons.addStretch()
            buttons.addWidget(self.apply)

            layout = QtWidgets.QVBoxLayout(self)
            layout.addWidget(tabs)
            layout.addWidget(self.status)
            layout.addLayout(buttons)
            self.resize(520, 420)
            self.load()

        # -- tabs

        def _form(self):
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            form.setFieldGrowthPolicy(
                QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            return page, form

        def _check(self, form, key, label, tip=None):
            w = QtWidgets.QCheckBox(label)
            if tip:
                w.setToolTip(tip)
            form.addRow(w)
            self.fields[key] = w

        def _pointer_tab(self):
            page, form = self._form()
            row = QtWidgets.QHBoxLayout()
            speed = QtWidgets.QSlider(Qt.Orientation.Horizontal)
            speed.setRange(1, 20)
            speed.setPageStep(1)
            speed.setTickPosition(QtWidgets.QSlider.TickPosition.TicksBelow)
            num = QtWidgets.QLabel()
            num.setMinimumWidth(24)
            speed.valueChanged.connect(lambda v: num.setText(str(v)))
            row.addWidget(QtWidgets.QLabel("Slow"))
            row.addWidget(speed)
            row.addWidget(QtWidgets.QLabel("Fast"))
            row.addWidget(num)
            form.addRow("Cursor speed:", row)
            self.fields["cursor_speed"] = speed

            dpi = QtWidgets.QComboBox()
            for s in DPI_SCALES:
                dpi.addItem("%d%%" % s, 96.0 * s / 100)
            dpi.setToolTip("Match your display scale in System Settings > Display")
            form.addRow("Display scale:", dpi)
            self.fields["screen_dpi"] = dpi

            palm = QtWidgets.QComboBox()
            for i, label in enumerate(["Off", "Short delay (1)", "Medium (2, Windows default)",
                                       "Long (3)", "Strictest (4)"]):
                palm.addItem(label, i)
            palm.setToolTip("How long taps and movement are ignored after typing")
            form.addRow("Palm check while typing:", palm)
            self.fields["aap_threshold"] = palm
            return page

        def _taps_tab(self):
            page, form = self._form()
            self._check(form, "tap_to_click", "Tap with one finger to click")
            self._check(form, "two_finger_tap", "Tap with two fingers to right-click")
            self._check(form, "tap_and_drag", "Double-tap and drag to multi-select",
                        "Tap, then touch again quickly and move to drag")
            self._check(form, "right_click_zone",
                        "Press the bottom-right corner to right-click")
            for key, label in (("tap_three", "Three-finger tap:"),
                               ("tap_four", "Four-finger tap:")):
                box = ActionBox(TAP_PRESETS, taps=True)
                form.addRow(label, box)
                self.fields[key] = box
            return page

        def _scroll_tab(self):
            page, form = self._form()
            self._check(form, "natural_scroll", "Natural scrolling (content follows fingers)",
                        "Off = Windows \"Down motion scrolls down\" setting")
            self._check(form, "inertia", "Keep scrolling after the fingers lift (inertia)")
            self._check(form, "pinch_zoom", "Pinch to zoom")
            return page

        def _gestures_tab(self):
            page, form = self._form()
            rows = [("swipe3_up", "Three fingers up:"),
                    ("swipe3_down", "Three fingers down:"),
                    ("swipe3_horizontal", "Three fingers sideways:"),
                    ("swipe4_up", "Four fingers up:"),
                    ("swipe4_down", "Four fingers down:"),
                    ("swipe4_left", "Four fingers left:"),
                    ("swipe4_right", "Four fingers right:")]
            for key, label in rows:
                box = ActionBox(SWIPE_PRESETS)
                form.addRow(label, box)
                self.fields[key] = box
            hint = QtWidgets.QLabel("Pick an action or type a key combo such as "
                                    "<i>ctrl+alt+t</i>. <i>alttab</i> holds Alt "
                                    "while your fingers stay down.")
            hint.setWordWrap(True)
            form.addRow(hint)
            return page

        # -- data

        def _fill(self, values):
            for key, w in self.fields.items():
                v = values[key]
                if isinstance(w, QtWidgets.QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, QtWidgets.QSlider):
                    w.setValue(int(v))
                elif isinstance(w, ActionBox):
                    w.set_value(str(v))
                elif key == "screen_dpi":
                    i = w.findData(float(v))
                    if i < 0:
                        w.addItem("%d%% (custom)" % round(float(v) / 0.96), float(v))
                        i = w.count() - 1
                    w.setCurrentIndex(i)
                else:
                    i = w.findData(int(v))
                    w.setCurrentIndex(max(i, 0))

        def _collect(self):
            values = dict(self.values)
            for key, w in self.fields.items():
                if isinstance(w, QtWidgets.QCheckBox):
                    values[key] = w.isChecked()
                elif isinstance(w, QtWidgets.QSlider):
                    values[key] = w.value()
                elif isinstance(w, ActionBox):
                    values[key] = w.value()
                else:
                    values[key] = w.currentData()
            return values

        def load(self):
            try:
                self.values, self.text = read_config(path)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                self.values, self.text = default_config(), ""
                self._status("Could not read %s: %s" % (path, exc), error=True)
            else:
                self._status("Editing %s. winptp is %s." % (path, service_state()))
            self._fill(self.values)

        def save(self):
            bad = [k for k, w in self.fields.items()
                   if isinstance(w, ActionBox) and not w.ok()]
            if bad:
                self._status("Fix the highlighted actions first: %s" % ", ".join(bad),
                             error=True)
                return
            values = self._collect()
            new_text = render_config(self.text, changed_keys(values, self.text))
            if new_text == self.text:
                self._status("Nothing changed.")
                return
            self.apply.setEnabled(False)
            self._status("Applying...")
            QtWidgets.QApplication.processEvents()
            if os.path.abspath(path) == CONFIG_PATH:
                ok, msg = apply_config(new_text)
            else:  # a copy (development): just write it
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_text)
                    ok, msg = True, "Saved %s." % path
                except OSError as exc:
                    ok, msg = False, str(exc)
            self.apply.setEnabled(True)
            if ok:
                self.values, self.text = values, new_text
                if path == CONFIG_PATH:
                    msg += " Service is %s." % service_state()
            self._status(msg, error=not ok)

        def _status(self, msg, error=False):
            self.status.setStyleSheet("color: #c0392b;" if error else "")
            self.status.setText(msg)

    return Window()


def main(argv=None):
    argv = sys.argv if argv is None else argv
    _, _, QtWidgets = _qt()
    app = QtWidgets.QApplication(argv)
    app.setApplicationName("winptp-settings")
    app.setDesktopFileName("winptp-settings")
    path = argv[1] if len(argv) > 1 else CONFIG_PATH
    w = build_window(path)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
