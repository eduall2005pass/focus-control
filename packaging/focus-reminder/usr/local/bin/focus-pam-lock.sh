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

end_ts=$(/usr/local/bin/focus-lockout-control status 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("deadline",0))' 2>/dev/null || true)
[[ "$end_ts" =~ ^[0-9]+(\.[0-9]+)?$ ]] || exit 0
if awk "BEGIN { exit !($end_ts > $(date +%s)) }"; then
    # Suspension is still active.
    # Write flag so the user service shows the lockout window immediately.
    # Do NOT call loginctl lock-sessions here — that would cause a login loop
    # (lock → login screen → password → PAM fires again → lock → loop).
    FLAG_DIR="${USER_HOME}/.local/share/focus-reminder"
    mkdir -p "$FLAG_DIR" 2>/dev/null || true
    echo "$end_ts" > "${FLAG_DIR}/pam-lock-requested" 2>/dev/null || true
fi

exit 0
