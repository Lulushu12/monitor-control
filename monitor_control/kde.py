"""Plasma integration: PowerDevil brightness, the Plasma OSD and kglobalaccel shortcuts.

Method calls go through busctl because it takes explicit D-Bus signatures (PySide6 can't marshal
the uint and int-array arguments these interfaces need). Signals are received with QtDBus.
"""

import json
import re
import shutil
import subprocess
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot, SLOT
from PySide6.QtDBus import QDBusConnection, QDBusServiceWatcher

BUSCTL = shutil.which("busctl") or "busctl"
CONTEXT = "monitor-control"

POWERDEVIL = "org.kde.Solid.PowerManagement"
BRIGHTNESS_PATH = "/org/kde/ScreenBrightness"
BRIGHTNESS_IFACE = "org.kde.ScreenBrightness"
DISPLAY_IFACE = "org.kde.ScreenBrightness.Display"
SUPPRESS_INDICATOR = 0x1

KGLOBALACCEL = "org.kde.kglobalaccel"
COMPONENT = "monitor-control"
COMPONENT_NAME = "Monitor Control"
IS_DEFAULT, SET_PRESENT = 0x1, 0x2

# Qt key codes, as kglobalaccel stores them.
META, CTRL, ALT = 0x10000000, 0x04000000, 0x08000000
KEY_PGUP, KEY_PGDN, KEY_M = 0x01000016, 0x01000017, 0x4D


def _busctl(*args, timeout=3):
    try:
        out = subprocess.run([BUSCTL, "--user", "--json=short", *args],
                             capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return json.loads(out.stdout)


def show_osd(text, icon="video-display"):
    _busctl("call", "org.kde.plasmashell", "/org/kde/osdService", "org.kde.osdService",
            "showText", "ss", icon, text)


class PowerDevilBrightness(QObject):
    """Brightness through PowerDevil, which already drives this monitor over DDC.

    Writing VCP 0x10 directly would fight PowerDevil: its idle dimming restores its own cached
    value. Going through it keeps the Plasma brightness slider, keys and OSD in sync.

    At login this app can start before PowerDevil has registered the monitor (it finishes
    initialising libddcutil about a second after it starts), so the display is looked up again
    when PowerDevil starts, when it reports a new display, and before use while none is known.
    """

    changed = Signal(int)  # percent
    RETRY_SECONDS = 2

    def __init__(self):
        super().__init__()
        self.path = None
        self.max = 10000
        self._last_try = 0.0
        self._pending = None
        self._pending_flags = 0
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self._flush)
        bus = QDBusConnection.sessionBus()
        # Matched by service name, so these also work if PowerDevil starts after us.
        for name, slot in (("BrightnessChanged", "_on_changed(QString,int,QString,QString)"),
                           ("DisplayAdded", "_on_display_added(QString)"),
                           ("DisplayRemoved", "_on_display_removed(QString)")):
            bus.connect(POWERDEVIL, BRIGHTNESS_PATH, BRIGHTNESS_IFACE, name, self, SLOT(slot))
        self._watcher = QDBusServiceWatcher(POWERDEVIL, bus,
                                            QDBusServiceWatcher.WatchForRegistration, self)
        self._watcher.serviceRegistered.connect(lambda _: self._discover(force=True))
        self._discover(force=True)

    def _discover(self, force=False):
        """Find PowerDevil's external display. Returns whether one is known."""
        if self.path:
            return True
        if not force and time.monotonic() - self._last_try < self.RETRY_SECONDS:
            return False
        self._last_try = time.monotonic()
        names = _busctl("get-property", POWERDEVIL, BRIGHTNESS_PATH, BRIGHTNESS_IFACE,
                        "DisplaysDBusNames")
        for name in (names or {}).get("data", []):
            path = f"{BRIGHTNESS_PATH}/{name}"
            internal = _busctl("get-property", POWERDEVIL, path, DISPLAY_IFACE, "IsInternal")
            if internal and not internal["data"]:
                maximum = _busctl("get-property", POWERDEVIL, path, DISPLAY_IFACE, "MaxBrightness")
                self.max = (maximum or {}).get("data") or 10000
                self.path = path
                break
        if self.path:
            value = self.get()
            if value is not None:
                self.changed.emit(value)
        return self.path is not None

    @property
    def available(self):
        return self._discover()

    def get(self):
        if not self.path:
            return None
        reply = _busctl("get-property", POWERDEVIL, self.path, DISPLAY_IFACE, "Brightness")
        return round(reply["data"] * 100 / self.max) if reply else None

    def set(self, percent, show_indicator=False):
        self._pending = round(max(0, min(100, percent)) * self.max / 100)
        self._pending_flags = 0 if show_indicator else SUPPRESS_INDICATOR
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self):
        if self._pending is not None and self._discover():
            _busctl("call", POWERDEVIL, self.path, DISPLAY_IFACE, "SetBrightnessWithContext",
                    "ius", str(self._pending), str(self._pending_flags), CONTEXT)
            self._pending = None

    @Slot(str, int, str, str)
    def _on_changed(self, display, value, client, context):
        if self.path and self.path.endswith("/" + display):
            self.changed.emit(round(value * 100 / self.max))

    @Slot(str)
    def _on_display_added(self, display):
        self._discover(force=True)

    @Slot(str)
    def _on_display_removed(self, display):
        if self.path and self.path.endswith("/" + display):
            self.path = None


