"""Settings window for the app itself. Every change applies and saves immediately."""

from pathlib import Path

from PySide6.QtCore import QByteArray, QProcess, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .settings import CONTROLS, SETTINGS_FILE
from .theme import SPACE

PREFERENCES_KEY = "Ctrl+Shift+,"  # KDE's standard Preferences shortcut


def _label(text, name="FieldLabel", wrap=False):
    lbl = QLabel(text)
    lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def _combo_row(title, options, current, on_pick, tooltip=""):
    """Label on the left, combo on the right, like the panel's pickers."""
    combo = QComboBox()
    combo.setToolTip(tooltip)
    for value, text in options:
        combo.addItem(text, value)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.activated.connect(lambda i: on_pick(combo.itemData(i)))
    lbl = _label(title)
    lbl.setBuddy(combo)
    row = QHBoxLayout()
    row.addWidget(lbl, 1)
    row.addWidget(combo, 1)
    return row, combo


class SettingsWindow(QWidget):
    def __init__(self, settings, autostart):
        super().__init__(None, Qt.Window)
        self.s = settings
        self.autostart = autostart
        self.setObjectName("SettingsWindow")
        self.setWindowTitle("Monitor Control Settings")
        self.setMinimumWidth(480)

        body = QWidget()
        body.setObjectName("SettingsBody")
        col = QVBoxLayout(body)
        col.setContentsMargins(SPACE[5], SPACE[5], SPACE[5], SPACE[5])
        col.setSpacing(SPACE[5])
        col.addWidget(self._panel_group())
        col.addWidget(self._appearance_group())
        col.addWidget(self._keyboard_group())
        col.addWidget(self._startup_group())
        col.addWidget(_label(f"Changes save automatically to {self._home(SETTINGS_FILE)}.",
                             "Muted", wrap=True))
        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)
        if self.s["settings_geometry"]:
            self.restoreGeometry(QByteArray.fromBase64(self.s["settings_geometry"].encode()))
        else:
            self.resize(520, 720)

    @staticmethod
    def _home(path):
        return str(path).replace(str(Path.home()), "~", 1)

    def _group(self, title):
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(SPACE[4], SPACE[5], SPACE[4], SPACE[4])
        lay.setSpacing(SPACE[3])
        return box, lay

    def _check(self, text, checked, on_toggle, tooltip=""):
        box = QCheckBox(text)
        box.setChecked(checked)
        box.setToolTip(tooltip)
        box.toggled.connect(on_toggle)
        return box

    def _panel_group(self):
        box, lay = self._group("Panel")
        lay.addWidget(_label("Controls to show"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE[5])
        grid.setVerticalSpacing(SPACE[2])
        for i, (key, text) in enumerate(CONTROLS.items()):
            check = self._check(text, self.s.shown(key), lambda on, k=key: self.s.set_shown(k, on))
            grid.addWidget(check, i // 2, i % 2)
        lay.addLayout(grid)
        lay.addWidget(_label("Controls your monitor doesn't support stay hidden either way.",
                             "Muted", wrap=True))

        row, _ = _combo_row("Open the panel", [("pointer", "Next to the pointer"),
                                                ("remembered", "Where I last left it")],
                            self.s["panel_position"], lambda v: self.s.set("panel_position", v),
                            "Drag the panel by its header to move it")
        lay.addLayout(row)
        lay.addWidget(_label("Drag the panel by its header to move it.", "Muted", wrap=True))
        lay.addWidget(self._check("Close the panel when I click elsewhere",
                                  self.s["close_on_focus_loss"],
                                  lambda on: self.s.set("close_on_focus_loss", on),
                                  "Off: the panel stays open until Esc, the shortcut or a tray click"))
        lay.addWidget(self._check("Compact layout", self.s["compact"],
                                  lambda on: self.s.set("compact", on), "Tighter spacing"))
        return box

    def _appearance_group(self):
        box, lay = self._group("Appearance")
        row, _ = _combo_row("Theme", [("system", "Follow the system"), ("dark", "Dark"),
                                      ("light", "Light")],
                            self.s["theme"], lambda v: self.s.set("theme", v),
                            "The tray icon always follows the system, to match the panel")
        lay.addLayout(row)
        return box

    def _keyboard_group(self):
        box, lay = self._group("Keyboard")
        steps = sorted({1, 2, 5, 10, 15, 20, 25, self.s["brightness_step"]})
        row, _ = _combo_row("Brightness shortcut step", [(v, f"{v} %") for v in steps],
                            self.s["brightness_step"], lambda v: self.s.set("brightness_step", v),
                            "How much Meta+Ctrl+PgUp / PgDown change the brightness")
        lay.addLayout(row)
        lay.addWidget(self._check("Show a message when a shortcut applies a profile",
                                  self.s["profile_osd"], lambda on: self.s.set("profile_osd", on),
                                  "Plasma's on-screen display, e.g. \"Profile: Night\""))
        edit = QPushButton("Edit shortcuts in System Settings…")
        edit.setToolTip("Opens System Settings → Shortcuts; look for Monitor Control")
        edit.clicked.connect(self._edit_shortcuts)
        self.edit_error = _label("", "Status", wrap=True)
        self.edit_error.setProperty("kind", "error")
        self.edit_error.hide()
        lay.addWidget(edit, 0, Qt.AlignLeft)
        lay.addWidget(self.edit_error)
        return box

    def _startup_group(self):
        box, lay = self._group("Startup")
        self.login = self._check("Start Monitor Control when I log in", self.autostart.enabled(),
                                 self.autostart.set)
        self.autostart.changed.connect(self._sync_login)
        lay.addWidget(self.login)
        return box

    def _sync_login(self, on):
        if self.login.isChecked() != on:
            self.login.blockSignals(True)
            self.login.setChecked(on)
            self.login.blockSignals(False)

    def _edit_shortcuts(self):
        result = QProcess.startDetached("systemsettings", ["kcm_keys", "--args", "monitor-control"])
        ok = result[0] if isinstance(result, tuple) else bool(result)
        self.edit_error.setText("" if ok else "Couldn't start System Settings. Open it yourself and "
                                "go to Keyboard → Shortcuts → Monitor Control.")
        self.edit_error.setVisible(not ok)

    def open(self):
        self._sync_login(self.autostart.enabled())
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.s.set("settings_geometry", bytes(self.saveGeometry().toBase64()).decode())
        super().closeEvent(event)
