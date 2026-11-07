#!/bin/sh
# Start the AppIndicator after the GNOME panel and its indicator extension.
set -u

systemctl --user import-environment \
  DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS \
  XDG_RUNTIME_DIR XDG_SESSION_TYPE >/dev/null 2>&1 || true

# Ubuntu ships this extension to render AppIndicator items in the top panel.
if [ -d /usr/share/gnome-shell/extensions/ubuntu-appindicators@ubuntu.com ]; then
  gnome-extensions enable ubuntu-appindicators@ubuntu.com >/dev/null 2>&1 || true
fi

systemctl --user daemon-reload >/dev/null 2>&1 || true
attempt=0
while [ "$attempt" -lt 10 ]; do
  if systemctl --user start \
      xdg-desktop-focus-compliance-enforcer-8f2e1a9c-tray.service \
      >/dev/null 2>&1; then
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 0.5
done
exit 0
