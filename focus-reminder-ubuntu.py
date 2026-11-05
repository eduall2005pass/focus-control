#!/usr/bin/env python3
# Focus Reminder — Ubuntu System Wide (GTK, no extra install)
# Premium redesign: design tokens, clear action hierarchy, full interaction
# states, keyboard support, confirmation for destructive actions.
# - Every 10 min: fullscreen system-wide alert (SKIPPED while ChatGPT/Gemini in use)
# - OpenCode/browser running -> alert MUST appear
# - Actions: Back to Work (primary) / Snooze 10 (secondary) /
#            Suspend Device 30 (secondary, lockout) / Power Off (destructive, confirm)
# Auto-started via systemd user service + autostart desktop file.

import atexit
import base64
import email.policy
import fcntl
import glob
import hashlib
import html
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import gi
gi.require_version("Gtk", "3.0")
try:
    gi.require_foreign("cairo")  # gives Cairo contexts/strokes support
except Exception:
    pass
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Gio, Pango

try:
    gi.require_version("WebKit2", "4.1")
    from gi.repository import WebKit2
except Exception:  # WebKit not available -> study view disabled, app still runs
    WebKit2 = None

FOCUS_VERSION = "1.0.8"

FOCUS_MINUTES = 10
SUSPEND_MINUTES = 30
SITE_WINDOW_MINUTES = 15  # study site visited within last N min -> studying

# Deen/study websites — visiting these counts as study, alert is skipped
STUDY_DOMAINS = ["muslimbangla.com"]

OPENCODE_PROCS = "opencode|OpenCode"
BROWSER_PROCS = "chrome|chromium|firefox|brave|msedge|edge"
# Study / teaching essentials — alert NEVER appears while these run
STUDY_PROCS = ("evince|atril|okular|zathura|xournalpp|soffice.bin|libreoffice|"
               "foliate|calibre|mupdf|gazboard|pdfarranger|FoxitReader")

# ---------- whole-device tracking config ----------
TRACK_SAMPLE_SEC = 5             # activity sampling interval
TRACK_IDLE_THRESHOLD_SEC = 120   # inactivity beyond this pauses counting
TRACK_FLUSH_EVERY = 6            # flush ledger every N samples (~30s)
DAILY_WASTE_LIMIT_MIN = 120      # daily non-study limit shown on dashboard
DEFAULT_NON_STUDY = True         # unclassified apps count as non-study
APP_OVERRIDES = {}               # e.g. {"code": "study"} — comm/class -> study|waste
LEDGER_DIR = os.path.expanduser("~/.local/share/focus-reminder")
HOOK_PATH = os.path.join(LEDGER_DIR, "focus-hook.json")
# Optional integration point: any tool/extension may write
# {"app": "msedge", "domain": "youtube.com", "ts": 1730000000} to HOOK_PATH.
# Fresh (<20s) hook data wins over all other providers. Records stay local.

# Rotating motivation: Qur'an (with reference) + study focus quotes
QUOTES = [
    ("By time, indeed mankind is in loss \u2014 except those who believe, "
     "do righteous deeds, and urge one another to truth and patience.",
     "Qur\u2019an 103:1\u20133 (Surah Al-\u02bbAsr)"),
    ("Indeed, with hardship comes ease.",
     "Qur\u2019an 94:6 (Surah Ash-Sharh)"),
    ("And that man shall have nothing but what he strives for.",
     "Qur\u2019an 53:39 (Surah An-Najm)"),
    ("So when you are free from your duties, then stand up for worship. "
     "And to your Lord direct your longing.",
     "Qur\u2019an 94:7\u20138 (Surah Ash-Sharh)"),
    ("Allah does not burden a soul beyond what it can bear.",
     "Qur\u2019an 2:286 (Surah Al-Baqarah)"),
    ("And He found you lost and guided you.",
     "Qur\u2019an 93:7 (Surah Ad-Duha)"),
    ("You don\u2019t have to be great to start, but you have to start to be great.",
     "Zig Ziglar"),
    ("Success is the sum of small efforts, repeated day in and day out.",
     "Robert Collier"),
    ("Don\u2019t watch the clock; do what it does. Keep going.",
     "Sam Levenson"),
]
_quote_index = 0

# ---------- design tokens ----------
NAVY_900 = "#10264a"
NAVY_800 = "#19376d"
NAVY_700 = "#214d86"
BLUE_600 = "#2563eb"
BLUE_700 = "#1d4ed8"
BLUE_100 = "#dbeafe"
BLUE_300 = "#93c5fd"
INK_900 = "#111827"
GRAY_500 = "#475569"
GRAY_400 = "#64748b"
GRAY_300 = "#94a3b8"
LINE = "#e2e8f0"
BG_SOFT = "#f1f5f9"
BTN_NEUTRAL = "#eef2f7"
RED_600 = "#dc2626"
RED_700 = "#b91c1c"
WHITE = "#ffffff"

FONT_TITLE = "Inter, 'Plus Jakarta Sans', Ubuntu, sans-serif"
FONT_BODY = "Inter, 'Plus Jakarta Sans', 'Noto Sans', sans-serif"

SPACE = 8  # 8px spacing base


def build_css(compact=False, animations=True):
    trans = "transition: background 120ms ease, box-shadow 120ms ease;" if animations else ""
    title_size = 24 if compact else 30
    card_pad = 20 if compact else 32
    card_w_note = ""  # width set via set_size_request instead
    return f"""
.window-bg {{ background: linear-gradient(135deg, {NAVY_900} 0%, {NAVY_800} 60%, {NAVY_700} 100%); }}
.card {{
  background: {WHITE};
  border: 1px solid {LINE};
  border-radius: 22px;
  padding: {card_pad}px;
  box-shadow: 0 18px 60px rgba(16, 38, 74, 0.35);
}}
.accent {{
  background: linear-gradient(90deg, {BLUE_600}, #38bdf8);
  border-radius: 4px;
  min-height: 6px;
  min-width: 120px;
}}
.eyebrow {{
  background: {BLUE_100}; color: {BLUE_700};
  font-family: {FONT_BODY}; font-weight: bold; font-size: 12px;
  letter-spacing: 2px;
  border-radius: 20px; padding: 8px 20px;
}}
.prio {{
  background: #fee2e2; color: #b91c1c;
  font-family: {FONT_BODY}; font-weight: bold; font-size: 12px;
  letter-spacing: 2px;
  border-radius: 20px; padding: 8px 20px;
}}
.headline {{
  font-family: {FONT_TITLE}; font-size: {title_size}px; font-weight: 800; color: {INK_900};
}}
.body {{
  font-family: {FONT_BODY}; font-size: 15px; color: {GRAY_500};
}}
.guide {{
  background: {BG_SOFT}; color: #334155;
  font-family: {FONT_BODY}; font-size: 14px;
  border: 1px solid {LINE};
  border-radius: 12px; padding: 14px 18px;
}}
button {{
  font-family: {FONT_BODY}; font-size: 15px;
  border-radius: 12px; padding: 12px 24px;
  {trans}
}}
button:focus {{ outline: 2px solid {BLUE_300}; outline-offset: 2px; }}
.btn-primary {{
  background: linear-gradient(180deg, #2f6df6, {BLUE_700});
  color: white; font-weight: bold;
  box-shadow: 0 6px 20px rgba(37, 99, 235, 0.4);
}}
.btn-primary:hover {{ background: linear-gradient(180deg, #3b78f7, #1e40af); }}
.btn-primary:active {{ background: {BLUE_700}; box-shadow: none; }}
.btn-primary:disabled {{ background: #c7d7f8; color: #f1f5f9; box-shadow: none; }}
.btn-secondary {{
  background: {BTN_NEUTRAL}; color: {INK_900};
  border: 1px solid {LINE};
}}
.btn-secondary:hover {{ background: #e3e9f2; }}
.btn-secondary:active {{ background: #d5dce6; box-shadow: none; }}
.btn-secondary:disabled {{ background: {BG_SOFT}; color: {GRAY_300}; }}
.btn-danger {{
  background: {WHITE}; color: {RED_600};
  border: 1.5px solid {RED_600}; font-weight: bold;
}}
.btn-danger:hover {{ background: {RED_600}; color: white; }}
.btn-danger:active {{ background: {RED_700}; color: white; border-color: {RED_700}; }}
.btn-danger:disabled {{ color: {GRAY_300}; border-color: {LINE}; }}
.caption {{ font-family: {FONT_BODY}; font-size: 12px; color: {GRAY_300}; }}
.goalrow {{
  background: {WHITE};
  border: 1px solid {LINE};
  border-radius: 14px; padding: 12px 16px;
}}
.goaldonerow {{ background: #f8fafc; }}
.goaltext {{ font-family: {FONT_BODY}; font-size: 14px; color: {INK_900}; }}
.goaldone {{ font-family: {FONT_BODY}; font-size: 14px; color: {GRAY_300}; }}
.chip {{
  background: {BG_SOFT}; color: {GRAY_400};
  font-family: {FONT_BODY}; font-size: 12px;
  border: 1px solid {LINE};
  border-radius: 999px; padding: 4px 12px;
}}
.chipdate {{ background: #eff6ff; color: {BLUE_700}; border-color: #bfdbfe; }}
.chiptime {{ background: #fffbeb; color: #b45309; border-color: #fde68a; }}
.tgl {{ font-size: 17px; border-radius: 999px; padding: 4px 9px; }}
.tglplain {{ color: #cbd5e1; }}
.tgldone {{ color: {BLUE_600}; }}
.lockbar {{ min-height: 12px; border-radius: 8px; }}
.unlockat {{ font-family: {FONT_BODY}; font-size: 12px; color: {GRAY_400}; }}
.delbtn {{
  color: {RED_600}; font-family: {FONT_BODY}; font-size: 14px; font-weight: bold;
  border-radius: 8px; padding: 6px 10px;
}}
.delbtn:hover {{ background: #fee2e2; }}
.mini {{
  background: {BTN_NEUTRAL}; color: {INK_900};
  font-family: {FONT_BODY}; font-size: 12px;
  border: 1px solid {LINE}; border-radius: 8px; padding: 6px 10px;
}}
.quote {{
  font-family: {FONT_BODY}; font-size: 14px; font-style: italic; color: {BLUE_700};
}}
.quoteref {{ font-family: {FONT_BODY}; font-size: 12px; color: {GRAY_300}; }}
.lock-time {{
  font-family: {FONT_TITLE}; font-size: 66px; font-weight: 800; color: {INK_900};
}}
.dialog-title {{ font-family: {FONT_TITLE}; font-size: 20px; font-weight: 800; color: {INK_900}; }}
.dash {{
  background: {INK_900};
  border-radius: 14px; padding: 16px 18px;
}}
.dasheye {{
  font-family: {FONT_BODY}; font-size: 11px; font-weight: bold;
  letter-spacing: 2px; color: {BLUE_300};
}}
.dashbig {{
  font-family: {FONT_TITLE}; font-size: 40px; font-weight: 800; color: white;
}}
.dashsub {{ font-family: {FONT_BODY}; font-size: 12px; color: #cbd5e1; }}
.dashrow {{ font-family: {FONT_BODY}; font-size: 13px; color: #e2e8f0; }}
.dashbar {{ min-height: 10px; border-radius: 6px; }}
.dashtop {{ font-family: {FONT_BODY}; font-size: 12px; color: #cbd5e1; }}
""".encode()


def animations_enabled():
    try:
        return Gtk.Settings.get_default().get_property("gtk-enable-animations")
    except Exception:
        return True


def is_compact_screen():
    try:
        d = Gdk.Display.get_default()
        m = d.get_primary_monitor() or d.get_monitor(0)
        g = m.get_geometry()
        return g.width < 1000 or g.height < 750
    except Exception:
        return False

# ---------- helpers ----------

_HOST = ["flatpak-spawn", "--host"] if os.environ.get("FLATPAK_ID") else []


def _run(cmd, **kw):
    """Run a system command; inside Flatpak, execute it on the host."""
    if _HOST:
        cmd = _HOST + list(cmd)
    return subprocess.run(cmd, **kw)  # noqa: direct call intended (avoids recursion)

def notify(msg):
    try:
        _run(["notify-send", "Focus Reminder", msg], timeout=5)
    except Exception:
        pass


def _proc_running(pattern):
    try:
        out = _run(["pgrep", "-x", pattern],
                             capture_output=True, text=True, timeout=5).stdout
        return bool(out.strip())
    except Exception:
        return False


def study_app_running():
    """PDF/document viewer, whiteboard etc. running -> studying, never nag."""
    return _proc_running(STUDY_PROCS)


def must_alert():
    """OpenCode or browser running -> reminder MUST appear, no skip."""
    return _proc_running(OPENCODE_PROCS) or _proc_running(BROWSER_PROCS)


def _query_copy(db_path, query):
    """Copy a (possibly locked) sqlite db to tmp and run query -> first cell or None."""
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        shutil.copy(db_path, tmp)
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True, timeout=5)
        row = con.execute(query).fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        try:
            if tmp:
                os.remove(tmp)
        except Exception:
            pass


def _web_recent(domains, window_min):
    """True if any of the domains was visited recently (Chromium + Firefox)."""
    cond = " OR ".join(f"url LIKE '%{d}%'" for d in domains)
    homes = ["google-chrome", "chromium", "BraveSoftware/Brave-Browser", "microsoft-edge"]
    for home in homes:
        for profile in ["Default", "Profile 1", "Profile 2"]:
            p = os.path.expanduser(f"~/.config/{home}/{profile}/History")
            if not os.path.exists(p):
                continue
            latest = _query_copy(p, f"SELECT max(last_visit_time) FROM urls WHERE {cond}")
            if latest:
                unix_ts = latest / 1_000_000 - 11_644_473_600  # Chrome epoch -> unix
                if time.time() - unix_ts < window_min * 60:
                    return True
    for p in glob.glob(os.path.expanduser("~/.mozilla/firefox/*.default*/places.sqlite")):
        latest = _query_copy(
            p, f"SELECT max(visit_date) FROM moz_historyvisits "
               f"JOIN moz_places ON moz_historyvisits.place_id = moz_places.id "
               f"WHERE {cond}")
        if latest:
            if time.time() - latest / 1_000_000 < window_min * 60:
                return True
    return False


def study_site_recent():
    """Deen/study sites (e.g. muslimbangla.com) visited recently -> studying."""
    try:
        return _web_recent(STUDY_DOMAINS, SITE_WINDOW_MINUTES)
    except Exception as e:
        print(f"Study-site check error (fail-open): {e}", flush=True)
        return False


def lock_session():
    try:
        _run(["loginctl", "lock-session"], timeout=10)
    except Exception as e:
        print(f"lock-session failed: {e}", flush=True)


suspend_until = 0
SUSPEND_STATE = os.path.join(LEDGER_DIR, "suspend.state")
NEXT_ALERT_STATE = os.path.join(LEDGER_DIR, "next-alert.state")


def _load_next_alert():
    try:
        with open(NEXT_ALERT_STATE) as f:
            return float(f.read().strip())
    except Exception:
        return 0


def _save_next_alert(ts):
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        with open(NEXT_ALERT_STATE, "w") as f:
            f.write(str(float(ts)))
    except Exception as e:
        print(f"next alert save error: {e}", flush=True)
SUSPEND_CHECKPOINT_STATE = os.path.join(LEDGER_DIR, "suspend-checkpoint.json")
LOCKOUT_SCHEDULE_STATE = os.path.join(LEDGER_DIR, "lockout-schedule.json")


def _atomic_write(path, content):
    os.makedirs(LEDGER_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".focus-state-", dir=LEDGER_DIR)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _save_suspend_state(ts, mode="active"):
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        now = time.time()
        _atomic_write(SUSPEND_STATE, str(float(ts)))
        schedule = _load_lockout_schedule(ts)
        stage_remaining = None
        if schedule:
            stage = int(schedule.get("stage", 0))
            stage_remaining = max(0.0, float(schedule["stage_start"])
                                  + int(schedule["segments"][stage]) - now)
        checkpoint = {
            "remaining_seconds": max(0.0, float(ts) - now),
            "stage_remaining_seconds": stage_remaining,
            "mode": mode,
            "saved_at": now,
            "schedule": schedule,
        }
        _atomic_write(SUSPEND_CHECKPOINT_STATE, json.dumps(checkpoint))
    except Exception as e:
        print(f"suspend state save error: {e}", flush=True)


def _load_suspend_state():
    try:
        with open(SUSPEND_STATE) as f:
            return float(f.read().strip())
    except Exception:
        return 0


def _restore_suspend_checkpoint():
    """Resume the saved remaining lockout time after boot or power loss."""
    try:
        with open(SUSPEND_CHECKPOINT_STATE) as f:
            checkpoint = json.load(f)
        remaining = max(0.0, float(checkpoint.get("remaining_seconds", 0)))
        if remaining <= 0:
            return _load_suspend_state()
        now = time.time()
        schedule = checkpoint.get("schedule")
        if (isinstance(schedule, dict) and isinstance(schedule.get("segments"), list)
                and schedule["segments"]):
            stage = max(0, min(int(schedule.get("stage", 0)),
                               len(schedule["segments"]) - 1))
            stage_left = checkpoint.get("stage_remaining_seconds")
            if stage_left is not None:
                duration = int(schedule["segments"][stage])
                stage_left = min(float(duration), max(0.0, float(stage_left)))
                schedule["stage"] = stage
                schedule["stage_start"] = now - (duration - stage_left)
                schedule["origin_start"] = now
                _save_lockout_schedule(schedule)
                end = _schedule_end(schedule)
            else:
                end = now + remaining
        else:
            end = now + remaining
        _atomic_write(SUSPEND_STATE, str(float(end)))
        _save_suspend_state(end, mode="active")
        print(f"Restored {max(1, int((end - now) / 60))} min of lockout after restart.",
              flush=True)
        return end
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return _load_suspend_state()


def _clear_suspend_state():
    for path in (SUSPEND_STATE, SUSPEND_CHECKPOINT_STATE):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def _save_lockout_schedule(schedule):
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        with open(LOCKOUT_SCHEDULE_STATE, "w") as f:
            json.dump(schedule, f)
    except Exception as e:
        print(f"lockout schedule save error: {e}", flush=True)


def _load_lockout_schedule(end_ts=None):
    try:
        with open(LOCKOUT_SCHEDULE_STATE) as f:
            schedule = json.load(f)
        if (isinstance(schedule, dict) and isinstance(schedule.get("segments"), list)
                and schedule["segments"] and 0 <= int(schedule.get("stage", 0))
                < len(schedule["segments"])):
            return schedule
    except Exception:
        pass
    if end_ts is None:
        end_ts = _load_suspend_state()
    seconds = max(0, int(end_ts - time.time()))
    if seconds:
        now = time.time()
        return {"segments": [seconds], "stage": 0, "stage_start": now,
                "origin_start": now}
    return None


def _schedule_end(schedule):
    return float(schedule["stage_start"]) + sum(
        int(x) for x in schedule["segments"][int(schedule["stage"]):])


def _clear_lockout_schedule():
    try:
        if os.path.exists(LOCKOUT_SCHEDULE_STATE):
            os.remove(LOCKOUT_SCHEDULE_STATE)
    except Exception:
        pass


def _begin_lockout_schedule(minutes):
    now = time.time()
    schedule = {"segments": [int(minutes * 60)], "stage": 0, "stage_start": now,
                "origin_start": now}
    _save_lockout_schedule(schedule)
    return now + int(minutes * 60)


def enforce_suspension_until(end_ts):
    """Non-skippable: re-enforce after reboot/poweroff until countdown ends."""
    # Lock immediately, before the countdown window is up, so there is no
    # usable-desktop gap right after power-on.
    lock_session()
    while time.time() < _load_suspend_state():
        if not _has_display():
            time.sleep(10)
            continue
        try:
            show_lockout_window((_load_suspend_state() - time.time()) / 60)
            if time.time() < _load_suspend_state():
                continue
            break  # returns only when timer ends (Power Off reboots -> state persists)
        except Exception as e:
            print(f"lockout enforce retry: {e}", flush=True)
            time.sleep(15)
    _clear_suspend_state()
    _clear_lockout_schedule()


def do_suspend(minutes=SUSPEND_MINUTES):
    """Start a durable emergency lockout without triggering broken hardware sleep."""
    do_pseudo_lockout(minutes, label="Emergency lockout")


def do_pseudo_lockout(minutes=SUSPEND_MINUTES, label="Focus lockout"):
    """Lock the device for N minutes WITHOUT sleeping (pseudo lockout).

    Device stays powered on but locked/countdown-blocked, so a logout, reboot
    or power-off cannot skip it: the remaining time is checkpointed and
    re-enforced on the next start.
    """
    global suspend_until
    end = _begin_lockout_schedule(minutes)
    suspend_until = end
    _save_suspend_state(end, mode="active")
    notify(f"{label} for {_fmt_short(minutes * 60)}. Device stays on and locked.")
    print(f"{label}: {minutes} min (no sleep) until "
          f"{time.strftime('%H:%M', time.localtime(end))}.", flush=True)
    try:
        show_lockout_window(minutes, pseudo=True)
    except Exception as e:
        print(f"pseudo lockout window error: {e}", flush=True)
    finally:
        # Only clear if our own deadline is over (power-off keeps it for restart).
        if _load_suspend_state() <= time.time():
            suspend_until = 0
            _clear_suspend_state()
            _clear_lockout_schedule()
    print("Pseudo lockout ended — normal reminders resumed.", flush=True)


def power_off():
    # Chain: session API (no extra perms) -> system poweroff (may ask password).
    notify("Shutting down...")
    try:
        r = _run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.SessionManager",
             "--object-path", "/org/gnome/SessionManager",
             "--method", "org.gnome.SessionManager.Shutdown"],
            capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            print("poweroff via SessionManager OK", flush=True)
            return
        print(f"SessionManager.Shutdown failed: {(r.stderr or r.stdout).strip()}", flush=True)
    except Exception as e:
        print(f"SessionManager.Shutdown error: {e}", flush=True)
    try:
        r = _run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.SessionManager",
             "--object-path", "/org/gnome/SessionManager",
             "--method", "org.gnome.SessionManager.RequestShutdown"],
            capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            print("poweroff via RequestShutdown OK (system dialog shown)", flush=True)
            return
        print(f"RequestShutdown failed: {(r.stderr or r.stdout).strip()}", flush=True)
    except Exception as e:
        print(f"RequestShutdown error: {e}", flush=True)
    try:
        r = _run(["systemctl", "poweroff"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            print("poweroff via systemctl OK", flush=True)
            return
        err = (r.stderr or r.stdout).strip()
    except Exception as e:
        err = str(e)
    print(f"poweroff FAILED: {err}", flush=True)
    notify(f"Power off failed: {err}")

# ---------- whole-device activity tracker ----------
# Honest capability map (GNOME Wayland):
#   idle time      -> org.gnome.Mutter.IdleMonitor (works)
#   screen lock    -> org.gnome.ScreenSaver (works)
#   active X-app   -> xprop on XWayland (partial: native Wayland apps invisible)
#   active domain  -> browser history sampling + optional hook file (no tab API)
#   Introspect.GetWindows is denied on this compositor, so it is NOT used.

STUDY_APP_SET = {s.lower() for s in STUDY_PROCS.split("|")}
BROWSER_APP_SET = {s.lower() for s in BROWSER_PROCS.split("|")}


def _has_display():
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True
    # Service may have started before graphical session — try importing
    # DISPLAY / WAYLAND_DISPLAY from the systemd user environment manager.
    try:
        out = subprocess.check_output(
            ["systemctl", "--user", "show-environment"],
            timeout=5, text=True, stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            if line.startswith("DISPLAY="):
                os.environ["DISPLAY"] = line.split("=", 1)[1]
            elif line.startswith("WAYLAND_DISPLAY="):
                os.environ["WAYLAND_DISPLAY"] = line.split("=", 1)[1]
            elif line.startswith("XAUTHORITY="):
                os.environ.setdefault("XAUTHORITY", line.split("=", 1)[1])
    except Exception:
        pass
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _idle_ms():
    try:
        out = _run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
             "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
             "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"],
            capture_output=True, text=True, timeout=5).stdout
        return int("".join(c for c in out if c.isdigit()) or 0)
    except Exception:
        return 0


def _screen_locked():
    try:
        out = _run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
             "--object-path", "/org/gnome/ScreenSaver",
             "--method", "org.gnome.ScreenSaver.GetActive"],
            capture_output=True, text=True, timeout=5).stdout
        return "true" in out.lower()
    except Exception:
        return False


def _xwayland_active():
    """Best-effort active app via XWayland. None when unknown/unsupported."""
    try:
        root = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                              capture_output=True, text=True, timeout=5).stdout
        if "#" not in root:
            return None
        wid = root.split("#")[-1].strip().split(",")[0].strip()
        if not wid or wid == "0x0":
            return None
        info = _run(["xprop", "-id", wid, "WM_CLASS"],
                              capture_output=True, text=True, timeout=5).stdout
        if "=" not in info or "not found" in info:
            return None
        toks = [t.strip().strip('"') for t in info.split("=", 1)[-1].split(",")]
        return toks[-1].lower() or None
    except Exception:
        return None


def _read_hook():
    """Optional integration: fresh hook file wins. Returns (app, domain) or None."""
    try:
        if not os.path.exists(HOOK_PATH):
            return None
        with open(HOOK_PATH) as f:
            d = json.load(f)
        if time.time() - float(d.get("ts", 0)) > 20:
            return None
        return (str(d.get("app") or "").lower() or None,
                str(d.get("domain") or "").lower() or None)
    except Exception:
        return None


