#!/bin/sh
# Install winptp as a system service. Run as your normal user; uses sudo.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
user="${SUDO_USER:-$USER}"

sudo apt-get install -y python3-evdev >/dev/null
sudo install -d /opt/winptp/winptp /etc/winptp
sudo install -m 644 "$here"/winptp/*.py /opt/winptp/winptp/
[ -f /etc/winptp/winptp.toml ] || sudo install -m 644 "$here/packaging/winptp.toml" /etc/winptp/
sudo install -m 644 "$here/packaging/winptp.service" "$here/packaging/winptp-resume.service" /etc/systemd/system/

# Settings app (Qt). Optional: the daemon runs without it.
sudo apt-get install -y python3-pyside6.qtwidgets >/dev/null 2>&1 \
  || echo "python3-pyside6.qtwidgets not available; the settings app needs PySide6 or PyQt6"
sudo install -m 755 "$here/packaging/winptp-apply" /opt/winptp/
sudo install -m 755 "$here/packaging/winptp-settings" /usr/local/bin/
sudo install -D -m 644 "$here/packaging/winptp-settings.desktop" \
  /usr/local/share/applications/winptp-settings.desktop
sudo install -D -m 644 "$here/packaging/io.github.quantumpixelnomad.winptp.policy" \
  /usr/share/polkit-1/actions/io.github.quantumpixelnomad.winptp.policy
echo uinput | sudo tee /etc/modules-load.d/winptp.conf >/dev/null

# The virtual pointer already carries Windows ballistics: tell KWin not to
# accelerate it again (flat profile, speed 0, scroll factor 1).
for kv in PointerAccelerationProfile=1 PointerAcceleration=0 ScrollFactor=1; do
  sudo -u "$user" kwriteconfig6 --file kcminputrc --group Libinput --group 7531 \
    --group 20564 --group "winptp pointer" --key "${kv%%=*}" "${kv#*=}"
done

# Pinch zoom reaches apps through the virtual "winptp touchpad". The
# touchpad on/off key (or Plasma's touchpad toggle) switches off every
# touchpad, this one included, and KWin remembers it: keep it on.
sudo -u "$user" kwriteconfig6 --file kcminputrc --group Libinput --group 7531 \
  --group 20566 --group "winptp touchpad" --key Enabled true

sudo systemctl daemon-reload
sudo systemctl enable winptp-resume.service
sudo systemctl enable --now winptp.service
sudo systemctl restart winptp.service
sleep 1
# and switch it on in the running session too
for d in /sys/class/input/event*; do
  if [ "$(cat "$d/device/name" 2>/dev/null)" = "winptp touchpad" ]; then
    sudo -u "$user" XDG_RUNTIME_DIR="/run/user/$(id -u "$user")" busctl --user \
      set-property org.kde.KWin "/org/kde/KWin/InputDevice/${d##*/}" \
      org.kde.KWin.InputDevice enabled b true 2>/dev/null || true
  fi
done
systemctl --no-pager --lines=5 status winptp.service || true
echo "winptp installed. Settings: run winptp-settings, or edit /etc/winptp/winptp.toml"
