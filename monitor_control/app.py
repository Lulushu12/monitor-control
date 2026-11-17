"""Monitor Control: a tray panel for the monitor's settings, instead of its button menu.

Run with no arguments to start (or show the panel if it's already running). With arguments,
it sends a command to the running instance:

    monitor-control show | toggle | refresh
    monitor-control brightness 40 | +10 | -10
    monitor-control profile Night
"""

import os
import shlex
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

from . import ddc, kde, profiles, theme
from .settings import Settings
from .settings_ui import PREFERENCES_KEY, SettingsWindow
from .ui import Panel

# An absolute path: QLocalServer otherwise uses TMPDIR, which nix-shell changes per invocation.
SOCKET_NAME = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                           "monitor-control.sock")
VCP = {"brightness": ddc.BRIGHTNESS, "contrast": ddc.CONTRAST, "color_preset": ddc.COLOR_PRESET,
       "gain_red": ddc.GAIN_RED, "gain_green": ddc.GAIN_GREEN, "gain_blue": ddc.GAIN_BLUE,
       "input_source": ddc.INPUT_SOURCE, "display_mode": ddc.DISPLAY_MODE,
       "shadow_control": ddc.SHADOW_CONTROL, "game_color": ddc.GAME_COLOR,
       "overdrive": ddc.OVERDRIVE, "low_blue": ddc.LOW_BLUE}
KEY_FOR_CODE = {code: key for key, code in VCP.items()}
AUTOSTART = Path.home() / ".config/autostart/monitor-control.desktop"


