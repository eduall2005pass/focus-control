# Focus Control (v1.0) — Whole-Device Focus Protection for Ubuntu

Fullscreen focus reminders, goal manager, typing practice, waste-time
ledger, emergency suspend (minutes / hours / days) with reboot-proof
lockout, and a persistent system tray. Runs automatically for every user.

## Features

- **10-minute reminder** — fullscreen, all workspaces, Back to Work / Goal /
  Emergency Suspend / Power Off in one row, goals preview, PRIORITY badge
- **Goal Manager** — Goal | Date | Time columns, live preview, done toggle, delete
- **Typing Test** — black fullscreen practice inside lockout, saves `.txt` to
  `~/Documents/typing test/`
- **Emergency Suspend** — tray menu or alert button: minutes (1–60), hours
  (1–24), days (1–3, double confirm); system-password required; device
  sleeps, countdown lockout on wake, persists across reboot
- **Waste ledger** — per-day JSON in `~/.local/share/focus-reminder/`,
  idle/lock aware, daily-limit escalation alert
- **Study exemptions** — PDF readers, whiteboards (GazBoard), LibreOffice,
  study sites (muslimbangla.com) skip the reminder
- **Tray** — persistent top-bar icon (needs
  `gir1.2-ayatanaappindicator3-0.1`), live waste label, no Quit by design
- **Protected package** — prerm refuses removal, apt hold, immutable files

## Install

```bash
sudo dpkg -i packaging/focus-reminder_1.0_all.deb
# tray icon needs: sudo apt install -y gir1.2-ayatanaappindicator3-0.1
```

Alternative (script, same contents): `sudo bash install-systemwide.sh`

Rebuild the package after code changes: `./build-deb.sh`

## Files

| File | Purpose |
| ---- | ------- |
| `focus-reminder-ubuntu.py` | The whole app (GTK3, stdlib only). Modes: default service, `--tray`, `--show-now` |
| `install-systemwide.sh` | Script installer (alternative to the .deb) |
| `build-deb.sh` | Rebuilds the .deb from current sources |
| `assets/focus-reminder.png` | Tray + app-menu icon |
| `packaging/focus-reminder_1.0_all/` | Debian package tree (+ built `.deb`) |

## Controls

- Services: `systemctl --user status focus-reminder.service focus-tray.service`
- Logs: `journalctl --user -u focus-reminder.service`
- Data: `~/.local/share/focus-reminder/` (ledger-*.json, goals.json, suspend.state)
- Stop: `systemctl --user stop focus-reminder.service focus-tray.service`

## Notes

- Suspend confirmations require the system (sudo) password; it is never stored.
- Power Off needs confirmation; shutdown itself may ask for auth (system policy).
- Uninstall is blocked by design (prerm + hold + immutable files).
