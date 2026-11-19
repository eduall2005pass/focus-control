#!/bin/bash
# focus-boot-enforce.sh
# Runs as ROOT via systemd system service (Before=display-manager.service).
#
# Responsibility: maintain the /run/focus-reminder-lock.flag so that the
# user service (focus-reminder.py) and PAM hook can read suspension state.
#
# IMPORTANT: This script does NOT call loginctl lock-sessions.
# Locking the session causes an unbreakable login loop:
#   lock → GDM login screen → user authenticates → service locks again → loop
# The fullscreen lockout window in focus-reminder.py is itself unclosable
# (delete-event blocked, keep-above) — session lock is not needed.
# -------------------------------------------------------------------------
set -euo pipefail

LEDGER_SUBPATH=".local/share/focus-reminder"
LOCK_FLAG="/run/focus-reminder-lock.flag"
CHECK_INTERVAL=5
BOOT_RESTORE_FLAG="/run/focus-reminder-checkpoint-restored"

log() {
    echo "[focus-boot-enforce] $*" | systemd-cat -t focus-boot-enforce -p info 2>/dev/null \
        || echo "[focus-boot-enforce] $*"
}

user_suspension_end() {
    local ledger="$1" state_file="$1/suspend.state" checkpoint="$1/suspend-checkpoint.json"
    local end_ts=""
    if [ -f "$state_file" ]; then
        end_ts=$(cat "$state_file" 2>/dev/null | tr -d '[:space:]') || end_ts=""
    fi
    if ! [[ "$end_ts" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        # Recover the original absolute deadline if the main state file was
        # lost during shutdown but the fsynced checkpoint survived.
        end_ts=$(python3 - "$checkpoint" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        state = json.load(f)
    end = float(state.get("saved_at", 0)) + max(0.0, float(state.get("remaining_seconds", 0)))
    print(end if end > 0 else "")
except Exception:
    print("")
PY
        )
        [[ "$end_ts" =~ ^[0-9]+(\.[0-9]+)?$ ]] || return 1
        printf '%s\n' "$end_ts" > "$state_file" 2>/dev/null || true
    fi
    awk "BEGIN { exit !($end_ts > $(date +%s)) }" || return 1
    printf '%s\n' "$end_ts"
}

refresh_suspend_checkpoint() {
    local ledger="$1" end_ts="$2"
    python3 - "$ledger" "$end_ts" <<'PY' || true
import json, os, sys, tempfile, time

ledger, end_text = sys.argv[1:]
try:
    end = float(end_text)
    now = time.time()
    remaining = max(0.0, end - now)
    if remaining <= 0:
        raise SystemExit(0)
    path = os.path.join(ledger, "suspend-checkpoint.json")
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        state = {"mode": "active", "schedule": None}
    state["saved_at"] = now
    state["remaining_seconds"] = remaining
    schedule = state.get("schedule")
    if isinstance(schedule, dict):
        try:
            stage = int(schedule.get("stage", 0))
            segments = schedule.get("segments", [])
            if 0 <= stage < len(segments):
                schedule_end = float(schedule["stage_start"]) + sum(
                    int(value) for value in segments[stage:])
                state["stage_remaining_seconds"] = max(
                    0.0, min(remaining, schedule_end - now))
        except Exception:
            pass
    fd, tmp = tempfile.mkstemp(prefix=".focus-checkpoint-", dir=ledger)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    try:
        home = os.path.dirname(os.path.dirname(os.path.dirname(ledger)))
        info = os.stat(home)
        os.chown(path, info.st_uid, info.st_gid)
    except OSError:
        pass
except Exception as exc:
    print(f"[focus-boot-enforce] checkpoint refresh failed: {exc}")
PY
}

restore_checkpoint_for_this_boot() {
    [ -e "$BOOT_RESTORE_FLAG" ] && return 0
    # Claim the one-time-per-boot restore so a daemon restart cannot extend
    # the countdown repeatedly. /run is cleared automatically at reboot.
    ( set -o noclobber; : > "$BOOT_RESTORE_FLAG" ) 2>/dev/null || return 0
    python3 - <<'PY'
import glob, json, os, tempfile, time

now = time.time()
for home in glob.glob("/home/*/"):
    ledger = os.path.join(home, ".local/share/focus-reminder")
    checkpoint = os.path.join(ledger, "suspend-checkpoint.json")
    state_file = os.path.join(ledger, "suspend.state")
    schedule_path = os.path.join(ledger, "lockout-schedule.json")
    try:
        os.unlink(os.path.join(ledger, "shell-lockout-active"))
    except OSError:
        pass
    end = 0.0
    try:
        with open(state_file) as f:
            end = float(f.read().strip())
    except Exception:
        end = 0.0
    state = {}
    try:
        with open(checkpoint) as f:
            state = json.load(f)
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}
    if end <= 0:
        try:
            end = (float(state.get("saved_at", 0))
                   + max(0.0, float(state.get("remaining_seconds", 0))))
        except Exception:
            end = 0.0
    if end <= 0:
        continue
    if end <= now:
        # The countdown finished while the laptop was powered off. Do not
        # restart it on boot: clear the stale lockout markers.
        for path in (state_file, checkpoint, schedule_path):
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            os.unlink(os.path.join(ledger, "pam-lock-requested"))
        except OSError:
            pass
        continue
    # Keep the ORIGINAL wall-clock deadline. Powered-off time therefore
    # counts down normally instead of pausing the timer.
    remaining = max(0.0, end - now)
    state["saved_at"] = now
    state["remaining_seconds"] = remaining
    schedule = state.get("schedule")
    if not isinstance(schedule, dict):
        try:
            with open(schedule_path) as f:
                schedule = json.load(f)
            if not isinstance(schedule, dict):
                schedule = None
            else:
                state["schedule"] = schedule
        except Exception:
            schedule = None
    if isinstance(schedule, dict):
        try:
            stage = int(schedule.get("stage", 0))
            segments = schedule.get("segments", [])
            if 0 <= stage < len(segments):
                schedule_end = float(schedule["stage_start"]) + sum(
                    int(value) for value in segments[stage:])
                state["stage_remaining_seconds"] = max(
                    0.0, min(remaining, schedule_end - now))
        except Exception:
            pass
    try:
        os.makedirs(ledger, exist_ok=True)
        for path, content in (
            (state_file, str(end)),
            (checkpoint, json.dumps(state)),
        ):
            fd, tmp = tempfile.mkstemp(prefix=".focus-boot-", dir=ledger)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        if isinstance(schedule, dict):
            fd, tmp = tempfile.mkstemp(prefix=".focus-boot-", dir=ledger)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(schedule, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, schedule_path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        try:
            uid, gid = os.stat(home).st_uid, os.stat(home).st_gid
            os.chown(state_file, uid, gid)
            os.chown(checkpoint, uid, gid)
            if isinstance(schedule, dict):
                os.chown(schedule_path, uid, gid)
        except OSError:
            pass
    except Exception as exc:
        print(f"[focus-boot-enforce] checkpoint restore failed for {home}: {exc}")
PY
}

log "Boot enforcement daemon started."

if [ "${1:-}" = "--restore" ]; then
    restore_checkpoint_for_this_boot
    exit 0
fi

while true; do
    any_active=0
    for homedir in /home/*/; do
        ledger="${homedir}${LEDGER_SUBPATH}"
        [ -d "$ledger" ] || continue
        end_ts=""
        if end_ts=$(user_suspension_end "$ledger"); then
            any_active=1
            printf '%s\n' "$end_ts" > "${ledger}/pam-lock-requested" 2>/dev/null || true
            refresh_suspend_checkpoint "$ledger" "$end_ts"
        fi
    done
    if [ "$any_active" -eq 1 ]; then
        echo active > "$LOCK_FLAG" 2>/dev/null || true

        # --- TTY / Virtual Terminal Lockdown ---
        # 1. Lock VT switching in Linux kernel via ioctl VT_LOCKSWITCH (0x560B)
        python3 -c '
import fcntl, os
for dev in ("/dev/console", "/dev/tty0", "/dev/tty1"):
    try:
        fd = os.open(dev, os.O_RDWR)
        try:
            fcntl.ioctl(fd, 0x560B, 1) # VT_LOCKSWITCH
            break
        finally:
            os.close(fd)
    except Exception:
        pass
' 2>/dev/null || true

        # 2. Stop getty/agetty services on text virtual terminals
        systemctl stop getty.target >/dev/null 2>&1 || true
        systemctl stop "getty@tty*.service" >/dev/null 2>&1 || true
        pkill -9 -f "agetty" >/dev/null 2>&1 || true
        pkill -9 -x "login" >/dev/null 2>&1 || true

        # 3. Detect graphical session VT and force switch back to GUI if on text console
        gui_vt=$(loginctl list-sessions --no-legend 2>/dev/null | while read -r sess uid user rest; do
            type=$(loginctl show-session "$sess" -p Type --value 2>/dev/null || true)
            if [ "$type" = "wayland" ] || [ "$type" = "x11" ]; then
                vtnr=$(loginctl show-session "$sess" -p VTNr --value 2>/dev/null || true)
                if [ -n "$vtnr" ] && [ "$vtnr" -ge 1 ] 2>/dev/null; then
                    echo "$vtnr"
                    break
                fi
            fi
        done)
        gui_vt="${gui_vt:-2}"
        cur_vt=$(cat /sys/class/tty/tty0/active 2>/dev/null || true)
        if [ -n "$cur_vt" ] && [ "$cur_vt" != "tty${gui_vt}" ]; then
            chvt "$gui_vt" 2>/dev/null || true
        fi

        # 4. Terminate any user processes/shells on non-GUI TTYs
        for tty_idx in 1 3 4 5 6 7 8 9 10 11 12; do
            if [ "$tty_idx" -ne "$gui_vt" ]; then
                pkill -9 -t "tty${tty_idx}" 2>/dev/null || true
            fi
        done
    else
        rm -f "$LOCK_FLAG" 2>/dev/null || true
        # Re-enable VT switching when lockout is over
        python3 -c '
import fcntl, os
for dev in ("/dev/console", "/dev/tty0", "/dev/tty1"):
    try:
        fd = os.open(dev, os.O_RDWR)
        try:
            fcntl.ioctl(fd, 0x560C, 1) # VT_UNLOCKSWITCH
            break
        finally:
            os.close(fd)
    except Exception:
        pass
' 2>/dev/null || true
    fi
    sleep "$CHECK_INTERVAL"
done
