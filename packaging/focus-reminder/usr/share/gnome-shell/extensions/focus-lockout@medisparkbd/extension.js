import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const STATE_DIR = '.local/share/focus-reminder';

export default class FocusLockoutExtension extends Extension {
    enable() {
        this._statePath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'suspend.state',
        ]);
        this._flagPath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'shell-lockout-active',
        ]);
        this._gtkReadyPath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'gtk-lockout-ready',
        ]);
        this._studyActivePath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'study-active.flag',
        ]);
        this._studySuspendPath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'study-suspended.state',
        ]);
        this._root = null;
        this._grab = null;
        this._end = 0;
        this._monitorId = 0;
        this._studyBtn = null;
        this._studyLaunching = false;
        this._monitors = [];
        this._setupFileMonitors();
        this._pollId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, 100, () => {
                try {
                    this._syncLockout();
                } catch (e) {
                    logError(e, 'FocusLockout error in timeout');
                }
                return GLib.SOURCE_CONTINUE;
            });
        try {
            this._syncLockout();
        } catch (e) {
            logError(e, 'FocusLockout error in initial sync');
        }
    }

    _setupFileMonitors() {
        const paths = [this._statePath, this._studyActivePath, this._gtkReadyPath, this._studySuspendPath];
        for (const p of paths) {
            try {
                const file = Gio.File.new_for_path(p);
                const mon = file.monitor_file(Gio.FileMonitorFlags.NONE, null);
                const handlerId = mon.connect('changed', () => {
                    try {
                        this._syncLockout();
                    } catch (e) {
                        logError(e, 'FocusLockout error in file monitor changed');
                    }
                });
                this._monitors.push({ mon, handlerId });
            } catch (e) {
                logError(e, `FocusLockout could not monitor ${p}`);
            }
        }
    }

    disable() {
        if (this._monitors) {
            for (const { mon, handlerId } of this._monitors) {
                try {
                    mon.disconnect(handlerId);
                    mon.cancel();
                } catch (_) {}
            }
            this._monitors = [];
        }
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        this._hideOverlay();
    }

    _readDeadline() {
        try {
            const [ok, bytes] = GLib.file_get_contents(this._statePath);
            if (!ok)
                return 0;
            const value = Number(new TextDecoder().decode(bytes).trim());
            return Number.isFinite(value) ? value : 0;
        } catch (_) {
            return 0;
        }
    }

    _isGtkReady() {
        try {
            const [ok, bytes] = GLib.file_get_contents(this._gtkReadyPath);
            if (!ok)
                return false;
            const ts = Number(new TextDecoder().decode(bytes).trim());
            return Number.isFinite(ts) && (Date.now() / 1000 - ts < 10);
        } catch (_) {
            return false;
        }
    }

    _isStudyActive() {
        try {
            const [ok, bytes] = GLib.file_get_contents(this._studyActivePath);
            if (!ok)
                return false;
            const ts = Number(new TextDecoder().decode(bytes).trim());
            return Number.isFinite(ts) && (Date.now() / 1000 - ts < 10);
        } catch (_) {
            return false;
        }
    }

    _getStudySuspendedUntil() {
        try {
            const [ok, bytes] = GLib.file_get_contents(this._studySuspendPath);
            if (!ok)
                return 0;
            const ts = Number(new TextDecoder().decode(bytes).trim());
            return Number.isFinite(ts) && ts > Date.now() / 1000 ? ts : 0;
        } catch (_) {
            return 0;
        }
    }

    _syncLockout() {
        const end = this._readDeadline();
        const now = Date.now() / 1000;
        if (end > now) {
            this._end = end;
            this._writeFlag();

            const isStudySuspended = this._getStudySuspendedUntil() > now;

            // If GTK lockout window is active or (Study mode is active AND NOT suspended), hide shell overlay
            if (this._isGtkReady() || (this._isStudyActive() && !isStudySuspended)) {
                if (this._root) {
                    this._hideOverlay();
                }
                return;
            }

            // Otherwise, show shell overlay (guarantees zero-delay modal shield on login/reboot)
            if (!this._root) {
                this._showOverlay();
            }

            // Dynamically update the countdown timer every second
            if (this._timerLabel) {
                this._timerLabel.set_text(this._formatRemaining(end));
            }
            if (this._unlockLabel) {
                this._unlockLabel.set_text(
                    `Unlocks at ${new Date(end * 1000).toLocaleString()}`);
            }

            // Update Study button state based on 30m suspension
            if (this._studyBtn) {
                const suspendedUntil = this._getStudySuspendedUntil();
                if (suspendedUntil > now) {
                    const minsLeft = Math.ceil((suspendedUntil - now) / 60);
                    this._studyBtn.reactive = false;
                    this._studyBtn.can_focus = false;
                    this._studyBtn.track_hover = false;
                    this._studyBtn.set_label(`📚 Study Suspended (${minsLeft}m)`);
                    this._studyBtn.style = 'background-color: #334155; color: #94a3b8; padding: 12px 32px; border-radius: 8px; font-weight: bold; font-size: 18px; border: 1px solid #475569; opacity: 0.6;';
                } else if (!this._studyLaunching) {
                    this._studyBtn.reactive = true;
                    this._studyBtn.can_focus = true;
                    this._studyBtn.track_hover = true;
                    this._studyBtn.set_label('📚  Study Mode');
                    this._studyBtn.style = 'background-color: #2563eb; color: #ffffff; padding: 12px 32px; border-radius: 8px; font-weight: bold; font-size: 18px;';
                }
            }
        } else if (this._root) {
            this._hideOverlay();
        } else {
            this._removeFlag();
        }
    }

    _formatRemaining(end) {
        let left = Math.max(0, Math.floor(end - Date.now() / 1000));
        const days = Math.floor(left / 86400);
        left %= 86400;
        const hours = Math.floor(left / 3600);
        left %= 3600;
        const minutes = Math.floor(left / 60);
        const seconds = left % 60;
        const pad = value => String(value).padStart(2, '0');
        return days > 0
            ? `${days}d ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
            : `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
    }

    _showOverlay() {
        GLib.mkdir_with_parents(GLib.path_get_dirname(this._flagPath), 0o700);
        try {
            GLib.spawn_command_line_async("gnome-extensions disable ubuntu-dock@ubuntu.com");
            GLib.spawn_command_line_async("gnome-extensions disable ding@rastersoft.com");
        } catch (_) {}

        const width = global.stage.width;
        const height = global.stage.height;
        this._root = new St.BoxLayout({
            name: 'focus-lockout-screen',
            vertical: true,
            reactive: true,
            can_focus: true,
            x: 0,
            y: 0,
            width,
            height,
            style: 'background-color: rgba(5, 13, 29, 0.99); padding: 48px;',
        });

        // Block all keyboard bypass
        this._root.connect('key-press-event', () => Clutter.EVENT_STOP);
        this._root.connect('key-release-event', () => Clutter.EVENT_STOP);

        // Trap ALL pointer clicks everywhere on the overlay, propagating only to active action buttons
        const isActionTarget = (src) => {
            if (!src) return false;
            if (src === this._studyBtn || src === powerBtn) return true;
            if (this._studyBtn && this._studyBtn.contains && this._studyBtn.contains(src)) return true;
            if (powerBtn && powerBtn.contains && powerBtn.contains(src)) return true;
            return false;
        };
        this._root.connect('button-press-event', (_actor, event) => {
            if (isActionTarget(event.get_source())) {
                return Clutter.EVENT_PROPAGATE;
            }
            return Clutter.EVENT_STOP;
        });
        this._root.connect('button-release-event', (_actor, event) => {
            if (isActionTarget(event.get_source())) {
                return Clutter.EVENT_PROPAGATE;
            }
            return Clutter.EVENT_STOP;
        });

        const topSpace = new St.Widget({y_expand: true});
        const bottomSpace = new St.Widget({y_expand: true});
        const content = new St.BoxLayout({
            vertical: true,
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
            style: 'spacing: 26px; max-width: 900px;',
        });
        const heading = new St.Label({
            text: 'FOCUS LOCKOUT',
            x_align: Clutter.ActorAlign.CENTER,
            style: 'color: #93c5fd; font-size: 20px; font-weight: bold;',
        });
        this._timerLabel = new St.Label({
            text: this._formatRemaining(this._end),
            x_align: Clutter.ActorAlign.CENTER,
            style: 'color: #ffffff; font-size: 72px; font-weight: 700;',
        });
        const explanation = new St.Label({
            text: 'This computer will unlock when the countdown ends.',
            x_align: Clutter.ActorAlign.CENTER,
            style: 'color: #cbd5e1; font-size: 20px;',
        });
        this._unlockLabel = new St.Label({
            text: `Unlocks at ${new Date(this._end * 1000).toLocaleString()}`,
            x_align: Clutter.ActorAlign.CENTER,
            style: 'color: #94a3b8; font-size: 16px;',
        });
        content.add_child(heading);
        content.add_child(this._timerLabel);
        content.add_child(explanation);
        content.add_child(this._unlockLabel);

        const btnBox = new St.BoxLayout({
            vertical: false,
            x_align: Clutter.ActorAlign.CENTER,
            style: 'spacing: 20px; margin-top: 20px;',
        });

        const suspendedUntil = this._getStudySuspendedUntil();
        const now = Date.now() / 1000;
        const isSuspended = suspendedUntil > now;
        const minsLeft = isSuspended ? Math.ceil((suspendedUntil - now) / 60) : 0;

        this._studyBtn = new St.Button({
            label: isSuspended ? `📚 Study Suspended (${minsLeft}m)` : '📚  Study Mode',
            style_class: 'button',
            reactive: !isSuspended,
            can_focus: !isSuspended,
            track_hover: !isSuspended,
            style: isSuspended
                ? 'background-color: #334155; color: #94a3b8; padding: 12px 32px; border-radius: 8px; font-weight: bold; font-size: 18px; border: 1px solid #475569; opacity: 0.6;'
                : 'background-color: #2563eb; color: #ffffff; padding: 12px 32px; border-radius: 8px; font-weight: bold; font-size: 18px;',
        });
        this._studyBtn.connect('clicked', () => {
            if (this._getStudySuspendedUntil() > Date.now() / 1000) {
                return;
            }
            if (this._studyLaunching) {
                return;
            }
            this._studyLaunching = true;
            this._studyBtn.reactive = false;
            this._studyBtn.set_label('⏳ Opening Study Mode…');
            // Overlay remains up until Python Study window touches study-active.flag, ensuring zero-gap transition.
            try {
                GLib.spawn_command_line_async('python3 /opt/focus-reminder/focus-reminder.py --study');
            } catch (err) {
                logError(err, 'Could not spawn study mode');
                this._studyLaunching = false;
                if (this._studyBtn) {
                    this._studyBtn.set_label('📚  Study Mode');
                    this._studyBtn.reactive = true;
                }
            }
        });

        const powerBtn = new St.Button({
            label: '⏻  Power Off',
            style_class: 'button',
            reactive: true,
            can_focus: true,
            track_hover: true,
            style: 'background-color: #dc2626; color: #ffffff; padding: 12px 32px; border-radius: 8px; font-weight: bold; font-size: 18px;',
        });
        powerBtn.connect('clicked', () => {
            try {
                GLib.spawn_command_line_async('systemctl poweroff');
            } catch (err) {
                logError(err, 'Could not power off');
            }
        });

        btnBox.add_child(this._studyBtn);
        btnBox.add_child(powerBtn);
        content.add_child(btnBox);

        this._root.add_child(topSpace);
        this._root.add_child(content);
        this._root.add_child(bottomSpace);

        // GNOME Shell 50.1 layoutManager addTopChrome: trackFullscreen only, NO affectsInputRegion
        Main.layoutManager.addTopChrome(this._root, {
            trackFullscreen: true,
            affectsStruts: false,
        });

        this._monitorId = Main.layoutManager.connect(
            'monitors-changed', () => this._resizeOverlay());
        this._resizeOverlay();
        try {
            this._grab = Main.pushModal(this._root);
        } catch (error) {
            logError(error, 'Could not acquire GNOME lockout modal grab');
        }
        if (!this._grab) {
            this._hideOverlay();
            return;
        }
        global.stage.set_key_focus(this._root);
    }

    _writeFlag() {
        if (!this._grab && !this._isGtkReady())
            return;
        try {
            GLib.file_set_contents(
                this._flagPath, `${Date.now() / 1000}\n`);
        } catch (error) {
            logError(error, 'Could not update GNOME lockout heartbeat');
        }
    }

    _resizeOverlay() {
        if (!this._root)
            return;
        this._root.set_position(0, 0);
        this._root.set_size(global.stage.width, global.stage.height);
    }

    _removeFlag() {
        try {
            GLib.unlink(this._flagPath);
        } catch (_) {}
    }

    _hideOverlay() {
        this._studyLaunching = false;
        if (this._monitorId) {
            Main.layoutManager.disconnect(this._monitorId);
            this._monitorId = 0;
        }
        if (this._grab) {
            try {
                Main.popModal(this._grab);
            } catch (_) {}
            this._grab = null;
        }
        if (this._root) {
            Main.layoutManager.removeChrome(this._root);
            this._root.destroy();
            this._root = null;
        }
        this._studyBtn = null;
        this._timerLabel = null;
        this._unlockLabel = null;
        this._removeFlag();

        const end = this._readDeadline();
        if (end <= Date.now() / 1000) {
            try {
                GLib.spawn_command_line_async("gnome-extensions enable ubuntu-dock@ubuntu.com");
                GLib.spawn_command_line_async("gnome-extensions enable ding@rastersoft.com");
            } catch (_) {}
        }
    }
}