class Controller(QObject):
    state_changed = Signal()
    profiles_changed = Signal()
    status = Signal(str, str, str, object)  # text, kind, action text, callback
    _read_done = Signal(object, object, float)  # values, error, monotonic time the read began

    def __init__(self, settings=None, autostart=None):
        super().__init__()
        self.settings = settings or Settings()
        self.autostart = autostart
        self.settings_window = None
        self.state, self.maxima = {}, {}
        self.model = ""
        self.bus = None
        self.writer = None
        self.active_profile = None
        self._reading = False
        self._touched = {}  # key -> monotonic time of the last user change
        self._read_done.connect(self._on_read)
        self._refresh_timer = QTimer(self, singleShot=True, interval=400)
        self._refresh_timer.timeout.connect(self.refresh)

        self.brightness = kde.PowerDevilBrightness()
        self.brightness.changed.connect(self._on_brightness)
        self.shortcuts = kde.GlobalShortcuts()
        self.shortcuts.triggered.connect(self.on_shortcut)
        try:
            self.profiles = profiles.load()
        except profiles.ProfileError as e:
            self.profiles = []
            QTimer.singleShot(0, lambda: self.status.emit(
                f"Couldn't read {profiles.PROFILES_FILE}: {e} Fix or remove the file; "
                "nothing was overwritten.", "error", "", None))
            self._profiles_broken = True
        else:
            self._profiles_broken = False

    # Monitor I/O
    def _codes(self):
        # Without PowerDevil, brightness is read and written over DDC like everything else.
        return ddc.READ_CODES + ([] if self.brightness.available else [ddc.BRIGHTNESS])

    def connect_monitor(self):
        if self._reading:
            return
        codes, started = self._codes(), time.monotonic()

        def work():
            try:
                bus, model = ddc.detect()
                self._bus_found = (bus, model)
                self._read_done.emit(ddc.read_values(bus, codes), None, started)
            except ddc.DdcError as e:
                self._read_done.emit(None, str(e), started)
        self._reading = True
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        if self.bus is None:
            return self.connect_monitor()
        if self._reading:
            return
        self._reading = True
        bus, codes, started = self.bus, self._codes(), time.monotonic()

        def work():
            try:
                self._read_done.emit(ddc.read_values(bus, codes), None, started)
            except ddc.DdcError as e:
                self._read_done.emit(None, str(e), started)
        threading.Thread(target=work, daemon=True).start()

    def _on_read(self, values, error, started):
        self._reading = False
        if error:
            self.status.emit(f"Couldn't reach the monitor: {error} Check the cable and that "
                             "DDC/CI is on in the monitor's menu.", "error", "Retry", self.refresh)
            return
        if self.bus is None:
            self.bus, self.model = self._bus_found
            self.writer = ddc.DdcWriter(self.bus)
            self.writer.failed.connect(lambda msg: self.status.emit(
                f"A setting didn't reach the monitor: {msg}", "error", "Retry", self.refresh))
            self.writer.idle.connect(self._refresh_timer.start)
            self.status.emit("", "info", "", None)
        for code, (value, maximum) in values.items():
            key = KEY_FOR_CODE.get(code)
            if key:
                if maximum:
                    self.maxima[key] = maximum
                # A value read before the user's latest change is stale; keep the newer one.
                if self._touched.get(key, 0) < started:
                    self.state[key] = value
        if self.brightness.available and self._touched.get("brightness", 0) < started:
            b = self.brightness.get()
            if b is not None:
                self.state["brightness"] = b
        self.state_changed.emit()

    def _on_brightness(self, percent):
        if self.state.get("brightness") != percent:
            self.state["brightness"] = percent
            self.state_changed.emit()

    def set(self, key, value, show_indicator=False):
        self.state[key] = value
        self._touched[key] = time.monotonic()
        self.active_profile = None
        if key == "brightness" and self.brightness.available:
            self.brightness.set(value, show_indicator)
        elif self.writer:
            self.writer.set(VCP[key], value)
        if key == "color_preset":
            self.state_changed.emit()  # shows or hides the RGB sliders

    # App settings window
    def open_settings(self):
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.settings, self.autostart)
        self.settings_window.open()

    def settings_window_visible(self):
        return self.settings_window is not None and self.settings_window.isVisible()

    # Profiles
    def snapshot(self):
        keys = ["brightness", "contrast", "shadow_control", "game_color", "color_preset",
                "display_mode", "overdrive", "low_blue"]
        if self.state.get("color_preset") == ddc.COLOR_PRESET_USER:
            keys += ["gain_red", "gain_green", "gain_blue"]
        return {k: self.state[k] for k in keys if k in self.state}

    def _apply(self, settings):
        for key in profiles.SETTING_KEYS:  # preset before gains, so gains land on User
            if key in settings:
                self.set(key, settings[key])
        self.state_changed.emit()

    def apply_profile(self, index, osd=False):
        if not 0 <= index < len(self.profiles):
            return
        p = self.profiles[index]
        before = self.snapshot()
        self._apply(p["settings"])
        self.active_profile = p["name"]
        self.profiles_changed.emit()
        if osd and self.settings["profile_osd"]:
            kde.show_osd(f"Profile: {p['name']}")
        self.status.emit(f"Applied {p['name']}.", "ok", "Undo", lambda: self._undo_apply(before))

    def _undo_apply(self, before):
        self._apply(before)
        self.active_profile = None
        self.profiles_changed.emit()
        self.status.emit("Restored the previous settings.", "info", "", None)

    def _store(self):
        if self._profiles_broken:
            self.status.emit("Profiles file is broken, so changes aren't saved. Fix or remove "
                             f"{profiles.PROFILES_FILE}.", "error", "", None)
            return False
        profiles.save(self.profiles)
        self.register_profile_shortcuts()
        self.profiles_changed.emit()
        return True

    def save_profile(self, name):
        existing = [i for i, p in enumerate(self.profiles) if p["name"].lower() == name.lower()]
        if existing:
            return self.update_profile(existing[0])
        self.profiles.append({"name": name, "settings": self.snapshot()})
        self.active_profile = name
        if self._store():
            self.status.emit(f"Saved {name}.", "ok", "", None)

    def update_profile(self, index):
        old = dict(self.profiles[index])
        self.profiles[index] = {"name": old["name"], "settings": self.snapshot()}
        self.active_profile = old["name"]
        if self._store():
            self.status.emit(f"Updated {old['name']} with the current settings.", "ok", "Undo",
                             lambda: self._restore_profile(index, old, replace=True))

    def rename_profile(self, index, name):
        self.profiles[index]["name"] = name
        self._store()

    def delete_profile(self, index):
        removed = self.profiles.pop(index)
        if self._store():
            self.status.emit(f"Deleted {removed['name']}.", "info", "Undo",
                             lambda: self._restore_profile(index, removed))

    def _restore_profile(self, index, profile, replace=False):
        if replace:
            self.profiles[index] = profile
        else:
            self.profiles.insert(index, profile)
        if self._store():
            self.status.emit(f"Restored {profile['name']}.", "info", "", None)

    def import_profiles(self, path):
        try:
            incoming = profiles.parse(Path(path).read_text())
        except (OSError, profiles.ProfileError) as e:
            self.status.emit(f"Couldn't import {Path(path).name}: {e}", "error", "", None)
            return
        before = [dict(p) for p in self.profiles]
        names = {p["name"].lower(): i for i, p in enumerate(self.profiles)}
        for p in incoming:  # same name replaces, new names are added
            if p["name"].lower() in names:
                self.profiles[names[p["name"].lower()]] = p
            else:
                self.profiles.append(p)
        self._profiles_broken = False
        if self._store():
            self.status.emit(f"Imported {len(incoming)} profile(s).", "ok", "Undo",
                             lambda: self._replace_all(before))

    def _replace_all(self, items):
        self.profiles = items
        if self._store():
            self.status.emit("Import undone.", "info", "", None)

    def export_profiles(self, path):
        try:
            profiles.save(self.profiles, path)
        except OSError as e:
            self.status.emit(f"Couldn't export: {e}", "error", "", None)
        else:
            self.status.emit(f"Exported {len(self.profiles)} profile(s) to {Path(path).name}.",
                             "ok", "", None)

    # Shortcuts
    def register_shortcuts(self):
        M, C, A = kde.META, kde.CTRL, kde.ALT
        self.shortcuts.register("show-panel", "Show the monitor panel", M | A | kde.KEY_M)
        self.shortcuts.register("brightness-up", "Monitor brightness up", M | C | kde.KEY_PGUP)
        self.shortcuts.register("brightness-down", "Monitor brightness down", M | C | kde.KEY_PGDN)
        self.register_profile_shortcuts()

    def register_profile_shortcuts(self):
        for n in range(1, profiles.MAX_SHORTCUT_PROFILES + 1):
            action = f"profile-{n}"
            if n <= len(self.profiles):
                self.shortcuts.register(action, f"Apply profile {n} ({self.profiles[n - 1]['name']})",
                                        kde.META | kde.ALT | (0x30 + n))
            elif action in self.shortcuts.active_keys:
                self.shortcuts.unregister(action)

    def on_shortcut(self, action):
        if action == "show-panel":
            self.panel.toggle()
        elif action in ("brightness-up", "brightness-down"):
            step = self.settings["brightness_step"] * (1 if action == "brightness-up" else -1)
            self.adjust_brightness(step)
        elif action.startswith("profile-"):
            self.apply_profile(int(action.split("-")[1]) - 1, osd=True)

    def adjust_brightness(self, delta=None, absolute=None):
        current = self.state.get("brightness", 50)
        value = absolute if absolute is not None else current + delta
        value = max(0, min(100, value))
        self.set("brightness", value, show_indicator=True)
        if not self.brightness.available:
            kde.show_osd(f"Brightness {value}%", "video-display-brightness")
        self.state_changed.emit()

    # Commands from a second `monitor-control …` invocation
    def run_command(self, args):
        if not args or args[0] in ("show", "toggle"):
            self.panel.popup() if not args or args[0] == "show" else self.panel.toggle()
        elif args[0] == "refresh":
            self.refresh()
        elif args[0] == "brightness" and len(args) == 2:
            v = args[1]
            if v[0] in "+-":
                self.adjust_brightness(int(v))
            else:
                self.adjust_brightness(absolute=int(v))
        elif args[0] == "profile" and len(args) >= 2:
            name = " ".join(args[1:]).lower()
            for i, p in enumerate(self.profiles):
                if p["name"].lower() == name:
                    return self.apply_profile(i, osd=True)
            return f"No profile named {' '.join(args[1:])}."
        else:
            return __doc__.split("\n\n", 1)[1].strip()


