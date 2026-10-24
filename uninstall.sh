#!/bin/sh
# Remove winptp and hand the touchpad back to plain libinput.
set -eu
sudo systemctl disable --now winptp.service winptp-resume.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/winptp.service /etc/systemd/system/winptp-resume.service \
  /etc/modules-load.d/winptp.conf
sudo rm -rf /opt/winptp
sudo rm -f /usr/local/bin/winptp-settings /usr/local/share/applications/winptp-settings.desktop \
  /usr/share/polkit-1/actions/io.github.quantumpixelnomad.winptp.policy
sudo systemctl daemon-reload
echo "winptp removed (settings kept in /etc/winptp)."