class GlobalShortcuts(QObject):
    """Registers actions with kglobalaccel, the way KGlobalAccel does for native KDE apps.

    Each action gets a default key; if the user rebinds it in System Settings → Shortcuts,
    kglobalaccel keeps their binding (SET_PRESENT without NoAutoloading loads the saved key).
    """

    triggered = Signal(str)  # action id

    def __init__(self):
        super().__init__()
        self.active_keys = {}
        object_path = "/component/" + re.sub(r"[^A-Za-z0-9_]", "_", COMPONENT)
        QDBusConnection.sessionBus().connect(
            KGLOBALACCEL, object_path, "org.kde.kglobalaccel.Component", "globalShortcutPressed",
            self, SLOT("_on_pressed(QString,QString,qlonglong)"))

    def register(self, action, friendly, default_key=0):
        """Register an action; returns the key that is active for it now (0 if none)."""
        ident = [COMPONENT, action, COMPONENT_NAME, friendly]
        keys = [str(default_key)] if default_key else []
        _busctl("call", KGLOBALACCEL, "/kglobalaccel", "org.kde.KGlobalAccel",
                "doRegister", "as", "4", *ident)
        _busctl("call", KGLOBALACCEL, "/kglobalaccel", "org.kde.KGlobalAccel",
                "setShortcut", "asaiu", "4", *ident, str(len(keys)), *keys, str(IS_DEFAULT))
        reply = _busctl("call", KGLOBALACCEL, "/kglobalaccel", "org.kde.KGlobalAccel",
                        "setShortcut", "asaiu", "4", *ident, str(len(keys)), *keys,
                        str(SET_PRESENT))
        active = (reply or {}).get("data", [[]])[0]
        self.active_keys[action] = active[0] if active else 0
        return self.active_keys[action]

    def unregister(self, action):
        _busctl("call", KGLOBALACCEL, "/kglobalaccel", "org.kde.KGlobalAccel",
                "unregister", "ss", COMPONENT, action)
        self.active_keys.pop(action, None)

    def key_text(self, action):
        """Human-readable key for tooltips, e.g. 'Meta+Alt+1'."""
        from PySide6.QtGui import QKeySequence
        key = self.active_keys.get(action, 0)
        return QKeySequence(key).toString(QKeySequence.NativeText) if key else ""

    @Slot(str, str, "qlonglong")
    def _on_pressed(self, component, action, timestamp):
        if component == COMPONENT:
            self.triggered.emit(action)
