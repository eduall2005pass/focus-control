#!/bin/bash
# focus-tty-auth-check.sh
# Invoked by PAM (/etc/pam.d/login) before TTY / console authentication.
# If focus lockout is active, rejects authentication to prevent TTY bypass.

if /usr/local/bin/focus-lockout-check; then
    echo "🔒 TTY Access Denied: Focus Lockout is active. TTY bypass is blocked." >&2
    exit 1
fi

exit 0
