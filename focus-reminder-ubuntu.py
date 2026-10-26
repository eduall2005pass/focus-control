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
import fcntl
import glob
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import urllib.parse
from urllib.parse import urlparse
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

try:
    gi.require_version("WebKit2", "4.1")
    from gi.repository import WebKit2
except Exception:  # WebKit not available -> study view disabled, app still runs
    WebKit2 = None

FOCUS_VERSION = "1.0"

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


def _save_suspend_state(ts):
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        with open(SUSPEND_STATE, "w") as f:
            f.write(str(float(ts)))
    except Exception as e:
        print(f"suspend state save error: {e}", flush=True)


def _load_suspend_state():
    try:
        with open(SUSPEND_STATE) as f:
            return float(f.read().strip())
    except Exception:
        return 0


def _clear_suspend_state():
    try:
        if os.path.exists(SUSPEND_STATE):
            os.remove(SUSPEND_STATE)
    except Exception:
        pass


def enforce_suspension_until(end_ts):
    """Non-skippable: re-enforce after reboot/poweroff until countdown ends."""
    while time.time() < end_ts:
        if not _has_display():
            time.sleep(10)
            continue
        try:
            lock_session()
            show_lockout_window((end_ts - time.time()) / 60)
            break  # returns only when timer ends (Power Off reboots -> state persists)
        except Exception as e:
            print(f"lockout enforce retry: {e}", flush=True)
            time.sleep(15)
    _clear_suspend_state()


