#!/bin/bash
# Focus Reminder — system-wide installer for ALL users.
# Run ONCE with sudo from the repo directory:
#     sudo bash install-systemwide.sh
# What it does:
#   1. Installs the app to /opt/focus-reminder/ (world readable/executable)
#   2. Installs the Inter font system-wide (apt package fonts-inter)
#   3. Installs user units to /etc/systemd/user/ for every user
#   4. Imports the graphical session environment, then starts both units
#      from XDG autostart after the display is available
# Per-user data (ledger, tasks, suspend state, locks) stays in each
# user's own $HOME, so privacy is preserved and instances never clash.
set -e
cd "$(dirname "$0")"
SRC_DIR="$(pwd)"
APP_SRC="focus-reminder-ubuntu.py"
[ -f "$APP_SRC" ] || { echo "ERROR: $APP_SRC not found. Run from repo root."; exit 1; }
[ "$(id -u)" = "0" ] || { echo "ERROR: run with sudo: sudo bash install-systemwide.sh"; exit 1; }

echo "[1/5] Installing app to /opt/focus-reminder/"
chattr -i /opt/focus-reminder/focus-reminder.py 2>/dev/null || true
trap 'chattr +i /opt/focus-reminder/focus-reminder.py 2>/dev/null || true' EXIT
install -Dm755 "$APP_SRC" /opt/focus-reminder/focus-reminder.py

echo "[2/6] Installing Inter font system-wide"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fonts-inter
fc-cache -f >/dev/null

echo "[2b/6] Removing legacy PAM login gate (valid passwords must remain usable)"
for pamfile in /etc/pam.d/gdm-password /etc/pam.d/gdm-autologin \
               /etc/pam.d/gdm-fingerprint /etc/pam.d/gdm-smartcard \
               /etc/pam.d/gdm-smartcard-sssd-or-password /etc/pam.d/lightdm; do
  [ -f "$pamfile" ] || continue
  sed -i '\|pam_exec.so quiet /usr/local/bin/focus-login-gate.py|d' "$pamfile"
done
rm -f /usr/local/bin/focus-login-gate.py

echo "[2c/6] Restoring the 2.0 boot enforcer and login handoff"
install -Dm755 "$SRC_DIR/packaging/focus-reminder/usr/local/sbin/focus-configure-boot-security" \
  /usr/local/sbin/focus-configure-boot-security
install -Dm755 "$SRC_DIR/packaging/focus-reminder/usr/local/bin/focus-boot-enforce.sh" \
  /usr/local/bin/focus-boot-enforce.sh
install -Dm755 "$SRC_DIR/packaging/focus-reminder/usr/local/bin/focus-pam-lock.sh" \
  /usr/local/bin/focus-pam-lock.sh
install -Dm644 "$SRC_DIR/packaging/focus-reminder/etc/systemd/system/focus-boot-enforce.service" \
  /etc/systemd/system/focus-boot-enforce.service
PAM_LINE="session optional pam_exec.so seteuid /usr/local/bin/focus-pam-lock.sh"
if [ -f /etc/pam.d/common-session ] \
   && ! grep -qF "focus-pam-lock.sh" /etc/pam.d/common-session; then
  printf '%s\n' "$PAM_LINE" >> /etc/pam.d/common-session
fi
systemctl daemon-reload
systemctl enable --now focus-boot-enforce.service

echo "[3/6] Installing app icon system-wide"
install -Dm644 "$SRC_DIR/assets/focus-reminder.png" /usr/share/icons/hicolor/48x48/apps/focus-reminder.png
gtk-update-icon-cache -f -q /usr/share/icons/hicolor 2>/dev/null || true

echo "[4/6] Installing user units for ALL users"
cat > /etc/systemd/user/xdg-desktop-focus-compliance-enforcer-8f2e1a9c.service <<'EOF'
[Unit]
Description=Focus Reminder - fullscreen alert (all users)
PartOf=graphical-session.target
After=graphical-session.target
CollectMode=inactive-or-failed

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/focus-reminder/focus-reminder.py
Restart=always
RestartSec=3
TimeoutStopSec=10