def _last_domain():
    """Most recent history URL's domain across browsers. (domain, ts) or (None, 0)."""
    best, best_ts = None, 0
    homes = ["google-chrome", "chromium", "BraveSoftware/Brave-Browser", "microsoft-edge"]
    for home in homes:
        for profile in ["Default", "Profile 1", "Profile 2"]:
            p = os.path.expanduser(f"~/.config/{home}/{profile}/History")
            if not os.path.exists(p):
                continue
            row = _query_copy(p, "SELECT url, last_visit_time FROM urls "
                                 "ORDER BY last_visit_time DESC LIMIT 1")
            if row:
                ts = row[1] / 1_000_000 - 11_644_473_600
                if ts > best_ts:
                    best_ts = ts
                    best = (urlparse(row[0]).hostname or "").lower()
    for p in glob.glob(os.path.expanduser("~/.mozilla/firefox/*.default*/places.sqlite")):
        row = _query_copy(
            p, "SELECT url, visit_date FROM moz_historyvisits "
               "JOIN moz_places ON moz_historyvisits.place_id = moz_places.id "
               "ORDER BY visit_date DESC LIMIT 1")
        if row:
            ts = row[0] / 1_000_000
            if ts > best_ts:
                best_ts = ts
                best = (urlparse(row[1]).hostname or "").lower()
    return best, best_ts


def classify_app(app):
    a = (app or "").lower()
    if not a:
        return "unknown"
    if a in APP_OVERRIDES:
        return APP_OVERRIDES[a]
    if a in STUDY_APP_SET:
        return "study"
    if a in BROWSER_APP_SET:
        return "browser"
    return "waste" if DEFAULT_NON_STUDY else "study"


def classify_domain(dom):
    # AI is NOT study: only STUDY_DOMAINS count as study, everything else is waste.
    d = (dom or "").lower().lstrip("www.")
    if not d:
        return None
    for s in STUDY_DOMAINS:
        if d == s or d.endswith("." + s):
            return "study"
    return "waste"


