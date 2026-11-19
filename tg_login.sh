#!/usr/bin/env bash
# Telegram Drive Login Helper
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="/home/prof21/.local/share/focus-reminder/tgfuse-venv/bin/python3"

if [ ! -f "$VENV_PY" ]; then
    echo "[-] Virtual environment not found at $VENV_PY"
    exit 1
fi

exec "$VENV_PY" "$SCRIPT_DIR/tg_drive/tg_auth.py" "$@"
