#!/bin/bash
# Rebuild the .deb from current release sources.
# Usage: ./build-deb.sh   (no root needed to build; sudo only to install)
set -e
cd "$(dirname "$0")"
PKGDIR="packaging/focus-reminder_1.0_all"
cp focus-reminder-ubuntu.py "$PKGDIR/opt/focus-reminder/focus-reminder.py"
cp assets/focus-reminder.png "$PKGDIR/usr/share/icons/hicolor/48x48/apps/focus-reminder.png"
python3 -m py_compile "$PKGDIR/opt/focus-reminder/focus-reminder.py"
dpkg-deb --build "$PKGDIR"
echo "Built: packaging/focus-reminder_1.0_all.deb"
echo "Install: sudo dpkg -i packaging/focus-reminder_1.0_all.deb"
