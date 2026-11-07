#!/bin/sh
# Start the UI services only after GNOME has created the user's display.
set -eu

systemctl --user import-environment \
  DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS \
  XDG_RUNTIME_DIR XDG_SESSION_TYPE >/dev/null 2>&1 || true
# This appends/enables the system extension without replacing the user's
# existing GNOME extensions. On later boots Shell loads it before autostart.
gnome-extensions enable focus-lockout@medisparkbd >/dev/null 2>&1 || true

state_file="$HOME/.local/share/focus-reminder/suspend.state"
overlay_flag="$HOME/.local/share/focus-reminder/shell-lockout-active"
if [ -r "$state_file" ]; then
  end_ts=$(cat "$state_file" 2>/dev/null || true)
  now=$(date +%s)
  if printf '%s\n' "$end_ts" | awk -v now="$now" \
      '$0 ~ /^[0-9]+([.][0-9]+)?$/ && ($0 + 0) > now {active=1} END {exit !active}'; then
    # Hold later GNOME autostarts until the shell overlay confirms its modal
    # grab. The GTK app remains the fallback if the extension cannot start.
    tries=0
    while [ "$tries" -lt 30 ]; do
      if [ -f "$overlay_flag" ] \
         && [ "$(stat -c %Y "$overlay_flag" 2>/dev/null || echo 0)" -ge "$now" ]; then
        break
      fi
      sleep 0.1
      tries=$((tries + 1))
    done
  fi
fi

# Start the main UI in this GNOME autostart process. GNOME 50 no longer
# honors the old startup-phase barrier, and starting this through the user
# manager could leave only the stale --show-now launcher visible at login.
exec /usr/bin/python3 /opt/focus-reminder/focus-reminder.py
