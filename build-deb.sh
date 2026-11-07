#!/bin/bash
# Rebuild the .deb from current release sources.
# Usage: ./build-deb.sh   (no root needed to build; sudo only to install)
set -e
cd "$(dirname "$0")"
PKGDIR="packaging/focus-reminder"
VERSION="$(sed -n 's/^Version: //p' "$PKGDIR/DEBIAN/control")"
DEB_OUT="packaging/focus-reminder_${VERSION}_all.deb"
cp focus-reminder-ubuntu.py "$PKGDIR/opt/focus-reminder/focus-reminder.py"
cp assets/focus-reminder.png "$PKGDIR/usr/share/icons/hicolor/48x48/apps/focus-reminder.png"
python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' \
  "$PKGDIR/opt/focus-reminder/focus-reminder.py"
find "$PKGDIR" -type d -name __pycache__ -prune -exec rm -rf {} +
dpkg-deb --root-owner-group --compression=gzip --build "$PKGDIR" "$DEB_OUT"
echo "Built: $DEB_OUT"
echo "Install: sudo dpkg -i $DEB_OUT"
