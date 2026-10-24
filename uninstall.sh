#!/bin/sh
# Remove winptp and hand the touchpad back to plain libinput.
set -eu
sudo systemctl disable --now winptp.service winptp-resume.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/winptp.service /etc/systemd/system/winptp-resume.service \
  /etc/modules-load.d/winptp.conf
sudo rm -rf /opt/winptp
sudo systemctl daemon-reload
echo "winptp removed (settings kept in /etc/winptp)."