def do_suspend(minutes=SUSPEND_MINUTES):
    """Sleep now; on wake enforce the remaining suspension with a countdown lockout."""
    global suspend_until
    suspend_until = time.time() + minutes * 60
    _save_suspend_state(suspend_until)
    notify(f"Device suspending for {_fmt_short(minutes * 60)}.")
    try:
        r = _run(["systemctl", "suspend"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"systemctl suspend failed: {(r.stderr or r.stdout).strip()} "
                  f"— lockout only", flush=True)
    except Exception as e:
        print(f"suspend error: {e} — lockout only", flush=True)
    # on wake (or if sleep failed): sleep time already counted via wall clock
    remaining = suspend_until - time.time()
    if remaining > 0:
        print(f"Post-wake lockout: {remaining / 60:.1f} min left.", flush=True)
        lock_session()
        show_lockout_window(remaining / 60)
        _clear_suspend_state()
    else:
        print("Suspension already served by sleep.", flush=True)
        _clear_suspend_state()


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
    ("org.gnome.desktop.wm.keybindings", "switch-windows", "@as []"),        # Alt+Tab (windows)
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
    """Re-raise window if anything steals focus. Returns timeout id for cleanup."""
    def _on_focus_out(*_):
        try:
            if win.get_visible() and (not is_lockout or _typing_win is None):
                GLib.idle_add(win.present)
        except Exception:
            pass
        return False
    win.connect("focus-out-event", _on_focus_out)

    def _guard():
        try:
            if win.get_visible() and (not is_lockout or _typing_win is None):
                win.present()
        except Exception:
            pass
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
    """Suspend confirmation WITH system-password check (days call this twice)."""
    return confirm_dialog(parent, "Confirm Suspend", f"Suspend device {label}?",
                          "The device will sleep now. It stays locked until the countdown ends,\neven across reboot.",
                          "\u23f8  Suspend", danger=False, passcode="verify")

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
    dlg = Gtk.Dialog(title="Emergency Suspend", transient_for=parent, modal=True)
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
    """Same flow as tray Emergency Suspend. Returns minutes or None. Pure (no execution)."""
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
    root = Gtk.MenuItem(label="\u23f8  Emergency Suspend")
    root.set_submenu(sub)
    menu.append(root)
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
    suspend_btn = Gtk.Button(label="\u23f8  Emergency")
    suspend_btn.get_style_context().add_class("btn-secondary")
    suspend_btn.set_size_request(135, 48)
    suspend_btn.set_tooltip_text("Suspend now — choose minutes, hours or days. System password required.")
    suspend_btn.connect("clicked", choose("emergency"))
    power_btn = Gtk.Button(label="\u23fb  Power Off")
    power_btn.get_style_context().add_class("btn-danger")
    power_btn.set_size_request(125, 48)
    power_btn.set_tooltip_text("Shut down this machine. Requires confirmation.")
    power_btn.connect("clicked", choose("poweroff"))
    for b in (close_btn, goal_btn, suspend_btn, power_btn):
        row_all.pack_start(b, False, False, 0)

    hint = Gtk.Label(label="Emergency Suspend sleeps this device (you choose length). Power Off asks for confirmation first.")
    hint.get_style_context().add_class("caption")
    hint.set_halign(Gtk.Align.CENTER)
    hint.set_line_wrap(True)

    buttons.extend([close_btn, goal_btn, suspend_btn, power_btn])

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


def show_lockout_window(minutes):
    """Unclosable fullscreen countdown — device unusable until timer ends."""
    end = time.time() + minutes * 60

    win = _new_fullscreen("Device Suspended")
    _apply_css(win)
    win.connect("delete-event", lambda *_: True)  # block Alt+F4 / close
    _compact = is_compact_screen()
    outer, card = _new_card(620 if _compact else 900)

    badge = _eyebrow("DEVICE SUSPENDED")

    time_label = Gtk.Label(label="")
    time_label.get_style_context().add_class("lock-time")
    time_label.set_halign(Gtk.Align.CENTER)

    sub = Gtk.Label(label="This device is locked. Please take a break.\n"
                           "It will unlock automatically when the timer ends.")
    sub.get_style_context().add_class("body")
    sub.set_halign(Gtk.Align.CENTER)
    sub.set_justify(Gtk.Justification.CENTER)

    total = max(1, minutes * 60)
    prog = Gtk.ProgressBar()
    prog.get_style_context().add_class("lockbar")
    prog.set_show_text(True)
    prog.set_halign(Gtk.Align.FILL)
    prog.set_hexpand(True)
    unlock_lbl = Gtk.Label(label=f"Unlocks at {time.strftime('%H:%M', time.localtime(end))}")
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

    def _off(*_):
        power_btn.set_sensitive(False)
        if confirm_power_off(win):
            power_off()
        else:
            power_btn.set_sensitive(True)
    power_btn.connect("clicked", _off)

    def fmt_left():
        left = max(0, int(end - time.time()))
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

    def tick():
        time_label.set_text(fmt_left())
        left = max(0, end - time.time())
        done_frac = min(1.0, 1.0 - left / total)
        prog.set_fraction(done_frac)
        prog.set_text(f"{int(done_frac * 100)}% served")
        if time.time() >= end:
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
        for w in (_accent(), badge, time_label, prog, unlock_lbl, sub, goals_lbl, goals_scroll, lockrow):
            card.pack_start(w, False, False, 0)
    else:
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2 * SPACE)
        left.set_hexpand(True)
        left.set_halign(Gtk.Align.FILL)
        for w in (badge, time_label, prog, unlock_lbl, sub, lockrow):
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
STUDY_CONFIG_PATH = os.path.join(LEDGER_DIR, "study-config.json")
MEDIA_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_SCAN_SKIP = {
    "node_modules", ".git", ".cache", "__pycache__", "snap", "venv", ".venv",
    "site-packages", "proc", "sys", "dev", "Trash", ".Trash-1000", "flatpak",
    "Steam", "steamapps", ".npm", ".cargo", ".rustup", ".local/share/Trash",
}