def _fmt_hms(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _fmt_short(sec):
    sec = max(0, int(sec))
    if sec >= 3600:
        return f"{sec // 3600}h {(sec % 3600) // 60}m"
    if sec >= 60:
        return f"{sec // 60}m"
    return f"{sec}s"


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.day = None
        self.agg = {"study": 0, "waste": 0, "by_app": {}, "by_domain": {}}
        self.cur = None          # open segment {start, end, app, domain, cls}
        self.state = "unknown"   # current activity label
        self.state_cls = "unknown"
        self.samples = 0
        self.limit_notified = False
        self.limit_pending = False
        self.domain_cache = (None, 0)  # (domain, checked_at)
        os.makedirs(LEDGER_DIR, exist_ok=True)
        self._load_day(time.strftime("%Y-%m-%d"))

    def _path(self, day):
        return os.path.join(LEDGER_DIR, f"ledger-{day}.json")

    def _load_day(self, day):
        self.day = day
        self.limit_notified = False
        self.limit_pending = False
        self.agg = {"study": 0, "waste": 0, "by_app": {}, "by_domain": {}}
        self.cur = None
        try:
            if os.path.exists(self._path(day)):
                with open(self._path(day)) as f:
                    d = json.load(f)
                for k in ("study", "waste"):
                    self.agg[k] = int(d.get(k, 0))
                self.agg["by_app"] = dict(d.get("by_app", {}))
                self.agg["by_domain"] = dict(d.get("by_domain", {}))
        except Exception as e:
            print(f"ledger load error: {e}", flush=True)

    def flush(self):
        with self.lock:
            day, agg = self.day, self.agg
        try:
            segs = []
            try:
                if os.path.exists(self._path(day)):
                    with open(self._path(day)) as f:
                        segs = json.load(f).get("segments", [])
            except Exception:
                pass
            # note: open segment is NOT stored here; it is finalized into
            # aggregates on the next sample / class change / day rollover.
            with open(self._path(day), "w") as f:
                json.dump({"date": day, "study": agg["study"], "waste": agg["waste"],
                           "by_app": agg["by_app"], "by_domain": agg["by_domain"],
                           "segments": segs[-500:]}, f)
        except Exception as e:
            print(f"ledger flush error: {e}", flush=True)

    def _finalize(self, end):
        c = self.cur
        if not c:
            return
        dur = max(0, end - c["start"])
        if dur > 0 and c["cls"] in ("study", "waste"):
            self.agg[c["cls"]] += int(dur)
            key = c["domain"] or c["app"] or "unknown"
            d = self.agg["by_domain"] if c["domain"] else self.agg["by_app"]
            d[key] = int(d.get(key, 0)) + int(dur)
        self.cur = None

    def _cached_domain(self, now):
        dom, checked = self.domain_cache
        if now - checked > 60:
            try:
                dom, _ = _last_domain()
            except Exception:
                dom = None
            self.domain_cache = (dom, now)
        return self.domain_cache[0]

    def sample(self):
        t = time.time()
        with self.lock:
            if time.strftime("%Y-%m-%d", time.localtime(t)) != self.day:
                self._finalize(t)
                self.flush()
                self._load_day(time.strftime("%Y-%m-%d", time.localtime(t)))
            if not _has_display():
                # boot/service without graphical session: nothing to show, pause
                self._finalize(t)
                self.state, self.state_cls = "No display", "idle"
                return
            if _screen_locked() or _idle_ms() / 1000 > TRACK_IDLE_THRESHOLD_SEC:
                self._finalize(t)
                self.state, self.state_cls = ("Locked", "idle") if _screen_locked() else ("Idle", "idle")
                return
            hook = _read_hook()
            if hook and (hook[0] or hook[1]):
                app, domain = hook
            else:
                app = _xwayland_active()
                domain = None
                if app in BROWSER_APP_SET or _proc_running(BROWSER_PROCS):
                    domain = self._cached_domain(t)
            if domain:
                cls = classify_domain(domain) or "waste"
                label = domain
            elif app:
                c = classify_app(app)
                if c == "browser":
                    d2 = self._cached_domain(t)
                    if d2:
                        cls, label, domain = classify_domain(d2) or "waste", d2, d2
                    else:
                        cls, label = "waste", app + " (browser)"
                elif c == "unknown":
                    cls, label = ("waste", "Unknown") if DEFAULT_NON_STUDY else ("study", "Unknown")
                else:
                    cls, label = c, app
            else:
                cls, label = ("waste", "Unknown") if DEFAULT_NON_STUDY else ("study", "Unknown")
                app = "unknown"
            if self.cur and self.cur["cls"] == cls and self.cur["app"] == (app or "") \
                    and self.cur["domain"] == (domain or ""):
                self.cur["end"] = t
            else:
                self._finalize(t)
                self.cur = {"start": t, "end": t, "app": app or "",
                            "domain": domain or "", "cls": cls}
            self.state, self.state_cls = label, cls
            total_waste = self.agg["waste"]
            if self.cur and self.cur["cls"] == "waste":
                total_waste += int(t - self.cur["start"])
            if total_waste >= DAILY_WASTE_LIMIT_MIN * 60 and not self.limit_notified:
                self.limit_notified = True
                self.limit_pending = True
                print("Daily waste limit crossed.", flush=True)
            self.samples += 1
            if self.samples % TRACK_FLUSH_EVERY == 0:
                self.flush()

    def snapshot(self):
        now = time.time()
        with self.lock:
            waste = self.agg["waste"]
            study = self.agg["study"]
            if self.cur:
                live = max(0, now - self.cur["start"])
                if self.cur["cls"] == "waste":
                    waste += int(live)
                elif self.cur["cls"] == "study":
                    study += int(live)
            return {"waste": waste, "study": study,
                    "state": self.state, "state_cls": self.state_cls,
                    "by_app": dict(self.agg["by_app"]),
                    "by_domain": dict(self.agg["by_domain"])}

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.sample()
            except Exception as e:
                print(f"tracker sample error: {e}", flush=True)
            self.stop_event.wait(TRACK_SAMPLE_SEC)


_tracker = None


def start_tracker():
    global _tracker
    if _tracker is None:
        _tracker = Tracker()
        atexit.register(_tracker.flush)
        threading.Thread(target=_tracker.loop, daemon=True).start()
    return _tracker


def track_snapshot():
    if _tracker is None:
        return {"waste": 0, "study": 0, "state": "Tracker off",
                "state_cls": "unknown", "by_app": {}, "by_domain": {}}
    return _tracker.snapshot()


# ---------- UI primitives ----------

def _apply_css(win):
    css = Gtk.CssProvider()
    css.load_from_data(build_css(compact=is_compact_screen(),
                                 animations=animations_enabled()))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    win.get_style_context().add_class("window-bg")


def _new_fullscreen(title):
    win = Gtk.Window(title=title)
    win.fullscreen()
    win.set_keep_above(True)
    win.set_decorated(False)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.stick()  # visible on ALL workspaces — never hidden elsewhere
    win.set_position(Gtk.WindowPosition.CENTER)
    return win


# Keys GNOME steals at compositor level (overview/recent/app-switcher).
# Disabled during lockout, restored verbatim afterwards.
_LOCKDOWN_KEYS = [
    ("org.gnome.mutter", "overlay-key", "''"),                       # Super -> overview
    ("org.gnome.desktop.interface", "enable-hot-corners", "false"),  # hot corner
    ("org.gnome.desktop.wm.keybindings", "switch-applications", "@as []"),  # Super+Tab / Alt+Tab
    ("org.gnome.desktop.wm.keybindings", "switch-applications-backward", "@as []"),
    ("org.gnome.desktop.wm.keybindings", "switch-windows", "@as []"),        # Alt+Tab (windows)
    ("org.gnome.desktop.wm.keybindings", "switch-windows-backward", "@as []"),
    ("org.gnome.desktop.wm.keybindings", "panel-run-dialog", "@as []"),      # Alt+F2
    ("org.gnome.desktop.wm.keybindings", "unmaximize", "@as []"),            # Super+Down shrink
    ("org.gnome.desktop.wm.keybindings", "begin-move", "@as []"),            # Alt+F7 move
    ("org.gnome.desktop.wm.keybindings", "begin-resize", "@as []"),          # Alt+F8 resize
    ("org.gnome.shell.keybindings", "toggle-overview", "@as []"),            # Super+S
    ("org.gnome.shell.keybindings", "toggle-application-view", "@as []"),    # Super+A
    ("org.gnome.desktop.notifications", "show-banners", "false"),  # banners can launch apps
]


def _gnome_lockdown():
    """Disable shell-escape keys. Returns saved (schema, key, raw) list for restore."""
    saved = []
    for schema, key, locked_val in _LOCKDOWN_KEYS:
        try:
            r = _run(["gsettings", "get", schema, key],
                     capture_output=True, text=True, timeout=10)
            raw = r.stdout.strip() if r.returncode == 0 else None
        except Exception as e:
            print(f"lockdown get {schema} {key}: {e}", flush=True)
            raw = None
        if raw is None:
            continue
        saved.append((schema, key, raw))
        try:
            _run(["gsettings", "set", schema, key, locked_val], timeout=10)
        except Exception as e:
            print(f"lockdown set {schema} {key}: {e}", flush=True)
    return saved


def _gnome_restore(saved):
    for schema, key, raw in saved:
        try:
            _run(["gsettings", "set", schema, key, raw], timeout=10)
        except Exception as e:
            print(f"lockdown restore {schema} {key}: {e}", flush=True)


def _gnome_heal():
    """Self-heal stale lockdown (crash/reboot during lockout left keys off)."""
    defaults = {
        ("org.gnome.mutter", "overlay-key"): "'Super'",
        ("org.gnome.desktop.wm.keybindings", "switch-applications"): "['<Super>Tab']",
        ("org.gnome.desktop.wm.keybindings", "switch-windows"): "['<Alt>Tab']",
        ("org.gnome.desktop.notifications", "show-banners"): "true",
    }
    for (schema, key), good in defaults.items():
        try:
            r = _run(["gsettings", "get", schema, key],
                     capture_output=True, text=True, timeout=10)
            cur = r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            cur = None
        if cur is None or cur == good:
            continue
        locked_look = cur in ("''", "@as []", "false")
        if locked_look:
            try:
                _run(["gsettings", "set", schema, key, good], timeout=10)
                print(f"self-heal: restored {schema} {key}", flush=True)
            except Exception as e:
                print(f"self-heal failed {schema} {key}: {e}", flush=True)


# Binaries that must not run while the device is suspended (browsers, which can
# open anything incl. AI agents; terminals/VT-launched shells; AI CLIs).
# Matched by exact basename of argv[0] — never by substring, never self.
LOCKOUT_PROC_DENY = frozenset({
    "chrome", "chromium", "chromium-browser", "firefox", "brave",
    "brave-browser", "msedge", "opera", "vivaldi",
    "gnome-terminal", "gnome-terminal-server", "xterm", "konsole",
    "alacritty", "kitty", "tilix", "terminator", "wezterm",
    "opencode", "claude", "gemini", "aichat", "tgpt", "sgpt",
    "codex", "cursor-agent", "aider", "ollama",
    "kilo", "cline", "cursor", "copilot", "agy",
})


def _lockout_exe_signatures():
    """One-time fingerprint of denylisted binaries: basenames, (dev, ino)
    pairs (catches renames/hardlinks), and size->sha256 (catches copies).
    Built once per lockout so renames/copies/spoofs can't dodge the sweep."""
    names, inos, hashes = set(), set(), {}
    dirs = (os.environ.get("PATH", "").split(":")
            + [os.path.expanduser("~/.local/bin"),
               os.path.expanduser("~/.opencode/bin")])
    seen = set()
    for name in LOCKOUT_PROC_DENY:
        names.add(name)
        for d in dirs:
            # real binary by PATH name (often our shim) + shadowed-aside real binary
            for p in (os.path.join(d, name),
                      os.path.join(d, "." + name + ".focus-real")):
                if p in seen:
                    continue
                seen.add(p)
                try:
                    if not os.path.exists(p):
                        continue
                    st = os.stat(p)
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    inos.add((st.st_dev, st.st_ino))
                    h = hashlib.sha256()
                    with open(p, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            h.update(chunk)
                    hashes.setdefault(st.st_size, set()).add(h.hexdigest())
                except Exception:
                    pass
    return {"inos": inos, "hashes": hashes}


def _exe_matches(exe, st, sigs):
    """True if a /proc/PID/exe target is a denylisted binary in any disguise."""
    try:
        if os.path.basename(exe.split(" (deleted)")[0]) in LOCKOUT_PROC_DENY:
            return True
        if (st.st_dev, st.st_ino) in sigs["inos"]:
            return True
        cands = sigs["hashes"].get(st.st_size)
        if cands:
            h = hashlib.sha256()
            with open(exe, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            if h.hexdigest() in cands:
                return True
    except Exception:
        pass
    return False


def _lockout_proc_sweep(sigs=None):
    """SIGKILL denylisted processes, however disguised. Best-effort;
    skips self and unowned PIDs."""
    me = os.getpid()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except Exception:
        return
    for pid in pids:
        try:
            if int(pid) == me:
                continue
            exe = os.readlink(f"/proc/{pid}/exe")
            if "ibus" in os.path.basename(exe) or "input-method" in exe:
                continue  # never kill input methods (Avro/Bengali typing)
            st = os.stat(exe)
            if _exe_matches(exe, st, sigs or {"inos": set(), "hashes": {}}):
                os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass


def _swallow_shell_keys(win):
    """App-level backup: swallow Super / Alt+Tab if compositor passes them through."""
    def _on_key(_w, ev):
        try:
            if ev.keyval in (Gdk.KEY_Super_L, Gdk.KEY_Super_R):
                return True
            mods = ev.state & Gtk.accelerator_get_default_mod_mask()
            if mods & Gdk.ModifierType.MOD1_MASK and ev.keyval in (
                    Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
                return True
        except Exception:
            pass
        return False
    win.connect("key-press-event", _on_key)


def _arm_focus_guard(win, is_lockout=False):
    """Re-raise + re-fullscreen the window if anything steals focus or shrinks it."""
    def _reassert():
        try:
            if not win.get_visible():
                return
            if is_lockout and _typing_win is not None:
                return
            gdkwin = win.get_window()
            if (gdkwin is not None
                    and not (gdkwin.get_state() & Gdk.WindowState.FULLSCREEN)):
                win.fullscreen()
            win.present()
        except Exception:
            pass

    def _on_focus_out(*_):
        GLib.idle_add(_reassert)
        return False
    win.connect("focus-out-event", _on_focus_out)

    def _guard():
        _reassert()
        return True
    return GLib.timeout_add(500, _guard)


def _new_card(width=None):
    if width is None:
        width = 560 if is_compact_screen() else 880
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    outer.set_halign(Gtk.Align.CENTER)
    outer.set_valign(Gtk.Align.CENTER)
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2 * SPACE)
    card.get_style_context().add_class("card")
    card.set_size_request(width, -1)
    card.set_margin_top(20); card.set_margin_bottom(20)
    card.set_margin_start(20); card.set_margin_end(20)
    outer.add(card)
    return outer, card


def _accent():
    a = Gtk.Box()
    a.get_style_context().add_class("accent")
    a.set_halign(Gtk.Align.CENTER)
    a.set_size_request(120, 6)
    return a


def _eyebrow(text):
    e = Gtk.Label(label=text)
    e.get_style_context().add_class("eyebrow")
    e.set_halign(Gtk.Align.CENTER)
    return e


def _sudo_check(pw):
    """Verify the system (sudo) password. Never stored or logged."""
    try:
        if not pw:
            return False
        r = _run(["sudo", "-S", "-v"], input=pw + "\n",
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception as e:
        print(f"sudo verify error: {e}", flush=True)
        return False


def confirm_dialog(parent, title, heading, message, ok_label, danger=True,
                   passcode=None):
    """Modal confirmation. passcode: None | 'verify' | 'set'. Returns True only deliberate Confirm."""
    dlg = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dlg.set_keep_above(True)
    dlg.set_skip_taskbar_hint(True)
    try:
        dlg.get_content_area().set_spacing(12)
        dlg.get_content_area().set_margin_top(20)
        dlg.get_content_area().set_margin_bottom(8)
        dlg.get_content_area().set_margin_start(24)
        dlg.get_content_area().set_margin_end(24)
    except Exception:
        pass
    t = Gtk.Label(label=heading)
    t.get_style_context().add_class("dialog-title")
    t.set_halign(Gtk.Align.START)
    m = Gtk.Label(label=message)
    m.get_style_context().add_class("body")
    m.set_halign(Gtk.Align.START)
    dlg.get_content_area().pack_start(t, False, False, 0)
    dlg.get_content_area().pack_start(m, False, False, 0)
    btn_cancel = dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
    btn_ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
    if danger:
        btn_ok.get_style_context().add_class("btn-danger")
    else:
        btn_ok.get_style_context().add_class("btn-primary")
    btn_cancel.get_style_context().add_class("btn-secondary")
    err = Gtk.Label(label="")
    err.get_style_context().add_class("caption")
    pin_entry = None
    if passcode == "verify":
        plab = Gtk.Label(label="Enter system (sudo) password:")
        plab.get_style_context().add_class("body")
        plab.set_halign(Gtk.Align.START)
        dlg.get_content_area().pack_start(plab, False, False, 0)
        pin_entry = Gtk.Entry()
        pin_entry.set_visibility(False)
        pin_entry.set_max_length(64)
        pin_entry.set_placeholder_text("System password")
        dlg.get_content_area().pack_start(pin_entry, False, False, 0)
    dlg.get_content_area().pack_start(err, False, False, 0)
    dlg.set_default_response(Gtk.ResponseType.CANCEL)  # Enter = safe, Esc = cancel
    dlg.show_all()
    if pin_entry:
        pin_entry.grab_focus()
        pin_entry.connect("activate", lambda *_: dlg.response(Gtk.ResponseType.OK))
    while True:
        resp = dlg.run()
        if resp != Gtk.ResponseType.OK:
            dlg.destroy()
            return False
        if passcode == "verify":
            pw = pin_entry.get_text()
            pin_entry.set_text("")
            if _sudo_check(pw):
                del pw
                dlg.destroy()
                return True
            err.set_text("Wrong system password — try again.")
        else:
            dlg.destroy()
            return True


def confirm_power_off(parent):
    """Modal confirmation. Returns True only on deliberate Confirm."""
    return confirm_dialog(parent, "Confirm Power Off", "Power off this device?",
                          "The machine will shut down immediately.\nUnsaved work may be lost.",
                          "\u23fb  Power Off", danger=True)


def confirm_suspend(parent, label):
    """Emergency lockout confirmation WITH system-password check."""
    return confirm_dialog(parent, "Confirm Emergency Lockout",
                          f"Start emergency lockout {label}?",
                          "The screen stays on so this computer does not get stuck in sleep.\nThe countdown resumes after a power-off or restart.",
                          "\u23f8  Start Lockout", danger=False, passcode="verify")

TYPING_DIR = os.path.expanduser("~/Documents/typing test")
GOALS_PATH = os.path.join(LEDGER_DIR, "goals.json")


def _load_goals():
    try:
        with open(GOALS_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_goals(goals):
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        with open(GOALS_PATH, "w") as f:
            json.dump(goals, f)
    except Exception as e:
        print(f"goals save error: {e}", flush=True)


def _add_goal(text, date, time_):
    goals = _load_goals()
    goals.append({"id": time.time_ns(), "text": text.strip(),
                  "date": date.strip(), "time": time_.strip(),
                  "done": False, "created": time.time()})
    _save_goals(goals)


def _toggle_goal(gid):
    goals = _load_goals()
    for g in goals:
        if g.get("id") == gid:
            g["done"] = not g.get("done")
    _save_goals(goals)


def _delete_goal(gid):
    _save_goals([g for g in _load_goals() if g.get("id") != gid])


_typing_win = None
_typing_view = None
_typing_parent = None
_typing_auto_closing = False

_study_win = None
_study_parent = None
_study_auto_closing = False


def _save_typing_text(text):
    """Save typing content. Returns path or None. Visible errors via notify."""
    if not (text or "").strip():
        notify("Nothing to save yet.")
        return None
    try:
        os.makedirs(TYPING_DIR, exist_ok=True)
        path = os.path.join(TYPING_DIR, time.strftime("typing-%Y%m%d-%H%M%S.txt"))
        with open(path, "w") as f:
            f.write(text)
        notify(f"Typing saved: {os.path.basename(path)}")
        return path
    except Exception as e:
        print(f"typing save error: {e}", flush=True)
        notify("Typing save failed.")
        return None


def _close_typing_autosave():
    """Called when lockout ends while typing is open: autosave + close all typing UI."""
    global _typing_auto_closing
    if _typing_win is None:
        return
    try:
        buf = _typing_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        if text.strip():
            _save_typing_text(text)
            print("typing autosaved on lockout end.", flush=True)
    except Exception as e:
        print(f"typing autosave error: {e}", flush=True)
    _typing_auto_closing = True
    try:
        _typing_win.destroy()  # destroy, NOT close (delete-event is blocked)
    except Exception:
        pass


def show_typing_test(parent=None):
    """Fullscreen black typing practice inside lockout. Suspension continues."""
    global _typing_win, _typing_view, _typing_parent, _typing_auto_closing
    _typing_auto_closing = False
    if parent is not None:
        _typing_parent = parent
        try:
            parent.hide()  # lockout steps aside so typing is visible + focused
        except Exception:
            pass
    win = _new_fullscreen("Typing Test")
    _typing_win = win
    win.set_modal(True)
    _swallow_shell_keys(win)
    _tguard = _arm_focus_guard(win)
    css = Gtk.CssProvider()
    css.load_from_data(b"""
    .typewin { background: #000000; }
    .typehead { font-family: Inter, sans-serif; font-size: 13px; color: #9ca3af;
                letter-spacing: 2px; font-weight: bold; }
    textview text { background: #000000; color: #e5e7eb;
                    font: 20px Monospace; padding: 24px; }
    """)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win.get_style_context().add_class("typewin")
    win.connect("delete-event", lambda *_: True)  # Alt+F4 blocked; use Back/Esc
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    outer.set_margin_top(30); outer.set_margin_bottom(30)
    outer.set_margin_start(40); outer.set_margin_end(40)
    head = Gtk.Label(label="TYPING TEST \u2014 LOCKED MODE (SUSPENSION CONTINUES)")
    head.get_style_context().add_class("typehead")
    head.set_halign(Gtk.Align.CENTER)
    scroll = Gtk.ScrolledWindow()
    scroll.set_hexpand(True); scroll.set_vexpand(True)
    view = Gtk.TextView()
    view.set_wrap_mode(Gtk.WrapMode.WORD)
    view.set_accepts_tab(True)
    scroll.add(view)
    _typing_view = view
    _tbuf = view.get_buffer()
    _block_note = Gtk.Label()
    _block_note.set_markup('<span foreground="#fca5a5">\u26d4 Focus Input Filter: '
                           'explicit content blocked</span>')
    _block_note.get_style_context().add_class("typehead")
    _block_note.set_halign(Gtk.Align.CENTER)
    _block_note.set_no_show_all(True)

    _good_text = {"v": ""}

    def _typing_filter(buf, _loc, text, _length):
        try:
            inserted = text or ""
            existing = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
            if not _explicit_hit(existing + inserted):
                _good_text["v"] = existing + inserted
                return False
            try:
                buf.stop_emission_by_name("insert-text")  # do not let it land
            except Exception:
                pass
            if len(inserted) == 1:
                def _trim():
                    cur = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
                    cut = len(cur)
                    while cut > 0 and not cur[cut - 1].isspace():
                        cut -= 1
                    if cut < len(cur):
                        buf.set_text(cur[:cut])
                        buf.place_cursor(buf.get_end_iter())
                    return False
                GLib.idle_add(_trim)
            else:
                good = _good_text["v"]
                def _revert():
                    buf.set_text(good)
                    buf.place_cursor(buf.get_end_iter())
                    return False
                GLib.idle_add(_revert)
            _block_note.show()
            Gdk.beep()
            GLib.timeout_add_seconds(3, lambda: (_block_note.hide(), False)[1])
        except Exception:
            pass
        return False

    _tbuf.connect("insert-text", _typing_filter)
    status_lbl = Gtk.Label(label="Type above. Save keeps a .txt copy.")
    status_lbl.get_style_context().add_class("typehead")
    status_lbl.set_halign(Gtk.Align.CENTER)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.set_halign(Gtk.Align.CENTER)
    save_btn = Gtk.Button(label="Save")
    save_btn.get_style_context().add_class("btn-primary")
    save_btn.set_size_request(140, 46)
    back_btn = Gtk.Button(label="Back to Lockout")
    back_btn.get_style_context().add_class("btn-ghost")
    back_btn.set_size_request(180, 46)

    def _save(*_):
        buf = view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        path = _save_typing_text(text)
        if path:
            try:
                status_lbl.set_text(f"Saved {os.path.basename(path)} ✓  ({time.strftime('%H:%M:%S')})")
            except Exception:
                pass
        else:
            try:
                status_lbl.set_text("Nothing saved — type something first.")
            except Exception:
                pass
    save_btn.connect("clicked", _save)
    back_btn.connect("clicked", lambda *_: win.destroy())  # destroy, NOT close

    def _on_key(_w, ev):
        try:
            if ev is not None and ev.keyval == Gdk.KEY_Escape:
                win.destroy()  # destroy, NOT close
                return True
        except Exception:
            pass
        return False
    win.connect("key-press-event", _on_key)
    row.pack_start(save_btn, False, False, 0)
    row.pack_start(back_btn, False, False, 0)
    outer.pack_start(head, False, False, 0)
    outer.pack_start(_block_note, False, False, 0)
    outer.pack_start(scroll, True, True, 0)
    outer.pack_start(status_lbl, False, False, 0)
    outer.pack_start(row, False, False, 0)
    win.add(outer)

    def _on_typing_destroy(*_):
        global _typing_win, _typing_view, _typing_parent
        try:
            GLib.source_remove(_tguard)
        except Exception:
            pass
        auto = _typing_auto_closing
        _typing_win = None
        _typing_view = None
        if not auto and _typing_parent is not None:
            try:
                _typing_parent.show()
                _typing_parent.present()
            except Exception:
                pass
        _typing_parent = None
        Gtk.main_quit()
    win.connect("destroy", _on_typing_destroy)
    win.show_all()
    win.present()
    view.grab_focus()
    Gtk.main()


def _suspend_value_dialog(title, label, minimum, maximum, step=1, is_combo=False):
    """Small dialog returning chosen int, or None on cancel."""
    dlg = Gtk.Dialog(title=title, modal=True)
    dlg.set_keep_above(True)
    dlg.set_skip_taskbar_hint(True)
    try:
        dlg.get_content_area().set_spacing(12)
        dlg.get_content_area().set_margin_top(20)
        dlg.get_content_area().set_margin_bottom(12)
        dlg.get_content_area().set_margin_start(24)
        dlg.get_content_area().set_margin_end(24)
    except Exception:
        pass
    lab = Gtk.Label(label=label)
    lab.get_style_context().add_class("body")
    lab.set_halign(Gtk.Align.START)
    dlg.get_content_area().pack_start(lab, False, False, 0)
    if is_combo:
        widget = Gtk.ComboBoxText()
        for i in range(minimum, maximum + 1):
            widget.append_text(f"{i} day{'s' if i > 1 else ''}")
        widget.set_active(0)
        get_val = lambda: widget.get_active() + minimum
    else:
        widget = Gtk.SpinButton.new_with_range(minimum, maximum, step)
        widget.set_value(minimum)
        get_val = lambda: int(widget.get_value())
    dlg.get_content_area().pack_start(widget, False, False, 0)
    dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dlg.add_button("Suspend", Gtk.ResponseType.OK)
    dlg.set_default_response(Gtk.ResponseType.OK)
    dlg.show_all()
    resp = dlg.run()
    val = get_val() if resp == Gtk.ResponseType.OK else None
    dlg.destroy()
    return val


_chosen_suspend_minutes = None


def _take_chosen_minutes():
    global _chosen_suspend_minutes
    m = _chosen_suspend_minutes or SUSPEND_MINUTES
    _chosen_suspend_minutes = None
    return m


def _choice_dialog(parent):
    """Emergency length choice. Returns 'minutes'|'hours'|'days'|None."""
    dlg = Gtk.Dialog(title="Emergency Lockout", transient_for=parent, modal=True)
    dlg.set_keep_above(True)
    dlg.set_skip_taskbar_hint(True)
    try:
        dlg.get_content_area().set_spacing(12)
        dlg.get_content_area().set_margin_top(20)
        dlg.get_content_area().set_margin_bottom(12)
        dlg.get_content_area().set_margin_start(24)
        dlg.get_content_area().set_margin_end(24)
    except Exception:
        pass
    lab = Gtk.Label(label="Choose suspension length:")
    lab.get_style_context().add_class("body")
    lab.set_halign(Gtk.Align.START)
    dlg.get_content_area().pack_start(lab, False, False, 0)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.set_halign(Gtk.Align.CENTER)
    dlg.get_content_area().pack_start(row, False, False, 0)
    b_min = Gtk.Button(label="Minutes")
    b_min.get_style_context().add_class("btn-secondary")
    b_hr = Gtk.Button(label="Hours")
    b_hr.get_style_context().add_class("btn-secondary")
    b_day = Gtk.Button(label="Days")
    b_day.get_style_context().add_class("btn-secondary")
    for b in (b_min, b_hr, b_day):
        b.set_size_request(110, 44)
        row.pack_start(b, False, False, 0)
    b_min.connect("clicked", lambda *_: dlg.response(1))
    b_hr.connect("clicked", lambda *_: dlg.response(2))
    b_day.connect("clicked", lambda *_: dlg.response(3))
    dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dlg.show_all()
    resp = dlg.run()
    dlg.destroy()
    return {1: "minutes", 2: "hours", 3: "days"}.get(resp)


def pick_suspend_duration(parent):
    """Same flow as tray Emergency Lockout. Returns minutes or None. Pure (no execution)."""
    choice = _choice_dialog(parent)
    if choice == "minutes":
        v = _suspend_value_dialog("Suspend Minutes", "Suspend for how many minutes? (max 60)", 1, 60)
        if v and confirm_suspend(parent, f"for {v} minute{'s' if v > 1 else ''}"):
            return v
    elif choice == "hours":
        v = _suspend_value_dialog("Suspend Hours", "Suspend for how many hours? (max 24)", 1, 24)
        if v and confirm_suspend(parent, f"for {v} hour{'s' if v > 1 else ''}"):
            return v * 60
    elif choice == "days":
        v = _suspend_value_dialog("Suspend Days", "Suspend for how many days? (max 7)", 1, 7, is_combo=True)
        if not v:
            return None
        label = f"for {v} day{'s' if v > 1 else ''}"
        if not confirm_suspend(parent, label + " — confirm (1/2)"):
            return None
        if not confirm_suspend(parent, label + " — FINAL confirm (2/2)"):
            return None
        return v * 24 * 60
    return None


def tray_suspend_minutes():
    v = _suspend_value_dialog("Suspend Minutes", "Suspend for how many minutes? (max 60)", 1, 60)
    if v and confirm_suspend(None, f"for {v} minute{'s' if v > 1 else ''}"):
        do_suspend(v)


def tray_suspend_hours():
    v = _suspend_value_dialog("Suspend Hours", "Suspend for how many hours? (max 24)", 1, 24)
    if v and confirm_suspend(None, f"for {v} hour{'s' if v > 1 else ''}"):
        do_suspend(v * 60)


def tray_suspend_days(parent=None):
    v = _suspend_value_dialog("Suspend Days", "Suspend for how many days? (max 7)", 1, 7, is_combo=True)
    if not v:
        return
    label = f"for {v} day{'s' if v > 1 else ''}"
    if not confirm_suspend(parent, label + " — confirm (1/2)"):
        return
    if not confirm_suspend(parent, label + " — FINAL confirm (2/2)"):
        return
    do_suspend(v * 24 * 60)


def _tray_show_reminder():
    try:
        action = show_alert()
    except Exception as e:
        print(f"tray reminder error: {e}", flush=True)
        return
    print(f"tray reminder action={action}", flush=True)
    if action == "suspend":
        do_suspend(_take_chosen_minutes())
    elif action == "lockout":
        do_pseudo_lockout(SUSPEND_MINUTES)
    elif action == "poweroff":
        power_off()
    elif action == "goal":
        try:
            show_goal_manager()
        except Exception as e:
            print(f"goal manager error: {e}", flush=True)


def build_tray_menu():
    menu = Gtk.Menu()

    def _item(label, cb, sensitive=True):
        it = Gtk.MenuItem(label=label)
        it.set_sensitive(sensitive)
        it.connect("activate", lambda *_: cb())
        menu.append(it)
        return it

    sub = Gtk.Menu()
    for label, cb in (("Minutes\u2026 (max 60)", tray_suspend_minutes),
                      ("Hours\u2026 (max 24)", tray_suspend_hours),
                      ("Days\u2026 (max 7, double confirm)", lambda: tray_suspend_days())):
        mi = Gtk.MenuItem(label=label)
        mi.connect("activate", lambda *_w, f=cb: f())
        sub.append(mi)
    root = Gtk.MenuItem(label="\u23f8  Emergency Lockout (screen stays on)")
    root.set_submenu(sub)
    menu.append(root)
    lock_item = Gtk.MenuItem(label="\U0001F512  Focus Lockout 30 min (no sleep)")
    lock_item.connect("activate", lambda *_: do_pseudo_lockout(SUSPEND_MINUTES))
    menu.append(lock_item)
    setup_item = Gtk.MenuItem(label="Telegram setup mode (15 min)")
    if setup_mode_active():
        setup_item.set_label("Telegram setup mode: ON \u2014 click to stop")

    def _toggle_setup(*_):
        if setup_mode_active():
            disable_setup_mode()
            setup_item.set_label("Telegram setup mode (15 min)")
        else:
            enable_setup_mode()
            setup_item.set_label("Telegram setup mode: ON \u2014 click to stop")

    setup_item.connect("activate", _toggle_setup)
    menu.append(setup_item)
    menu.append(Gtk.SeparatorMenuItem())
    def _waste_text():
        try:
            with open(os.path.join(LEDGER_DIR, f"ledger-{time.strftime('%Y-%m-%d')}.json")) as f:
                d = json.load(f)
            return f"Waste today: {_fmt_short(d.get('waste', 0))}"
        except Exception:
            return "Waste today: \u2014"

    waste_item = _item(_waste_text(), lambda: None, sensitive=False)

    def _refresh_waste(*_):
        try:
            waste_item.set_label(_waste_text())
        except Exception:
            pass
        return True

    menu.connect("show", lambda *_: _refresh_waste())  # refresh on every open
    GLib.timeout_add_seconds(30, _refresh_waste)  # + live refresh every 30s
    _item("Show Reminder Now", _tray_show_reminder)
    # NOTE: no Quit item by design — the tray stays persistent (see xdg-desktop-focus-compliance-enforcer-8f2e1a9c-tray.service).
    menu.show_all()
    return menu


def tray_loop():
    """Top-bar tray ONLY (persistent, no quit, no separate button).
    Waits for the AppIndicator package instead of falling back."""
    while not _has_display():
        print("Tray waiting for display…", flush=True)
        time.sleep(15)
    _AI = None
    while True:
        if _AI is None:
            try:
                import gi
                gi.require_version("AyatanaAppIndicator3", "0.1")
                from gi.repository import AyatanaAppIndicator3 as _AI
            except Exception as e:
                print("Tray waiting for AppIndicator package "
                      "(run: sudo apt install gir1.2-ayatanaappindicator3-0.1). "
                      f"({e})", flush=True)
                time.sleep(60)
                continue
        try:
            ind = _AI.Indicator.new("focus-reminder", "focus-reminder",
                                    _AI.IndicatorCategory.APPLICATION_STATUS)
            ind.set_status(_AI.IndicatorStatus.ACTIVE)
            ind.set_menu(build_tray_menu())
            print("Tray: top-bar indicator active.", flush=True)
            Gtk.main()  # returns only if our own dialog quit it wrongly; rebuild guard below
        except Exception as e:
            print(f"tray error: {e}", flush=True)
        time.sleep(10)


# ---------- main reminder ----------

def _goal_rows_box(interactive=False, on_change=None, limit=None):
    """Modern goals preview rows. Interactive (manager) or read-only (alert/lockout)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE)
    goals = _load_goals()
    if not goals:
        e = Gtk.Label(label="No goals yet \u2014 open Goal Manager to add.")
        e.get_style_context().add_class("caption")
        e.set_halign(Gtk.Align.CENTER)
        box.pack_start(e, False, False, 0)
        return box
    shown = goals if not limit else goals[:limit]
    for g in shown:
        done = bool(g.get("done"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=int(1.5 * SPACE))
        row.get_style_context().add_class("goalrow")
        if done:
            row.get_style_context().add_class("goaldonerow")
        if interactive:
            tgl = Gtk.Button(label="\u25cf" if done else "\u25cb")
            tgl.get_style_context().add_class("tgl")
            tgl.get_style_context().add_class("tgldone" if done else "tglplain")
            tgl.set_tooltip_text("Mark done / undone")
            tgl.connect("clicked", lambda *_w, i=g.get("id"): (_toggle_goal(i), on_change()))
            row.pack_start(tgl, False, False, 0)
        else:
            st = Gtk.Label()
            st.set_markup(f"<span color='#16a34a'>\u25cf</span>" if done
                          else f"<span color='#2563eb'>\u25cb</span>")
            row.pack_start(st, False, False, 0)
        txt = Gtk.Label()
        txt.set_halign(Gtk.Align.START)
        txt.set_hexpand(True)
        txt.set_line_wrap(True)
        esc = GLib.markup_escape_text(g.get("text", ""))
        txt.set_markup(f"<s>{esc}</s>" if done else esc)
        txt.get_style_context().add_class("goaldone" if done else "goaltext")
        row.pack_start(txt, True, True, 0)
        for chip_txt, chip_cls in ((g.get("date", ""), "chipdate"),
                                   (g.get("time", ""), "chiptime")):
            if chip_txt:
                ch = Gtk.Label(label=chip_txt)
                ch.get_style_context().add_class("chip")
                ch.get_style_context().add_class(chip_cls)
                row.pack_start(ch, False, False, 0)
        if interactive:
            dele = Gtk.Button(label="\u2715")
            dele.get_style_context().add_class("delbtn")
            dele.set_tooltip_text("Delete goal")
            dele.connect("clicked", lambda *_w, i=g.get("id"): (_delete_goal(i), on_change()))
            row.pack_start(dele, False, False, 0)
        box.pack_start(row, False, False, 0)
    if limit and len(goals) > limit:
        more = Gtk.Label(label=f"+{len(goals) - limit} more in Goal Manager")
        more.get_style_context().add_class("caption")
        more.set_halign(Gtk.Align.CENTER)
        box.pack_start(more, False, False, 0)
    return box


def show_goal_manager():
    """Fullscreen goal manager: Goal | Date | Time inputs + live modern preview."""
    win = _new_fullscreen("Goal Manager")
    _apply_css(win)
    outer, card = _new_card(720)

    badge = _eyebrow("GOAL MANAGER")
    title = Gtk.Label(label="Set Your Goals")
    title.get_style_context().add_class("headline")
    title.set_halign(Gtk.Align.CENTER)
    sub = Gtk.Label(label="Plan with date and time. The preview below updates live.")
    sub.get_style_context().add_class("body")
    sub.set_halign(Gtk.Align.CENTER)

    form = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=SPACE)
    form.set_halign(Gtk.Align.FILL)
    goal_entry = Gtk.Entry()
    goal_entry.set_placeholder_text("Goal — e.g. Finish physics chapter 5")
    goal_entry.set_hexpand(True)
    goal_entry.set_halign(Gtk.Align.FILL)
    date_entry = Gtk.Entry()
    date_entry.set_placeholder_text("YYYY-MM-DD")
    date_entry.set_width_chars(12)
    date_entry.set_max_length(10)
    time_entry = Gtk.Entry()
    time_entry.set_placeholder_text("HH:MM")
    time_entry.set_width_chars(7)
    time_entry.set_max_length(5)
    form.pack_start(goal_entry, True, True, 0)
    form.pack_start(date_entry, False, False, 0)
    form.pack_start(time_entry, False, False, 0)

    quick = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=SPACE)
    quick.set_halign(Gtk.Align.CENTER)
    today_btn = Gtk.Button(label="Today")
    today_btn.get_style_context().add_class("mini")
    today_btn.connect("clicked", lambda *_: date_entry.set_text(time.strftime("%Y-%m-%d")))
    now_btn = Gtk.Button(label="Now")
    now_btn.get_style_context().add_class("mini")
    now_btn.connect("clicked", lambda *_: time_entry.set_text(time.strftime("%H:%M")))
    add_btn = Gtk.Button(label="+  Add Goal")
    add_btn.get_style_context().add_class("btn-primary")
    add_btn.set_size_request(150, 44)
    quick.pack_start(today_btn, False, False, 0)
    quick.pack_start(now_btn, False, False, 0)
    quick.pack_start(add_btn, False, False, 0)

    err = Gtk.Label(label="")
    err.get_style_context().add_class("caption")
    err.set_halign(Gtk.Align.CENTER)

    scroll = Gtk.ScrolledWindow()
    scroll.set_min_content_height(220)
    scroll.set_vexpand(True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    glist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE)
    scroll.add(glist)

    def refresh():
        for c in list(glist.get_children()):
            glist.remove(c)
        glist.pack_start(_goal_rows_box(interactive=True, on_change=refresh), False, False, 0)
        glist.show_all()

    def _add(*_):
        if not goal_entry.get_text().strip():
            err.set_text("Write a goal first.")
            goal_entry.grab_focus()
            return
        err.set_text("")
        _add_goal(goal_entry.get_text(), date_entry.get_text(), time_entry.get_text())
        goal_entry.set_text("")
        date_entry.set_text("")
        time_entry.set_text("")
        goal_entry.grab_focus()
        refresh()
    add_btn.connect("clicked", _add)
    goal_entry.connect("activate", _add)

    close_btn = Gtk.Button(label="Close")
    close_btn.get_style_context().add_class("btn-ghost")
    close_btn.set_size_request(140, 46)
    close_btn.set_halign(Gtk.Align.CENTER)
    close_btn.connect("clicked", lambda *_: win.close())  # destroy quits once

    for w in (_accent(), badge, title, sub, form, quick, err, scroll, close_btn):
        card.pack_start(w, False, False, 0)
    win.add(outer)
    win.connect("destroy", lambda *_: Gtk.main_quit())
    win.show_all()
    win.present()
    refresh()
    goal_entry.grab_focus()
    Gtk.main()


def show_alert():
    """Fullscreen alert. Returns: close | goal | suspend | poweroff."""
    result = {"action": "close", "done": False, "tick": None}

    def _stop_tick():
        if result.get("tick"):
            try:
                GLib.source_remove(result["tick"])
            except Exception:
                pass
            result["tick"] = None

    buttons = []

    def choose(action):
        def _cb(*_):
            if result["done"]:  # prevent double activation
                return
            result["done"] = True
            for b in buttons:
                b.set_sensitive(False)
            if action == "poweroff":
                for b in buttons:
                    b.set_sensitive(True)
                result["done"] = False
                if confirm_power_off(win):
                    result["action"] = "poweroff"
                    win.close()  # destroy handler performs the single main_quit
                return
            if action == "emergency":
                global _chosen_suspend_minutes
                for b in buttons:
                    b.set_sensitive(True)
                result["done"] = False
                m = pick_suspend_duration(win)
                if m:
                    _chosen_suspend_minutes = m
                    result["action"] = "suspend"
                    win.close()  # destroy handler performs the single main_quit
                return
            result["action"] = action
            win.close()  # destroy handler performs the single main_quit
        return _cb

    global _quote_index
    qtext, qref = QUOTES[_quote_index % len(QUOTES)]
    _quote_index += 1

    win = _new_fullscreen("Focus Reminder")
    _apply_css(win)
    outer, card = _new_card()

    badge = _eyebrow("PROFESSIONAL REMINDER")

    prio = Gtk.Label(label="\u25cf PRIORITY")
    prio.get_style_context().add_class("prio")
    prio.set_halign(Gtk.Align.CENTER)
    prio.set_tooltip_text("Your priority work comes first.")
    topbadges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=SPACE)
    topbadges.set_halign(Gtk.Align.CENTER)
    topbadges.pack_start(badge, False, False, 0)
    topbadges.pack_start(prio, False, False, 0)

    title = Gtk.Label(label="Stop. Refocus on Your Priority Work.")
    title.get_style_context().add_class("headline")
    title.set_halign(Gtk.Align.CENTER)
    title.set_line_wrap(True)
    title.set_justify(Gtk.Justification.CENTER)

    # single bottom action row: all 4 buttons in one line (frees vertical space)
    row_all = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=SPACE)
    row_all.set_halign(Gtk.Align.CENTER)
    close_btn = Gtk.Button(label="\u2713  Back to Work")
    close_btn.get_style_context().add_class("btn-primary")
    close_btn.set_size_request(185, 48)
    close_btn.set_tooltip_text("Dismiss this reminder and restart the 10-minute focus cycle. (Enter)")
    close_btn.set_can_default(True)
    close_btn.connect("clicked", choose("close"))
    goal_btn = Gtk.Button(label="\u25ce  Goal")
    goal_btn.get_style_context().add_class("btn-secondary")
    goal_btn.set_size_request(115, 48)
    goal_btn.set_tooltip_text("Open the Goal Manager to plan tasks with dates.")
    goal_btn.connect("clicked", choose("goal"))
    suspend_btn = Gtk.Button(label="\u23f8  Emergency Lockout")
    suspend_btn.get_style_context().add_class("btn-secondary")
    suspend_btn.set_size_request(135, 48)
    suspend_btn.set_tooltip_text(
        "Start an emergency lockout for minutes, hours or days. Screen stays on; password required.")
    suspend_btn.connect("clicked", choose("emergency"))
    lock_btn = Gtk.Button(label="\U0001F512  Lock 30")
    lock_btn.get_style_context().add_class("btn-secondary")
    lock_btn.set_size_request(135, 48)
    lock_btn.set_tooltip_text("Focus lockout for 30 min — device stays ON but locked. Cannot be skipped.")
    lock_btn.connect("clicked", choose("lockout"))
    power_btn = Gtk.Button(label="\u23fb  Power Off")
    power_btn.get_style_context().add_class("btn-danger")
    power_btn.set_size_request(125, 48)
    power_btn.set_tooltip_text("Shut down this machine. Requires confirmation.")
    power_btn.connect("clicked", choose("poweroff"))
    for b in (close_btn, goal_btn, suspend_btn, lock_btn, power_btn):
        row_all.pack_start(b, False, False, 0)

    hint = Gtk.Label(label="Emergency Lockout keeps this device on and countdown-locked. "
                           "Lock 30 starts a fixed 30-minute lockout. Power Off asks for confirmation first.")
    hint.get_style_context().add_class("caption")
    hint.set_halign(Gtk.Align.CENTER)
    hint.set_line_wrap(True)

    buttons.extend([close_btn, goal_btn, suspend_btn, lock_btn, power_btn])

    for w in (_accent(), topbadges, title):
        card.pack_start(w, False, False, 0)

    goals_head = Gtk.Label(label="MY GOALS \u2014 edit in Goal Manager")
    goals_head.get_style_context().add_class("caption")
    goals_head.set_halign(Gtk.Align.CENTER)
    goalsbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=int(0.5 * SPACE))
    goalsbox.set_halign(Gtk.Align.FILL)
    goalsbox.set_hexpand(True)
    goalsbox.pack_start(goals_head, False, False, 0)
    goals_scroll = Gtk.ScrolledWindow()
    goals_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    goals_scroll.set_min_content_height(120)
    goals_scroll.set_max_content_height(240 if is_compact_screen() else 420)
    goals_scroll.set_halign(Gtk.Align.FILL)
    goals_scroll.set_hexpand(True)
    goals_scroll.set_vexpand(True)
    goals_scroll.add(_goal_rows_box(interactive=False, limit=30))
    goalsbox.pack_start(goals_scroll, True, True, 0)
    card.pack_start(goalsbox, True, True, 0)

    card.pack_start(row_all, False, False, int(0.75 * SPACE))
    card.pack_start(hint, False, False, 0)

    if is_compact_screen():
        # narrow/short viewport: stack all actions vertically, full width
        for _c in list(row_all.get_children()):
            row_all.remove(_c)
        card.remove(row_all)
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE)
        stack.set_halign(Gtk.Align.FILL)
        for b in buttons:
            b.set_size_request(0, 48)
            b.set_hexpand(True)
            b.set_halign(Gtk.Align.FILL)
            stack.pack_start(b, False, False, 0)
        card.pack_start(stack, False, False, 0)
        card.reorder_child(stack, 4)
        card.reorder_child(hint, 5)

    def _on_destroy(*_):
        _stop_tick()
        Gtk.main_quit()

    win.add(outer)
    win.connect("destroy", _on_destroy)
    win.show_all()
    win.present()  # force to front
    win.set_default(close_btn)   # Enter triggers primary
    close_btn.grab_focus()       # keyboard focus starts on primary
    try:
        print("\a", flush=True)
    except Exception:
        pass
    Gtk.main()
    return result["action"]


def show_lockout_window(minutes, pseudo=False):
    """Unclosable fullscreen countdown — device unusable until timer ends."""
    global _guard_id, _sweep_id
    schedule = _load_lockout_schedule(_load_suspend_state())
    if schedule is None:
        schedule = {"segments": [int(minutes * 60)], "stage": 0,
                    "stage_start": time.time(), "origin_start": time.time()}
    end = _schedule_end(schedule)

    win = _new_fullscreen("Focus Lockout" if pseudo else "Device Suspended")
    _apply_css(win)
    win.connect("delete-event", lambda *_: True)  # block Alt+F4 / close
    _compact = is_compact_screen()
    outer, card = _new_card(620 if _compact else 900)

    badge = _eyebrow("FOCUS LOCKOUT" if pseudo else "DEVICE SUSPENDED")

    time_label = Gtk.Label(label="")
    time_label.get_style_context().add_class("lock-time")
    time_label.set_halign(Gtk.Align.CENTER)

    sub = Gtk.Label(label=("This device is focus-locked but still on.\n"
                           "It will unlock automatically when the timer ends.")
                          if pseudo else
                          ("This device is locked. Please take a break.\n"
                           "It will unlock automatically when the timer ends."))
    sub.get_style_context().add_class("body")
    sub.set_halign(Gtk.Align.CENTER)
    sub.set_justify(Gtk.Justification.CENTER)

    total = max(1, sum(int(x) for x in schedule["segments"]))
    prog = Gtk.ProgressBar()
    prog.get_style_context().add_class("lockbar")
    prog.set_show_text(True)
    prog.set_halign(Gtk.Align.FILL)
    prog.set_hexpand(True)
    unlock_lbl = Gtk.Label(label=f"Unlocks at {time.strftime('%Y-%m-%d %H:%M', time.localtime(end))}")
    unlock_lbl.get_style_context().add_class("unlockat")
    unlock_lbl.set_halign(Gtk.Align.CENTER)

    goals_lbl = Gtk.Label(label="MY GOALS")
    goals_lbl.get_style_context().add_class("caption")
    goals_lbl.set_halign(Gtk.Align.CENTER)
    goals_box = _goal_rows_box(interactive=False, limit=20)
    goals_box.set_halign(Gtk.Align.FILL)
    goals_box.set_hexpand(True)
    goals_scroll = Gtk.ScrolledWindow()
    goals_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    goals_scroll.set_min_content_height(120)
    goals_scroll.set_max_content_height(300 if _compact else 460)
    goals_scroll.set_halign(Gtk.Align.FILL)
    goals_scroll.set_hexpand(True)
    goals_scroll.set_vexpand(True)
    goals_scroll.add(goals_box)

    lockrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    lockrow.set_halign(Gtk.Align.CENTER)
    study_btn = Gtk.Button(label="\U0001F4DA  Study")
    study_btn.get_style_context().add_class("btn-primary")
    study_btn.set_size_request(160, 48)
    study_btn.set_tooltip_text("Study links, PDF library and typing (suspension continues).")
    study_btn.connect("clicked", lambda *_: show_study_window(parent=win, end_ts=end))
    type_btn = Gtk.Button(label="Typing Test")
    type_btn.get_style_context().add_class("btn-ghost")
    type_btn.set_size_request(160, 48)
    type_btn.set_tooltip_text("Practice typing (black fullscreen). Suspension continues.")
    type_btn.connect("clicked", lambda *_: show_typing_test(parent=win))
    power_btn = Gtk.Button(label="\u23fb  Power Off")
    power_btn.get_style_context().add_class("btn-danger")
    power_btn.set_size_request(160, 48)
    power_btn.set_halign(Gtk.Align.CENTER)
    power_btn.set_tooltip_text("Shut down this machine. Requires confirmation.")
    lockrow.pack_start(study_btn, False, False, 0)
    lockrow.pack_start(type_btn, False, False, 0)
    lockrow.pack_start(power_btn, False, False, 0)

    extend_btn = Gtk.Button()
    extend_btn.get_style_context().add_class("btn-primary")
    extend_btn.set_halign(Gtk.Align.CENTER)
    def _off(*_):
        power_btn.set_sensitive(False)
        if confirm_power_off(win):
            power_off()
        else:
            power_btn.set_sensitive(True)
    power_btn.connect("clicked", _off)

    def _set_extend_button():
        stage_seconds = int(schedule["segments"][int(schedule["stage"])])
        if stage_seconds in (3 * 86400, 7 * 86400):
            extra_days = 3 if stage_seconds == 7 * 86400 else 7
            extend_btn.set_label(f"Extend by {extra_days} days")
            extend_btn.set_sensitive(len(schedule["segments"]) <= int(schedule["stage"]) + 1)
            extend_btn.show()
        else:
            extend_btn.hide()

    def _extend_lockout(*_):
        nonlocal end, total
        stage_seconds = int(schedule["segments"][int(schedule["stage"])])
        if (stage_seconds not in (3 * 86400, 7 * 86400)
                or len(schedule["segments"]) > int(schedule["stage"]) + 1):
            return
        extra_seconds = 3 * 86400 if stage_seconds == 7 * 86400 else 7 * 86400
        schedule["segments"].append(extra_seconds)
        _save_lockout_schedule(schedule)
        end = _schedule_end(schedule)
        total = max(1, sum(int(x) for x in schedule["segments"]))
        global suspend_until
        suspend_until = end
        _save_suspend_state(end)
        unlock_lbl.set_text(f"Unlocks at {time.strftime('%Y-%m-%d %H:%M', time.localtime(end))}")
        _set_extend_button()
        notify(f"Lockout extended by {extra_seconds // 86400} days.")

    extend_btn.connect("clicked", _extend_lockout)
    _set_extend_button()

    def _stage_end():
        return float(schedule["stage_start"]) + int(
            schedule["segments"][int(schedule["stage"])])

    def fmt_left():
        left = max(0, int(_stage_end() - time.time()))
        d, rem = divmod(left, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        if d:
            return f"{d}d {h:02d}:{m:02d}:{s:02d}"
        return f"{h:02d}:{m:02d}:{s:02d}"

    time_label.set_text(fmt_left())
    prog.set_fraction(0.0)
    prog.set_text("0% served")

    ending = {"phase": 0}
    checkpoint_tick = {"last": 0.0}

    def tick():
        nonlocal end, total
        if time.time() >= _stage_end() and int(schedule["stage"]) + 1 < len(schedule["segments"]):
            stage_boundary = _stage_end()
            schedule["stage"] = int(schedule["stage"]) + 1
            schedule["stage_start"] = stage_boundary
            _save_lockout_schedule(schedule)
            end = _schedule_end(schedule)
            total = max(1, sum(int(x) for x in schedule["segments"]))
            global suspend_until
            suspend_until = end
            _save_suspend_state(end)
            unlock_lbl.set_text(f"Unlocks at {time.strftime('%Y-%m-%d %H:%M', time.localtime(end))}")
            _set_extend_button()
        now = time.time()
        if now - checkpoint_tick["last"] >= 5:
            _save_suspend_state(end, mode="active")
            checkpoint_tick["last"] = now
        time_label.set_text(fmt_left())
        left = max(0, end - time.time())
        done_frac = min(1.0, max(0.0,
                                 (time.time() - float(schedule.get("origin_start", schedule["stage_start"])))
                                 / total))
        prog.set_fraction(done_frac)
        prog.set_text(f"{int(done_frac * 100)}% served")
        if time.time() >= _stage_end() and int(schedule["stage"]) + 1 >= len(schedule["segments"]):
            if _typing_win is not None and ending["phase"] == 0:
                # typing open: close it first (quits ITS nested loop),
                # then finish lockout on the next tick in our own loop
                ending["phase"] = 1
                _close_typing_autosave()
                return True
            if _study_win is not None and ending["phase"] == 0:
                # study open: close it (its own nested loop), then lockout
                ending["phase"] = 1
                global _study_auto_closing
                _study_auto_closing = True
                _study_win.destroy()
                return True
            win.destroy()  # destroy, NOT close (delete-event is blocked)
            return False
        return True

    if _compact:
        for w in (_accent(), badge, time_label, prog, unlock_lbl, sub,
                  extend_btn,
                  goals_lbl, goals_scroll, lockrow):
            card.pack_start(w, False, False, 0)
    else:
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2 * SPACE)
        left.set_hexpand(True)
        left.set_halign(Gtk.Align.FILL)
        for w in (badge, time_label, prog, unlock_lbl, sub,
                  extend_btn, lockrow):
            left.pack_start(w, False, False, 0)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE)
        right.set_hexpand(True)
        right.set_halign(Gtk.Align.FILL)
        right.pack_start(goals_lbl, False, False, 0)
        right.pack_start(goals_scroll, True, True, 0)
        panes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2 * SPACE)
        panes.set_halign(Gtk.Align.FILL)
        panes.set_hexpand(True)
        panes.pack_start(left, True, True, 0)
        panes.pack_start(right, True, True, 0)
        card.pack_start(_accent(), False, False, 0)
        card.pack_start(panes, True, True, 0)
    win.add(outer)
    _shell_saved = _gnome_lockdown()
    win.set_modal(True)
    _swallow_shell_keys(win)
    _guard_id = _arm_focus_guard(win, is_lockout=True)
    _lockout_sigs = _lockout_exe_signatures()
    _lockout_proc_sweep(_lockout_sigs)  # immediate: nothing usable survives
    _sweep_id = GLib.timeout_add_seconds(
        3, lambda: (_lockout_proc_sweep(_lockout_sigs), True)[1])

    def _on_lock_destroy(*_):
        for _sid in (_guard_id, _sweep_id):
            try:
                GLib.source_remove(_sid)
            except Exception:
                pass
        _gnome_restore(_shell_saved)
        Gtk.main_quit()
    win.connect("destroy", _on_lock_destroy)
    win.show_all()
    win.present()
    type_btn.grab_focus()
    GLib.timeout_add_seconds(1, tick)
    Gtk.main()

# ---------- study container ----------

STUDY_INBOX = os.path.expanduser("~/Documents/study-inbox")
DOWNLOAD_DIR = os.path.expanduser("~/Downloads")
STUDY_READING_STATE = os.path.join(LEDGER_DIR, "study-reading-state.json")
STUDY_RECENTS_STATE = os.path.join(LEDGER_DIR, "study-recents.json")
STUDY_WEB_DATA_DIR = os.path.join(LEDGER_DIR, "study-web-data")
STUDY_WEB_CACHE_DIR = os.path.join(LEDGER_DIR, "study-web-cache")
_study_web_context = None

# ---------- Input Filtering Service ----------
# Blocks explicit adult text from ever being typed/sent in the study browser and
# the typing test. Normalisation defeats leetspeak, spacing and zero-width tricks.
INPUT_FILTER_NAME = "Focus Input Filter"
STUDY_WEB_COOKIE_DB = os.path.join(STUDY_WEB_DATA_DIR, "cookies.sqlite")

BLOCKED_INPUT_WORDS = [
    # English / international
    "porn", "porno", "pornography", "xxx", "xnxx", "xvideo", "xhamster", "pornhub",
    "sex", "sexy", "sexting", "sexual", "nude", "nudes", "naked", "nsfw",
    "horny", "aroused", "orgasm", "erotic", "hentai", "milf", "incest",
    "boobs", "boob", "pussy", "dick", "cock", "penis", "cum", "semen",
    "blowjob", "handjob", "anal", "anus", "masturbate", "masturbation",
    "slut", "whore", "prostitute", "onlyfans", "camgirl", "rapist", "condom",
    "viagra", "chudai", "chudachudi", "chodachudi",
    # Bangla script
    "\u09aa\u09b0\u09cd\u09a8", "\u09aa\u09b0\u09cd\u09a3",
    "\u09b8\u09c7\u0995\u09cd\u09b8", "\u09b8\u09c7\u0995\u09cd\u09b8\u09bf",
    "\u09a8\u0997\u09cd\u09a8", "\u0989\u09b2\u0999\u09cd\u0997",
    "\u09b9\u09b0\u09cd\u09a8\u09bf",
    "\u09ac\u09cc\u09a6\u09bf", "\u09ad\u09be\u09ac\u09bf",
    "\u099a\u09c1\u09a6\u09be", "\u099a\u09cb\u09a6\u09be",
    "\u099a\u09c1\u09a6\u09bf", "\u099a\u09cb\u09a6\u09a8",
    "\u099a\u09c1\u09a6\u09be\u099a\u09c1\u09a6\u09bf",
    "\u099a\u09cb\u09a6\u09be\u099a\u09c1\u09a6\u09bf",
    # Bangla typed in Latin letters
    "boudi", "bhabi", "chuda", "choda", "chudi", "chudir", "chudar", "chodar",
    "chodon", "chudacudi", "chudacudir", "chudachudir",
    # Story / slang terms (single + pair words)
    "choti", "panu", "jouno", "jounota", "sexstory", "sexstories", "sexvideo",
    "sexchat", "sex golpo", "choti golpo", "panu golpo", "jouno golpo",
    "chudacudir golpo", "chudir golpo", "chodar golpo", "choda chudi golpo",
    "bangla choti", "bangla panu", "desi sex", "desi porn", "desi choti",
    "kamasutra", "kamsutra", "erotica", "smut", "tits", "titties", "cumshot",
    "gangbang", "orgy", "threesome", "dildo", "vibrator", "fetish", "bondage",
    "stripper", "stripclub", "brothel", "magi", "khanki", "randi", "mms",
    "mms video", "blue film", "adult video", "adult story", "sex story",
    # More Bangla script
    "\u099a\u099f\u09bf", "\u09aa\u09be\u09a8\u09c1",
    "\u09af\u09cc\u09a8", "\u09af\u09cc\u09a8\u09a4\u09be",
    "\u09af\u09cc\u09a8\u0997\u09b2\u09cd\u09aa",
    "\u09aa\u09b0\u09cd\u09a8\u09cb\u0997\u09cd\u09b0\u09be\u09ab\u09bf",
    "\u099a\u099f\u09bf \u0997\u09b2\u09cd\u09aa",
    "\u09aa\u09be\u09a8\u09c1 \u0997\u09b2\u09cd\u09aa",
    "\u09af\u09cc\u09a8 \u0997\u09b2\u09cd\u09aa",
    "\u0997\u09c1\u09a6", "\u09ad\u09cb\u09a6\u09be",
    "\u09af\u09cb\u09a8\u09bf", "\u09b8\u09cd\u09a4\u09a8",
    "\u09ac\u09c0\u09b0\u09cd\u09af", "\u09b9\u09b8\u09cd\u09a4\u09ae\u09c8\u09a5\u09c1\u09a8",
    "\u0995\u09be\u09ae\u09c1\u0995", "\u0985\u09b6\u09cd\u09b2\u09c0\u09b2",
    "\u0996\u09be\u09a8\u0995\u09bf", "\u09ae\u09be\u0997\u09bf",
    "\u09b0\u09be\u09a8\u09cd\u09a1\u09bf",
]

# Tokens too risky for compact (substring) matching -> whole-word match only.
_BLOCK_WORD_ONLY = {"sex", "anal", "cum", "dick", "cock", "nude", "xxx",
                    "magi", "randi", "bhabi", "mms"}
_BLOCK_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
                             "6": "g", "7": "t", "8": "b", "9": "g", "@": "a",
                             "$": "s", "!": "i", "|": "i"})
_ZERO_WIDTH = "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\ufeff"


def _input_norm(text):
    s = str(text or "").lower()
    s = "".join(ch for ch in s if ch not in _ZERO_WIDTH)
    s = unicodedata.normalize("NFKD", s)
    # strip Latin accents only (keep Bangla/Indic vowel signs)
    s = "".join(ch for ch in s if not (unicodedata.combining(ch) and ord(ch) < 0x0900))
    return s.translate(_BLOCK_LEET)


def _compact(text):
    s = re.sub(r"[^a-z0-9\u0980-\u09FF]+", "", _input_norm(text))
    return re.sub(r"(.)\1+", r"\1", s)


_BLOCK_NORM = [(w, re.sub(r"[^a-z0-9\u0980-\u09FF]+", " ",
                          _input_norm(w)).strip()) for w in BLOCKED_INPUT_WORDS]
_BLOCK_COMPACT = [(w, _compact(w)) for w in BLOCKED_INPUT_WORDS]


def _explicit_hit(text):
    """Return the matched explicit word, or None (normalisation-proof)."""
    if not text:
        return None
    spaced = " " + re.sub(r"[^a-z0-9\u0980-\u09FF]+", " ",
                          _input_norm(text)).strip() + " "
    for word, norm in _BLOCK_NORM:
        if norm and (" " + norm + " ") in spaced:
            return word
    comp = _compact(text)
    for word, comp_word in _BLOCK_COMPACT:
        if not comp_word or len(comp_word) < 4 or word in _BLOCK_WORD_ONLY:
            continue
        if comp_word in comp:
            return word
    return None

def _build_input_filter_js():
    import json as _json
    words = _json.dumps(BLOCKED_INPUT_WORDS)
    return r"""
(function () {
  if (window.__focusInputFilter) { return; }
  window.__focusInputFilter = true;
  var RAW = __WORDS__;
  var ZERO = /[\u200b-\u200f\u202a-\u202e\u2060\ufeff]/g;
  var LEET = {'0':'o','1':'i','3':'e','4':'a','5':'s','6':'g','7':'t','8':'b','9':'g',
              '@':'a','$':'s','!':'i','|':'i'};
  function norm(s) {
    s = String(s || '').toLowerCase().replace(ZERO, '');
    try { s = s.normalize('NFKD').replace(/[\u0300-\u036f]/g, ''); } catch (e) {}
    return s.replace(/[013456789@$!|]/g, function (c) { return LEET[c] || c; });
  }
  function spaced(s) {
    return ' ' + norm(s).replace(/[^a-z0-9\u0980-\u09FF]+/g, ' ').trim() + ' ';
  }
  function sq(s) {
    return norm(s).replace(/[^a-z0-9\u0980-\u09FF]+/g, '').replace(/(.)\1+/g, '$1');
  }
  var BAN = RAW.map(norm);
  var BANC = RAW.map(sq);
  var WORD_ONLY = {'sex':1,'anal':1,'cum':1,'dick':1,'cock':1,'nude':1,'xxx':1,
                   'magi':1,'randi':1,'bhabi':1,'mms':1};
  function hit(text) {
    if (!text) { return null; }
    var t = spaced(text);
    var i;
    for (i = 0; i < BAN.length; i++) {
      var w = BAN[i].replace(/[^a-z0-9\u0980-\u09FF]+/g, ' ').trim();
      if (w && t.indexOf(' ' + w + ' ') !== -1) { return RAW[i]; }
    }
    var c = sq(text);
    for (i = 0; i < BANC.length; i++) {
      var cw = BANC[i];
      if (!cw || cw.length < 4 || WORD_ONLY[RAW[i]]) { continue; }
      if (c.indexOf(cw) !== -1) { return RAW[i]; }
    }
    return null;
  }
  window.__focusFilterHit = hit;

  var warned = 0;
  function toast(msg) {
    var now = Date.now();
    if (now - warned < 1000) { return; }
    warned = now;
    var d = document.getElementById('__focus_filter_toast');
    if (!d) {
      d = document.createElement('div');
      d.id = '__focus_filter_toast';
      d.style.cssText = 'position:fixed;top:14px;left:50%;transform:translateX(-50%);' +
        'z-index:2147483647;background:#7f1d1d;color:#fff;font:bold 14px sans-serif;' +
        'padding:10px 18px;border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.55);' +
        'pointer-events:none;max-width:80vw;';
      (document.body || document.documentElement).appendChild(d);
    }
    d.textContent = '\u26d4 Focus Input Filter: ' + msg;
    d.style.display = 'block';
    clearTimeout(d.__t);
    d.__t = setTimeout(function () { d.style.display = 'none'; }, 2600);
  }
  function editable(el) {
    return !!el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.isContentEditable);
  }
  function val(el) { return el.isContentEditable ? el.innerText : el.value; }
  var lastGood = new WeakMap();

  document.addEventListener('focusin', function (e) {
    var el = e.target;
    if (editable(el)) { lastGood.set(el, el.isContentEditable ? el.innerHTML : el.value); }
  }, true);

  document.addEventListener('input', function (e) {
    var el = e.target;
    if (!editable(el)) { return; }
    var bad = hit(val(el));
    if (bad) {
      var good = lastGood.get(el);
      if (good !== undefined) {
        if (el.isContentEditable) { el.innerHTML = good; } else { el.value = good; }
      }
      toast('explicit content blocked (' + bad + ')');
      return;
    }
    lastGood.set(el, el.isContentEditable ? el.innerHTML : el.value);
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || !editable(e.target)) { return; }
    if (hit(val(e.target))) {
      e.preventDefault();
      e.stopImmediatePropagation();
      toast('message not allowed');
    }
  }, true);

  document.addEventListener('paste', function (e) {
    var el = e.target;
    if (!editable(el)) { return; }
    var cd = e.clipboardData || window.clipboardData;
    var txt = cd ? (cd.getData('text') || '') : '';
    if (hit(txt)) {
      e.preventDefault();
      e.stopImmediatePropagation();
      toast('paste blocked');
    }
  }, true);

  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f || !f.querySelectorAll) { return; }
    var bad = null;
    f.querySelectorAll('input,textarea').forEach(function (el) {
      if (!bad) { bad = hit(el.value); }
    });
    if (bad) {
      e.preventDefault();
      e.stopImmediatePropagation();
      toast('send blocked');
    }
  }, true);

  document.addEventListener('click', function (e) {
    var el = e.target;
    var box = editable(el) ? el : (editable(document.activeElement) ? document.activeElement : null);
    if (!box && el && el.closest) {
      var root = el.closest('form,[role="dialog"]');
      if (root) { box = root.querySelector('input,textarea,[contenteditable="true"]'); }
    }
    if (box && hit(val(box))) {
      e.preventDefault();
      e.stopImmediatePropagation();
      toast('send blocked');
    }
  }, true);
})();
""".replace("__WORDS__", words)


INPUT_FILTER_JS = _build_input_filter_js()
_study_ucm = None

UPLOAD_PASSWORD_PATH = os.path.join(LEDGER_DIR, "phone-upload-password")
UPLOAD_PUBLIC_URL = "https://upload21.medisparkbd.com/"
# Public Telegram group: anyone can send PDFs here; Telegram Web handles download.
PHONE_PDF_UPLOAD_URL = "https://t.me/pdf258upload"
UPLOAD_SERVER_HOST = "127.0.0.1"
UPLOAD_SERVER_PORT = 8765
UPLOAD_MAX_BYTES = 90 * 1024 * 1024
_phone_upload_server = None
STUDY_CONFIG_PATH = os.path.join(LEDGER_DIR, "study-config.json")
MEDIA_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_SCAN_SKIP = {
    "node_modules", ".git", ".cache", "__pycache__", "snap", "venv", ".venv",
    "site-packages", "proc", "sys", "dev", "Trash", ".Trash-1000", "flatpak",
    "Steam", "steamapps", ".npm", ".cargo", ".rustup", ".local/share/Trash",
}

_STUDY_DEFAULTS = {
    "version": 6,
    "links": [
        {"title": "Boimate", "url": "https://boimate.com"},
        {"title": "Gemini Notebook", "url": "https://notebooklm.google.com"},
        {"title": "Medical Contents", "url": "https://medical-contents.abdursakib.workers.dev/0:/"},
        {"title": "MuslimBangla", "url": "https://muslimbangla.com"},
        {"title": "iHadis", "url": "https://ihadis.com/en"},
        {"title": "LiveMCQ", "url": "https://web.livemcq.com/"},
        {"title": "Translate", "url": "https://translate.google.com"},
        {"title": "Phone PDF Upload", "url": PHONE_PDF_UPLOAD_URL},
    ],
    "allow_hosts": [
        "boimate.com", "www.boimate.com", "*.boimate.com",
        "medical-contents.abdursakib.workers.dev",
        "drive.google.com",
        "notebooklm.google.com", "notebook.google.com",
        "*.notebook.google.com", "notebooklm-pa.googleapis.com",
        "content-push.googleapis.com", "csp.withgoogle.com",
        "accounts.google.com", "accounts.youtube.com", "myaccount.google.com",
        "translate.google.com", "translate.googleapis.com", "clients6.google.com",
        "www.google.com", "google.com", "apis.google.com", "content.googleapis.com",
        "www.gstatic.com", "ssl.gstatic.com", "fonts.gstatic.com", "gstatic.com",
        "fonts.googleapis.com", "font.gstatic.com", "googleusercontent.com",
        "*.googleusercontent.com", "usercontent.google.com",
        "*.usercontent.google.com", "*.gstatic.com", "*.googleapis.com",
        "muslimbangla.com", "www.muslimbangla.com",
        "ihadis.com", "www.ihadis.com",
        "livemcq.com", "www.livemcq.com", "web.livemcq.com", "*.livemcq.com",
        "127.0.0.1", "localhost", "upload21.medisparkbd.com",
        "t.me", "telegram.org", "www.telegram.org",
        "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com",
        "*.cloudfront.net", "*.amazonaws.com", "*.b-cdn.net",
        "*.digitaloceanspaces.com", "*.supabase.co", "*.firebaseapp.com",
    ],
    "scan_roots": ["~", "/media", "/mnt", "/run/media"],
    "download_exts": sorted(MEDIA_EXTS),
}


def _study_config():
    """Load study config, writing defaults on first run."""
    try:
        with open(STUDY_CONFIG_PATH) as f:
            cfg = json.load(f)
        if (isinstance(cfg, dict) and cfg.get("links") and cfg.get("allow_hosts")
                and int(cfg.get("version", 1)) >= 4):
            # Add newly shipped study resources to existing user configurations.
            changed = False
            kept_links = []
            for item in cfg["links"]:
                uri = item.get("url", "") if isinstance(item, dict) else ""
                host = (urlparse(uri).hostname or "").lower()
                title = str(item.get("title", "")).strip().lower() \
                    if isinstance(item, dict) else ""
                if host.endswith("whatsapp.com") or host.endswith("whatsapp.net"):
                    changed = True
                    continue
                if host.endswith("trycloudflare.com"):
                    changed = True
                    continue
                if title == "phone pdf upload":
                    if uri != PHONE_PDF_UPLOAD_URL:
                        item["url"] = PHONE_PDF_UPLOAD_URL
                        changed = True
                kept_links.append(item)
            if len(kept_links) != len(cfg["links"]):
                cfg["links"] = kept_links
            old_hosts = list(cfg["allow_hosts"])
            cfg["allow_hosts"] = [
                host for host in old_hosts
                if "whatsapp.com" not in str(host).lower()
                and "whatsapp.net" not in str(host).lower()
                and "trycloudflare.com" not in str(host).lower()
                and str(host).lower() not in (
                    "*.t.me", "*.telegram.org", "cdn-telegram.org",
                    "*.cdn-telegram.org", "telegram.me", "*.telegram.me")]
            if cfg["allow_hosts"] != old_hosts:
                changed = True
            for link in _STUDY_DEFAULTS["links"]:
                if not any(item.get("url") == link["url"] for item in cfg["links"]
                           if isinstance(item, dict)):
                    cfg["links"].append(link)
                    changed = True
            for host in _STUDY_DEFAULTS["allow_hosts"]:
                if host not in cfg["allow_hosts"]:
                    cfg["allow_hosts"].append(host)
                    changed = True
            if int(cfg.get("version", 1)) < _STUDY_DEFAULTS["version"]:
                cfg["version"] = _STUDY_DEFAULTS["version"]
                changed = True
            if changed:
                try:
                    with open(STUDY_CONFIG_PATH, "w") as f:
                        json.dump(cfg, f, indent=2)
                except Exception as e:
                    print(f"study config update error: {e}", flush=True)
            return cfg
    except Exception:
        pass
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        with open(STUDY_CONFIG_PATH, "w") as f:
            json.dump(_STUDY_DEFAULTS, f, indent=2)
    except Exception as e:
        print(f"study config write error: {e}", flush=True)
    return dict(_STUDY_DEFAULTS)


def _ensure_phone_upload_password():
    os.makedirs(LEDGER_DIR, exist_ok=True)
    try:
        with open(UPLOAD_PASSWORD_PATH) as f:
            return f.read().strip()
    except OSError:
        pass
    generated = secrets.token_urlsafe(24)
    try:
        fd = os.open(UPLOAD_PASSWORD_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(generated + "\n")
        return generated
    except FileExistsError:
        with open(UPLOAD_PASSWORD_PATH) as f:
            return f.read().strip()


def start_phone_upload_server():
    """Loopback-only authenticated PDF inbox, intended for an HTTPS tunnel."""
    global _phone_upload_server
    if _phone_upload_server is not None:
        return _phone_upload_server
    password = _ensure_phone_upload_password()

    class UploadHandler(BaseHTTPRequestHandler):
        server_version = "FocusPhoneUpload/1.0"

        def log_message(self, fmt, *args):
            print("phone upload: " + (fmt % args), flush=True)

        def _headers(self, content_type, length=None):
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'unsafe-inline'; "
                             "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            if length is not None:
                self.send_header("Content-Length", str(length))

        def _authorized(self):
            expected = "Basic " + base64.b64encode(
                ("study:" + password).encode("utf-8")).decode("ascii")
            supplied = self.headers.get("Authorization", "")
            if hmac.compare_digest(supplied, expected):
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Study PDF Upload"')
            body = b"Password required\n"
            self._headers("text/plain; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)
            return False

        def _html(self, body, status=200):
            page = ("<!doctype html><html lang='en'><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                    "<title>Study PDF Inbox</title><style>"
                    "*{box-sizing:border-box}body{margin:0;background:#edf2f8;color:#17243a;"
                    "font:16px system-ui,sans-serif}.wrap{max-width:680px;margin:24px auto;padding:16px}"
                    ".card{background:white;border:1px solid #dce5f1;border-radius:16px;"
                    "padding:22px;margin-bottom:16px;box-shadow:0 8px 28px #142b4612}"
                    "h1{font-size:22px;margin:0 0 6px}p{color:#64748b}.upload{width:100%;"
                    "padding:14px;border:1px dashed #94a3b8;border-radius:10px;background:#f8fafc}"
                    "button{margin-top:12px;width:100%;border:0;border-radius:10px;padding:14px;"
                    "background:#1d4ed8;color:white;font-weight:700;font-size:16px}li{margin:12px 0}"
                    "a{color:#1d4ed8;word-break:break-word}</style><body><main class='wrap'>"
                    + body + "</main></body></html>").encode("utf-8")
            self.send_response(status)
            self._headers("text/html; charset=utf-8", len(page))
            self.end_headers()
            self.wfile.write(page)

        def do_GET(self):
            if not self._authorized():
                return
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.startswith("/download/"):
                name = os.path.basename(urllib.parse.unquote(parsed.path[len("/download/"):]))
                target = os.path.join(STUDY_INBOX, name)
                if not name.lower().endswith(".pdf") or not os.path.isfile(target):
                    self.send_error(404)
                    return
                length = os.path.getsize(target)
                self.send_response(200)
                self._headers("application/pdf", length)
                self.send_header("Content-Disposition",
                                 "attachment; filename*=UTF-8''" +
                                 urllib.parse.quote(name, safe=""))
                self.end_headers()
                with open(target, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
                return
            if parsed.path != "/":
                self.send_error(404)
                return
            os.makedirs(STUDY_INBOX, exist_ok=True)
            files = []
            for name in os.listdir(STUDY_INBOX):
                path = os.path.join(STUDY_INBOX, name)
                try:
                    if name.lower().endswith(".pdf") and os.path.isfile(path):
                        files.append((os.path.getmtime(path), name, os.path.getsize(path)))
                except OSError:
                    continue
            files.sort(reverse=True)
            rows = "".join(
                "<li><a href='/download/" + urllib.parse.quote(name, safe="") + "'>"
                + html.escape(name) + "</a> · " + f"{size / (1024 * 1024):.1f} MB</li>"
                for _mtime, name, size in files[:100])
            if not rows:
                rows = "<li>No PDFs uploaded yet.</li>"
            body = ("<section class='card'><h1>Study PDF Inbox</h1>"
                    "<p>Upload PDFs from your phone. They appear in the Study library.</p>"
                    "<form action='/upload' method='post' enctype='multipart/form-data'>"
                    "<input class='upload' type='file' name='files' accept='application/pdf,.pdf' "
                    "multiple required><button type='submit'>Upload PDF files</button></form></section>"
                    "<section class='card'><h1>Recent uploads</h1><ul>" + rows + "</ul></section>")
            self._html(body)

        def do_POST(self):
            if not self._authorized():
                return
            if urllib.parse.urlsplit(self.path).path != "/upload":
                self.send_error(404)
                return
            origin = self.headers.get("Origin", "")
            host = self.headers.get("Host", "")
            if origin and urllib.parse.urlsplit(origin).netloc != host:
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "Invalid content length")
                return
            if length <= 0 or length > UPLOAD_MAX_BYTES:
                self.send_error(413, "Upload must be under 90 MB")
                return
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith("multipart/form-data;"):
                self.send_error(400, "Choose a PDF file")
                return
            body = self.rfile.read(length)
            safe_content_type = content_type.replace("\r", "").replace("\n", "")
            raw = ("MIME-Version: 1.0\r\nContent-Type: " + safe_content_type
                   + "\r\n\r\n").encode("ascii", "replace") + body
            message = BytesParser(policy=email.policy.default).parsebytes(raw)
            if not message.is_multipart():
                self.send_error(400, "Invalid upload form")
                return
            uploads = []
            total = 0
            for part in message.iter_parts():
                filename = part.get_filename()
                if not filename or part.get_param("name", header="content-disposition") != "files":
                    continue
                data = part.get_payload(decode=True) or b""
                total += len(data)
                name = os.path.basename(filename.replace("\\", "/"))
                name = re.sub(r"[^\w .()\-]", "_", name, flags=re.UNICODE).strip(" .")
                if not name.lower().endswith(".pdf") or not data:
                    self.send_error(415, "Only non-empty PDF files are accepted")
                    return
                if b"%PDF-" not in data[:1024]:
                    self.send_error(415, "The selected file is not a valid PDF")
                    return
                uploads.append((name, data))
            if not uploads or total > UPLOAD_MAX_BYTES:
                self.send_error(400, "Choose at least one PDF under 90 MB")
                return
            os.makedirs(STUDY_INBOX, exist_ok=True)
            saved = []
            with self.server.upload_lock:
                for name, data in uploads:
                    stem, suffix = os.path.splitext(name)
                    target = os.path.join(STUDY_INBOX, name)
                    index = 1
                    while os.path.exists(target):
                        index += 1
                        target = os.path.join(STUDY_INBOX, f"{stem} ({index}){suffix}")
                    temporary = target + ".upload-" + secrets.token_hex(5)
                    try:
                        with open(temporary, "xb") as f:
                            f.write(data)
                        os.replace(temporary, target)
                        saved.append(os.path.basename(target))
                    finally:
                        try:
                            if os.path.exists(temporary):
                                os.remove(temporary)
                        except OSError:
                            pass
            print(f"Phone upload received {len(saved)} PDF(s).", flush=True)
            self._html("<section class='card'><h1>Upload complete</h1><ul>" +
                       "".join("<li>" + html.escape(name) + "</li>" for name in saved) +
                       "</ul><p><a href='/'>Return to the inbox</a></p></section>", 201)

    class LoopbackUploadServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        upload_lock = threading.Lock()

    try:
        os.makedirs(STUDY_INBOX, exist_ok=True)
        _phone_upload_server = LoopbackUploadServer(
            (UPLOAD_SERVER_HOST, UPLOAD_SERVER_PORT), UploadHandler)
        threading.Thread(target=_phone_upload_server.serve_forever,
                         name="focus-phone-upload", daemon=True).start()
        print(f"Phone PDF upload portal listening on {UPLOAD_SERVER_HOST}:{UPLOAD_SERVER_PORT}.",
              flush=True)
    except OSError as e:
        print(f"Phone PDF upload portal could not start: {e}", flush=True)
    return _phone_upload_server


def _host_allowed(host, patterns):
    host = (host or "").lower().split(":")[0]
    if not host:
        return False
    for p in patterns:
        p = str(p).lower().strip()
        if not p:
            continue
        if p.startswith("*."):
            base = p[1:]  # /.example.com
            if host.endswith(base) or host == p[2:]:
                return True
        elif host == p:
            return True
    return False


# Telegram stays limited to one public group page: no Telegram Web, no other chats.
TELEGRAM_GROUP_HOSTS = ("t.me", "www.t.me")
TELEGRAM_GROUP_PATH = "/pdf258upload"

# Temporary "setup mode": lets the user log in to Telegram Web (needed once for
# downloads), then auto-expires back to the strict single-group rule.
SETUP_MODE_STATE = os.path.join(LEDGER_DIR, "setup-mode.state")
SETUP_MODE_MINUTES = 15
TELEGRAM_SETUP_SUFFIXES = (
    "t.me", "telegram.org", "telegram.me", "telegram.dog",
    "cdn-telegram.org", "telesco.pe")


def setup_mode_until():
    try:
        with open(SETUP_MODE_STATE) as f:
            return float(f.read().strip())
    except Exception:
        return 0


def setup_mode_active():
    return setup_mode_until() > time.time()


def _setup_host_allowed(host):
    if not setup_mode_active():
        return False
    return any(host == s or host.endswith("." + s) for s in TELEGRAM_SETUP_SUFFIXES)


def enable_setup_mode(minutes=SETUP_MODE_MINUTES):
    until = time.time() + minutes * 60
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        with open(SETUP_MODE_STATE, "w") as f:
            f.write(str(until))
    except Exception as e:
        print(f"setup mode save error: {e}", flush=True)
        return 0
    at = time.strftime("%H:%M", time.localtime(until))
    notify(f"Telegram setup mode ON for {minutes} min (until {at}).")
    print(f"Telegram setup mode ON for {minutes} min, auto-off at {at}.", flush=True)
    return until


def disable_setup_mode():
    try:
        if os.path.exists(SETUP_MODE_STATE):
            os.remove(SETUP_MODE_STATE)
    except Exception:
        pass
    print("Telegram setup mode OFF — strict single-group rule restored.", flush=True)


def _page_allowed(uri, patterns):
    """Host allowlist plus path lock for Telegram (only the PDF upload group)."""
    try:
        parsed = urlparse(uri or "")
        host = (parsed.hostname or "").lower()
        if _setup_host_allowed(host):
            return True
        if not _host_allowed(host, patterns):
            return False
        if host in TELEGRAM_GROUP_HOSTS:
            path = (parsed.path or "/").rstrip("/").lower()
            return path == TELEGRAM_GROUP_PATH or path.startswith(TELEGRAM_GROUP_PATH + "/")
        return True
    except Exception:
        return False


def _scan_media(roots, limit=6000):
    """All PDFs/images under roots (whole laptop). Newest first."""
    found = []
    exts = MEDIA_EXTS
    for root in roots:
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in _SCAN_SKIP]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in exts:
                    p = os.path.join(dirpath, fn)
                    try:
                        found.append((os.path.getmtime(p), p))
                    except Exception:
                        found.append((0, p))
                    if len(found) >= limit:
                        found.sort(reverse=True)
                        return found
    found.sort(reverse=True)
    return found


def _copy_into_inbox(paths, exts):
    """Copy allowed media files into the study inbox. Returns copied count."""
    copied = 0
    try:
        os.makedirs(STUDY_INBOX, exist_ok=True)
    except Exception as e:
        print(f"inbox create error: {e}", flush=True)
        return 0
    for src in paths:
        try:
            if not os.path.isfile(src):
                continue
            ext = os.path.splitext(src)[1].lower()
            if ext not in exts:
                continue
            dst = os.path.join(STUDY_INBOX, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dst):
                if os.path.exists(dst):
                    stem, e = os.path.splitext(dst)
                    dst = f"{stem}-{int(time.time())}{e}"
                shutil.copy2(src, dst)
            copied += 1
        except Exception as e:
            print(f"inbox copy error: {e}", flush=True)
    return copied


def _import_usb_pdf_uri(uri):
    """Copy one PDF from a mounted drive or GVfs phone into the Study inbox."""
    scheme = urllib.parse.urlsplit(uri or "").scheme.lower()
    if scheme not in ("file", "mtp", "gphoto2"):
        return None, "Choose a file from USB storage or a connected phone"
    try:
        source = Gio.File.new_for_uri(uri)
        raw_name = source.get_basename() or ""
        if not raw_name.lower().endswith(".pdf"):
            return None, "Only PDF files can be imported from USB/Type-C"
        name = re.sub(r"[^\w .()\-]", "_", raw_name, flags=re.UNICODE).strip(" .")
        if not name or not name.lower().endswith(".pdf"):
            return None, "Invalid PDF filename"
        os.makedirs(STUDY_INBOX, exist_ok=True)
        temporary = os.path.join(STUDY_INBOX, ".usb-import-" + secrets.token_hex(8) + ".pdf")
        source.copy(Gio.File.new_for_path(temporary), Gio.FileCopyFlags.NONE,
                    None, None, None)
        try:
            with open(temporary, "rb") as f:
                signature = f.read(1024)
            if b"%PDF-" not in signature:
                return None, "The selected file is not a valid PDF"
            stem, suffix = os.path.splitext(name)
            target = os.path.join(STUDY_INBOX, name)
            index = 1
            while os.path.exists(target):
                index += 1
                target = os.path.join(STUDY_INBOX, f"{stem} ({index}){suffix}")
            os.replace(temporary, target)
            temporary = ""
            return target, None
        finally:
            if temporary:
                try:
                    os.remove(temporary)
                except OSError:
                    pass
    except Exception as e:
        print(f"USB PDF import error: {e}", flush=True)
        return None, "Could not read that PDF from the connected device"


def _pdf_metadata(path):
    pages, page_pts = 1, (595.0, 842.0)
    try:
        r = _run(["pdfinfo", path], capture_output=True, text=True, timeout=20)
        for line in (r.stdout or "").splitlines():
            if line.lower().startswith("pages:"):
                pages = max(1, int(line.split(":", 1)[1].strip()))
            elif line.lower().startswith("page size:"):
                try:
                    dims = line.split(":", 1)[1].split("pts", 1)[0]
                    width, height = dims.split("x", 1)
                    page_pts = (float(width.strip()), float(height.strip()))
                except Exception:
                    pass
    except Exception:
        pass
    return pages, page_pts


def _pdf_pages(path):
    return _pdf_metadata(path)[0]


def _render_pdf_page(path, page, zoom=1.0, cancel=None):
    """Render one PDF page to a PNG (poppler) and return a pixbuf."""
    out = None
    try:
        fd, out = tempfile.mkstemp(prefix=f"focus-pdf-{os.getpid()}-{page}-")
        os.close(fd)
        os.remove(out)
        dpi = max(12, min(168, int(96 * min(float(zoom), 1.75))))
        cmd = ["pdftoppm", "-png", "-r", str(dpi), "-f", str(page),
               "-l", str(page), "-singlefile", path, out]
        proc = subprocess.Popen(_HOST + cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 45
        while proc.poll() is None:
            if (cancel is not None and cancel()) or time.monotonic() >= deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                proc.communicate()
                return None
            time.sleep(0.1)
        proc.communicate()
        png = out + ".png"
        if proc.returncode == 0 and os.path.exists(png):
            info = GdkPixbuf.Pixbuf.get_file_info(png)
            if info and (info[1] > 2200 or info[2] > 3000):
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(png, 2200, 3000, True)
            return GdkPixbuf.Pixbuf.new_from_file(png)
        if proc.returncode != 0:
            print(f"pdftoppm failed with exit code {proc.returncode}", flush=True)
    except Exception as e:
        print(f"pdf render error: {e}", flush=True)
    finally:
        if out:
            for suffix in ("", ".png"):
                try:
                    os.remove(out + suffix)
                except OSError:
                    pass
    return None


def show_study_window(parent=None, end_ts=None):
    """Study container inside lockout: whitelisted links, PDF/image library,
    remote PDF upload, media intake. Suspension keeps running."""
    global _study_win, _study_parent, _study_web_context, _study_ucm

    if WebKit2 is None:
        print("WebKit2 unavailable — study view disabled", flush=True)
        return

    cfg = _study_config()
    allow = cfg.get("allow_hosts") or _STUDY_DEFAULTS["allow_hosts"]
    dl_exts = set(x.lower() for x in (cfg.get("download_exts") or MEDIA_EXTS))

    if parent is not None:
        _study_parent = parent
        try:
            parent.hide()
        except Exception:
            pass

    win = _new_fullscreen("Study")
    _study_win = win
    win.set_modal(True)
    _swallow_shell_keys(win)
    guard_id = _arm_focus_guard(win)

    css = Gtk.CssProvider()
    css.load_from_data(b"""
    .studywin { background: #0b1220; }
    .studybar { background: #10264a; padding: 10px 18px; }
    .studybar entry { background: rgba(8, 20, 39, 0.72); color: #e5eefc;
                      border: 1px solid rgba(148, 163, 184, 0.24);
                      border-radius: 8px; padding: 6px 9px; }
    .sidebar { background: #101b2c; border-right: 1px solid rgba(148, 163, 184, 0.16);
               padding: 12px; }
    .studyhead { font-family: Inter, sans-serif; font-size: 13px; color: #dbeafe;
                 letter-spacing: 2px; font-weight: bold; }
    .studytime { font-family: Inter, sans-serif; font-size: 13px; color: #9ca3af; }
    .sidehead { font-family: Inter, sans-serif; font-size: 10px; color: #8fa2bd;
                letter-spacing: 1.35px; font-weight: 700; padding: 12px 8px 7px 8px; }
    .sidebar separator { background: rgba(148, 163, 184, 0.16); margin: 10px 4px; }
    .sidebtn { background: rgba(255,255,255,0.035); border: 1px solid rgba(148,163,184,0.10);
               box-shadow: none; padding: 8px 10px; border-radius: 9px; margin: 2px 0; }
    .sidebtn:hover { background: rgba(59, 130, 246, 0.16);
                     border-color: rgba(96,165,250,0.32); }
    .sidebtn label { font-family: Inter, sans-serif; font-size: 12px; color: #e2e8f0; }
    .sidebtn:active { background: rgba(37, 99, 235, 0.30); }
    .panel { background: #f8fafc; }
    .studytip { font-family: Inter, sans-serif; font-size: 12px; color: #9ca3af; }
    .homepage { background: #0b1220; padding: 40px; }
    .floatbar { background: rgba(12, 22, 40, 0.92); border-radius: 14px;
                padding: 7px; border: 1px solid rgba(96, 165, 250, 0.28); }
    .floatbtn { background: rgba(30, 58, 95, 0.55); border: none; box-shadow: none;
                border-radius: 9px; padding: 0; min-width: 38px; min-height: 38px; }
    .floatbtn:hover { background: rgba(37, 99, 235, 0.55); }
    .webnavbtn { min-width: 28px; min-height: 28px; padding: 0; }
    .webnavbtn label { font-size: 17px; }
    .floatbtn-on { background: rgba(59, 130, 246, 0.85); }
    .floatbtn label { font-size: 16px; color: #e5eefc; }
    .floatpage { font-family: Inter, sans-serif; font-size: 10px; color: #93a8c9;
                 padding: 2px 0; }
    .toastlbl { background: rgba(15, 30, 55, 0.95); border-radius: 10px;
                border: 1px solid rgba(96, 165, 250, 0.35);
                padding: 8px 16px; font-family: Inter, sans-serif;
                font-size: 12px; color: #e5eefc; }
    .backbtn { background: rgba(37, 99, 235, 0.22); border: 1px solid rgba(96, 165, 250, 0.35);
               box-shadow: none; border-radius: 10px; margin-top: 6px; }
    .backbtn:hover { background: rgba(37, 99, 235, 0.45); }
    .backbtn label { font-family: Inter, sans-serif; font-size: 13px; color: #dbeafe;
                     font-weight: bold; }
    .loadscreen { background: rgba(15, 28, 47, 0.94); border-radius: 10px;
                  border: 1px solid rgba(96,165,250,0.24); padding: 8px 12px; }
    .loadhead { font-family: Inter, sans-serif; font-size: 12px; font-weight: 600;
                color: #dbeafe; }
    .recentpanel { background: #101b2c; border: 1px solid rgba(96,165,250,0.34);
                   border-radius: 12px; padding: 12px; }
    .recentheading { font-family: Inter, sans-serif; font-size: 11px; font-weight: 700;
                     letter-spacing: 1px; color: #bfdbfe; padding: 4px 4px 10px; }
    .recentrow { background: rgba(255,255,255,0.04); border: 1px solid rgba(148,163,184,0.12);
                 box-shadow: none; border-radius: 8px; padding: 8px; }
    .recentrow:hover { background: rgba(37,99,235,0.25); }
    .recentrow label { color: #e2e8f0; font-family: Inter, sans-serif; font-size: 11px; }
    """)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win.get_style_context().add_class("studywin")
    win.connect("delete-event", lambda *_: True)  # Alt+F4 blocked

    outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    bar.get_style_context().add_class("studybar")
    head = Gtk.Label(label="STUDY MODE \u2014 LOCKED (SUSPENSION CONTINUES)")
    head.get_style_context().add_class("studyhead")
    head.set_halign(Gtk.Align.START)
    time_lbl = Gtk.Label(label="")
    time_lbl.get_style_context().add_class("studytime")
    time_lbl.set_halign(Gtk.Align.END)
    time_lbl.set_hexpand(True)
    bar.pack_start(head, False, False, 0)
    bar.pack_start(time_lbl, True, True, 0)
    usb_import_btn = Gtk.Button(label="Import PDF")
    usb_import_btn.get_style_context().add_class("webnavbtn")
    usb_import_btn.get_style_context().add_class("floatbtn")
    usb_import_btn.set_tooltip_text(
        "Import PDF files from a USB drive or a phone connected by Type-C")
    bar.pack_start(usb_import_btn, False, False, 0)
    lib_search_entry = Gtk.Entry()
    lib_search_entry.set_width_chars(16)
    lib_search_entry.set_placeholder_text("Search PDF/image…")
    lib_search_entry.set_tooltip_text("Search PDFs and images in the library")
    bar.pack_start(lib_search_entry, False, False, 0)
    battery_lbl = Gtk.Label(label="Battery —")
    battery_lbl.get_style_context().add_class("studytime")
    battery_lbl.set_tooltip_text("Live battery charge")
    bar.pack_start(battery_lbl, False, False, 4)
    recent_btn = Gtk.Button(label="Recent ▾")
    recent_btn.get_style_context().add_class("webnavbtn")
    recent_btn.get_style_context().add_class("floatbtn")
    recent_btn.set_tooltip_text("Hover to show recently visited sites and documents")
    bar.pack_start(recent_btn, False, False, 0)
    split_btn = Gtk.Button(label="Split view")
    split_btn.get_style_context().add_class("webnavbtn")
    split_btn.get_style_context().add_class("floatbtn")
    split_btn.set_tooltip_text("Show two study pages side by side")
    bar.pack_start(split_btn, False, False, 0)

    right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    side.set_size_request(270, -1)
    side.get_style_context().add_class("sidebar")

    links_head = Gtk.Label(label="STUDY LINKS")
    links_head.get_style_context().add_class("sidehead")
    links_head.set_halign(Gtk.Align.START)
    links_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

    lib_head = Gtk.Label(label="YOUR LIBRARY")
    lib_head.get_style_context().add_class("sidehead")
    lib_head.set_halign(Gtk.Align.START)
    lib_title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    lib_title_row.pack_start(lib_head, True, True, 0)
    lib_parent_btn = Gtk.Button(label="↑ Parent")
    lib_parent_btn.get_style_context().add_class("sidebtn")
    lib_parent_btn.set_relief(Gtk.ReliefStyle.NONE)
    lib_parent_btn.set_tooltip_text("Open the parent directory")
    lib_title_row.pack_end(lib_parent_btn, False, False, 0)
    lib_count = Gtk.Label(label="PDFs and images on this device")
    lib_count.get_style_context().add_class("studytip")
    lib_count.set_halign(Gtk.Align.START)
    lib_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    lib_scroll = Gtk.ScrolledWindow()
    lib_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    lib_scroll.set_vexpand(True)
    lib_scroll.add(lib_box)

    side.pack_start(links_head, False, False, 0)
    side.pack_start(links_box, False, False, 0)
    side.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
    side.pack_start(lib_title_row, False, False, 0)
    side.pack_start(lib_count, False, False, 0)
    side.pack_start(lib_scroll, True, True, 0)

    stack = Gtk.Stack()
    stack.set_hexpand(True)
    stack.set_vexpand(True)
    stack.set_transition_type(Gtk.StackTransitionType.NONE)
    stack_wrap = Gtk.Overlay()
    stack_wrap.add(stack)

    if _study_web_context is None:
        try:
            os.makedirs(STUDY_WEB_DATA_DIR, exist_ok=True)
            os.makedirs(STUDY_WEB_CACHE_DIR, exist_ok=True)
            data_manager = WebKit2.WebsiteDataManager(
                base_data_directory=STUDY_WEB_DATA_DIR,
                base_cache_directory=STUDY_WEB_CACHE_DIR)
            _study_web_context = WebKit2.WebContext.new_with_website_data_manager(
                data_manager)
            _cm = _study_web_context.get_cookie_manager()
            _cm.set_accept_policy(WebKit2.CookieAcceptPolicy.ALWAYS)
            try:
                _cm.set_persistent_storage(STUDY_WEB_COOKIE_DB,
                                           WebKit2.CookiePersistentStorage.SQLITE)
            except Exception as _e:
                print(f"cookie storage unavailable: {_e}", flush=True)
            try:
                data_manager.set_itp_enabled(False)  # keep Google/WhatsApp logins
            except Exception:
                pass
            try:
                _study_web_context.set_cache_model(WebKit2.CacheModel.WEB_BROWSER)
            except Exception:
                pass
        except Exception as e:
            print(f"persistent study browser profile unavailable: {e}", flush=True)
            try:
                _study_web_context = WebKit2.WebContext.get_default()
            except Exception:
                _study_web_context = None
    if _study_ucm is None:
        try:
            _study_ucm = WebKit2.UserContentManager()
            _study_ucm.add_script(WebKit2.UserScript.new(
                INPUT_FILTER_JS,
                WebKit2.UserContentInjectedFrames.ALL_FRAMES,
                WebKit2.UserScriptInjectionTime.START,
                None, None))
        except Exception as _e:
            print(f"input filter script unavailable: {_e}", flush=True)
            _study_ucm = None
    try:
        if _study_web_context is not None and _study_ucm is not None:
            web = WebKit2.WebView(web_context=_study_web_context,
                                  user_content_manager=_study_ucm)
        elif _study_web_context is not None:
            web = WebKit2.WebView.new_with_context(_study_web_context)
        else:
            web = WebKit2.WebView()
    except Exception:
        web = WebKit2.WebView()
    try:
        st = web.get_settings()
        st.set_enable_developer_extras(False)
        st.set_enable_html5_local_storage(True)
        st.set_enable_html5_database(True)
        st.set_enable_javascript(True)
        st.set_enable_page_cache(False)
    except Exception:
        pass

    def _on_web_authenticate(_view, request):
        try:
            if (request.get_host() or "").lower().split(":", 1)[0] not in (
                    "127.0.0.1", "localhost"):
                return False
            dialog = Gtk.Dialog(title="Phone Upload Sign-in", transient_for=win,
                                flags=Gtk.DialogFlags.MODAL)
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button("Sign in", Gtk.ResponseType.OK)
            dialog.set_default_response(Gtk.ResponseType.OK)
            box = dialog.get_content_area()
            box.set_spacing(10)
            box.set_margin_top(16); box.set_margin_bottom(16)
            box.set_margin_start(18); box.set_margin_end(18)
            user_entry = Gtk.Entry()
            user_entry.set_text("study")
            pass_entry = Gtk.Entry()
            pass_entry.set_visibility(False)
            pass_entry.set_placeholder_text("Upload password")
            box.pack_start(Gtk.Label(label="Username"), False, False, 0)
            box.pack_start(user_entry, False, False, 0)
            box.pack_start(Gtk.Label(label="Password"), False, False, 0)
            box.pack_start(pass_entry, False, False, 0)
            box.show_all()
            result = dialog.run()
            username, passphrase = user_entry.get_text(), pass_entry.get_text()
            dialog.destroy()
            if result != Gtk.ResponseType.OK:
                request.cancel()
                return True
            credential = WebKit2.Credential.new(
                username, passphrase, WebKit2.CredentialPersistence.FOR_SESSION)
            request.authenticate(credential)
            return True
        except Exception as e:
            print(f"upload portal sign-in error: {e}", flush=True)
            try:
                request.cancel()
            except Exception:
                pass
            return True

    web.connect("authenticate", _on_web_authenticate)
    web.set_hexpand(True)
    web.set_vexpand(True)
    web_scroll = Gtk.ScrolledWindow()
    web_scroll.add(web)
    web_wrap = Gtk.Overlay()
    web_wrap.add(web_scroll)
    web_nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    web_nav.get_style_context().add_class("floatbar")
    web_nav.set_halign(Gtk.Align.END)
    web_nav.set_valign(Gtk.Align.START)
    web_nav.set_margin_top(10)
    web_nav.set_margin_end(12)
    web_back = Gtk.Button(label="‹")
    web_forward = Gtk.Button(label="›")
    for nav_btn in (web_back, web_forward):
        nav_btn.get_style_context().add_class("floatbtn")
        nav_btn.get_style_context().add_class("webnavbtn")
        nav_btn.set_size_request(30, 30)
        web_nav.pack_start(nav_btn, False, False, 0)
    web_back.set_tooltip_text("Back")
    web_forward.set_tooltip_text("Forward")
    web_wrap.add_overlay(web_nav)

    def _update_web_nav(*_):
        web_back.set_sensitive(web.can_go_back())
        web_forward.set_sensitive(web.can_go_forward())

    web_back.connect("clicked", lambda *_: web.go_back())
    web_forward.connect("clicked", lambda *_: web.go_forward())
    web.connect("load-changed", _update_web_nav)
    web.connect("notify::estimated-load-progress", _update_web_nav)

    split_return_page = {"name": "home"}
    split_paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
    split_paned.set_wide_handle(True)
    split_paned.set_hexpand(True)
    split_paned.set_vexpand(True)

    def _split_load_uri(view, raw_uri, local_pdf_uri=None):
        uri = (raw_uri or "").strip()
        if not uri:
            return
        if uri.startswith("file:"):
            if uri != local_pdf_uri:
                _toast("Only a PDF selected from this device can open here")
                return
            view.load_uri(uri)
            return
        if "://" not in uri:
            uri = "https://" + uri
        parsed = urlparse(uri)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not _page_allowed(uri, allow):
            _toast(f"Blocked: {host or uri} — not in study list")
            return
        view.load_uri(uri)

    def _build_split_pane(caption):
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        caption_label = Gtk.Label(label=caption)
        caption_label.get_style_context().add_class("sidehead")
        chooser = Gtk.ComboBoxText()
        chooser.append("", "Select link…")
        for link in cfg.get("links", []):
            if isinstance(link, dict) and link.get("url"):
                chooser.append(str(link["url"]), str(link.get("title") or "Study link"))
        chooser.set_hexpand(True)
        pdf_button = Gtk.Button(label="Select PDF")
        back_button = Gtk.Button(label="‹")
        forward_button = Gtk.Button(label="›")
        for button in (pdf_button, back_button, forward_button):
            button.get_style_context().add_class("webnavbtn")
            button.get_style_context().add_class("floatbtn")
        pdf_button.set_tooltip_text("Choose a PDF from this device")
        back_button.set_tooltip_text("Back in this pane")
        forward_button.set_tooltip_text("Forward in this pane")
        toolbar.pack_start(caption_label, False, False, 2)
        toolbar.pack_start(chooser, True, True, 0)
        toolbar.pack_start(pdf_button, False, False, 0)
        toolbar.pack_start(back_button, False, False, 0)
        toolbar.pack_start(forward_button, False, False, 0)
        pane.pack_start(toolbar, False, False, 0)

        try:
            view = WebKit2.WebView.new_with_context(_study_web_context)
        except Exception:
            view = WebKit2.WebView()
        view.set_hexpand(True)
        view.set_vexpand(True)
        try:
            settings = view.get_settings()
            settings.set_enable_developer_extras(False)
            settings.set_enable_html5_local_storage(True)
            settings.set_enable_javascript(True)
            settings.set_enable_page_cache(False)
        except Exception:
            pass
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(view)
        pane.pack_start(scroll, True, True, 0)

        local_pdf_uri = {"uri": None}
        def _choose(_chooser):
            selected = chooser.get_active_id()
            if selected:
                local_pdf_uri["uri"] = None
                _split_load_uri(view, selected)

        def _select_pdf(*_):
            dialog = Gtk.FileChooserDialog(
                title="Select PDF for " + caption.lower() + " pane",
                parent=win, action=Gtk.FileChooserAction.OPEN)
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button("Open PDF", Gtk.ResponseType.ACCEPT)
            pdf_filter = Gtk.FileFilter()
            pdf_filter.set_name("PDF files")
            pdf_filter.add_mime_type("application/pdf")
            pdf_filter.add_pattern("*.pdf")
            pdf_filter.add_pattern("*.PDF")
            dialog.add_filter(pdf_filter)
            if os.path.isdir(STUDY_INBOX):
                dialog.set_current_folder(STUDY_INBOX)
            response = dialog.run()
            selected_path = dialog.get_filename() if response == Gtk.ResponseType.ACCEPT else None
            dialog.destroy()
            if selected_path and selected_path.lower().endswith(".pdf"):
                local_pdf_uri["uri"] = Gio.File.new_for_path(
                    os.path.abspath(selected_path)).get_uri()
                chooser.set_active_id("")
                _split_load_uri(view, local_pdf_uri["uri"], local_pdf_uri["uri"])

        def _policy(_view, decision, decision_type):
            try:
                if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
                    action = decision.get_navigation_action()
                    uri = action.get_request().get_uri() or ""
                    parsed = urlparse(uri)
                    if parsed.scheme in ("about", "blob", "data"):
                        return False
                    if parsed.scheme == "file" and uri == local_pdf_uri["uri"]:
                        return False
                    if (parsed.scheme in ("http", "https")
                            and _page_allowed(uri, allow)):
                        return False
                    decision.ignore()
                    _toast(f"Blocked: {parsed.hostname or uri} — not in study list")
                    return True
                if decision_type == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
                    action = decision.get_navigation_action()
                    uri = action.get_request().get_uri() or ""
                    parsed = urlparse(uri)
                    decision.ignore()
                    if (parsed.scheme in ("http", "https")
                            and _page_allowed(uri, allow)):
                        GLib.idle_add(_split_load_uri, view, address, uri)
                    else:
                        _toast("Popup blocked: site is not in the study list")
                    return True
                if decision_type == WebKit2.PolicyDecisionType.RESPONSE:
                    response = decision.get_response()
                    mime = (response.get_mime_type() or "").lower().split(";", 1)[0]
                    uri = response.get_uri() or ""
                    parsed = urlparse(uri)
                    if (_page_allowed(uri, allow)
                            and (mime == "application/pdf"
                                 or parsed.path.lower().endswith(".pdf"))):
                        decision.download()
                        return True
            except Exception as e:
                print(f"split view policy error: {e}", flush=True)
            return False

        def _loaded(_view, event):
            if event == WebKit2.LoadEvent.FINISHED:
                _record_recent_website(view.get_uri(), view.get_title())

        pdf_button.connect("clicked", _select_pdf)
        chooser.connect("changed", _choose)
        back_button.connect("clicked", lambda *_: view.go_back()
                            if view.can_go_back() else None)
        forward_button.connect("clicked", lambda *_: view.go_forward()
                               if view.can_go_forward() else None)
        view.connect("authenticate", _on_web_authenticate)
        view.connect("decide-policy", _policy)
        view.connect("load-changed", _loaded)
        view.connect("context-menu", lambda *_: True)
        return pane

    split_left = _build_split_pane("LEFT")
    split_right = _build_split_pane("RIGHT")
    split_paned.pack1(split_left, True, False)
    split_paned.pack2(split_right, True, False)
    split_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    split_title = Gtk.Label(label="TWO STUDY PAGES — SIDE BY SIDE")
    split_title.get_style_context().add_class("sidehead")
    split_title.set_halign(Gtk.Align.START)
    split_panel.pack_start(split_title, False, False, 4)
    split_panel.pack_start(split_paned, True, True, 0)

    def _toggle_split(*_):
        if stack.get_visible_child_name() == "split":
            stack.set_visible_child_name(split_return_page["name"])
        else:
            split_return_page["name"] = stack.get_visible_child_name() or "home"
            stack.set_visible_child_name("split")

    def _sync_split_button(_stack, _property):
        active = stack.get_visible_child_name() == "split"
        split_btn.set_label("Single view" if active else "Split view")
        split_btn.set_tooltip_text(
            "Return to the previous page" if active else "Show two study pages side by side")

    split_btn.connect("clicked", _toggle_split)
    stack.connect("notify::visible-child-name", _sync_split_button)

    toast_state = {"id": 0}

    def _hide_toast():
        toast_state["id"] = 0
        try:
            toast_lbl.hide()
        except Exception:
            pass
        return False

    def _toast(msg):
        try:
            toast_lbl.set_text(msg)
            toast_lbl.show()
            if toast_state["id"]:
                GLib.source_remove(toast_state["id"])
            toast_state["id"] = GLib.timeout_add(2600, _hide_toast)
        except Exception:
            print(msg, flush=True)

    def _load_state_file(path, fallback):
        try:
            with open(path) as f:
                value = json.load(f)
            return value if isinstance(value, type(fallback)) else fallback
        except Exception:
            return fallback

    raw_reading_pages = _load_state_file(STUDY_READING_STATE, {})
    reading_pages = {}
    for path, page in raw_reading_pages.items():
        try:
            reading_pages[str(path)] = max(1, int(page))
        except (TypeError, ValueError):
            continue
    raw_recent_items = _load_state_file(STUDY_RECENTS_STATE, [])
    recent_items = [item for item in raw_recent_items
                    if isinstance(item, dict)
                    and ((item.get("kind") == "doc" and item.get("path"))
                         or (item.get("kind") == "web" and item.get("url")))][:40]
    recent_rows = None
    recent_panel = None
    recent_hide_state = {"id": 0}

    def _write_state_file(path, data):
        try:
            os.makedirs(LEDGER_DIR, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception as e:
            print(f"study state save error: {e}", flush=True)

    def _record_recent(kind, key, title, page=None):
        if not key:
            return
        now = time.time()
        identity = ("doc", os.path.realpath(key)) if kind == "doc" else ("web", key)
        recent_items[:] = [
            item for item in recent_items
            if not ((item.get("kind"),
                     os.path.realpath(item.get("path", "")) if item.get("kind") == "doc"
                     else item.get("url", "")) == identity)
        ]
        item = {"kind": kind, "title": title or key, "time": now}
        if kind == "doc":
            item.update(path=os.path.realpath(key), page=max(1, int(page or 1)))
        else:
            item["url"] = key
        recent_items.insert(0, item)
        del recent_items[40:]
        _write_state_file(STUDY_RECENTS_STATE, recent_items)
        if recent_rows is not None:
            _render_recent()

    def _record_recent_website(uri, title):
        try:
            parsed = urlparse(uri or "")
            if parsed.scheme not in ("http", "https") or not _page_allowed(uri, allow):
                return
            label = title or parsed.hostname
            _record_recent("web", uri, label)
        except Exception:
            pass

    def _record_recent_document(path, page=1):
        _record_recent("doc", path, os.path.basename(path), page)

    def _save_reading_page(path, page):
        if not path or not path.lower().endswith(".pdf"):
            return
        key = os.path.realpath(path)
        reading_pages[key] = max(1, int(page))
        _write_state_file(STUDY_READING_STATE, reading_pages)
        _record_recent_document(key, page)

    def _battery_text():
        for supply in glob.glob("/sys/class/power_supply/*"):
            name = os.path.basename(supply)
            try:
                with open(os.path.join(supply, "type")) as f:
                    kind = f.read().strip().lower()
                if kind != "battery" and not name.upper().startswith("BAT"):
                    continue
                with open(os.path.join(supply, "capacity")) as f:
                    percent = max(0, min(100, int(f.read().strip())))
                return f"Battery {percent}%"
            except (OSError, ValueError):
                continue
        return "Battery N/A"

    def _refresh_battery():
        try:
            battery_lbl.set_text(_battery_text())
        except Exception:
            pass
        return True

    _refresh_battery()
    battery_timer = GLib.timeout_add_seconds(30, _refresh_battery)

    def _on_decide_policy(_view, decision, dtype):
        try:
            if dtype == WebKit2.PolicyDecisionType.RESPONSE:
                response = decision.get_response()
                uri = response.get_uri() or ""
                mime = (response.get_mime_type() or "").lower().split(";", 1)[0].strip()
                host = (urlparse(uri).hostname or "").lower()
                path = urlparse(uri).path.lower()
                if (_page_allowed(uri, allow)
                        and (mime == "application/pdf" or path.endswith(".pdf"))):
                    decision.download()
                    return True
            if dtype == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
                uri = decision.get_navigation_action().get_request().get_uri()
                if uri and uri.startswith(("file://", "blob:", "data:", "about:")):
                    return False  # in-page PDF/blob from an already-allowed page
                host = (urlparse(uri).hostname or "").lower() if uri else ""
                if uri and _page_allowed(uri, allow):
                    if uri.lower().split("?")[0].endswith(".pdf"):
                        # fetch into inbox and open in the built-in reader
                        decision.ignore()
                        try:
                            web.download_uri(uri)
                        except Exception as e:
                            print(f"pdf download error: {e}", flush=True)
                        return True
                    return False
                decision.ignore()
                _toast(f"Blocked: {host or uri} \u2014 not in study list")
                return True
            if dtype == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
                uri = decision.get_navigation_action().get_request().get_uri()
                host = (urlparse(uri).hostname or "").lower() if uri else ""
                decision.ignore()
                if uri and _page_allowed(uri, allow):
                    # BoiMate and file hosts sometimes target a new window. Reuse
                    # the current view so its normal download policy can handle it.
                    GLib.idle_add(web.load_uri, uri)
                else:
                    _toast("Popup blocked: site is not in the study list")
                return True
        except Exception as e:
            print(f"policy error: {e}", flush=True)
        return False

    def _on_create(*_a):
        _toast("Popup blocked")
        return None

    def _on_context_menu(*_a):
        return True

    def _on_download(download):
        try:
            name = download.get_suggested_filename() or "download"
            name = os.path.basename(urllib.parse.unquote(name.replace("\\", "/")))
            ext = os.path.splitext(name)[1].lower()
            response = download.get_response()
            mime = (response.get_mime_type() or "").lower().split(";", 1)[0].strip() \
                if response else ""
            inferred_ext = {
                "application/pdf": ".pdf", "image/jpeg": ".jpg",
                "image/png": ".png", "image/webp": ".webp",
                "image/gif": ".gif", "image/bmp": ".bmp",
            }.get(mime, "")
            page_host = (urlparse(web.get_uri() or "").hostname or "").lower()
            if not inferred_ext and mime in ("", "application/octet-stream", "application/pdf") \
                    and _host_allowed(page_host, ("boimate.com", "*.boimate.com")):
                inferred_ext = ".pdf"
            allowed_exts = set(dl_exts) | {".pdf"}
            if ext not in allowed_exts and inferred_ext in allowed_exts:
                name = (os.path.splitext(name)[0] or "boimate-book") + inferred_ext
                ext = inferred_ext
            if ext not in allowed_exts:
                download.cancel()
                _toast(f"File type not supported: {name}")
                return
            try:
                os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                if not os.access(DOWNLOAD_DIR, os.W_OK):
                    raise OSError("Downloads folder is not writable")
                download_dir = DOWNLOAD_DIR
            except OSError:
                os.makedirs(STUDY_INBOX, exist_ok=True)
                download_dir = STUDY_INBOX
                _toast("Downloads folder unavailable; saving to Study Inbox")
            dst = os.path.join(download_dir, name)
            if os.path.exists(dst):
                stem, e = os.path.splitext(dst)
                dst = f"{stem}-{int(time.time())}{e}"
            download.set_destination("file://" + urllib.parse.quote(dst))

            def _done(*_a):
                if not os.path.isfile(dst) or os.path.getsize(dst) <= 0:
                    _toast("Download did not finish; try the book link again")
                    return
                location = "Downloads" if download_dir == DOWNLOAD_DIR else "Study Inbox"
                _toast(f"Downloaded to {location}: {os.path.basename(dst)}")
                refresh_library()
                if dst.lower().endswith(".pdf"):
                    _open_media(dst)

            def _failed(_download, error):
                print(f"download failed: {error}", flush=True)
                _toast(f"Download failed: {error}")

            download.connect("finished", _done)
            download.connect("failed", _failed)
        except Exception as e:
            print(f"download error: {e}", flush=True)
            try:
                download.cancel()
            except Exception:
                pass

    web.connect("decide-policy", _on_decide_policy)
    web.connect("create", _on_create)
    web.connect("context-menu", _on_context_menu)
    download_context = None
    download_handler_id = 0
    try:
        download_context = web.get_context()
        download_handler_id = download_context.connect(
            "download-started", lambda _c, d: _on_download(d))
    except Exception:
        pass

    # ---- home (bismillah) ----
    home = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    home.set_valign(Gtk.Align.CENTER)
    home.set_halign(Gtk.Align.CENTER)
    home.get_style_context().add_class("homepage")
    bism = Gtk.Label()
    bism.set_markup('<span font_family="Amiri" size="52000" foreground="#e8eefc">'
                    '\u0628\u0650\u0633\u0652\u0645\u0650 \u0627\u0644\u0644\u064e\u0651\u0647\u0650 '
                    '\u0627\u0644\u0631\u064e\u0651\u062d\u0652\u0645\u064e\u0670\u0646\u0650 '
                    '\u0627\u0644\u0631\u064e\u0651\u062d\u0650\u064a\u0645\u0650</span>')
    bism.set_halign(Gtk.Align.CENTER)
    home.pack_start(bism, False, False, 0)
    flavor = Gtk.Label()
    flavor.set_markup('<span font_family="Amiri" size="20000" foreground="#9cb3d9">'
                      '\u0628\u0650\u0633\u0652\u0645\u0650 \u0627\u0644\u0644\u064e\u0651\u0647\u0650 '
                      '\u0627\u0644\u0631\u064e\u0651\u062d\u0652\u0645\u064e\u0670\u0646\u0650 '
                      '\u0627\u0644\u0631\u064e\u0651\u062d\u0650\u064a\u0645\u0650</span>')
    flavor.set_opacity(0.55)
    flavor.set_halign(Gtk.Align.CENTER)
    home.pack_start(flavor, False, False, 0)
    stack.add_named(home, "home")

    # ---- PDF / image reader with annotation layer ----
    reader = Gtk.Overlay()
    canvas = Gtk.DrawingArea()
    canvas.set_hexpand(True)
    canvas.set_vexpand(True)
    canvas.set_can_focus(True)
    img_scroll = Gtk.ScrolledWindow()
    img_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    img_scroll.set_vexpand(True)
    img_scroll.add(canvas)
    img_scroll.set_overlay_scrolling(False)
    reader.add(img_scroll)

    floatbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    floatbar.get_style_context().add_class("floatbar")
    floatbar.set_halign(Gtk.Align.END)
    floatbar.set_valign(Gtk.Align.CENTER)
    floatbar.set_margin_end(14)
    floatbar.set_margin_top(14)
    floatbar.set_margin_bottom(14)

    prev_btn = Gtk.Button(label="\u25b2")
    next_btn = Gtk.Button(label="\u25bc")
    zoom_in = Gtk.Button(label="+")
    zoom_out = Gtk.Button(label="\u2212")
    fit_btn = Gtk.Button(label="Fit")
    fit_btn.set_tooltip_text("Fit PDF page width to the viewer")
    actual_size_btn = Gtk.Button(label="100%")
    undo_btn = Gtk.Button(label="\u21b6")
    redo_btn = Gtk.Button(label="\u21b7")
    save_btn = Gtk.Button(label="\U0001F4BE")
    pen_btn = Gtk.Button(label="\u270e")
    high_btn = Gtk.Button(label="\u25ad")
    erase_btn = Gtk.Button(label="\u232b")
    page_lbl = Gtk.Label(label="1/1")
    page_lbl.get_style_context().add_class("floatpage")
    for b in (prev_btn, next_btn, zoom_in, zoom_out, fit_btn, actual_size_btn,
              undo_btn, redo_btn, save_btn,
              pen_btn, high_btn, erase_btn):
        b.get_style_context().add_class("floatbtn")
        b.set_size_request(38, 38)
        floatbar.pack_start(b, False, False, 0)
    fit_btn.set_size_request(48, 34)
    actual_size_btn.set_size_request(52, 34)
    floatbar.pack_start(page_lbl, False, False, 0)
    floatbar.set_no_show_all(False)
    reader.add_overlay(floatbar)
    toolbar_edge = Gtk.EventBox()
    toolbar_edge.set_visible_window(False)
    toolbar_edge.set_size_request(10, -1)
    toolbar_edge.set_halign(Gtk.Align.END)
    toolbar_edge.set_valign(Gtk.Align.FILL)
    toolbar_edge.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK
                            | Gdk.EventMask.LEAVE_NOTIFY_MASK
                            | Gdk.EventMask.POINTER_MOTION_MASK)
    reader.add_overlay(toolbar_edge)
    toolbar_hover = {"inside": False, "hide_id": 0}

    def _cancel_toolbar_hide():
        if toolbar_hover["hide_id"]:
            try:
                GLib.source_remove(toolbar_hover["hide_id"])
            except Exception:
                pass
            toolbar_hover["hide_id"] = 0

    def _schedule_toolbar_hide(delay=1200):
        _cancel_toolbar_hide()
        def _hide():
            toolbar_hover["hide_id"] = 0
            if not toolbar_hover["inside"]:
                floatbar.set_opacity(0.0)
                floatbar.set_sensitive(False)
            return False
        toolbar_hover["hide_id"] = GLib.timeout_add(delay, _hide)

    def _reveal_toolbar(*_args):
        if not pdf_layout["active"]:
            return False
        floatbar.set_sensitive(True)
        floatbar.set_opacity(1.0)
        if not toolbar_hover["inside"]:
            _schedule_toolbar_hide()
        return False

    def _toolbar_enter(*_args):
        toolbar_hover["inside"] = True
        _cancel_toolbar_hide()
        _reveal_toolbar()
        return False

    def _toolbar_leave(*_args):
        toolbar_hover["inside"] = False
        _schedule_toolbar_hide()
        return False

    toolbar_edge.connect("enter-notify-event", _toolbar_enter)
    toolbar_edge.connect("motion-notify-event", _toolbar_enter)
    toolbar_edge.connect("leave-notify-event", _toolbar_leave)
    floatbar.connect("enter-notify-event", _toolbar_enter)
    floatbar.connect("leave-notify-event", _toolbar_leave)
    for _tool in floatbar.get_children():
        _tool.connect("enter-notify-event", _toolbar_enter)
        _tool.connect("leave-notify-event", _toolbar_leave)
    stack.add_named(web_wrap, "web")
    stack.add_named(reader, "reader")
    stack.add_named(split_panel, "split")

    import cairo as _pcairo  # pycairo: pixbuf filter + annotation PDF surface

    state = {
        "path": None, "page": 1, "pages": 1, "zoom": 1.0,
        "pb": None, "tool": "pen",
        "strokes": [], "redo": [], "drawing": None,
        "page_pts": (595.0, 842.0), "backup_done": False,
    }
    TOOL_STYLE = {"pen": ((0.85, 0.10, 0.10), 3.0, 1.0),
                  "high": ((1.0, 0.85, 0.10), 16.0, 0.35)}
    PDF_GAP = 22
    pdf_layout = {"active": False, "page_w": 0, "page_h": 0,
                  "canvas_w": 0, "canvas_h": 0, "fit_width": True,
                  "restoring": False, "restore_page": 1}
    pdf_needed_pages = set()
    pdf_pending = set()

    def _canvas_size():
        return canvas.get_allocated_width(), canvas.get_allocated_height()

    def _chaikin(pts, iters=2):
        """Round a raw pointer trail into a natural ink stroke."""
        for _ in range(iters):
            if len(pts) < 3:
                break
            out = [pts[0]]
            for i in range(len(pts) - 1):
                px, py = pts[i]
                qx, qy = pts[i + 1]
                out.append((0.75 * px + 0.25 * qx, 0.75 * py + 0.25 * qy))
                out.append((0.25 * px + 0.75 * qx, 0.25 * py + 0.75 * qy))
            out.append(pts[-1])
            pts = out
        return pts

    def _trace(cr, pts, sx, sy):
        if not pts:
            return
        if len(pts) == 1:
            return
        cr.move_to(pts[0][0] * sx, pts[0][1] * sy)
        for x, y in pts[1:]:
            cr.line_to(x * sx, y * sy)

    def _draw_stroke(cr, s, w, h):
        pts = _chaikin(s["pts"])
        if len(pts) < 1:
            return
        (r, g, b), wdt, alpha = TOOL_STYLE.get(s["tool"], TOOL_STYLE["pen"])
        cr.set_source_rgba(r, g, b, alpha)
        cr.set_line_width(wdt)
        cr.set_line_cap(1)   # round
        cr.set_line_join(1)
        if len(pts) == 1:
            cr.arc(pts[0][0] * w, pts[0][1] * h, wdt / 2.0, 0, 6.283)
            cr.fill()
        else:
            _trace(cr, pts, w, h)
            cr.stroke()

    def _on_draw(_w, cr):
        w, h = _canvas_size()
        if pdf_layout["active"]:
            return _draw_pdf_continuous(cr, w, h)
        pb = state["pb"]
        if pb is not None and w > 1 and h > 1:
            x = max(0, (w - pb.get_width()) // 2)
            y = max(0, (h - pb.get_height()) // 2)
            Gdk.cairo_set_source_pixbuf(cr, pb, x, y)
            cr.get_source().set_filter(_pcairo.Filter.BILINEAR)
            cr.paint()
        for s in state["strokes"]:
            _draw_stroke(cr, s, w, h)
        cur = state["drawing"]
        if cur is not None:
            _draw_stroke(cr, cur, w, h)
        return False

    canvas.connect("draw", _on_draw)

    def _set_pb(pb, natural=False):
        state["pb"] = pb
        if pb is not None:
            canvas.set_size_request(pb.get_width(), pb.get_height())
            canvas.queue_draw()

    pdf_meta_cache = {}
    page_cache = {}
    page_cache_order = []
    page_request = {"id": 0}
    pdf_meta_lock = threading.Lock()
    media_worker_lock = threading.Lock()

    def _fit_zoom():
        page_w = max(1.0, float(state["page_pts"][0]))
        view_w = img_scroll.get_allocated_width()
        if view_w <= 1:
            view_w = max(360, win.get_allocated_width() - 310)
        return max(0.10, min(3.0, (view_w - PDF_GAP * 2) * 72.0 / (96.0 * page_w)))

    def _update_pdf_layout():
        if not pdf_layout["active"]:
            return
        page_pts_w, page_pts_h = state["page_pts"]
        page_w = max(1, int(page_pts_w * 96.0 / 72.0 * state["zoom"]))
        page_h = max(1, int(page_pts_h * 96.0 / 72.0 * state["zoom"]))
        viewport_w = img_scroll.get_allocated_width()
        if viewport_w <= 1:
            viewport_w = max(360, win.get_allocated_width() - 290)
        canvas_w = max(viewport_w, page_w + PDF_GAP * 2)
        canvas_h = max(1, (page_h + PDF_GAP) * state["pages"] + PDF_GAP)
        pdf_layout.update(page_w=page_w, page_h=page_h,
                          canvas_w=canvas_w, canvas_h=canvas_h)
        canvas.set_size_request(canvas_w, canvas_h)
        canvas.queue_draw()

    def _draw_pdf_continuous(cr, width, height):
        page_w = pdf_layout["page_w"]
        page_h = pdf_layout["page_h"]
        stride = page_h + PDF_GAP
        clip = cr.clip_extents()
        first = max(0, int(max(0, clip[1] - PDF_GAP) // stride))
        last = min(state["pages"] - 1,
                   int(max(0, clip[3] - PDF_GAP) // stride))
        cr.save()
        cr.set_source_rgb(0.12, 0.15, 0.19)
        cr.paint()
        path = state["path"]
        for index in range(first, last + 1):
            page = index + 1
            x = max(PDF_GAP, (width - page_w) // 2)
            y = PDF_GAP + index * stride
            cr.set_source_rgb(0.07, 0.09, 0.12)
            cr.rectangle(x - 3, y - 3, page_w + 6, page_h + 6)
            cr.fill()
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.rectangle(x, y, page_w, page_h)
            cr.fill()
            key = (path, page, round(state["zoom"], 2))
            pb = page_cache.get(key)
            if pb is not None:
                cr.save()
                cr.translate(x, y)
                cr.scale(page_w / float(pb.get_width()),
                         page_h / float(pb.get_height()))
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
                cr.get_source().set_filter(_pcairo.Filter.BILINEAR)
                cr.paint()
                cr.restore()
            else:
                cr.set_source_rgb(0.32, 0.37, 0.44)
                cr.select_font_face("Sans", _pcairo.FONT_SLANT_NORMAL,
                                    _pcairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(15)
                cr.move_to(x + 18, y + 32)
                cr.show_text(f"Loading page {page}")
            strokes, _redo_mem = _page_strokes(path, page)
            cr.save()
            cr.translate(x, y)
            for stroke in strokes:
                _draw_stroke(cr, stroke, page_w, page_h)
            drawing = state["drawing"]
            if drawing is not None and drawing.get("page") == page:
                _draw_stroke(cr, drawing, page_w, page_h)
            cr.restore()
        cr.restore()
        return False

    def _apply_pdf_page(request_id, path, page, zoom, pb, error):
        key = (path, page, round(zoom, 2))
        pdf_pending.discard(key)
        if request_id != page_request["id"] or state["path"] != path:
            return False
        if not win.get_visible() or page not in pdf_needed_pages:
            return False
        if error or pb is None:
            if error != "cancelled" and page == state["page"]:
                _toast(f"Could not render PDF page {page}: {error or 'render failed'}")
            return False
        page_cache[key] = pb
        if key in page_cache_order:
            page_cache_order.remove(key)
        page_cache_order.append(key)
        keep = set((path, n, round(zoom, 2)) for n in pdf_needed_pages)
        while len(page_cache_order) > max(8, len(keep)):
            old_key = page_cache_order.pop(0)
            if old_key not in keep:
                page_cache.pop(old_key, None)
            else:
                page_cache_order.append(old_key)
                if all(k in keep for k in page_cache_order):
                    break
        canvas.queue_draw()
        return False

    def _request_pdf_page(page):
        path = state["path"]
        zoom = float(state["zoom"])
        key = (path, page, round(zoom, 2))
        if not path or key in page_cache or key in pdf_pending:
            return
        pdf_pending.add(key)
        request_id = page_request["id"]

        def _work():
            with media_worker_lock:
                if request_id != page_request["id"] or page not in pdf_needed_pages:
                    GLib.idle_add(_apply_pdf_page, request_id, path, page, zoom,
                                  None, "cancelled")
                    return
                try:
                    pb = _render_pdf_page(
                        path, page, zoom,
                        cancel=lambda: (request_id != page_request["id"]
                                        or page not in pdf_needed_pages))
                    error = None if pb is not None else (
                        "cancelled" if page not in pdf_needed_pages else "render failed")
                except Exception as e:
                    pb, error = None, str(e)
            GLib.idle_add(_apply_pdf_page, request_id, path, page, zoom, pb, error)

        threading.Thread(target=_work, daemon=True).start()

    def _ensure_pdf_pages(*_):
        if not pdf_layout["active"]:
            return False
        adjustment = img_scroll.get_vadjustment()
        top = adjustment.get_value()
        view_h = max(1, img_scroll.get_allocated_height())
        stride = pdf_layout["page_h"] + PDF_GAP
        first = max(1, int(max(0, top - PDF_GAP) // stride) + 1)
        last = min(state["pages"], int(max(0, top + view_h - PDF_GAP) // stride) + 1)
        needed = set(range(max(1, first - 1), min(state["pages"], last + 1) + 1))
        pdf_needed_pages.clear()
        pdf_needed_pages.update(needed)
        for page in sorted(needed):
            _request_pdf_page(page)
        canvas.queue_draw()
        return False

    def _select_pdf_page(page, save=True):
        page = max(1, min(state["pages"], int(page)))
        if page != state["page"]:
            _remember()
            state["page"] = page
            state["strokes"], state["redo"] = _page_strokes(state["path"], page)
            if save:
                _save_reading_page(state["path"], page)
        page_lbl.set_text(f"{state['page']}/{state['pages']}")
        prev_btn.set_sensitive(state["page"] > 1)
        next_btn.set_sensitive(state["page"] < state["pages"])

    def _on_pdf_scroll(*_):
        if not pdf_layout["active"] or pdf_layout["restoring"]:
            return False
        adjustment = img_scroll.get_vadjustment()
        stride = pdf_layout["page_h"] + PDF_GAP
        page = int(max(0, adjustment.get_value() - PDF_GAP) // stride) + 1
        _select_pdf_page(page)
        _ensure_pdf_pages()
        return False

    def _restore_pdf_scroll():
        if not pdf_layout["active"]:
            return False
        stride = pdf_layout["page_h"] + PDF_GAP
        adjustment = img_scroll.get_vadjustment()
        page = max(1, min(state["pages"], int(pdf_layout["restore_page"])))
        target = max(0, PDF_GAP + (page - 1) * stride)
        maximum = max(0, adjustment.get_upper() - adjustment.get_page_size())
        if target > maximum + 2:
            attempts = getattr(_restore_pdf_scroll, "attempts", 0)
            if attempts < 60:
                _restore_pdf_scroll.attempts = attempts + 1
                GLib.timeout_add(80, _restore_pdf_scroll)
                return False
        _restore_pdf_scroll.attempts = 0
        state["page"] = page
        pdf_layout["restoring"] = False
        adjustment.set_value(min(target, maximum))
        _on_pdf_scroll()
        return False

    def _show_page():
        if pdf_layout["active"]:
            _ensure_pdf_pages()
        canvas.queue_draw()

    def _init_pdf_document(request_id, path, resume_page, meta, error):
        if request_id != page_request["id"] or state["path"] != path:
            return False
        if error:
            page_lbl.set_text("PDF unavailable")
            _toast(f"Could not read PDF: {error}")
            return False
        pdf_meta_cache[path] = meta
        state["pages"], state["page_pts"] = meta
        state["page"] = max(1, min(state["pages"], resume_page))
        pdf_layout["restore_page"] = state["page"]
        pdf_layout["restoring"] = True
        state["zoom"] = _fit_zoom()
        pdf_layout["active"] = True
        pdf_layout["fit_width"] = True
        _update_pdf_layout()
        state["strokes"], state["redo"] = _page_strokes(path, state["page"])
        _save_reading_page(path, state["page"])
        _select_pdf_page(state["page"], save=False)
        _reveal_toolbar()
        GLib.idle_add(_restore_pdf_scroll)
        return False

    def _load_pdf_document(request_id, path, resume_page):
        def _work():
            try:
                with media_worker_lock:
                    with pdf_meta_lock:
                        meta = pdf_meta_cache.get(path)
                        if meta is None:
                            meta = _pdf_metadata(path)
                            pdf_meta_cache[path] = meta
                error = None
            except Exception as e:
                meta, error = None, str(e)
            GLib.idle_add(_init_pdf_document, request_id, path, resume_page, meta, error)
        threading.Thread(target=_work, daemon=True).start()

    def _goto_pdf_page(page):
        if not pdf_layout["active"]:
            return
        page = max(1, min(state["pages"], int(page)))
        stride = pdf_layout["page_h"] + PDF_GAP
        img_scroll.get_vadjustment().set_value(max(0, PDF_GAP + (page - 1) * stride))
        _select_pdf_page(page)
        _ensure_pdf_pages()

    img_scroll.get_vadjustment().connect("value-changed", _on_pdf_scroll)

    def _on_reader_resize(_widget, _allocation):
        if pdf_layout["active"] and pdf_layout["fit_width"]:
            new_zoom = _fit_zoom()
            if abs(new_zoom - state["zoom"]) > 0.01:
                pdf_layout["restore_page"] = state["page"]
                pdf_layout["restoring"] = True
                page_request["id"] += 1
                pdf_pending.clear()
                pdf_needed_pages.clear()
                page_cache.clear()
                page_cache_order.clear()
                state["zoom"] = new_zoom
                _update_pdf_layout()
                GLib.idle_add(_restore_pdf_scroll)
                return
        _ensure_pdf_pages()

    img_scroll.connect("size-allocate", _on_reader_resize)

    STROKE_MEM = {}

    def _page_strokes(path, page):
        return STROKE_MEM.get((path, page), ([], []))

    def _remember():
        STROKE_MEM[(state["path"], state["page"])] = (state["strokes"], state["redo"])

    def _open_media(path):
        ext = os.path.splitext(path)[1].lower()
        state.update(backup_done=False)
        page_request["id"] += 1
        request_id = page_request["id"]
        pdf_needed_pages.clear()
        pdf_pending.clear()
        pdf_layout["active"] = False
        state["drawing"] = None
        stack.set_visible_child_name("reader")
        state["pb"] = None
        canvas.set_size_request(1, 1)
        canvas.queue_draw()
        if ext == ".pdf":
            floatbar.show_all()
            toolbar_edge.show()
            toolbar_hover["inside"] = False
            _reveal_toolbar()
            resume_page = max(1, int(reading_pages.get(os.path.realpath(path), 1)))
            state.update(path=path, page=resume_page, pages=1, zoom=1.0,
                         strokes=[], redo=[], drawing=None)
            page_lbl.set_text(f"Resuming at page {resume_page}…" if resume_page > 1
                              else "Loading PDF…")
            _record_recent_document(path, resume_page)
            prev_btn.set_sensitive(False)
            next_btn.set_sensitive(False)
            page_cache.clear()
            page_cache_order.clear()
            _load_pdf_document(request_id, path, resume_page)
            canvas.grab_focus()
        else:
            _cancel_toolbar_hide()
            toolbar_hover["inside"] = False
            toolbar_edge.hide()
            floatbar.hide()
            state.update(path=path, pages=1, page=1, zoom=1.0,
                         strokes=[], redo=[], drawing=None)
            _record_recent_document(path, 1)
            page_lbl.set_text("Loading image…")
            view_w = img_scroll.get_allocated_width()
            view_h = img_scroll.get_allocated_height()
            if view_w <= 1 or view_h <= 1:
                screen = Gdk.Screen.get_default()
                screen_w = screen.get_width() if screen else 1440
                screen_h = screen.get_height() if screen else 900
                view_w = max(view_w, screen_w - 300)
                view_h = max(view_h, screen_h - 150)

            def _load_image():
                with media_worker_lock:
                    if request_id != page_request["id"]:
                        return
                    try:
                        info = GdkPixbuf.Pixbuf.get_file_info(path)
                        if info and info[1] > 0 and info[2] > 0:
                            source_w, source_h = info[1], info[2]
                            max_w = max(1, min(2400, view_w - 24 if view_w > 1 else 1200))
                            max_h = max(1, min(3000, view_h - 24 if view_h > 1 else 1400))
                            scale = min(1.0, max_w / float(source_w), max_h / float(source_h))
                            target_w = max(1, int(source_w * scale))
                            target_h = max(1, int(source_h * scale))
                            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                                path, target_w, target_h, True)
                        else:
                            pb = GdkPixbuf.Pixbuf.new_from_file(path)
                        error = None
                    except Exception as e:
                        pb, error = None, str(e)
                def _apply_image():
                    if (request_id != page_request["id"] or state["path"] != path
                            or not win.get_visible()):
                        return False
                    if pb is None:
                        page_lbl.set_text("Image unavailable")
                        _toast(f"Cannot open image: {error}")
                    else:
                        _set_pb(pb)
                        page_lbl.set_text("Image")
                    canvas.grab_focus()
                    return False
                GLib.idle_add(_apply_image)

            threading.Thread(target=_load_image, daemon=True).start()

    # ---- annotation input ----
    def _pdf_point(x, y, page=None, clamp=False):
        if not pdf_layout["active"]:
            return None
        page_w, page_h = pdf_layout["page_w"], pdf_layout["page_h"]
        stride = page_h + PDF_GAP
        if page is None:
            page = int((y - PDF_GAP) // stride) + 1
        page = max(1, min(state["pages"], page))
        px = max(PDF_GAP, (_canvas_size()[0] - page_w) // 2)
        py = PDF_GAP + (page - 1) * stride
        local_x, local_y = x - px, y - py
        if not clamp and (local_x < 0 or local_x > page_w
                          or local_y < 0 or local_y > page_h):
            return None
        norm = (max(0.0, min(1.0, local_x / max(1, page_w))),
                max(0.0, min(1.0, local_y / max(1, page_h))))
        return page, norm

    def _on_press(_w, ev):
        if state["path"] is None or not state["path"].lower().endswith(".pdf"):
            return False
        if ev.button != 1:
            return False
        point = _pdf_point(ev.x, ev.y)
        if point is None:
            return False
        page, norm_point = point
        if page != state["page"]:
            _select_pdf_page(page)
        if state["tool"] == "erase":
            x, y = norm_point
            before = len(state["strokes"])
            state["strokes"] = [
                s for s in state["strokes"]
                if not any(abs(px - x) < 0.02 and abs(py - y) < 0.02 for px, py in s["pts"])
            ]
            if len(state["strokes"]) != before:
                _remember()
                canvas.queue_draw()
            return True
        state["drawing"] = {"tool": state["tool"], "page": page,
                             "pts": [norm_point]}
        canvas.queue_draw()
        return True

    def _on_motion(_w, ev):
        if state["drawing"] is None:
            return False
        pts = state["drawing"]["pts"]
        point = _pdf_point(ev.x, ev.y, state["drawing"]["page"], clamp=True)
        if point is None:
            return False
        p = point[1]
        if pts:
            w, h = pdf_layout["page_w"], pdf_layout["page_h"]
            dx = (p[0] - pts[-1][0]) * w
            dy = (p[1] - pts[-1][1]) * h
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > 3.0:
                n = min(40, int(dist / 3.0))
                ox, oy = pts[-1]
                for k in range(1, n):
                    pts.append((ox + (p[0] - ox) * k / n, oy + (p[1] - oy) * k / n))
        pts.append(p)
        canvas.queue_draw()
        return True

    def _on_release(_w, _ev):
        cur = state["drawing"]
        state["drawing"] = None
        if cur and cur["pts"]:
            state["strokes"].append(cur)
            state["redo"] = []
            _remember()
            canvas.queue_draw()
        return True

    canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                      | Gdk.EventMask.POINTER_MOTION_MASK
                      | Gdk.EventMask.BUTTON_MOTION_MASK
                      | Gdk.EventMask.BUTTON1_MOTION_MASK)
    canvas.connect("button-press-event", _on_press)
    canvas.connect("motion-notify-event", _on_motion)
    canvas.connect("button-release-event", _on_release)

    def _undo():
        if state["strokes"]:
            state["redo"].append(state["strokes"].pop())
            _remember()
            canvas.queue_draw()

    def _redo():
        if state["redo"]:
            state["strokes"].append(state["redo"].pop())
            _remember()
            canvas.queue_draw()

    def _save_annotations():
        path = state["path"]
        if not path or not path.lower().endswith(".pdf"):
            _toast("Save works on PDFs")
            return
        if not state["strokes"]:
            _toast("Nothing to save")
            return
        try:
            import cairo as _cairo
            from pypdf import PdfReader, PdfWriter

            w_pts, h_pts = state["page_pts"]
            tmp_pdf = os.path.join(tempfile.gettempdir(),
                                   f"focus-annot-{os.getpid()}-{state['page']}.pdf")
            surf = _cairo.PDFSurface(tmp_pdf, w_pts, h_pts)
            cr = _cairo.Context(surf)
            cr.set_line_cap(1)
            cr.set_line_join(1)
            for s in state["strokes"]:
                pts = _chaikin(s["pts"])
                (r, g, b), wdt, alpha = TOOL_STYLE.get(s["tool"], TOOL_STYLE["pen"])
                cr.set_source_rgba(r, g, b, alpha)
                cr.set_line_width(max(0.6, wdt * w_pts / 900.0))
                if len(pts) == 1:
                    cr.arc(pts[0][0] * w_pts, pts[0][1] * h_pts,
                           max(0.6, wdt * w_pts / 1800.0), 0, 6.283)
                    cr.fill()
                elif len(pts) > 1:
                    _trace(cr, pts, w_pts, h_pts)
                    cr.stroke()
            surf.finish()

            reader_pdf = PdfReader(path)
            overlay = PdfReader(tmp_pdf).pages[0]
            idx = min(len(reader_pdf.pages) - 1, max(0, state["page"] - 1))
            reader_pdf.pages[idx].merge_page(overlay)

            if not state["backup_done"]:
                shutil.copy2(path, path + ".bak")
                state["backup_done"] = True

            out = path + ".annot-tmp"
            writer = PdfWriter()
            for pg in reader_pdf.pages:
                writer.add_page(pg)
            with open(out, "wb") as f:
                writer.write(f)
            os.replace(out, path)
            state["strokes"], state["redo"] = [], []
            STROKE_MEM[(path, state["page"])] = ([], [])
            for cache_key in [k for k in page_cache if k[0] == path]:
                page_cache.pop(cache_key, None)
                if cache_key in page_cache_order:
                    page_cache_order.remove(cache_key)
            _toast(f"Saved into {os.path.basename(path)}")
            _show_page()
        except Exception as e:
            print(f"annot save error: {e}", flush=True)
            _toast(f"Save failed: {e}")

    def _set_tool(t):
        state["tool"] = t
        for b, name in ((pen_btn, "pen"), (high_btn, "high"), (erase_btn, "erase")):
            b.get_style_context().remove_class("floatbtn-on")
            if name == t:
                b.get_style_context().add_class("floatbtn-on")

    pen_btn.connect("clicked", lambda *_: _set_tool("pen"))
    high_btn.connect("clicked", lambda *_: _set_tool("high"))
    erase_btn.connect("clicked", lambda *_: _set_tool("erase"))
    undo_btn.connect("clicked", lambda *_: _undo())
    redo_btn.connect("clicked", lambda *_: _redo())
    save_btn.connect("clicked", lambda *_: _save_annotations())
    _set_tool("pen")

    def _change_page(delta):
        if pdf_layout["active"]:
            _goto_pdf_page(state["page"] + delta)

    def _change_zoom(delta):
        if not pdf_layout["active"]:
            return
        _remember()
        pdf_layout["restore_page"] = state["page"]
        pdf_layout["restoring"] = True
        page_request["id"] += 1
        pdf_pending.clear()
        pdf_needed_pages.clear()
        page_cache.clear()
        page_cache_order.clear()
        pdf_layout["fit_width"] = False
        state["zoom"] = max(0.10, min(3.0, state["zoom"] + delta))
        _update_pdf_layout()
        GLib.idle_add(_restore_pdf_scroll)

    def _fit_page(*_):
        if not pdf_layout["active"]:
            return
        pdf_layout["restore_page"] = state["page"]
        pdf_layout["restoring"] = True
        page_request["id"] += 1
        pdf_pending.clear()
        pdf_needed_pages.clear()
        page_cache.clear()
        page_cache_order.clear()
        pdf_layout["fit_width"] = True
        state["zoom"] = _fit_zoom()
        _update_pdf_layout()
        GLib.idle_add(_restore_pdf_scroll)

    def _actual_size(*_):
        if not pdf_layout["active"]:
            return
        pdf_layout["restore_page"] = state["page"]
        pdf_layout["restoring"] = True
        page_request["id"] += 1
        pdf_pending.clear()
        pdf_needed_pages.clear()
        page_cache.clear()
        page_cache_order.clear()
        pdf_layout["fit_width"] = False
        state["zoom"] = 1.0
        _update_pdf_layout()
        GLib.idle_add(_restore_pdf_scroll)

    prev_btn.connect("clicked", lambda *_: _change_page(-1))
    next_btn.connect("clicked", lambda *_: _change_page(1))
    zoom_in.connect("clicked", lambda *_: _change_zoom(0.25))
    zoom_out.connect("clicked", lambda *_: _change_zoom(-0.25))
    fit_btn.connect("clicked", _fit_page)
    actual_size_btn.connect("clicked", _actual_size)

    def _load_link(url):
        if url.startswith("file://") or _page_allowed(url, allow):
            stack.set_visible_child_name("web")
            web.load_uri(url)
        else:
            _toast("Blocked: not in study list")

    for item in cfg.get("links", []):
        b = Gtk.Button(label="\u2197  " + str(item.get("title", "Link")))
        b.get_style_context().add_class("sidebtn")
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_halign(Gtk.Align.FILL)
        b.set_tooltip_text(str(item.get("url", "")))
        b.connect("clicked", lambda _b, u=item.get("url", ""): _load_link(u))
        links_box.pack_start(b, False, False, 0)

    lib_sig = {"n": 0}
    lib_state = {
        "cwd": os.path.expanduser("~"),
        "home": os.path.expanduser("~"),
        "dirs": {},          # dirpath -> [(mtime, path)]
        "roots": [os.path.expanduser(r) for r in
                  (cfg.get("scan_roots") or _STUDY_DEFAULTS["scan_roots"])],
    }
    MAX_ROWS = 400

    def _crumb(path):
        home = lib_state["home"]
        return "~" if path == home else (
            "~" + path[len(home):] if path.startswith(home + "/") else path)

    def _subfolders(dirs, cwd):
        """Immediate sub-folders under cwd with total media counts."""
        prefix = cwd.rstrip("/") + "/"
        counts = {}
        for d, files in dirs.items():
            if d == cwd or not d.startswith(prefix):
                continue
            top = d[len(prefix):].split("/")[0]
            counts[top] = counts.get(top, 0) + len(files)
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))

    def _render_library():
        for ch in lib_box.get_children():
            lib_box.remove(ch)
        cwd = lib_state["cwd"]
        dirs = lib_state["dirs"]
        query = lib_search_entry.get_text().strip().lower()
        rows = 0

        parent = os.path.dirname(cwd.rstrip("/")) or "/"
        lib_parent_btn.set_sensitive(cwd.rstrip("/") != "/")

        for name, count in (() if query else _subfolders(dirs, cwd)):
            b = Gtk.Button(label=f"\U0001F4C1 {name}  ({count})")
            b.get_style_context().add_class("sidebtn")
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_halign(Gtk.Align.FILL)
            b.set_tooltip_text(os.path.join(cwd, name))
            b.connect("clicked", lambda _b, n=name: _goto(os.path.join(cwd, n)))
            lib_box.pack_start(b, False, False, 0)
            rows += 1
            if rows >= MAX_ROWS:
                break

        # other drive/volume roots reachable from home
        if not query and cwd.rstrip("/") == lib_state["home"].rstrip("/"):
            for r in lib_state["roots"]:
                r = os.path.abspath(r)
                if r.rstrip("/") == cwd.rstrip("/") or not os.path.isdir(r):
                    continue
                cnt = sum(len(v) for d, v in dirs.items()
                          if d == r or d.startswith(r.rstrip("/") + "/"))
                if not cnt:
                    continue
                b = Gtk.Button(label=f"\U0001F5B4 {r}  ({cnt})")
                b.get_style_context().add_class("sidebtn")
                b.set_relief(Gtk.ReliefStyle.NONE)
                b.set_halign(Gtk.Align.FILL)
                b.connect("clicked", lambda _b, rr=r: _goto(rr))
                lib_box.pack_start(b, False, False, 0)
                rows += 1

        if query:
            files = sorted(
                (_mt, path)
                for folder_files in dirs.values()
                for _mt, path in folder_files
                if query in os.path.basename(path).lower())
        else:
            files = dirs.get(cwd, [])
        for _mt, path in files[:max(0, MAX_ROWS - rows)]:
            icon = "\U0001F4C4" if path.lower().endswith(".pdf") else "\U0001F5BC"
            label = os.path.basename(path)
            if len(label) > 28:
                label = label[:25] + "\u2026"
            if query:
                label = f"{label}  ·  {_crumb(os.path.dirname(path))}"
            b = Gtk.Button(label=f"{icon} {label}")
            b.get_style_context().add_class("sidebtn")
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_halign(Gtk.Align.FILL)
            b.set_tooltip_text(path)
            b.connect("clicked", lambda _b, p=path: _open_media(p))
            lib_box.pack_start(b, False, False, 0)
            rows += 1
        if query and not files:
            empty = Gtk.Label(label="No matching PDF or image")
            empty.get_style_context().add_class("studytip")
            empty.set_halign(Gtk.Align.START)
            lib_box.pack_start(empty, False, False, 6)

        total = sum(len(v) for v in dirs.values())
        lib_head.set_text(f"LIBRARY \u2022 {_crumb(cwd)}")
        if query:
            lib_count.set_text(f"{len(files)} match(es) across {total} files")
        else:
            lib_count.set_text(f"{total} files \u2022 {len(_subfolders(dirs, cwd))} folder(s) here")
        try:
            lib_box.show_all()
        except Exception:
            pass

    lib_search_entry.connect("changed", lambda *_: _render_library())

    def _goto(path):
        path = os.path.abspath(path)
        known = set(lib_state["dirs"].keys())
        for d in known:
            if d.startswith(path.rstrip("/") + "/"):
                known.add(path)
                break
        if path in known or path == "/" or os.path.isdir(path):
            lib_state["cwd"] = path
            _render_library()

    lib_parent_btn.connect(
        "clicked", lambda *_: _goto(os.path.dirname(lib_state["cwd"].rstrip("/")) or "/"))

    def refresh_library():
        lib_sig["n"] += 1
        token = lib_sig["n"]

        def _work():
            dirs = {}
            for _m, p in _scan_media(lib_state["roots"]):
                dirs.setdefault(os.path.dirname(p), []).append((_m, p))
            GLib.idle_add(lambda: _apply(dirs, token))

        def _apply(dirs, tok):
            if tok != lib_sig["n"]:
                return False
            lib_state["dirs"] = dirs
            _render_library()
            return False

        threading.Thread(target=_work, daemon=True).start()

    refresh_library()

    def _import_usb_pdfs(*_):
        chooser = Gtk.FileChooserDialog(
            title="Import PDF from USB/Type-C",
            parent=win,
            action=Gtk.FileChooserAction.OPEN)
        chooser.add_button("Cancel", Gtk.ResponseType.CANCEL)
        chooser.add_button("Import PDFs", Gtk.ResponseType.ACCEPT)
        chooser.set_select_multiple(True)
        pdf_filter = Gtk.FileFilter()
        pdf_filter.set_name("PDF files only")
        pdf_filter.add_mime_type("application/pdf")
        pdf_filter.add_pattern("*.pdf")
        pdf_filter.add_pattern("*.PDF")
        chooser.add_filter(pdf_filter)
        response = chooser.run()
        uris = chooser.get_uris() if response == Gtk.ResponseType.ACCEPT else []
        chooser.destroy()
        if not uris:
            return
        copied = 0
        last_error = None
        for uri in uris:
            path, error = _import_usb_pdf_uri(uri)
            if path:
                copied += 1
            elif error:
                last_error = error
        if copied:
            lib_state["cwd"] = STUDY_INBOX
            refresh_library()
            _toast(f"Imported {copied} PDF(s) into Study Inbox")
        elif last_error:
            _toast(last_error)
        else:
            _toast("No PDFs were imported")

    usb_import_btn.connect("clicked", _import_usb_pdfs)

    back_btn = Gtk.Button(label="\u2190 Back to Lockout")
    back_btn.get_style_context().add_class("backbtn")
    back_btn.set_size_request(-1, 42)
    side.pack_start(back_btn, False, False, 4)

    right.pack_start(bar, False, False, 0)
    right.pack_start(stack_wrap, True, True, 0)
    outer.pack_start(side, False, False, 0)
    outer.pack_start(right, True, True, 0)

    root = Gtk.Overlay()
    root.add(outer)
    toast_lbl = Gtk.Label(label="")
    toast_lbl.get_style_context().add_class("toastlbl")
    toast_lbl.set_halign(Gtk.Align.CENTER)
    toast_lbl.set_valign(Gtk.Align.END)
    toast_lbl.set_margin_bottom(70)
    toast_lbl.set_no_show_all(True)
    root.add_overlay(toast_lbl)

    recent_panel = Gtk.EventBox()
    recent_panel.set_visible_window(False)
    recent_panel.set_halign(Gtk.Align.END)
    recent_panel.set_valign(Gtk.Align.START)
    recent_panel.set_margin_top(58)
    recent_panel.set_margin_end(14)
    recent_panel.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    recent_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    recent_card.get_style_context().add_class("recentpanel")
    recent_card.set_size_request(320, -1)
    recent_heading = Gtk.Label(label="RECENT SITES & DOCUMENTS")
    recent_heading.get_style_context().add_class("recentheading")
    recent_heading.set_halign(Gtk.Align.START)
    recent_headrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    recent_headrow.pack_start(recent_heading, True, True, 0)
    recent_close_btn = Gtk.Button(label="×")
    recent_close_btn.get_style_context().add_class("webnavbtn")
    recent_close_btn.set_relief(Gtk.ReliefStyle.NONE)
    recent_close_btn.set_tooltip_text("Close recent items")
    recent_headrow.pack_end(recent_close_btn, False, False, 0)
    recent_card.pack_start(recent_headrow, False, False, 0)
    recent_scroll = Gtk.ScrolledWindow()
    recent_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    recent_scroll.set_min_content_height(260)
    recent_scroll.set_max_content_height(520)
    recent_scroll.set_vexpand(True)
    recent_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    recent_scroll.add(recent_rows)
    recent_card.pack_start(recent_scroll, True, True, 0)
    recent_panel.add(recent_card)
    recent_panel.set_no_show_all(True)
    root.add_overlay(recent_panel)

    def _render_recent():
        if recent_rows is None:
            return
        for child in recent_rows.get_children():
            recent_rows.remove(child)
        if not recent_items:
            empty = Gtk.Label(label="Websites and documents you open will appear here.")
            empty.set_line_wrap(True)
            empty.set_halign(Gtk.Align.START)
            empty.set_margin_top(12)
            empty.set_margin_bottom(12)
            recent_rows.pack_start(empty, False, False, 0)
        for item in recent_items[:30]:
            kind = item.get("kind")
            if kind == "doc":
                path = item.get("path", "")
                page = max(1, int(item.get("page", reading_pages.get(path, 1))))
                icon = "PDF" if path.lower().endswith(".pdf") else "FILE"
                subtitle = f"{icon}  Page {page}  ·  {os.path.dirname(path)}"
            elif kind == "web":
                path = ""
                url = item.get("url", "")
                subtitle = urlparse(url).hostname or url
            else:
                continue
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_halign(Gtk.Align.FILL)
            button.get_style_context().add_class("recentrow")
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            title_lbl = Gtk.Label(label=("📄  " if kind == "doc" else "↗  ")
                                  + str(item.get("title") or os.path.basename(path)))
            title_lbl.set_halign(Gtk.Align.START)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            subtitle_lbl = Gtk.Label(label=subtitle)
            subtitle_lbl.set_halign(Gtk.Align.START)
            subtitle_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(title_lbl, False, False, 0)
            row.pack_start(subtitle_lbl, False, False, 0)
            button.add(row)
            button.set_tooltip_text(item.get("path") or item.get("url", ""))
            button.connect("clicked", lambda _b, recent=dict(item): _open_recent(recent))
            recent_rows.pack_start(button, False, False, 0)
        recent_rows.show_all()

    def _open_recent(item):
        if item.get("kind") == "doc":
            path = item.get("path", "")
            if os.path.isfile(path):
                _open_media(path)
            else:
                _toast("That document is no longer available at its saved location")
        elif item.get("kind") == "web":
            url = item.get("url", "")
            if url and _page_allowed(url, allow):
                stack.set_visible_child_name("web")
                web.load_uri(url)
            else:
                _toast("This recent website is no longer allowed")
        _hide_recent_panel()

    def _hide_recent_panel(*_):
        if recent_hide_state["id"]:
            GLib.source_remove(recent_hide_state["id"])
            recent_hide_state["id"] = 0
        recent_panel.hide()
        recent_panel.set_no_show_all(True)

    def _show_recent_panel(*_):
        if recent_hide_state["id"]:
            GLib.source_remove(recent_hide_state["id"])
            recent_hide_state["id"] = 0
        if not recent_panel.get_visible():
            _render_recent()
        recent_panel.set_no_show_all(False)
        recent_panel.show_all()
        recent_panel.set_no_show_all(True)
        return False

    recent_btn.connect("enter-notify-event", _show_recent_panel)
    recent_panel.connect("enter-notify-event", _show_recent_panel)
    recent_close_btn.connect("clicked", _hide_recent_panel)
    recent_btn.connect("clicked", lambda *_: _hide_recent_panel()
                       if recent_panel.get_visible() else _show_recent_panel())

    load_screen = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    load_screen.get_style_context().add_class("loadscreen")
    load_screen.set_no_show_all(True)
    load_screen.set_halign(Gtk.Align.START)
    load_screen.set_valign(Gtk.Align.START)
    load_screen.set_margin_top(12)
    load_screen.set_margin_start(12)
    load_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    load_inner.set_halign(Gtk.Align.END)
    load_inner.set_valign(Gtk.Align.START)
    load_head = Gtk.Label(label="Opening page…")
    load_head.get_style_context().add_class("loadhead")
    load_inner.pack_start(load_head, False, False, 0)
    load_screen.pack_start(load_inner, True, True, 0)
    stack_wrap.add_overlay(load_screen)

    load_timer = {"safety_id": 0}

    def _do_hide():
        load_timer["safety_id"] = 0
        try:
            load_screen.hide()
            load_screen.set_no_show_all(True)
        except Exception:
            pass
        return False

    def _show_loading():
        try:
            if load_screen.get_visible():
                return
            load_screen.set_no_show_all(False)
            load_screen.show_all()
            load_timer["safety_id"] = GLib.timeout_add_seconds(20, _do_hide)
        except Exception:
            pass

    def _hide_loading():
        """Hide the non-blocking status immediately when WebKit finishes."""
        try:
            if load_timer["safety_id"]:
                GLib.source_remove(load_timer["safety_id"])
                load_timer["safety_id"] = 0
            _do_hide()
        except Exception:
            pass

    if WebKit2 is not None:
        def _on_load_changed(_v, ev):
            try:
                if ev == WebKit2.LoadEvent.STARTED:
                    _show_loading()
                elif ev == WebKit2.LoadEvent.FINISHED:
                    _hide_loading()
                    _record_recent_website(web.get_uri(), web.get_title())
            except Exception:
                pass

        def _on_load_failed(*_a):
            _hide_loading()

        web.connect("load-changed", _on_load_changed)
        web.connect("load-failed", _on_load_failed)

    win.add(root)

    # ---- intake: drag & drop / clipboard ----
    try:
        win.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
                          Gdk.DragAction.COPY)
    except Exception:
        pass

    def _on_drop(_w, _ctx, _x, _y, data, _info, _time):
        try:
            uris = data.get_uris()
            paths = [urllib.parse.unquote(u[7:]) for u in uris if u.startswith("file://")]
            n = _copy_into_inbox(paths, MEDIA_EXTS)
            _toast(f"Saved {n} file(s) to study inbox" if n else
                   "Only PDF/image files are accepted")
            if n:
                refresh_library()
        except Exception as e:
            print(f"drop error: {e}", flush=True)
        return True

    win.connect("drag-data-received", _on_drop)

    def _paste_image():
        try:
            cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            pb = cb.wait_for_image()
            if pb is None:
                _toast("Clipboard has no image")
                return
            os.makedirs(STUDY_INBOX, exist_ok=True)
            dst = os.path.join(STUDY_INBOX,
                               time.strftime("clipboard-%Y%m%d-%H%M%S.png"))
            pb.savev(dst, "png", [], [])
            _toast(f"Saved {os.path.basename(dst)}")
            refresh_library()
        except Exception as e:
            print(f"paste error: {e}", flush=True)

    def _handle_key(kv, state_mask):
        try:
            st = state_mask
            mods = st & Gtk.accelerator_get_default_mod_mask()
            ctrl = bool(mods & Gdk.ModifierType.CONTROL_MASK)
            if kv == Gdk.KEY_Escape:
                win.destroy()
                return True
            if ctrl and kv in (Gdk.KEY_l, Gdk.KEY_t, Gdk.KEY_n, Gdk.KEY_w,
                               Gdk.KEY_p, Gdk.KEY_j, Gdk.KEY_u):
                return True
            if kv in (Gdk.KEY_F6, Gdk.KEY_F11, Gdk.KEY_F12):
                return True
            if ctrl and kv == Gdk.KEY_v and stack.get_visible_child_name() != "web":
                _paste_image()
                return True
            if stack.get_visible_child_name() == "reader":
                if pdf_layout["active"] and kv in (
                        Gdk.KEY_Up, Gdk.KEY_Left, Gdk.KEY_KP_Up, Gdk.KEY_KP_Left):
                    _change_page(-1)
                    return True
                if pdf_layout["active"] and kv in (
                        Gdk.KEY_Down, Gdk.KEY_Right, Gdk.KEY_KP_Down, Gdk.KEY_KP_Right):
                    _change_page(1)
                    return True
                if kv in (Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down):
                    next_btn.emit("clicked")
                    return True
                if kv in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
                    prev_btn.emit("clicked")
                    return True
                if ctrl and kv in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                    zoom_in.emit("clicked")
                    return True
                if ctrl and kv in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                    zoom_out.emit("clicked")
                    return True
                if ctrl and kv == Gdk.KEY_0:
                    _actual_size()
                    return True
                if ctrl and kv == Gdk.KEY_s:
                    _save_annotations()
                    return True
                if ctrl and kv == Gdk.KEY_z:
                    if st & Gdk.ModifierType.SHIFT_MASK:
                        _redo()
                    else:
                        _undo()
                    return True
                if ctrl and kv == Gdk.KEY_y:
                    _redo()
                    return True
                if kv == Gdk.KEY_z:
                    _undo()
                    return True
                if kv == Gdk.KEY_y:
                    _redo()
                    return True
                if kv == Gdk.KEY_1:
                    _set_tool("pen")
                    return True
                if kv == Gdk.KEY_2:
                    _set_tool("high")
                    return True
                if kv == Gdk.KEY_3:
                    _set_tool("erase")
                    return True
        except Exception:
            pass
        return False

    def _on_key(_w, ev):
        return _handle_key(ev.keyval, ev.state)

    win.connect("key-press-event", _on_key)
    for _kw in [canvas, img_scroll, floatbar, *floatbar.get_children()]:
        _kw.connect("key-press-event", _on_key)

    back_btn.connect("clicked", lambda *_: win.destroy())

    def _tick():
        if end_ts:
            left = max(0, int(end_ts - time.time()))
            d, rem = divmod(left, 86400)
            h, rem = divmod(rem, 3600)
            m, s = divmod(rem, 60)
            time_lbl.set_text(f"\u23f3 {d}d {h:02d}:{m:02d}:{s:02d} left" if d
                              else f"\u23f3 {h:02d}:{m:02d}:{s:02d} left")
            if left <= 0:
                win.destroy()
                return False
        return True
    GLib.timeout_add_seconds(1, _tick)

    # start on the bismillah home page (no auto-load of a link)
    stack.set_visible_child_name("home")

    def _on_destroy(*_a):
        global _study_win, _study_parent
        global _study_auto_closing
        if download_context is not None and download_handler_id:
            try:
                download_context.disconnect(download_handler_id)
            except Exception:
                pass
        for source_id in (guard_id, battery_timer,
                          recent_hide_state.get("id", 0)):
            try:
                if source_id:
                    GLib.source_remove(source_id)
            except Exception:
                pass
        auto = _study_auto_closing
        if state.get("path") and str(state["path"]).lower().endswith(".pdf"):
            _save_reading_page(state["path"], state["page"])
        _study_win = None
        if not auto and _study_parent is not None:
            try:
                _study_parent.show()
                _study_parent.present()
            except Exception:
                pass
        _study_parent = None
        _study_auto_closing = False
        Gtk.main_quit()

    win.connect("destroy", _on_destroy)
    win.show_all()
    win.present()
    win.grab_focus()
    Gtk.main()


def show_study_or_typing(parent, end_ts):
    show_study_window(parent=parent, end_ts=end_ts)


# ---------- main loop ----------

def main():
    # Single instance: never run twice (no duplicate alerts/ledgers).
    os.makedirs(LEDGER_DIR, exist_ok=True)
    _lock_fh = open(os.path.join(LEDGER_DIR, "instance.lock"), "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another instance is already running — exiting.", flush=True)
        raise SystemExit(0)
    start_phone_upload_server()
    print(f"Focus Reminder chalu. Proti {FOCUS_MINUTES} min por alert (study cholle skip).", flush=True)
    tr = start_tracker()

    def _bye(*_):
        try:
            tr.stop_event.set()
            tr.flush()
        except Exception:
            pass
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    global suspend_until
    suspend_until = 0
    pending = _restore_suspend_checkpoint()
    if pending > time.time():
        print("Unfinished suspension found — enforcing until countdown ends.", flush=True)
        enforce_suspension_until(pending)
    else:
        _clear_suspend_state()
        _gnome_heal()  # no lockout active: undo any stale shell lockdown
    next_alert = _load_next_alert()
    if next_alert <= 0:
        next_alert = time.time() + FOCUS_MINUTES * 60
        _save_next_alert(next_alert)
    elif next_alert <= time.time():
        print("Reminder overdue from before power-off — showing immediately.", flush=True)
    while True:
        while time.time() < suspend_until:
            time.sleep(20)
        while time.time() < next_alert and not tr.limit_pending:
            time.sleep(20)
        # Persist the next cycle BEFORE showing, so a reboot cannot buy free time.
        next_alert = time.time() + FOCUS_MINUTES * 60
        _save_next_alert(next_alert)
        if tr.limit_pending:
            tr.limit_pending = False
            notify(f"Daily waste limit ({DAILY_WASTE_LIMIT_MIN}m) exceeded!")
            print("Daily waste limit exceeded — alert shown now.", flush=True)
        if time.time() < suspend_until:
            continue
        if _load_suspend_state() > time.time():
            print("External suspension active — alert deferred.", flush=True)
            time.sleep(20)
            continue
        if study_app_running() or study_site_recent():
            print("Study/Deen (app or site) in use — alert skipped.", flush=True)
            continue
        if must_alert():
            print("OpenCode/browser running — alert shown (must).", flush=True)
        try:
            action = show_alert()
        except Exception as e:
            print(f"alert display error (no screen?) — skipped: {e}", flush=True)
            continue
        print(f"action={action}", flush=True)
        if action == "suspend":
            do_suspend(_take_chosen_minutes())
        elif action == "lockout":
            do_pseudo_lockout(SUSPEND_MINUTES)
        elif action == "poweroff":
            power_off()
            return
        elif action == "goal":
            try:
                show_goal_manager()
            except Exception as e:
                print(f"goal manager error: {e}", flush=True)

def show_now_once():
    """One-shot reminder (app-menu entry). Full actions, no tracker, no locks."""
    while True:
        try:
            action = show_alert()
        except Exception as e:
            print(f"show-now error: {e}", flush=True)
            return
        print(f"show-now action={action}", flush=True)
        if action == "suspend":
            do_suspend(_take_chosen_minutes())
        elif action == "lockout":
            do_pseudo_lockout(SUSPEND_MINUTES)
        elif action == "poweroff":
            power_off()
            return
        elif action == "goal":
            try:
                show_goal_manager()
            except Exception as e:
                print(f"goal manager error: {e}", flush=True)
        else:
            return


if __name__ == "__main__":
    import sys
    if "--show-upload-password" in sys.argv:
        print(_ensure_phone_upload_password(), flush=True)
    elif "--setup-telegram" in sys.argv:
        _minutes = SETUP_MODE_MINUTES
        _i = sys.argv.index("--setup-telegram")
        if len(sys.argv) > _i + 1:
            try:
                _minutes = max(1, min(120, int(sys.argv[_i + 1])))
            except ValueError:
                pass
        enable_setup_mode(_minutes)
    elif "--setup-telegram-off" in sys.argv:
        disable_setup_mode()
    elif "--lockout" in sys.argv:
        # One-shot pseudo lockout: locks now, stays on, no sleep.
        _mins = SUSPEND_MINUTES
        _idx = sys.argv.index("--lockout")
        if len(sys.argv) > _idx + 1:
            try:
                _mins = max(1, min(24 * 60, int(sys.argv[_idx + 1])))
            except ValueError:
                pass
        while not _has_display():
            print("Lockout waiting for display…", flush=True)
            time.sleep(10)
        do_pseudo_lockout(_mins)
    elif "--show-now" in sys.argv:
        while not _has_display():
            print("Show-now waiting for display…", flush=True)
            time.sleep(10)
        show_now_once()
    elif "--tray" in sys.argv or "--emergency-button" in sys.argv:
        # single instance for the tray process too (separate lock from main)
        try:
            os.makedirs(LEDGER_DIR, exist_ok=True)
            _tray_fh = open(os.path.join(LEDGER_DIR, "tray.lock"), "w")
            fcntl.flock(_tray_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Tray already running — exiting.", flush=True)
            raise SystemExit(0)
        tray_loop()
    else:
        main()
