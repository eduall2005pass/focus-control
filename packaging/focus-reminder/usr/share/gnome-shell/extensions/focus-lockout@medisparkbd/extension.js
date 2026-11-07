import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
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
        this._wifiActivePath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'wifi-active.flag',
        ]);
        this._studySuspendPath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'study-suspended.state',
        ]);
        this._root = null;
        this._grab = null;
        this._end = 0;
        this._monitorId = 0;
        this._studyBtn = null;
        this._wifiBtn = null;
        this._studyLaunching = false;
        this._lockdownActive = false;
        this._overviewBlocked = false;
        this._notificationSettings = null;
        this._windowCreatedId = global.display.connect('window-created', (_display, metaWindow) => {
            try {
                const title = metaWindow.get_title();
                const wmClass = metaWindow.get_wm_class();
                if (title === 'Study' || wmClass === 'focus-reminder' || title === 'Open PDF / Image' || title === 'Wi-Fi Networks' || title === 'Extend Lockout Time') {
                    metaWindow.activate(global.get_current_time());
                }
            } catch (_) {}
        });
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
        const paths = [this._statePath, this._studyActivePath, this._wifiActivePath, this._gtkReadyPath, this._studySuspendPath];
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
        if (this._windowCreatedId) {
            global.display.disconnect(this._windowCreatedId);
            this._windowCreatedId = 0;
        }
        this._hideOverlay();
        this._setLockdownActive(false);
    }

    _setLockdownActive(active) {
        if (this._lockdownActive === active) return;
        this._lockdownActive = active;

        // 1. Overview block/unblock (blocks Super key and Activities overview completely)
        try {
            if (active) {
                if (Main.overview && !this._overviewBlocked) {
                    Main.overview.block();
                    this._overviewBlocked = true;
                }
            } else {
                if (Main.overview && this._overviewBlocked) {
                    Main.overview.unblock();
                    this._overviewBlocked = false;
                }
            }
        } catch (_) {}

        // 2. Hide/show and deactivate Clutter stage dock actors (Ubuntu Dock)
        try {
            const findDocks = (actor) => {
                let found = [];
                if (!actor) return found;
                if (actor.name === 'dashtodockContainer' ||
                    (actor.has_style_class_name && actor.has_style_class_name('dashtodock'))) {
                    found.push(actor);
                }
                if (actor.get_children) {
                    for (const child of actor.get_children()) {
                        found = found.concat(findDocks(child));
                    }
                }
                return found;
            };
            const docks = findDocks(global.stage);
            for (const dock of docks) {
                dock.visible = !active;
                dock.reactive = !active;
                dock.opacity = active ? 0 : 255;
                if (active && !dock._focusLockHook) {
                    dock._focusLockHook = true;
                    dock.connect('notify::visible', () => {
                        if (this._lockdownActive && dock.visible) {
                            dock.visible = false;
                            dock.reactive = false;
                        }
                    });
                }
            }
        } catch (_) {}

        // 3. Dash-to-dock / Ubuntu Dock GSettings lockdown
        try {
            const s = new Gio.Settings({schema_id: 'org.gnome.shell.extensions.dash-to-dock'});
            if (active) {
                s.set_boolean('dock-fixed', false);
                s.set_boolean('autohide', true);
                s.set_boolean('autohide-in-fullscreen', false);
                s.set_boolean('intellihide', false);
                s.set_boolean('require-pressure-to-show', true);
                s.set_double('pressure-threshold', 1000000.0);
                s.set_boolean('show-running', false);
                s.set_boolean('show-favorites', false);
                s.set_boolean('show-show-apps-button', false);
                s.set_boolean('hot-keys', false);
            } else {
                s.reset('dock-fixed');
                s.reset('autohide');
                s.reset('autohide-in-fullscreen');
                s.reset('intellihide');
                s.reset('require-pressure-to-show');
                s.reset('pressure-threshold');
                s.reset('show-running');
                s.reset('show-favorites');
                s.reset('show-show-apps-button');
                s.reset('hot-keys');
            }
        } catch (_) {}

        // 4. Prevent notification banners from appearing over the lockout.
        try {
            const notifications = new Gio.Settings({
                schema_id: 'org.gnome.desktop.notifications',
            });
            if (active) {
                if (!this._notificationSettings) {
                    this._notificationSettings = {
                        showBanners: notifications.get_boolean('show-banners'),
                    };
                }
                notifications.set_boolean('show-banners', false);
            } else if (this._notificationSettings) {
                notifications.set_boolean(
                    'show-banners', this._notificationSettings.showBanners);
                this._notificationSettings = null;
            }
        } catch (_) {}
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
            const age = Date.now() / 1000 - ts;
            if (!Number.isFinite(ts) || age < 0 || age >= 8)
                return false;
            const wins = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null);
            return wins.some(w => w && (w.get_title() === 'Study' || (w.get_wm_class && w.get_wm_class() === 'focus-reminder')));
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
            this._setLockdownActive(true);

            // Ensure overlay root is created and covering screen
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

            const isStudySuspended = this._getStudySuspendedUntil() > now;
            const studyActive = this._isStudyActive() && !isStudySuspended;

            if (studyActive) {
                // Study Mode is open on screen!
                // 1. Release modal grab so user can interact with Study Mode:
                if (this._grab) {
                    try {
                        Main.popModal(this._grab);
                    } catch (_) {}
                    this._grab = null;
                }
                // 2. Position this._root BELOW global.top_window_group (Study Mode window is in top_window_group).
                // BUT DO NOT HIDE this._root! this._root stays permanently visible behind Study Mode!
                if (this._root && Main.layoutManager.uiGroup.contains(global.top_window_group)) {
                    try {
                        Main.layoutManager.uiGroup.set_child_below_sibling(this._root, global.top_window_group);
                    } catch (_) {}
                }
                return;
            }

            // Study Mode is NOT active (or just closed):
            // 1. Move this._root to the very TOP of uiGroup:
            if (this._root) {
                try {
                    Main.layoutManager.uiGroup.set_child_above_sibling(this._root, null);
                } catch (_) {}
            }
            // 2. Re-acquire modal grab to block all desktop interaction:
            if (this._root && !this._grab) {
                try {
                    this._grab = Main.pushModal(this._root);
                } catch (e) {
                    logError(e, 'FocusLockout grab error');
                }
                global.stage.set_key_focus(this._root);
            }

            // 3. Reset Study button state if it was in 'Opening...' state
            if (this._studyBtn && this._studyLaunching) {
                this._studyLaunching = false;
                this._studyBtn.reactive = true;
                this._studyBtn.set_label('📚  Study Mode');
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
        } else {
            if (this._root) {
                this._hideOverlay();
            } else {
                this._removeFlag();
            }
            try { GLib.unlink(this._studyActivePath); } catch (_) {}
            try { GLib.unlink(this._wifiActivePath); } catch (_) {}
            this._setLockdownActive(false);
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
            // Safety timeout so button never stays stuck disabled
            GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
                if (this._studyLaunching && this._root) {
                    this._studyLaunching = false;
                    if (this._studyBtn) {
                        this._studyBtn.set_label('📚  Study Mode');
                        this._studyBtn.reactive = true;
                    }
                }
                return GLib.SOURCE_REMOVE;
            });
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
                GLib.spawn_command_line_async('systemctl poweroff -i');
            } catch (err) {
                logError(err, 'Could not power off');
            }
        });

        this._wifiBtn = new St.Button({
            label: '📶  Wi-Fi',
            style_class: 'button',
            reactive: true,
            can_focus: true,
            track_hover: true,
            style: 'background-color: #1e293b; color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.45); padding: 12px 32px; border-radius: 8px; font-weight: bold; font-size: 18px;',
        });
        this._wifiBtn.connect('clicked', () => {
            if (this._btnBox) this._btnBox.hide();
            if (this._wifiCard) {
                this._wifiCard.show();
                this._scanWifi();
            }
        });

        this._btnBox = btnBox;
        this._btnBox.add_child(this._studyBtn);
        this._btnBox.add_child(this._wifiBtn);
        this._btnBox.add_child(powerBtn);
        content.add_child(this._btnBox);

        this._wifiCard = this._buildWifiCard();
        this._wifiCard.visible = false;
        content.add_child(this._wifiCard);

        this._root.add_child(topSpace);
        this._root.add_child(content);
        this._root.add_child(bottomSpace);

        // GNOME Shell 50.1 layoutManager addTopChrome: trackFullscreen: false so fullscreen windows never hide lockout
        Main.layoutManager.addTopChrome(this._root, {
            trackFullscreen: false,
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

    _buildWifiCard() {
        const card = new St.BoxLayout({
            vertical: true,
            x_align: Clutter.ActorAlign.CENTER,
            style: 'background-color: rgba(15, 23, 42, 0.98); border: 1px solid rgba(56, 189, 248, 0.4); border-radius: 14px; padding: 24px; min-width: 480px; max-width: 540px; margin-top: 15px;',
        });

        const headBox = new St.BoxLayout({
            vertical: false,
            x_expand: true,
            style: 'margin-bottom: 14px;',
        });
        const titleLbl = new St.Label({
            text: '📶  WI-FI NETWORKS',
            y_align: Clutter.ActorAlign.CENTER,
            style: 'color: #38bdf8; font-weight: bold; font-size: 16px;',
        });
        const headSpacer = new St.Widget({x_expand: true});

        const scanBtn = new St.Button({
            label: '↻  Scan',
            style_class: 'button',
            reactive: true,
            can_focus: true,
            track_hover: true,
            style: 'background-color: #1e293b; color: #94a3b8; padding: 6px 14px; border-radius: 6px; font-size: 13px; margin-right: 8px;',
        });
        scanBtn.connect('clicked', () => this._scanWifi());

        const closeBtn = new St.Button({
            label: '✕  Close',
            style_class: 'button',
            reactive: true,
            can_focus: true,
            track_hover: true,
            style: 'background-color: #334155; color: #ffffff; padding: 6px 14px; border-radius: 6px; font-weight: bold; font-size: 13px;',
        });
        closeBtn.connect('clicked', () => {
            if (this._wifiCard) this._wifiCard.hide();
            if (this._btnBox) this._btnBox.show();
        });

        headBox.add_child(titleLbl);
        headBox.add_child(headSpacer);
        headBox.add_child(scanBtn);
        headBox.add_child(closeBtn);
        card.add_child(headBox);

        this._wifiStatusLbl = new St.Label({
            text: 'Ready to scan…',
            style: 'color: #cbd5e1; font-size: 13px; margin-bottom: 12px;',
        });
        card.add_child(this._wifiStatusLbl);

        this._wifiListBox = new St.BoxLayout({
            vertical: true,
            style: 'spacing: 6px;',
        });
        this._wifiScroll = new St.ScrollView({
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            enable_mouse_scrolling: true,
            style: 'max-height: 240px; min-height: 120px; margin-bottom: 12px;',
        });
        this._wifiScroll.set_child(this._wifiListBox);
        card.add_child(this._wifiScroll);

        this._wifiPassBox = new St.BoxLayout({
            vertical: true,
            style: 'background-color: #1e293b; border: 1px solid #475569; border-radius: 8px; padding: 12px; margin-top: 6px; spacing: 8px;',
        });
        this._wifiPassBox.hide();

        this._wifiTargetSsidLbl = new St.Label({
            text: 'Connect to: ',
            style: 'color: #f1f5f9; font-weight: bold; font-size: 13px;',
        });
        this._wifiPassEntry = new St.Entry({
            hint_text: 'Enter Wi-Fi Password',
            can_focus: true,
            style: 'background-color: #0f172a; color: #ffffff; border: 1px solid #3b82f6; border-radius: 6px; padding: 8px 12px; font-size: 14px;',
        });
        this._wifiPassEntry.clutter_text.set_password_char('\u25cf');

        const passBtnBox = new St.BoxLayout({
            vertical: false,
            style: 'spacing: 10px;',
        });
        const connectBtn = new St.Button({
            label: 'Connect',
            style_class: 'button',
            reactive: true,
            can_focus: true,
            track_hover: true,
            style: 'background-color: #2563eb; color: #ffffff; padding: 8px 20px; border-radius: 6px; font-weight: bold; font-size: 13px;',
        });
        const cancelBtn = new St.Button({
            label: 'Cancel',
            style_class: 'button',
            reactive: true,
            can_focus: true,
            track_hover: true,
            style: 'background-color: #475569; color: #ffffff; padding: 8px 16px; border-radius: 6px; font-size: 13px;',
        });

        connectBtn.connect('clicked', () => {
            const password = this._wifiPassEntry.get_text();
            this._connectWifi(this._wifiTargetSsid, password);
        });
        this._wifiPassEntry.clutter_text.connect('activate', () => {
            const password = this._wifiPassEntry.get_text();
            this._connectWifi(this._wifiTargetSsid, password);
        });
        cancelBtn.connect('clicked', () => {
            this._wifiPassBox.hide();
        });

        passBtnBox.add_child(connectBtn);
        passBtnBox.add_child(cancelBtn);

        this._wifiPassBox.add_child(this._wifiTargetSsidLbl);
        this._wifiPassBox.add_child(this._wifiPassEntry);
        this._wifiPassBox.add_child(passBtnBox);
        card.add_child(this._wifiPassBox);

        return card;
    }

    _scanWifi() {
        if (!this._wifiStatusLbl || !this._wifiListBox) return;
        this._wifiStatusLbl.set_text('Scanning for Wi-Fi networks…');
        this._wifiListBox.destroy_all_children();

        const proc = new Gio.Subprocess({
            argv: ['nmcli', '-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'],
            flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
        });
        proc.init(null);
        proc.communicate_utf8_async(null, null, (p, res) => {
            try {
                const [ok, stdout, stderr] = p.communicate_utf8_finish(res);
                if (!this._wifiListBox) return;
                this._wifiListBox.destroy_all_children();
                if (!ok || !stdout) {
                    this._wifiStatusLbl.set_text(stderr ? `Error: ${stderr.trim()}` : 'No networks found.');
                    return;
                }
                const lines = stdout.split('\n');
                const seen = new Set();
                const networks = [];
                let connectedSsid = null;
                for (const rawLine of lines) {
                    const line = rawLine.trim();
                    if (!line) continue;
                    const parts = line.split(':');
                    if (parts.length < 4) continue;
                    const inUse = parts[0].trim() === '*';
                    const ssid = parts[1].trim();
                    if (!ssid || seen.has(ssid)) continue;
                    seen.add(ssid);
                    if (inUse) connectedSsid = ssid;
                    const signal = parseInt(parts[2], 10) || 0;
                    const security = parts.slice(3).join(':').trim();
                    networks.push({ inUse, ssid, signal, security });
                }

                networks.sort((a, b) => {
                    if (a.inUse) return -1;
                    if (b.inUse) return 1;
                    return b.signal - a.signal;
                });

                if (connectedSsid) {
                    this._wifiStatusLbl.set_text(`Connected to: ${connectedSsid}`);
                } else {
                    this._wifiStatusLbl.set_text(`Found ${networks.length} networks. Click one to connect.`);
                }

                if (networks.length === 0) {
                    const noNetLbl = new St.Label({
                        text: 'No Wi-Fi networks found. Click Scan to retry.',
                        style: 'color: #94a3b8; font-size: 13px; padding: 10px;',
                    });
                    this._wifiListBox.add_child(noNetLbl);
                    return;
                }

                for (const net of networks) {
                    const row = new St.BoxLayout({
                        vertical: false,
                        x_expand: true,
                        style: net.inUse
                            ? 'background-color: rgba(37, 99, 235, 0.2); border: 1px solid #2563eb; border-radius: 6px; padding: 6px 10px; margin-bottom: 2px;'
                            : 'background-color: rgba(30, 41, 59, 0.6); border: 1px solid rgba(148, 163, 184, 0.15); border-radius: 6px; padding: 6px 10px; margin-bottom: 2px;',
                    });

                    const signalBars = net.signal > 70 ? '▂▄▆█' : (net.signal > 40 ? '▂▄▆_' : '▂▄__');
                    const lockIcon = net.security ? '🔒 ' : '';
                    const inUseIcon = net.inUse ? '✓ ' : '';

                    const ssidBtn = new St.Button({
                        label: `${inUseIcon}${lockIcon}${net.ssid}`,
                        style_class: 'button',
                        reactive: !net.inUse,
                        can_focus: !net.inUse,
                        x_align: Clutter.ActorAlign.START,
                        x_expand: true,
                        style: net.inUse
                            ? 'background-color: transparent; color: #60a5fa; font-weight: bold; font-size: 13px; text-align: left; padding: 0;'
                            : 'background-color: transparent; color: #f1f5f9; font-size: 13px; text-align: left; padding: 0;',
                    });
                    if (!net.inUse) {
                        ssidBtn.connect('clicked', () => {
                            if (net.security) {
                                this._wifiTargetSsid = net.ssid;
                                this._wifiTargetSsidLbl.set_text(`Connect to: ${net.ssid} (${net.security})`);
                                this._wifiPassEntry.set_text('');
                                this._wifiPassBox.show();
                                global.stage.set_key_focus(this._wifiPassEntry);
                            } else {
                                this._connectWifi(net.ssid, '');
                            }
                        });
                    }

                    const signalLbl = new St.Label({
                        text: `${signalBars} ${net.signal}%`,
                        y_align: Clutter.ActorAlign.CENTER,
                        style: 'color: #94a3b8; font-size: 11px; margin-left: 8px;',
                    });

                    row.add_child(ssidBtn);
                    row.add_child(signalLbl);

                    if (net.inUse) {
                        const discBtn = new St.Button({
                            label: 'Disconnect',
                            style_class: 'button',
                            reactive: true,
                            can_focus: true,
                            style: 'background-color: #ef4444; color: #ffffff; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-left: 8px;',
                        });
                        discBtn.connect('clicked', () => {
                            this._disconnectWifi(net.ssid);
                        });
                        row.add_child(discBtn);
                    }

                    this._wifiListBox.add_child(row);
                }
            } catch (err) {
                if (this._wifiStatusLbl) {
                    this._wifiStatusLbl.set_text(`Scan failed: ${err.message || err}`);
                }
            }
        });
    }

    _connectWifi(ssid, password) {
        if (!ssid) return;
        if (this._wifiPassBox) this._wifiPassBox.hide();
        if (this._wifiStatusLbl) {
            this._wifiStatusLbl.set_text(`⏳ Connecting to "${ssid}"…`);
        }
        const argv = password
            ? ['nmcli', 'dev', 'wifi', 'connect', ssid, 'password', password]
            : ['nmcli', 'dev', 'wifi', 'connect', ssid];

        const proc = new Gio.Subprocess({
            argv,
            flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
        });
        proc.init(null);
        proc.communicate_utf8_async(null, null, (p, res) => {
            try {
                const [ok, stdout, stderr] = p.communicate_utf8_finish(res);
                if (p.get_successful()) {
                    if (this._wifiStatusLbl) {
                        this._wifiStatusLbl.set_text(`✓ Successfully connected to "${ssid}"!`);
                    }
                    GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
                        this._scanWifi();
                        return GLib.SOURCE_REMOVE;
                    });
                } else {
                    const msg = (stderr || stdout || 'Unknown error').trim();
                    if (this._wifiStatusLbl) {
                        this._wifiStatusLbl.set_text(`❌ Connection failed: ${msg}`);
                    }
                }
            } catch (err) {
                if (this._wifiStatusLbl) {
                    this._wifiStatusLbl.set_text(`❌ Error: ${err.message || err}`);
                }
            }
        });
    }

    _disconnectWifi(ssid) {
        if (!ssid) return;
        if (this._wifiStatusLbl) {
            this._wifiStatusLbl.set_text(`Disconnecting from "${ssid}"…`);
        }
        const proc = new Gio.Subprocess({
            argv: ['nmcli', 'connection', 'down', 'id', ssid],
            flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
        });
        proc.init(null);
        proc.communicate_utf8_async(null, null, (p, res) => {
            try {
                p.communicate_utf8_finish(res);
                if (this._wifiStatusLbl) {
                    this._wifiStatusLbl.set_text(`Disconnected from "${ssid}".`);
                }
                GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
                    this._scanWifi();
                    return GLib.SOURCE_REMOVE;
                });
            } catch (_) {}
        });
    }

    _writeFlag() {
        if (!this._grab)
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
        this._wifiBtn = null;
        this._btnBox = null;
        this._wifiCard = null;
        this._wifiStatusLbl = null;
        this._wifiListBox = null;
        this._wifiScroll = null;
        this._wifiPassBox = null;
        this._wifiPassEntry = null;
        this._wifiTargetSsid = null;
        this._timerLabel = null;
        this._unlockLabel = null;
        this._removeFlag();
    }
}