class Autostart(QObject):
    """"Start at login", shared by the tray menu and the settings window. The autostart file's
    existence is the setting, so it can't drift from what Plasma actually does."""

    changed = Signal(bool)

    def __init__(self, exec_line):
        super().__init__()
        self.exec_line = exec_line

    def enabled(self):
        return AUTOSTART.exists()

    def set(self, on):
        if on == self.enabled():
            return
        if on:
            AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART.write_text("[Desktop Entry]\nType=Application\nName=Monitor Control\n"
                                 f"Exec={self.exec_line}\nIcon=video-display\n"
                                 "X-KDE-autostart-phase=2\n")
        else:
            AUTOSTART.unlink()
        self.changed.emit(on)


def build_tray(app, c):
    tray = QSystemTrayIcon(theme.tray_icon(app), app)
    app.styleHints().colorSchemeChanged.connect(lambda _: tray.setIcon(theme.tray_icon(app)))
    tray.setToolTip("Monitor Control")
    menu = QMenu()
    key = c.shortcuts.key_text("show-panel")
    menu.addAction(f"Open panel\t{key}", c.panel.popup)
    profiles_menu = menu.addMenu("Apply profile")
    menu.addAction("Refresh from monitor", c.refresh)
    menu.addSeparator()
    menu.addAction("Import profiles…", lambda: _pick(c.import_profiles, save=False))
    menu.addAction("Export profiles…", lambda: _pick(c.export_profiles, save=True))
    menu.addSeparator()
    login = QAction("Start at login", menu, checkable=True, checked=c.autostart.enabled())
    login.toggled.connect(c.autostart.set)
    c.autostart.changed.connect(lambda on: login.isChecked() != on and login.setChecked(on))
    menu.addAction(login)
    menu.addAction(f"Settings…\t{PREFERENCES_KEY}", c.open_settings)
    menu.addAction("Quit", app.quit)

    def fill_profiles():
        profiles_menu.clear()
        group = QActionGroup(profiles_menu)
        for i, p in enumerate(c.profiles):
            k = c.shortcuts.key_text(f"profile-{i + 1}")
            a = profiles_menu.addAction(f"{p['name']}\t{k}", lambda n=i: c.apply_profile(n))
            a.setCheckable(True)
            a.setChecked(p["name"] == c.active_profile)
            group.addAction(a)
        profiles_menu.setEnabled(bool(c.profiles))
    c.profiles_changed.connect(fill_profiles)
    fill_profiles()

    def tooltip():
        b = c.state.get("brightness")
        tray.setToolTip("Monitor Control" + (f": brightness {b}%" if b is not None else ""))
    c.state_changed.connect(tooltip)

    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: c.panel.toggle()
                           if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    return tray


