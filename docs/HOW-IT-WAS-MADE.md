# How winptp was made

## The problem

On Windows, a "precision touchpad" feels the same on almost every laptop.
That is because the touchpad itself only reports raw finger positions, and
Windows does all the interpretation: pointer acceleration, tap detection,
palm rejection, scrolling and gestures. There is no vendor driver to port.

Linux already talks to these touchpads with the same HID protocol (the
kernel's `hid-multitouch` driver), so the hardware side was never the
issue. The difference people notice is in the interpretation layer:
libinput makes different choices about acceleration curves, tap timing,
scroll speed and gestures. winptp replaces that layer with one that makes
Windows' choices.

## The approach

1. **Measure Windows.** We studied how Windows 10 22H2 interprets touchpad
   input: its default touchpad settings, its pointer acceleration curve,
   and the timing and distance thresholds it uses for taps, drags,
   scrolling, pinch and 3/4-finger swipes. The
   [rea](https://github.com/morluto/rea) toolkit helped with the analysis.
   Only these numbers and behaviours were carried over. No Windows code or
   files are part of winptp.

2. **Write a fresh engine.** `winptp/engine.py` is a new implementation in
   plain Python. It is a pure state machine: it gets one frame of finger
   positions (in millimetres) at a time and returns actions such as "move
   the pointer", "scroll", "click" or "press Meta+W". Keeping it free of
   hardware code means every behaviour has a unit test.

3. **Plumb it into Linux.** `winptp/daemon.py` grabs the real touchpad
   with evdev, so libinput stops seeing it, and creates virtual devices
   with uinput: a pointer, a touchpad (so apps get native smooth pinch),
   and a keyboard for gesture shortcuts. KWin is told not to accelerate
   the virtual pointer again, since winptp already applied the Windows
   curve.

4. **Map Windows gestures to Plasma.** Task View becomes Overview, Show
   Desktop stays Show Desktop, the app switcher is Alt+Tab held while the
   fingers are down, and virtual desktop switching uses Plasma's
   shortcuts.

5. **Tune by feel.** A few things could not be measured, such as the
   exact feel of scroll inertia, so those were tuned by hand on real
   hardware. A couple of Windows defaults were deliberately changed after
   daily use: the tap-and-drag window is shorter to avoid accidental
   drags, and the palm check while typing is one level less strict.

## Limits

- Tested on one laptop so far (see the README).
- KDE Plasma only, because the gestures are mapped to KWin shortcuts.
- While winptp runs, Plasma's own touchpad settings page does nothing.

Ideas and fixes are welcome as issues or pull requests.
