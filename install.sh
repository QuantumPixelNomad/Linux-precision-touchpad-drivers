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
echo uinput | sudo tee /etc/modules-load.d/winptp.conf >/dev/null

# The virtual pointer already carries Windows ballistics: tell KWin not to
# accelerate it again (flat profile, speed 0, scroll factor 1).
for kv in PointerAccelerationProfile=1 PointerAcceleration=0 ScrollFactor=1; do
  sudo -u "$user" kwriteconfig6 --file kcminputrc --group Libinput --group 7531 \
    --group 20564 --group "winptp pointer" --key "${kv%%=*}" "${kv#*=}"
done

sudo systemctl daemon-reload
sudo systemctl enable winptp-resume.service
sudo systemctl enable --now winptp.service
sleep 1
systemctl --no-pager --lines=5 status winptp.service || true
echo "winptp installed. Settings: /etc/winptp/winptp.toml"
