import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Config from 'resource:///org/gnome/shell/misc/config.js';

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
        this._studyStartingPath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'study-starting.flag',
        ]);
        this._wifiActivePath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'wifi-active.flag',
        ]);
        this._studySuspendPath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'study-suspended.state',
        ]);
        this._panelsBackupPath = GLib.build_filenamev([
            GLib.get_home_dir(), STATE_DIR, 'panels-backup.json',
        ]);
        this._panelNotifyHook = 0;
        this._panelBoxNotifyHook = 0;
        this._detectEnvironment();

        // Crash recovery: if extension loads when lockout is expired, restore any stale backup
        if (this._readDeadline() <= Date.now() / 1000) {
            const stale = this._loadPanelsBackup();
            if (stale) {
                this._restoreDesktopPanels();
            }
        }
        this._root = null;
        this._grab = null;
        this._end = 0;
        this._monitorId = 0;
        this._studyBtn = null;
        this._wifiBtn = null;
        this._stageEventId = 0;
        this._studyLaunching = false;
        this._lockdownActive = false;
        this._overviewBlocked = false;
        this._windowCreatedId = global.display.connect('window-created', (_display, metaWindow) => {
            try {
                const title = metaWindow.get_title() || '';
                const wmClass = metaWindow.get_wm_class() || '';
                if (this._lockdownActive) {
                    const titleLower = title.toLowerCase();
                    const wmLower = wmClass.toLowerCase();
                    if (wmLower.includes('gnome-control-center') ||
                        wmLower.includes('systemsettings') ||
                        wmLower.includes('gnome-tweaks') ||
                        wmLower.includes('gnome-extensions') ||
                        wmLower.includes('extension-manager') ||
                        wmLower.includes('org.gnome.extensions') ||
                        titleLower === 'settings' ||
                        titleLower.startsWith('settings —') ||
                        titleLower.startsWith('settings -') ||
                        titleLower.includes('extensions')) {
                        console.log(`[FocusLockout] Neutralizing desktop settings/extension window: ${title} (${wmClass})`);
                        metaWindow.delete(global.get_current_time());
                        this._sweepSettingsApps();
                        return;
                    }
                }
                if (title === 'Study' || wmClass === 'focus-reminder' || title === 'Open PDF / Image' || title === 'Wi-Fi Networks' || title === 'Extend Lockout Time') {
                    metaWindow.activate(global.get_current_time());
                }
            } catch (_) {}
        });
        this._stageEventId = global.stage.connect('captured-event', (_stage, event) => {
            if (!this._lockdownActive)
                return Clutter.EVENT_PROPAGATE;

            const type = event.type();
            if (type === Clutter.EventType.BUTTON_PRESS || type === Clutter.EventType.BUTTON_RELEASE) {
                const btn = event.get_button();
                if (btn === 3 || btn === 2) {
                    // No shell or desktop context menu is allowed during an
                    // active lockout, including the Study Mode handoff window.
                    return Clutter.EVENT_STOP;
                }
            }

            // Block Ctrl+Alt+F1 through Ctrl+Alt+F12 (TTY / Virtual Terminal switching bypass)
            if (type === Clutter.EventType.KEY_PRESS || type === Clutter.EventType.KEY_RELEASE) {
                const state = event.get_state();
                const ctrlAlt = (Clutter.ModifierType.CONTROL_MASK | Clutter.ModifierType.MOD1_MASK);
                if ((state & ctrlAlt) === ctrlAlt) {
                    const symbol = event.get_key_symbol();
                    if (symbol >= 0xffbe && symbol <= 0xffc9) {
                        return Clutter.EVENT_STOP;
                    }
                }
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._monitors = [];
        this._setupFileMonitors();
        this._pollId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, 50, () => {
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
        const paths = ['/run/focus-reminder/lockout.json', this._statePath, this._studyActivePath, this._studyStartingPath,
            this._wifiActivePath, this._gtkReadyPath, this._studySuspendPath];
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
        if (this._stageEventId) {
            try { global.stage.disconnect(this._stageEventId); } catch (_) {}
            this._stageEventId = 0;
        }
        this._hideOverlay();
        this._setLockdownActive(false);
    }

    _detectEnvironment() {
        this._env = {
            shellVersion: '50.1',
            sessionType: 'wayland',
            ubuntuVersion: '26.04',
            dockUuid: 'ubuntu-dock@ubuntu.com',
        };
        try {
            this._env.shellVersion = Config.PACKAGE_VERSION || '50.1';
        } catch (_) {}
        try {
            if (typeof Meta.is_wayland_compositor === 'function' && Meta.is_wayland_compositor()) {
                this._env.sessionType = 'wayland';
            } else {
                this._env.sessionType = GLib.getenv('XDG_SESSION_TYPE') || 'x11';
            }
        } catch (_) {
            this._env.sessionType = GLib.getenv('XDG_SESSION_TYPE') || 'unknown';
        }
        try {
            const [ok, data] = GLib.file_get_contents('/etc/os-release');
            if (ok) {
                const text = new TextDecoder().decode(data);
                const m = text.match(/VERSION_ID=["']?([^"'\n]+)/);
                if (m) this._env.ubuntuVersion = m[1];
            }
        } catch (_) {}
        try {
            this._env.dockUuid = this._detectDockUuid();
        } catch (_) {}
        console.log(`[FocusLockout] Environment detected: GNOME Shell ${this._env.shellVersion}, Ubuntu ${this._env.ubuntuVersion}, Session: ${this._env.sessionType}, Dock: ${this._env.dockUuid}`);
    }

    _detectDockUuid() {
        const candidates = ['ubuntu-dock@ubuntu.com', 'dash-to-dock@vibou.hopla.net'];
        for (const uuid of candidates) {
            try {
                if (Main.extensionManager && typeof Main.extensionManager.lookup === 'function' && Main.extensionManager.lookup(uuid)) {
                    return uuid;
                }
            } catch (_) {}
        }
        try {
            const s = new Gio.Settings({schema_id: 'org.gnome.shell'});
            const enabled = s.get_strv('enabled-extensions');
            for (const uuid of candidates) {
                if (enabled.includes(uuid)) return uuid;
            }
        } catch (_) {}
        return 'ubuntu-dock@ubuntu.com';
    }

    _detectDesktopIconUuids() {
        const candidates = [
            'gtk4-ding@smedius.gitlab.com',
            'ding@rastersoft.com',
            'desktop-icons@csoriano',
            'desktop-icons-ng@rastersoft.com',
        ];
        const result = new Set(candidates);
        try {
            const s = new Gio.Settings({schema_id: 'org.gnome.shell'});
            const enabled = s.get_strv('enabled-extensions');
            for (const uuid of enabled) {
                if (/ding|desktop.*icon/i.test(uuid)) {
                    result.add(uuid);
                }
            }
        } catch (_) {}
        if (Main.extensionManager && typeof Main.extensionManager.getUuids === 'function') {
            try {
                for (const uuid of Main.extensionManager.getUuids()) {
                    if (/ding|desktop.*icon/i.test(uuid)) {
                        result.add(uuid);
                    }
                }
            } catch (_) {}
        }
        return Array.from(result);
    }

    _isExtensionEnabled(uuid) {
        try {
            if (Main.extensionManager && typeof Main.extensionManager.lookup === 'function') {
                const ext = Main.extensionManager.lookup(uuid);
                if (ext && ext.state !== undefined) {
                    return ext.state === 1; // ExtensionState.ENABLED
                }
            }
        } catch (_) {}
        try {
            const s = new Gio.Settings({schema_id: 'org.gnome.shell'});
            const enabled = s.get_strv('enabled-extensions');
            return enabled.includes(uuid);
        } catch (_) {
            return false;
        }
    }

    _savePanelsBackup(data) {
        try {
            GLib.mkdir_with_parents(GLib.path_get_dirname(this._panelsBackupPath), 0o700);
            GLib.file_set_contents(this._panelsBackupPath, JSON.stringify(data));
        } catch (e) {
            logError(e, 'FocusLockout could not write panels backup');
        }
    }

    _loadPanelsBackup() {
        try {
            const [ok, bytes] = GLib.file_get_contents(this._panelsBackupPath);
            if (ok) {
                return JSON.parse(new TextDecoder().decode(bytes));
            }
        } catch (_) {}
        return null;
    }

    _deletePanelsBackup() {
        try {
            GLib.unlink(this._panelsBackupPath);
        } catch (_) {}
    }

    _findDockActors(actor) {
        let found = [];
        if (!actor) return found;
        const name = actor.name || '';
        const hasClass = (typeof actor.has_style_class_name === 'function');
        if (name === 'dashtodockContainer' ||
            (hasClass && (actor.has_style_class_name('dashtodock') || actor.has_style_class_name('dashtodockContainer')))) {
            found.push(actor);
        }
        if (typeof actor.get_children === 'function') {
            for (const child of actor.get_children()) {
                found = found.concat(this._findDockActors(child));
            }
        }
        return found;
    }

    _sweepSettingsApps() {
        // Settings / Extension Manager are the known lockout bypass. Kill them
        // even if they were started after the lockdown began. Long binary names
        // are truncated in /proc/PID/comm, so match the full argv[0] with -f.
        for (const name of [
            'gnome-control-center', 'gnome-settings', 'gnome-tweaks',
            'dconf-editor', 'gnome-extensions-app',
            'gnome-shell-extension-prefs', 'extension-manager',
            'gnome-shell-extension-manager',
        ]) {
            GLib.spawn_command_line_async(
                `pkill -9 -f '^([^ ]*/)?${name}( |$)'`);
        }
    }

    _setLockdownActive(active) {
        if (this._lockdownActive === active) return;
        console.log(`[FocusLockout] lockdown active=${active}`);
        this._lockdownActive = active;

        if (active) {
            this._applyLockdown();
        } else {
            this._restoreDesktopPanels();
        }
    }

    _applyLockdown() {
        // 1. Save original panels, dock, overview & hot corners state to persistent disk backup if not already saved
        try {
            let backup = this._loadPanelsBackup();
            if (!backup) {
                const dockUuid = (this._env && this._env.dockUuid) || this._detectDockUuid();
                const isDockEnabled = this._isExtensionEnabled(dockUuid);
                const isDingEnabled = this._isExtensionEnabled('ding@rastersoft.com');
                const allDesktopUuids = this._detectDesktopIconUuids();
                const enabledDesktopUuids = allDesktopUuids.filter(u => this._isExtensionEnabled(u));
                const act = Main.panel?.statusArea?.activities;
                let mutterOverlayKey = 'Super_L';
                let hotCornersEnabled = false;
                try {
                    const ms = new Gio.Settings({schema_id: 'org.gnome.mutter'});
                    mutterOverlayKey = ms.get_string('overlay-key');
                } catch (_) {}
                try {
                    const is = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
                    hotCornersEnabled = is.get_boolean('enable-hot-corners');
                } catch (_) {}

                backup = {
                    savedAt: Math.floor(Date.now() / 1000),
                    dockUuid,
                    dockEnabled: isDockEnabled,
                    dingEnabled: isDingEnabled,
                    desktopIconUuids: enabledDesktopUuids,
                    panelVisible: Main.panel ? Main.panel.visible : true,
                    panelReactive: Main.panel ? Main.panel.reactive : true,
                    panelOpacity: Main.panel ? Main.panel.opacity : 255,
                    panelBoxVisible: Main.layoutManager.panelBox ? Main.layoutManager.panelBox.visible : true,
                    panelBoxReactive: Main.layoutManager.panelBox ? Main.layoutManager.panelBox.reactive : true,
                    panelBoxOpacity: Main.layoutManager.panelBox ? Main.layoutManager.panelBox.opacity : 255,
                    actVisible: act ? act.visible : true,
                    actReactive: act ? act.reactive : true,
                    actOpacity: act ? act.opacity : 255,
                    mutterOverlayKey,
                    hotCornersEnabled,
                };
                this._savePanelsBackup(backup);
            }
        } catch (e) {
            logError(e, 'FocusLockout error backing up panel state');
        }

        // 2. Completely hide GNOME Activities Button & guard against re-reveal
        try {
            const act = Main.panel?.statusArea?.activities;
            if (act) {
                act.visible = false;
                act.reactive = false;
                act.opacity = 0;
                if (act.container) {
                    act.container.visible = false;
                    act.container.reactive = false;
                    act.container.opacity = 0;
                }
                if (!this._actNotifyHook) {
                    this._actNotifyHook = act.connect('notify::visible', () => {
                        if (this._lockdownActive && act.visible) {
                            act.visible = false;
                            act.reactive = false;
                            act.opacity = 0;
                        }
                    });
                }
            }
        } catch (e) {
            logError(e, 'FocusLockout Activities button hide error');
        }

        // 3. Completely hide Top Bar (Main.panel & panelBox) and remove struts
        try {
            if (Main.panel) {
                Main.panel.visible = false;
                Main.panel.reactive = false;
                Main.panel.opacity = 0;
                if (!this._panelNotifyHook) {
                    this._panelNotifyHook = Main.panel.connect('notify::visible', () => {
                        if (this._lockdownActive && Main.panel.visible) {
                            Main.panel.visible = false;
                            Main.panel.reactive = false;
                            Main.panel.opacity = 0;
                        }
                    });
                }
            }
            if (Main.layoutManager.panelBox) {
                Main.layoutManager.panelBox.visible = false;
                Main.layoutManager.panelBox.reactive = false;
                Main.layoutManager.panelBox.opacity = 0;
                if (!this._panelBoxNotifyHook) {
                    this._panelBoxNotifyHook = Main.layoutManager.panelBox.connect('notify::visible', () => {
                        if (this._lockdownActive && Main.layoutManager.panelBox.visible) {
                            Main.layoutManager.panelBox.visible = false;
                            Main.layoutManager.panelBox.reactive = false;
                            Main.layoutManager.panelBox.opacity = 0;
                        }
                    });
                }
            }
            if (typeof Main.layoutManager._queueUpdateRegions === 'function') {
                Main.layoutManager._queueUpdateRegions();
            }
        } catch (e) {
            logError(e, 'FocusLockout Top Bar hide error');
        }

        // 4. Completely hide and disable Ubuntu Dock, Desktop Icons & eliminate edge reveal
        try {
            const dockUuid = (this._env && this._env.dockUuid) || 'ubuntu-dock@ubuntu.com';
            // Disable dock extension: removes dock actor, eliminates pressure barriers & auto-hide edge reveal
            if (Main.extensionManager && typeof Main.extensionManager.disableExtension === 'function') {
                try { Main.extensionManager.disableExtension(dockUuid); } catch (_) {}
            }
            GLib.spawn_command_line_async(`gnome-extensions disable ${dockUuid}`);

            // Disable all desktop icon extensions (DING, GTK4-DING, etc.) so right-click desktop menu never exists
            const allDesktopUuids = this._detectDesktopIconUuids();
            for (const uuid of allDesktopUuids) {
                if (Main.extensionManager && typeof Main.extensionManager.disableExtension === 'function') {
                    try { Main.extensionManager.disableExtension(uuid); } catch (_) {}
                }
                GLib.spawn_command_line_async(`gnome-extensions disable ${uuid}`);
            }
            GLib.spawn_command_line_async('pkill -9 -f "dingManager.js|adw-ding|gtk4-ding"');

            // Immediately neutralize any settings / control-center processes and open windows
            this._sweepSettingsApps();
            this._lastSettingsSweep = Date.now();
            try {
                const wins = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null);
                for (const win of wins) {
                    if (!win) continue;
                    const wm = ((win.get_wm_class && win.get_wm_class()) || '').toLowerCase();
                    const t = ((win.get_title && win.get_title()) || '').toLowerCase();
                    if (wm.includes('gnome-control-center') ||
                        wm.includes('gnome-tweaks') ||
                        wm.includes('gnome-extensions') ||
                        wm.includes('extension-manager') ||
                        wm.includes('org.gnome.extensions') ||
                        t === 'settings' || t.startsWith('settings -') ||
                        t.includes('extensions')) {
                        win.delete(global.get_current_time());
                    }
                }
            } catch (_) {}

            // Disable reactivity on desktop background actors so right-clicks cannot trigger anything
            try {
                if (Main.layoutManager) {
                    if (Main.layoutManager._backgroundGroup) {
                        Main.layoutManager._backgroundGroup.reactive = false;
                    }
                    if (Main.layoutManager._bgManagers) {
                        for (const bgMgr of Main.layoutManager._bgManagers) {
                            if (bgMgr && bgMgr.backgroundActor) {
                                bgMgr.backgroundActor.reactive = false;
                                if (bgMgr.backgroundActor._backgroundMenu) {
                                    try { bgMgr.backgroundActor._backgroundMenu.close(); } catch (_) {}
                                }
                            }
                        }
                    }
                }
            } catch (bgErr) {
                logError(bgErr, 'FocusLockout background neutralize error');
            }

            // Also hide any Clutter dock actors on stage immediately
            const docks = this._findDockActors(global.stage);
            for (const dock of docks) {
                dock.visible = false;
                dock.reactive = false;
                dock.opacity = 0;
                if (!dock._focusLockHook) {
                    dock._focusLockHook = true;
                    dock.connect('notify::visible', () => {
                        if (this._lockdownActive && dock.visible) {
                            dock.visible = false;
                            dock.reactive = false;
                            dock.opacity = 0;
                        }
                    });
                }
            }
        } catch (e) {
            logError(e, 'FocusLockout Dock hide error');
        }

        // 5. Completely neutralize Activities Overview (clicks, hot corners, Super key, gestures)
        try {
            if (Main.overview) {
                if (Main.overview.visible) {
                    Main.overview.hide();
                }
                if (!this._origOverviewShow) {
                    this._origOverviewShow = Main.overview.show ? Main.overview.show.bind(Main.overview) : null;
                    this._origOverviewToggle = Main.overview.toggle ? Main.overview.toggle.bind(Main.overview) : null;
                    this._origOverviewShowApps = Main.overview.showApps ? Main.overview.showApps.bind(Main.overview) : null;
                    this._origOverviewIsDummy = Main.overview.isDummy;
                }
                Main.overview.isDummy = true;
                Main.overview.show = () => {};
                Main.overview.toggle = () => {};
                Main.overview.showApps = () => {};
            }
        } catch (e) {
            logError(e, 'FocusLockout overview neutralize error');
        }

        // 6. Disable Hot Corners pressure barriers & interface setting
        try {
            if (Main.layoutManager && Main.layoutManager.hotCorners) {
                for (const hc of Main.layoutManager.hotCorners) {
                    if (hc && typeof hc.setBarrierSize === 'function') {
                        hc.setBarrierSize(0);
                    }
                }
            }
            const is = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
            is.set_boolean('enable-hot-corners', false);
        } catch (_) {}

        // 7. Neutralize Super key in Mutter
        try {
            const ms = new Gio.Settings({schema_id: 'org.gnome.mutter'});
            ms.set_string('overlay-key', '');
        } catch (_) {}
    }

    _restoreDesktopPanels() {
        // 1. Unhook Top Bar and Activities notify guards
        if (this._actNotifyHook) {
            try {
                const act = Main.panel?.statusArea?.activities;
                if (act) act.disconnect(this._actNotifyHook);
            } catch (_) {}
            this._actNotifyHook = 0;
        }
        if (this._panelNotifyHook && Main.panel) {
            try { Main.panel.disconnect(this._panelNotifyHook); } catch (_) {}
            this._panelNotifyHook = 0;
        }
        if (this._panelBoxNotifyHook && Main.layoutManager.panelBox) {
            try { Main.layoutManager.panelBox.disconnect(this._panelBoxNotifyHook); } catch (_) {}
            this._panelBoxNotifyHook = 0;
        }

        // 2. Load original state from persistent backup (or fallback to defaults)
        const backup = this._loadPanelsBackup();

        // 3. Restore GNOME Activities Button
        try {
            const act = Main.panel?.statusArea?.activities;
            if (act) {
                act.visible = backup ? (backup.actVisible ?? true) : true;
                act.reactive = backup ? (backup.actReactive ?? true) : true;
                act.opacity = backup ? (backup.actOpacity ?? 255) : 255;
                if (act.container) {
                    act.container.visible = true;
                    act.container.reactive = true;
                    act.container.opacity = 255;
                }
            }
        } catch (e) {
            logError(e, 'FocusLockout Activities button restore error');
        }

        // 4. Restore Top Bar
        try {
            if (Main.panel) {
                Main.panel.visible = backup ? backup.panelVisible : true;
                Main.panel.reactive = backup ? backup.panelReactive : true;
                Main.panel.opacity = backup ? backup.panelOpacity : 255;
            }
            if (Main.layoutManager.panelBox) {
                Main.layoutManager.panelBox.visible = backup ? backup.panelBoxVisible : true;
                Main.layoutManager.panelBox.reactive = backup ? backup.panelBoxReactive : true;
                Main.layoutManager.panelBox.opacity = backup ? backup.panelBoxOpacity : 255;
            }
            if (typeof Main.layoutManager._queueUpdateRegions === 'function') {
                Main.layoutManager._queueUpdateRegions();
            }
        } catch (e) {
            logError(e, 'FocusLockout Top Bar restore error');
        }

        // 5. Restore Ubuntu Dock & Desktop Icons without modifying user preferences
        try {
            const dockUuid = (backup && backup.dockUuid) || (this._env && this._env.dockUuid) || 'ubuntu-dock@ubuntu.com';
            const shouldEnableDock = backup ? backup.dockEnabled : true;
            const desktopUuidsToEnable = (backup && backup.desktopIconUuids)
                ? backup.desktopIconUuids
                : (backup && backup.dingEnabled ? ['ding@rastersoft.com'] : []);

            if (shouldEnableDock) {
                if (Main.extensionManager && typeof Main.extensionManager.enableExtension === 'function') {
                    try { Main.extensionManager.enableExtension(dockUuid); } catch (_) {}
                }
                GLib.spawn_command_line_async(`gnome-extensions enable ${dockUuid}`);
            }

            for (const uuid of desktopUuidsToEnable) {
                if (Main.extensionManager && typeof Main.extensionManager.enableExtension === 'function') {
                    try { Main.extensionManager.enableExtension(uuid); } catch (_) {}
                }
                GLib.spawn_command_line_async(`gnome-extensions enable ${uuid}`);
            }

            // Restore desktop background reactivity
            try {
                if (Main.layoutManager) {
                    if (Main.layoutManager._backgroundGroup) {
                        Main.layoutManager._backgroundGroup.reactive = true;
                    }
                    if (Main.layoutManager._bgManagers) {
                        for (const bgMgr of Main.layoutManager._bgManagers) {
                            if (bgMgr && bgMgr.backgroundActor) {
                                bgMgr.backgroundActor.reactive = true;
                            }
                        }
                    }
                }
            } catch (_) {}

            // Also restore stage actors if any exist
            const docks = this._findDockActors(global.stage);
            for (const dock of docks) {
                dock.visible = true;
                dock.reactive = true;
                dock.opacity = 255;
            }
        } catch (e) {
            logError(e, 'FocusLockout Dock restore error');
        }

        // 6. Restore Activities Overview methods and state
        try {
            if (Main.overview) {
                if (this._origOverviewShow) Main.overview.show = this._origOverviewShow;
                if (this._origOverviewToggle) Main.overview.toggle = this._origOverviewToggle;
                if (this._origOverviewShowApps) Main.overview.showApps = this._origOverviewShowApps;
                Main.overview.isDummy = this._origOverviewIsDummy !== undefined
                    ? this._origOverviewIsDummy
                    : !Main.sessionMode.hasOverview;
                this._origOverviewShow = null;
                this._origOverviewToggle = null;
                this._origOverviewShowApps = null;
            }
        } catch (e) {
            logError(e, 'FocusLockout overview restore error');
        }

        // 7. Restore Hot Corners and Mutter overlay-key from backup
        try {
            if (backup && backup.hotCornersEnabled !== undefined) {
                const is = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
                is.set_boolean('enable-hot-corners', backup.hotCornersEnabled);
            } else {
                // No backup (crash / stale file): fall back to the schema
                // defaults instead of leaving hot corners disabled.
                try {
                    const is = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
                    is.reset('enable-hot-corners');
                } catch (_) {}
            }
            if (Main.layoutManager && typeof Main.layoutManager._updateHotCorners === 'function') {
                Main.layoutManager._updateHotCorners();
            }
            if (backup && backup.mutterOverlayKey !== undefined) {
                const ms = new Gio.Settings({schema_id: 'org.gnome.mutter'});
                ms.set_string('overlay-key', backup.mutterOverlayKey);
            } else {
                // Never leave the Super key disabled if the backup is missing.
                try {
                    const ms = new Gio.Settings({schema_id: 'org.gnome.mutter'});
                    ms.reset('overlay-key');
                } catch (_) {}
            }
        } catch (_) {}

        // 8. Clean up backup file
        this._deletePanelsBackup();
    }

    _readDeadline() {
        try {
            const [runtimeOk, runtimeBytes] = GLib.file_get_contents('/run/focus-reminder/lockout.json');
            if (runtimeOk) {
                const runtime = JSON.parse(new TextDecoder().decode(runtimeBytes));
                const value = Number(runtime.deadline);
                if (Number.isFinite(value) && value > 0)
                    return value;
            }
        } catch (_) {
        }
        return 0;
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
            const [runtimeOk, runtimeBytes] = GLib.file_get_contents('/run/focus-reminder/lockout.json');
            if (runtimeOk) {
                const runtime = JSON.parse(new TextDecoder().decode(runtimeBytes));
                if (runtime.study_mapped === true) {
                    const pid = Number(runtime.study_pid);
                    if (pid > 0) {
                        const [pidOk, pidBytes] = GLib.file_get_contents(`/proc/${pid}/cmdline`);
                        if (pidOk) {
                            const cmdline = new TextDecoder().decode(pidBytes).toLowerCase();
                            if (cmdline.includes('focus-reminder') || cmdline.includes('python'))
                                return true;
                        }
                    }
                    return false;
                }
            }
            const [ok, bytes] = GLib.file_get_contents(this._studyActivePath);
            if (!ok)
                return false;
            const ts = Number(new TextDecoder().decode(bytes).trim());
            const age = Date.now() / 1000 - ts;
            return Number.isFinite(ts) && age >= 0 && age < 6;
        } catch (_) {
            return false;
        }
    }

    _isStudyStarting() {
        try {
            const [runtimeOk, runtimeBytes] = GLib.file_get_contents('/run/focus-reminder/lockout.json');
            if (runtimeOk) {
                const runtime = JSON.parse(new TextDecoder().decode(runtimeBytes));
                if (runtime.study_starting === true)
                    return true;
                if (runtime.study_mapped === true)
                    return false;
            }
            const [ok, bytes] = GLib.file_get_contents(this._studyStartingPath);
            if (!ok)
                return false;
            const ts = Number(new TextDecoder().decode(bytes).trim());
            const age = Date.now() / 1000 - ts;
            return Number.isFinite(ts) && age >= 0 && age < 30;
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
        const lockoutActive = end > now;
        const isStudySuspended = this._getStudySuspendedUntil() > now;
        // study-active means the Study window has mapped; study-starting means
        // it is still loading. Both must lock the desktop down, but only the
        // mapped window is allowed to hide the lockout overlay.
        const studyMapped = this._isStudyActive() && !isStudySuspended;
        const studyStarting = this._isStudyStarting() && !isStudySuspended;
        const studyBusy = studyMapped || studyStarting;

        if (lockoutActive || studyBusy)
            this._setLockdownActive(true);
        else
            this._setLockdownActive(false);

        if (lockoutActive || studyBusy) {
            const sweepNow = Date.now();
            if (sweepNow - (this._lastSettingsSweep || 0) >= 2000) {
                this._lastSettingsSweep = sweepNow;
                this._sweepSettingsApps();
            }
        }

        if (!lockoutActive) {
            // No countdown: make sure no stale overlay stays on screen.
            if (this._root) {
                this._hideOverlay();
            } else {
                this._removeFlag();
            }
            if (!studyBusy) {
                try { GLib.unlink(this._studyActivePath); } catch (_) {}
                try { GLib.unlink(this._studyStartingPath); } catch (_) {}
                try { GLib.unlink(this._wifiActivePath); } catch (_) {}
            }
            return;
        }

        this._end = end;
        this._writeFlag();

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

        if (studyMapped) {
            // Study Mode is open on screen. Release the modal grab and hide
            // the overlay so the Study window is usable. The desktop remains
            // locked down (right-click/settings/extension-manager blocked).
            if (this._grab) {
                try {
                    Main.popModal(this._grab);
                } catch (_) {}
                this._grab = null;
            }
            if (this._root) {
                this._root.hide();
            }
            return;
        }

        // Study Mode is NOT active (or is still starting). Keep the overlay up
        // until the Study window actually maps, so the desktop is never shown.
        // 1. Make this._root visible and move it to the very TOP of uiGroup:
        if (this._root) {
            this._root.show();
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

        // Block right-click and middle-click context menus on lockout screen
        this._root.connect('button-press-event', (_actor, event) => {
            const btn = event.get_button();
            if (btn === 3 || btn === 2) {
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._root.connect('button-release-event', (_actor, event) => {
            const btn = event.get_button();
            if (btn === 3 || btn === 2) {
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        // Block all keyboard bypass, but allow typing in wifi password entry
        this._root.connect('key-press-event', (actor, event) => {
            const focus = global.stage.get_key_focus();
            if (this._wifiPassEntry) {
                if (focus === this._wifiPassEntry ||
                    focus === this._wifiPassEntry.clutter_text ||
                    (this._wifiPassEntry.contains && this._wifiPassEntry.contains(focus))) {
                    return Clutter.EVENT_PROPAGATE;
                }
            }
            return Clutter.EVENT_STOP;
        });
        this._root.connect('key-release-event', (actor, event) => {
            const focus = global.stage.get_key_focus();
            if (this._wifiPassEntry) {
                if (focus === this._wifiPassEntry ||
                    focus === this._wifiPassEntry.clutter_text ||
                    (this._wifiPassEntry.contains && this._wifiPassEntry.contains(focus))) {
                    return Clutter.EVENT_PROPAGATE;
                }
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
                const script = this._getScriptPath();
                GLib.spawn_command_line_async(
                    `python3 ${GLib.shell_quote(script)} --study`);
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
            hint_text: 'Enter Wi-Fi Password (leave empty if saved)',
            can_focus: true,
            style: 'background-color: #0f172a; color: #ffffff; border: 1px solid #3b82f6; border-radius: 6px; padding: 8px 12px; font-size: 14px;',
        });
        this._wifiPassEntry.clutter_text.set_password_char('\u25cf');
        this._wifiPassEntry.clutter_text.connect('key-press-event', (o, e) => {
            if (e.get_key_symbol() === Clutter.KEY_Escape) {
                this._wifiPassBox.hide();
                global.stage.set_key_focus(this._root);
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

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
            global.stage.set_key_focus(this._root);
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

        const savedConns = new Set();
        try {
            const [savedOk, savedOut] = GLib.spawn_command_line_sync('nmcli -t -f NAME,TYPE connection show');
            if (savedOk && savedOut) {
                const text = new TextDecoder().decode(savedOut);
                for (const line of text.split('\n')) {
                    const parts = line.trim().split(':');
                    if (parts.length >= 2 && parts[1] === '802-11-wireless') {
                        savedConns.add(parts[0]);
                    }
                }
            }
        } catch (_) {}

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
                    const isSaved = savedConns.has(ssid);
                    networks.push({ inUse, ssid, signal, security, isSaved });
                }

                networks.sort((a, b) => {
                    if (a.inUse) return -1;
                    if (b.inUse) return 1;
                    if (a.isSaved && !b.isSaved) return -1;
                    if (!a.isSaved && b.isSaved) return 1;
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
                    const savedTag = net.isSaved ? ' (Saved)' : '';

                    const ssidBtn = new St.Button({
                        label: `${inUseIcon}${lockIcon}${net.ssid}${savedTag}`,
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
                                this._wifiTargetSsidLbl.set_text(
                                    net.isSaved
                                        ? `Connect to: ${net.ssid} (Saved — leave password blank or enter new)`
                                        : `Connect to: ${net.ssid} (${net.security})`
                                );
                                this._wifiPassEntry.set_text('');
                                this._wifiPassBox.show();
                                this._wifiPassEntry.clutter_text.grab_key_focus();
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

    _getScriptPath() {
        // Prefer whichever real script exists: installed package first, then
        // the development checkout(s). Never return a non-existent path.
        const home = GLib.get_home_dir();
        const candidates = [
            '/opt/focus-reminder/focus-reminder.py',
            GLib.build_filenamev([home, 'projects', 'focus-control', 'focus-reminder.py']),
            GLib.build_filenamev([home, 'MediSparkBD', 'focus-control', 'focus-reminder.py']),
            GLib.build_filenamev([home, 'MediSparkBD', 'focus-control', 'focus-reminder-ubuntu.py']),
            GLib.build_filenamev([home, 'focus-control', 'focus-reminder.py']),
        ];
        let best = null;
        let bestTime = -1;
        for (const path of candidates) {
            try {
                if (!GLib.file_test(path, GLib.FileTest.EXISTS))
                    continue;
                const info = Gio.File.new_for_path(path).query_info(
                    'time::modified', Gio.FileQueryInfoFlags.NONE, null);
                const stamp = info.get_modification_date_time();
                const t = stamp ? stamp.to_unix() : 0;
                if (t > bestTime) {
                    best = path;
                    bestTime = t;
                }
            } catch (_) {}
        }
        return best || candidates[0];
    }

    _connectWifi(ssid, password) {
        if (!ssid) return;
        if (this._wifiPassBox) this._wifiPassBox.hide();
        global.stage.set_key_focus(this._root);
        if (this._wifiStatusLbl) {
            this._wifiStatusLbl.set_text(`⏳ Connecting to "${ssid}"…`);
        }
        const script = this._getScriptPath();
        const argv = password
            ? ['python3', script, '--connect-wifi', ssid, password]
            : ['python3', script, '--connect-wifi', ssid];

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
                    const msg = (stdout || stderr || 'Unknown error').trim().replace(/^ERROR:\s*/, '');
                    if (this._wifiStatusLbl) {
                        this._wifiStatusLbl.set_text(`❌ ${msg}`);
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
