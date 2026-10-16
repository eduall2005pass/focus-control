#!/bin/bash
# Deploy lockout shims (needs sudo for /usr/local/bin part).
# Phase 1 (root): /usr/local/bin shims cover ALL users incl. root on VT consoles.
# Phase 2 (user): shadow shims in early-PATH home dirs (real binary moved
#   aside to .<name>.focus-real, shim delegates to it). Transparent normally,
#   refusal during suspension.
# Usage: sudo ./make-lockout-shims.sh   (run from anywhere; uses $SUDO_USER)
set -e
NAMES="chrome chromium chromium-browser firefox brave brave-browser msedge opera vivaldi gnome-terminal gnome-terminal-server xterm konsole alacritty kitty tilix terminator wezterm opencode claude gemini aichat tgpt sgpt codex cursor-agent aider ollama kilo cline cursor copilot agy"

make_shim() {
  local dest="$1" real="$2"
  cat > "$dest" <<EOF
#!/bin/sh
# focus-reminder lockout shim (transparent unless suspended).
NAME=\$(basename "\$0")
NOW=\$(date +%s)
locked=0
for f in /home/*/.local/share/focus-reminder/suspend.state; do
  [ -f "\$f" ] || continue
  END=\$(cat "\$f" 2>/dev/null)
  case "\$END" in ''|*[!0-9.]*) continue;; esac
  if awk "BEGIN{exit !( \$END > \$NOW )}"; then locked=1; break; fi
done
if [ "\$locked" = 1 ]; then
  echo "Device suspended - '\$NAME' is disabled until the countdown ends." >&2
  exit 126
fi
REAL="$real"
if [ -z "\$REAL" ]; then
  OIFS=\$IFS; IFS=:
  for d in \$PATH; do
    [ "\$d" = /usr/local/bin ] && continue
    if [ -x "\$d/\$NAME" ]; then REAL="\$d/\$NAME"; break; fi
  done
  IFS=\$OIFS
fi
if [ -z "\$REAL" ]; then echo "\$NAME: command not found" >&2; exit 127; fi
exec "\$REAL" "\$@"
EOF
  chmod 755 "$dest"
}

# Phase 2: shadow early-PATH hits (as the real user, not root)
shadow_user() {
  for n in $NAMES; do
    hit=$(command -v "$n" 2>/dev/null)
    case "$hit" in /usr/local/bin/*|"") continue;; esac
    [ -f "$hit" ] || continue
    dir=$(dirname "$hit")
    case "$dir" in /usr/bin|/bin|/usr/sbin|/sbin|/snap/bin) continue;; esac
    [ -e "$dir/.$n.focus-real" ] || mv "$hit" "$dir/.$n.focus-real"
    make_shim "$dir/$n" "$dir/.$n.focus-real"
  done
  echo "phase2: user shadow shims done"
}

# Phase 1: system shims (delegate by PATH lookup)
if [ "$1" = --user ]; then shadow_user; exit 0; fi

for n in $NAMES; do make_shim "/usr/local/bin/$n" ""; done
echo "phase1: system shims done"

# Phase 2 runs as the login user (shadow early-PATH binaries)
if [ -n "$SUDO_USER" ]; then
  su - "$SUDO_USER" -c "bash '$0' --user"
else
  echo "SKIP phase2 (no SUDO_USER; run: ./make-lockout-shims.sh --user)"
fi