def _pick(callback, save):
    path = profiles.CONFIG_DIR / "profiles-export.json"
    if save:
        chosen, _ = QFileDialog.getSaveFileName(None, "Export profiles", str(path), "JSON (*.json)")
    else:
        chosen, _ = QFileDialog.getOpenFileName(None, "Import profiles", str(Path.home()), "JSON (*.json)")
    if chosen:
        callback(chosen)


def send_to_running(args):
    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if not sock.waitForConnected(300):
        return None
    sock.write(("\x1f".join(args) + "\n").encode())
    sock.waitForBytesWritten(1000)
    sock.waitForReadyRead(3000)
    reply = bytes(sock.readAll()).decode()
    sock.disconnectFromServer()
    return reply


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    args = [a for a in argv[1:] if a]
    # Wayland doesn't let a client place its own windows; under XWayland the panel can open
    # beside the tray icon. Set QT_QPA_PLATFORM=wayland to opt out (KWin then places it).
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    app = QApplication(argv)
    app.setApplicationName("monitor-control")
    app.setApplicationDisplayName("Monitor Control")
    app.setDesktopFileName("monitor-control")
    app.setQuitOnLastWindowClosed(False)

    reply = send_to_running(args)
    if reply is not None:
        if reply.strip():
            print(reply.strip())
        return 0
    if args and args[0] not in ("show", "toggle"):
        print("Monitor Control isn't running. Start it with `monitor-control` first.", file=sys.stderr)
        return 1

    # What "Start at login" runs: the launcher if there is one (a nix-shell interpreter path
    # would be garbage-collected), else this interpreter.
    launcher = os.environ.get("MONITOR_CONTROL_EXEC")
    exec_line = shlex.join([launcher] if launcher else [sys.executable, "-m", "monitor_control"])
    settings = Settings()
    theme.apply(app, settings["theme"])
    app.styleHints().colorSchemeChanged.connect(lambda _: theme.apply(app, settings["theme"]))
    settings.changed.connect(lambda key: key == "theme" and theme.apply(app, settings["theme"]))

    c = Controller(settings, Autostart(exec_line))
    c.panel = Panel(c)
    if settings.load_error:
        c.status.emit(settings.load_error, "error", "", None)
    c.register_shortcuts()
    c.panel.rebuild_profiles()  # tooltips now know the active keys
    tray = build_tray(app, c)  # noqa: F841 (kept alive by reference)

    QLocalServer.removeServer(SOCKET_NAME)
    server = QLocalServer(app)
    server.listen(SOCKET_NAME)

    def on_client():
        sock = server.nextPendingConnection()

        def read():
            if not sock.canReadLine():
                return
            line = bytes(sock.readLine()).decode().rstrip("\n")
            out = c.run_command([a for a in line.split("\x1f") if a])
            sock.write(((out or "") + "\n").encode())
            sock.flush()
            sock.disconnectFromServer()
        sock.readyRead.connect(read)
    server.newConnection.connect(on_client)

    c.connect_monitor()
    if args and args[0] == "show":
        c.panel.popup()
    return app.exec()
