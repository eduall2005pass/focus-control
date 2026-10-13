#!/bin/sh
# First-run setup after `snap install focus-control`.
# Run once as each user who wants it:  focus-control.enable
# Installs persistent per-user services (reminder + tray) using the snap's code.
set -e
UNITDIR="$HOME/.config/systemd/user"
AUTODIR="$HOME/.config/autostart"
mkdir -p "$UNITDIR" "$AUTODIR"
cat > "$UNITDIR/focus-reminder.service" <<'EOF'
[Unit]
Description=Focus Reminder - fullscreen alert (snap)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/snap/bin/focus-control
Restart=always
RestartSec=5
TimeoutStopSec=10

[Install]
WantedBy=default.target
EOF
cat > "$UNITDIR/focus-tray.service" <<'EOF'
[Unit]
Description=Focus Reminder Tray - persistent, no quit (snap)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/snap/bin/focus-control.tray
Restart=always
RestartSec=5
TimeoutStopSec=10

[Install]
WantedBy=default.target
EOF
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR XDG_SESSION_TYPE 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now focus-reminder.service focus-tray.service
echo "Focus Control enabled: reminder + tray will start on every login."