[Install]
WantedBy=graphical-session.target
EOF
cat > /etc/systemd/user/xdg-desktop-focus-compliance-enforcer-8f2e1a9c-tray.service <<'EOF'
[Unit]
Description=Focus Reminder Tray - persistent, no quit (all users)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/focus-reminder/focus-reminder.py --tray
Restart=always
RestartSec=3
TimeoutStopSec=10

[Install]
WantedBy=graphical-session.target
EOF
mkdir -p /etc/systemd/user/default.target.wants
rm -f /etc/systemd/user/default.target.wants/xdg-desktop-focus-compliance-enforcer-8f2e1a9c.service
rm -f /etc/systemd/user/default.target.wants/xdg-desktop-focus-compliance-enforcer-8f2e1a9c-tray.service
for session in gnome ubuntu ubuntu-xorg ubuntu-wayland; do
  rm -f "/etc/systemd/user/gnome-session@${session}.target.d/focus-reminder.conf"
done
systemctl --global enable xdg-desktop-focus-compliance-enforcer-8f2e1a9c.service \
  xdg-desktop-focus-compliance-enforcer-8f2e1a9c-tray.service >/dev/null 2>&1 || true

echo "[5/6] Installing XDG autostart (session environment import + Avro input)"
install -Dm644 "$SRC_DIR/packaging/focus-reminder/usr/share/gnome-shell/extensions/focus-lockout@medisparkbd/metadata.json" \
  /usr/share/gnome-shell/extensions/focus-lockout@medisparkbd/metadata.json
install -Dm644 "$SRC_DIR/packaging/focus-reminder/usr/share/gnome-shell/extensions/focus-lockout@medisparkbd/extension.js" \
  /usr/share/gnome-shell/extensions/focus-lockout@medisparkbd/extension.js
install -Dm755 "$SRC_DIR/packaging/focus-reminder/usr/local/bin/focus-session-start.sh" \
  /usr/local/bin/focus-session-start.sh
install -Dm755 "$SRC_DIR/packaging/focus-reminder/usr/local/bin/focus-tray-start.sh" \
  /usr/local/bin/focus-tray-start.sh
install -Dm644 "$SRC_DIR/packaging/focus-reminder/etc/xdg/autostart/focus-reminder-env.desktop" \
  /etc/xdg/autostart/focus-reminder-env.desktop
install -Dm644 "$SRC_DIR/packaging/focus-reminder/etc/xdg/autostart/focus-reminder-tray.desktop" \
  /etc/xdg/autostart/focus-reminder-tray.desktop

# Remove the old per-user launcher autostart so it cannot show a normal
# reminder on top of an active boot-restored lockout.
for old_autostart in /home/*/.config/autostart/focus-reminder.desktop; do
  [ -L "$old_autostart" ] || continue
  target=$(readlink -f "$old_autostart" 2>/dev/null || true)
  if [ "$target" = /usr/share/applications/focus-reminder.desktop ]; then
    rm -f "$old_autostart"
  fi
done

install -Dm755 "$(dirname "$0")/packaging/focus-reminder/usr/local/bin/ibus-avro-setup.sh" \
  /usr/local/bin/ibus-avro-setup.sh 2>/dev/null || true
cat > /etc/xdg/autostart/ibus-avro.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=IBus (Avro Phonetic)
Comment=Start IBus with Avro so Bengali typing works everywhere, incl. lockout
Exec=/usr/local/bin/ibus-avro-setup.sh
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF

# Keep the application immutable after a legitimate systemwide update.
chattr +i /opt/focus-reminder/focus-reminder.py 2>/dev/null || true
chattr +i /etc/systemd/system/focus-boot-enforce.service 2>/dev/null || true
chattr +i /usr/local/bin/focus-boot-enforce.sh /usr/local/bin/focus-pam-lock.sh 2>/dev/null || true

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
echo "The Reminder and Tray start from the graphical-session hook after display variables are imported."
