#!/bin/bash
# Focus Reminder — system-wide installer for ALL users.
# Run ONCE with sudo from the repo directory:
#     sudo bash install-systemwide.sh
# What it does:
#   1. Installs the app to /opt/focus-reminder/ (world readable/executable)
#   2. Installs the Inter font system-wide (apt package fonts-inter)
#   3. Installs user units to /etc/systemd/user/ + enables for ALL users
#      via default.target.wants symlinks (main + tray, both persistent)
#   4. Installs /etc/xdg/autostart entry that imports the graphical
#      session environment (DISPLAY/Wayland) into each user's manager
# Per-user data (ledger, tasks, suspend state, locks) stays in each
# user's own $HOME, so privacy is preserved and instances never clash.
set -e
cd "$(dirname "$0")"
APP_SRC="focus-reminder-ubuntu.py"
[ -f "$APP_SRC" ] || { echo "ERROR: $APP_SRC not found. Run from repo root."; exit 1; }
[ "$(id -u)" = "0" ] || { echo "ERROR: run with sudo: sudo bash install-systemwide.sh"; exit 1; }

echo "[1/5] Installing app to /opt/focus-reminder/"
install -Dm755 "$APP_SRC" /opt/focus-reminder/focus-reminder.py

echo "[2/6] Installing Inter font system-wide"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fonts-inter
fc-cache -f >/dev/null

echo "[3/6] Installing app icon system-wide"
install -Dm644 "$SRC_DIR/assets/focus-reminder.png" /usr/share/icons/hicolor/48x48/apps/focus-reminder.png
gtk-update-icon-cache -f -q /usr/share/icons/hicolor 2>/dev/null || true

echo "[4/6] Installing user units for ALL users"
cat > /etc/systemd/user/focus-reminder.service <<'EOF'
[Unit]
Description=Focus Reminder - fullscreen alert (all users)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/focus-reminder/focus-reminder.py
Restart=always
RestartSec=5
TimeoutStopSec=10

[Install]
WantedBy=default.target
EOF
cat > /etc/systemd/user/focus-tray.service <<'EOF'
[Unit]
Description=Focus Reminder Tray - persistent, no quit (all users)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/focus-reminder/focus-reminder.py --tray
Restart=always
RestartSec=5
TimeoutStopSec=10

[Install]
WantedBy=default.target
EOF
mkdir -p /etc/systemd/user/default.target.wants
ln -sf /etc/systemd/user/focus-reminder.service /etc/systemd/user/default.target.wants/focus-reminder.service
ln -sf /etc/systemd/user/focus-tray.service /etc/systemd/user/default.target.wants/focus-tray.service

echo "[5/6] Installing XDG autostart (display environment import)"
cat > /etc/xdg/autostart/focus-reminder-env.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Focus Reminder Env
Comment=Import graphical session environment for Focus Reminder (all users)
Exec=/bin/sh -c 'systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR XDG_SESSION_TYPE'
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

echo "[6/6] Installing app-menu entry + verifying"
cat > /usr/share/applications/focus-reminder.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Focus Reminder
Comment=Stay focused: reminders, goals, waste tracking, device suspend
Exec=/usr/bin/python3 /opt/focus-reminder/focus-reminder.py --show-now
Icon=focus-reminder
Terminal=false
Categories=Utility;Productivity;
NoDisplay=false
EOF
update-desktop-database /usr/share/applications 2>/dev/null || true
python3 -m py_compile /opt/focus-reminder/focus-reminder.py && echo "compile OK"
fc-match "Inter" | head -1

echo "DONE. Every user gets Focus Reminder + Tray from their next login."
echo "NOTE: if user 'prof21' still has old per-user units, disable them:"
echo "  systemctl --user disable --now focus-reminder.service focus-tray.service"
echo "  rm ~/.config/autostart/focus-reminder.desktop"
