#!/bin/sh
# Build dist/winptp_<version>_all.deb. Needs only dpkg-deb.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$here/winptp/__init__.py")"
root="$(mktemp -d)"
chmod 755 "$root"
trap 'rm -rf "$root"' EXIT

install -d "$root/DEBIAN" "$root/opt/winptp/winptp" "$root/etc/winptp" \
  "$root/usr/lib/systemd/system" "$root/usr/share/doc/winptp" "$root/usr/bin" \
  "$root/usr/share/applications" "$root/usr/share/polkit-1/actions"
install -m 644 "$here"/winptp/*.py "$root/opt/winptp/winptp/"
install -m 644 "$here/packaging/winptp.toml" "$root/etc/winptp/"
install -m 644 "$here/packaging/winptp.service" "$here/packaging/winptp-resume.service" \
  "$root/usr/lib/systemd/system/"
install -m 755 "$here/packaging/winptp-apply" "$root/opt/winptp/"
install -m 755 "$here/packaging/winptp-settings" "$root/usr/bin/"
install -m 644 "$here/packaging/winptp-settings.desktop" "$root/usr/share/applications/"
install -m 644 "$here/packaging/io.github.quantumpixelnomad.winptp.policy" \
  "$root/usr/share/polkit-1/actions/"
install -m 644 "$here/README.md" "$root/usr/share/doc/winptp/"
install -m 644 "$here/LICENSE" "$root/usr/share/doc/winptp/copyright"
sed "s/@VERSION@/$version/" "$here/packaging/debian/control.in" > "$root/DEBIAN/control"
install -m 644 "$here/packaging/debian/conffiles" "$root/DEBIAN/"
install -m 755 "$here/packaging/debian/postinst" "$here/packaging/debian/prerm" \
  "$here/packaging/debian/postrm" "$root/DEBIAN/"

mkdir -p "$here/dist"
dpkg-deb --root-owner-group --build "$root" "$here/dist/winptp_${version}_all.deb"
