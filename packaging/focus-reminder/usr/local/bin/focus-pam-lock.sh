#!/bin/bash
# focus-pam-lock.sh
# Called by PAM immediately after a user authenticates (pam_exec.so).
# If a focus-reminder suspension is active for this user, lock the
# session before the graphical environment has a chance to load.
# -----------------------------------------------------------------------
# PAM environment variables available:
#   PAM_USER  — the user logging in
#   PAM_TYPE  — open_session / close_session / etc.

[ "$PAM_TYPE" = "open_session" ] || exit 0

USER_HOME=$(getent passwd "$PAM_USER" 2>/dev/null | cut -d: -f6)
[ -n "$USER_HOME" ] || exit 0

STATE_FILE="${USER_HOME}/.local/share/focus-reminder/suspend.state"
CHECKPOINT_FILE="${USER_HOME}/.local/share/focus-reminder/suspend-checkpoint.json"
end_ts=""
if [ -f "$STATE_FILE" ]; then
    end_ts=$(cat "$STATE_FILE" 2>/dev/null | tr -d '[:space:]') || end_ts=""
fi
if ! [[ "$end_ts" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    end_ts=$(python3 - "$CHECKPOINT_FILE" <<'PY'
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
    [[ "$end_ts" =~ ^[0-9]+(\.[0-9]+)?$ ]] || exit 0
    printf '%s\n' "$end_ts" > "$STATE_FILE" 2>/dev/null || true
fi

[[ "$end_ts" =~ ^[0-9]+(\.[0-9]+)?$ ]] || exit 0

now=$(date +%s)
if awk "BEGIN { exit !($end_ts > $now) }"; then
    # Suspension is still active.
    # Write flag so the user service shows the lockout window immediately.
    # Do NOT call loginctl lock-sessions here — that would cause a login loop
    # (lock → login screen → password → PAM fires again → lock → loop).
    FLAG_DIR="${USER_HOME}/.local/share/focus-reminder"
    mkdir -p "$FLAG_DIR" 2>/dev/null || true
    echo "$end_ts" > "${FLAG_DIR}/pam-lock-requested" 2>/dev/null || true
fi

exit 0