_STUDY_DEFAULTS = {
    "version": 2,
    "links": [
        {"title": "Boimate", "url": "https://boimate.com"},
        {"title": "NotebookLM", "url": "https://notebooklm.google.com"},
        {"title": "MuslimBangla", "url": "https://muslimbangla.com"},
        {"title": "iHadis", "url": "https://ihadis.com/en"},
        {"title": "LiveMCQ", "url": "https://web.livemcq.com/"},
        {"title": "Translate", "url": "https://translate.google.com"},
        {"title": "WhatsApp (read-only)", "url": "https://web.whatsapp.com"},
    ],
    "allow_hosts": [
        "boimate.com", "www.boimate.com",
        "notebooklm.google.com",
        "accounts.google.com", "accounts.youtube.com", "myaccount.google.com",
        "translate.google.com", "translate.googleapis.com", "clients6.google.com",
        "www.google.com", "google.com", "apis.google.com", "content.googleapis.com",
        "www.gstatic.com", "ssl.gstatic.com", "fonts.gstatic.com", "gstatic.com",
        "fonts.googleapis.com", "font.gstatic.com", "googleusercontent.com",
        "*.googleusercontent.com", "*.gstatic.com", "*.googleapis.com",
        "muslimbangla.com", "www.muslimbangla.com",
        "ihadis.com", "www.ihadis.com",
        "livemcq.com", "www.livemcq.com", "web.livemcq.com",
        "web.whatsapp.com", "*.whatsapp.com", "*.whatsapp.net",
        "static.whatsapp.net", "mmg.whatsapp.net",
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
                and int(cfg.get("version", 1)) >= 2):
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


def _pdf_pages(path):
    try:
        r = _run(["pdfinfo", path], capture_output=True, text=True, timeout=20)
        for line in (r.stdout or "").splitlines():
            if line.lower().startswith("pages:"):
                return max(1, int(line.split(":")[1].strip()))
    except Exception:
        pass
    return 1


def _render_pdf_page(path, page, zoom=1.0):
    """Render one PDF page to a PNG (poppler) and return a pixbuf."""
    try:
        out = os.path.join(tempfile.gettempdir(),
                           f"focus-pdf-{os.getpid()}-{page}-{int(zoom * 100)}")
        r = _run(["pdftoppm", "-png", "-r", str(int(110 * zoom)), "-f", str(page),
                  "-l", str(page), "-singlefile", path, out],
                 capture_output=True, text=True, timeout=45)
        png = out + ".png"
        if r.returncode == 0 and os.path.exists(png):
            return GdkPixbuf.Pixbuf.new_from_file(png)
    except Exception as e:
        print(f"pdf render error: {e}", flush=True)
    return None


def show_study_window(parent=None, end_ts=None):
    """Study container inside lockout: whitelisted links, PDF/image library,
    read-only WhatsApp, media intake. Suspension keeps running."""
    global _study_win, _study_parent

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
    .studyhead { font-family: Inter, sans-serif; font-size: 13px; color: #dbeafe;
                 letter-spacing: 2px; font-weight: bold; }
    .studytime { font-family: Inter, sans-serif; font-size: 13px; color: #9ca3af; }
    .sidehead { font-family: Inter, sans-serif; font-size: 11px; color: #9ca3af;
                letter-spacing: 1.6px; font-weight: bold; padding: 8px 4px 4px 4px; }
    .sidebtn { background: transparent; border: none; box-shadow: none;
               padding: 7px 8px; border-radius: 8px; }
    .sidebtn:hover { background: rgba(37, 99, 235, 0.18); }
    .sidebtn label { font-family: Inter, sans-serif; font-size: 13px; color: #e5e7eb; }
    .sidebtn:active { background: rgba(37, 99, 235, 0.30); }
    .panel { background: #f8fafc; }
    .studytip { font-family: Inter, sans-serif; font-size: 12px; color: #9ca3af; }
    """)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win.get_style_context().add_class("studywin")
    win.connect("delete-event", lambda *_: True)  # Alt+F4 blocked

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
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

    body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    side.set_size_request(250, -1)
    side.set_margin_top(6); side.set_margin_bottom(6)
    side.set_margin_start(10); side.set_margin_end(10)

    links_head = Gtk.Label(label="LINKS")
    links_head.get_style_context().add_class("sidehead")
    links_head.set_halign(Gtk.Align.START)
    links_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)

    lib_head = Gtk.Label(label="LIBRARY")
    lib_head.get_style_context().add_class("sidehead")
    lib_head.set_halign(Gtk.Align.START)
    lib_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    lib_scroll = Gtk.ScrolledWindow()
    lib_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    lib_scroll.set_vexpand(True)
    lib_scroll.add(lib_box)

    side.pack_start(links_head, False, False, 0)
    side.pack_start(links_box, False, False, 0)
    side.pack_start(lib_head, False, False, 0)
    side.pack_start(lib_scroll, True, True, 0)

    stack = Gtk.Stack()
    stack.set_hexpand(True)
    stack.set_vexpand(True)

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
    web.set_hexpand(True)
    web.set_vexpand(True)
    web_scroll = Gtk.ScrolledWindow()
    web_scroll.add(web)

    def _toast(msg):
        try:
            tip_lbl.set_text(msg)
        except Exception:
            pass

    def _on_decide_policy(_view, decision, dtype):
        try:
            if dtype == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
                uri = decision.get_navigation_action().get_request().get_uri()
                if uri and uri.startswith(("file://", "blob:", "data:", "about:")):
                    return False  # in-page PDF/blob from an already-allowed page
                host = ""
                try:
                    host = (decision.get_navigation_action().get_request().get_uri()
                            .split("://", 1)[1].split("/", 1)[0])
                except Exception:
                    host = ""
                if _host_allowed(host, allow):
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
                decision.ignore()
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
            ext = os.path.splitext(name)[1].lower()
            if ext not in dl_exts:
                download.cancel()
                _toast(f"Download blocked: {name} (only PDF/image allowed)")
                return
            os.makedirs(STUDY_INBOX, exist_ok=True)
            dst = os.path.join(STUDY_INBOX, name)
            if os.path.exists(dst):
                stem, e = os.path.splitext(dst)
                dst = f"{stem}-{int(time.time())}{e}"
            download.set_destination("file://" + dst)

            def _done(*_a):
                _toast(f"Saved {os.path.basename(dst)}")
                refresh_library()
                if dst.lower().endswith(".pdf"):
                    _open_media(dst)  # boimate-style PDF: readable right away
            download.connect("finished", _done)
        except Exception as e:
            print(f"download error: {e}", flush=True)

    web.connect("decide-policy", _on_decide_policy)
    web.connect("create", _on_create)
    web.connect("context-menu", _on_context_menu)
    for _target in (web,):
        try:
            _target.connect("download-started", lambda _v, d: _on_download(d))
        except Exception:
            pass
    try:
        web.get_context().connect("download-started", lambda _c, d: _on_download(d))
    except Exception:
        pass

    # read-only WhatsApp: hide composer/attachment UI, keep media accessible
    try:
        ro_css = """
        footer, [data-testid="conversation-compose-box-input"],
        [data-testid="compose-box"], [data-testid="chat-attachment-menu"],
        [data-testid="attach-menu"], [data-testid="emoji-panel"] { display: none !important; }
        """
        sheet = WebKit2.UserStyleSheet(ro_css, WebKit2.UserContentInjectedFrames.TOP_FRAME,
                                       WebKit2.UserStyleLevel.USER, None, None)
        web.get_user_content_manager().add_style_sheet(sheet)
    except Exception as e:
        print(f"whatsapp readonly css skipped: {e}", flush=True)

    # ---- PDF / image reader ----
    reader = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    reader.set_margin_top(8); reader.set_margin_bottom(8)
    reader.set_margin_start(10); reader.set_margin_end(10)
    img = Gtk.Image()
    img_scroll = Gtk.ScrolledWindow()
    img_scroll.add(img)
    img_scroll.set_vexpand(True)
    nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    nav.set_halign(Gtk.Align.CENTER)
    prev_btn = Gtk.Button(label="\u2190 Prev")
    next_btn = Gtk.Button(label="Next \u2192")
    for b in (prev_btn, next_btn):
        b.get_style_context().add_class("btn-ghost")
        b.set_size_request(110, 40)
    page_lbl = Gtk.Label(label="")
    page_lbl.get_style_context().add_class("caption")
    zoom_out = Gtk.Button(label="\u2212")
    zoom_in = Gtk.Button(label="+")
    for b in (zoom_out, zoom_in):
        b.get_style_context().add_class("btn-ghost")
        b.set_size_request(52, 40)
    nav.pack_start(prev_btn, False, False, 0)
    nav.pack_start(page_lbl, False, False, 0)
    nav.pack_start(next_btn, False, False, 0)
    nav.pack_start(zoom_out, False, False, 0)
    nav.pack_start(zoom_in, False, False, 0)
    reader.pack_start(img_scroll, True, True, 0)
    reader.pack_start(nav, False, False, 0)
    stack.add_named(web_scroll, "web")
    stack.add_named(reader, "reader")

    state = {"path": None, "page": 1, "pages": 1, "zoom": 1.0}

    def _show_page():
        p = state["path"]
        if not p:
            return
        pb = _render_pdf_page(p, state["page"], state["zoom"])
        if pb is not None:
            img.set_from_pixbuf(pb)
            page_lbl.set_text(f"{state['page']} / {state['pages']}")
        else:
            _toast("Could not render this PDF page")

    def _open_media(path):
        try:
            h = min(Gdk.Screen.get_default().get_height() - 220, 900)
        except Exception:
            h = 700
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            state.update(path=path, page=1, pages=_pdf_pages(path))
            stack.set_visible_child_name("reader")
            _show_page()
        else:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, -1, h, True)
                img.set_from_pixbuf(pb)
                page_lbl.set_text("image")
                stack.set_visible_child_name("reader")
            except Exception as e:
                _toast(f"Cannot open image: {e}")

    prev_btn.connect("clicked", lambda *_: (
        state.update(page=max(1, state["page"] - 1)), _show_page()))
    next_btn.connect("clicked", lambda *_: (
        state.update(page=min(state["pages"], state["page"] + 1)), _show_page()))
    zoom_in.connect("clicked", lambda *_: (
        state.update(zoom=min(3.0, state["zoom"] + 0.25)), _show_page()))
    zoom_out.connect("clicked", lambda *_: (
        state.update(zoom=max(0.5, state["zoom"] - 0.25)), _show_page()))

    def _load_link(url):
        if url.startswith("file://") or _host_allowed(
                url.split("://", 1)[1].split("/", 1)[0], allow):
            stack.set_visible_child_name("web")
            web.load_uri(url)
        else:
            _toast("Blocked: not in study list")

    for item in cfg.get("links", []):
        b = Gtk.Button(label="\u2022  " + str(item.get("title", "Link")))
        b.get_style_context().add_class("sidebtn")
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.set_halign(Gtk.Align.FILL)
        b.set_tooltip_text(str(item.get("url", "")))
        b.connect("clicked", lambda _b, u=item.get("url", ""): _load_link(u))
        links_box.pack_start(b, False, False, 0)

    lib_sig = {"n": 0}

    def _fill_library(items):
        for ch in lib_box.get_children():
            lib_box.remove(ch)
        shown = 0
        for _mt, path in items:
            icon = "\U0001F4C4" if path.lower().endswith(".pdf") else "\U0001F5BC"
            label = os.path.basename(path)
            if len(label) > 30:
                label = label[:27] + "\u2026"
            b = Gtk.Button(label=f"{icon} {label}")
            b.get_style_context().add_class("sidebtn")
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_halign(Gtk.Align.FILL)
            b.set_tooltip_text(path)
            b.connect("clicked", lambda _b, p=path: _open_media(p))
            lib_box.pack_start(b, False, False, 0)
            shown += 1
        lib_head.set_text(f"LIBRARY ({shown})")
        try:
            lib_box.show_all()  # children added after window show need explicit show
        except Exception:
            pass

    scan_state = {"items": [], "done": False}

    def refresh_library():
        lib_sig["n"] += 1
        token = lib_sig["n"]

        def _work():
            items = _scan_media(cfg.get("scan_roots") or _STUDY_DEFAULTS["scan_roots"])
            GLib.idle_add(lambda: _apply(items, token))

        def _apply(items, tok):
            if tok != lib_sig["n"]:
                return False
            scan_state["items"] = items
            scan_state["done"] = True
            _fill_library(items)
            return False

        threading.Thread(target=_work, daemon=True).start()

    refresh_library()

    tip_lbl = Gtk.Label(label="Esc = back to lockout  \u2022  PDF/image downloads only")
    tip_lbl.get_style_context().add_class("studytip")

    footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    footer.set_margin_top(6); footer.set_margin_bottom(8)
    footer.set_margin_start(14); footer.set_margin_end(14)
    back_btn = Gtk.Button(label="\u2190 Back to Lockout")
    back_btn.get_style_context().add_class("btn-ghost")
    back_btn.set_size_request(190, 44)
    type_btn = Gtk.Button(label="Typing Test")
    type_btn.get_style_context().add_class("btn-ghost")
    type_btn.set_size_request(150, 44)
    intake_lbl = Gtk.Label(label="Drop PDF/image here \u2022 Ctrl+V to save clipboard image")
    intake_lbl.get_style_context().add_class("studytip")
    intake_lbl.set_hexpand(True)
    intake_lbl.set_halign(Gtk.Align.END)
    footer.pack_start(back_btn, False, False, 0)
    footer.pack_start(type_btn, False, False, 0)
    footer.pack_start(intake_lbl, True, True, 0)

    body.pack_start(side, False, False, 0)
    body.pack_start(stack, True, True, 0)
    outer.pack_start(bar, False, False, 0)
    outer.pack_start(tip_lbl, False, False, 0)
    outer.pack_start(body, True, True, 0)
    outer.pack_start(footer, False, False, 0)
    win.add(outer)

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

    def _on_key(_w, ev):
        try:
            kv = ev.keyval
            mods = ev.state & Gtk.accelerator_get_default_mod_mask()
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
        except Exception:
            pass
        return False

    win.connect("key-press-event", _on_key)

    back_btn.connect("clicked", lambda *_: win.destroy())
    type_btn.connect("clicked", lambda *_: show_typing_test(parent=win))

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

    # first link auto-loads (study page ready immediately)
    links = cfg.get("links") or []
    if links:
        _load_link(links[0].get("url", ""))

    def _on_destroy(*_a):
        global _study_win, _study_parent
        try:
            GLib.source_remove(guard_id)
        except Exception:
            pass
        auto = _study_auto_closing
        _study_win = None
        if not auto and _study_parent is not None:
            try:
                _study_parent.show()
                _study_parent.present()
            except Exception:
                pass
        _study_parent = None
        Gtk.main_quit()

    win.connect("destroy", _on_destroy)
    win.show_all()
    win.present()
    web.grab_focus()
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
    pending = _load_suspend_state()
    if pending > time.time():
        print("Unfinished suspension found — enforcing until countdown ends.", flush=True)
        enforce_suspension_until(pending)
    else:
        _clear_suspend_state()
        _gnome_heal()  # no lockout active: undo any stale shell lockdown
    while True:
        while time.time() < suspend_until:
            time.sleep(20)
        end = time.time() + FOCUS_MINUTES * 60
        while time.time() < end and not tr.limit_pending:
            time.sleep(20)
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
    if "--show-now" in sys.argv:
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
