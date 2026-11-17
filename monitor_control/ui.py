"""The tray panel: sliders, pickers and profiles."""

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLayout,
                               QLineEdit, QMenu, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
                               QWidget)

from . import ddc
from .settings_ui import PREFERENCES_KEY
from .theme import SPACE

PANEL_WIDTH = 336


def _label(text, name="FieldLabel"):
    lbl = QLabel(text)
    lbl.setObjectName(name)
    return lbl


class SliderRow(QWidget):
    moved = Signal(int)

    def __init__(self, title, tooltip=""):
        super().__init__()
        self.title = _label(title)
        self.value = _label("–", "Value")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setPageStep(10)
        self.slider.setToolTip(tooltip)
        self.title.setBuddy(self.slider)
        self.slider.valueChanged.connect(self._changed)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(SPACE[1])
        grid.addWidget(self.title, 0, 0)
        grid.addWidget(self.value, 0, 1)
        grid.addWidget(self.slider, 1, 0, 1, 2)

    def _changed(self, v):
        self.value.setText(f"{v}")
        self.moved.emit(v)

    def set_value(self, v, maximum=None):
        """Programmatic update; never fights a drag in progress."""
        if self.slider.isSliderDown():
            return
        self.slider.blockSignals(True)
        if maximum:
            self.slider.setMaximum(maximum)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.value.setText(f"{v}")


class PickerRow(QWidget):
    picked = Signal(int)

    def __init__(self, title, options, tooltip=""):
        super().__init__()
        self.combo = QComboBox()
        self.combo.setToolTip(tooltip)
        for code, name in options.items():
            self.combo.addItem(name, code)
        self.combo.activated.connect(lambda i: self.picked.emit(self.combo.itemData(i)))
        lbl = _label(title)
        lbl.setBuddy(self.combo)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(lbl, 1)
        row.addWidget(self.combo, 1)

    def set_value(self, code):
        i = self.combo.findData(code)
        if i < 0:
            # Something the menu set that this app doesn't know (e.g. a new firmware value).
            self.combo.addItem(f"Other (0x{code:02x})", code)
            i = self.combo.count() - 1
        self.combo.setCurrentIndex(i)


class DragHandle(QWidget):
    """The panel header. Dragging it moves the frameless panel through the window manager."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Handle")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag to move the panel")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
            event.accept()
        else:
            super().mousePressEvent(event)


class Panel(QFrame):
    """Frameless panel that opens from the tray and hides when it loses focus."""

    def __init__(self, controller):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.c = controller
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Monitor Control")
        self._was_active = False

        # Qt doesn't paint stylesheet backgrounds on a translucent top-level window, so the
        # rounded card is an inner frame.
        card = self.card = QFrame(self)
        card.setObjectName("Panel")
        card.setFixedWidth(PANEL_WIDTH)
        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        # The window always matches its content, so rows that appear once the monitor answers
        # (or hide when it doesn't) never leave gaps.
        wrap.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        wrap.addWidget(card)
        outer = self.outer = QVBoxLayout(card)

        handle = DragHandle()
        head = QHBoxLayout(handle)
        head.setContentsMargins(0, 0, 0, 0)
        title = _label("Monitor", "Heading")
        self.model = _label("", "Muted")
        settings_btn = QPushButton("Settings")
        settings_btn.setObjectName("HeaderButton")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.setFocusPolicy(Qt.TabFocus)  # reachable by Tab, but never the initial focus
        settings_btn.setToolTip(f"App settings ({PREFERENCES_KEY})")
        settings_btn.clicked.connect(lambda: self.c.open_settings())
        head.addWidget(title)
        head.addWidget(self.model)
        head.addStretch(1)
        head.addWidget(settings_btn)
        outer.addWidget(handle)

        # Picture
        self.brightness = SliderRow("&Brightness", "Also follows Plasma's brightness keys")
        self.contrast = SliderRow("&Contrast")
        self.shadow = SliderRow("&Shadow control", "Brightens dark areas without washing out bright ones")
        self.game_color = SliderRow("Game colo&r", "Color saturation, 0–20")
        self.game_color.slider.setPageStep(2)
        self.red = SliderRow("Red")
        self.green = SliderRow("Green")
        self.blue = SliderRow("Blue")
        self.gains = QWidget()
        self.gains.setObjectName("Gains")
        g = QVBoxLayout(self.gains)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(SPACE[3])
        for row in (self.red, self.green, self.blue):
            g.addWidget(row)
        self.mode = PickerRow("&Game mode", ddc.DISPLAY_MODES, "The monitor's game picture modes")
        self.overdrive = PickerRow("O&verdrive", ddc.LEVELS,
                                   "Faster pixel response; Strong can add bright halos behind motion")
        self.low_blue = PickerRow("&Low blue light", ddc.LEVELS, "Warmer picture that cuts blue light")
        self.preset = PickerRow("C&olor", ddc.COLOR_PRESETS, "Pick User to set red, green and blue")
        self.input = PickerRow("&Input", ddc.INPUT_SOURCES,
                               "Switching shows the other input; this screen goes dark")
        self.sections = [self._section(self.brightness, self.contrast, self.shadow, self.game_color),
                         self._section(self.mode, self.overdrive, self.low_blue, self.preset,
                                       self.gains, self.input)]
        for box in self.sections:
            outer.addWidget(box)
        # Settings key → row. Vendor features also need the monitor to have answered for them.
        self.rows = {"brightness": self.brightness, "contrast": self.contrast,
                     "shadow_control": self.shadow, "game_color": self.game_color,
                     "display_mode": self.mode, "overdrive": self.overdrive,
                     "low_blue": self.low_blue, "color_preset": self.preset,
                     "input_source": self.input}
        self._vendor = {"shadow_control", "game_color", "overdrive", "low_blue"}

        # Profiles
        prof = QVBoxLayout()
        prof.setSpacing(SPACE[2])
        prof.addWidget(_label("Profiles"))
        self.profile_grid = QGridLayout()
        self.profile_grid.setSpacing(SPACE[2])
        prof.addLayout(self.profile_grid)
        self.save_btn = QPushButton("Save current settings…")
        self.save_btn.setToolTip("Save the picture settings as a profile (everything except input)")
        self.save_btn.clicked.connect(self._start_save)
        self.name_edit = QLineEdit(placeholderText="Profile name, then Enter")
        self.name_edit.returnPressed.connect(self._finish_save)
        self.name_edit.hide()
        prof.addWidget(self.save_btn)
        prof.addWidget(self.name_edit)
        outer.addLayout(prof)

        # Status line: what happened, plus Undo or Retry where it helps.
        status = QHBoxLayout()
        self.status = _label("", "Status")
        self.status.setWordWrap(True)
        self.status_action = QPushButton()
        self.status_action.setObjectName("Link")
        self.status_action.setCursor(Qt.PointingHandCursor)
        self.status_action.hide()
        self.status_action.clicked.connect(self._status_clicked)
        self._status_callback = None
        status.addWidget(self.status, 1)
        status.addWidget(self.status_action, 0, Qt.AlignTop)
        outer.addLayout(status)

        self.brightness.moved.connect(lambda v: self.c.set("brightness", v))
        self.contrast.moved.connect(lambda v: self.c.set("contrast", v))
        self.shadow.moved.connect(lambda v: self.c.set("shadow_control", v))
        self.game_color.moved.connect(lambda v: self.c.set("game_color", v))
        self.red.moved.connect(lambda v: self.c.set("gain_red", v))
        self.green.moved.connect(lambda v: self.c.set("gain_green", v))
        self.blue.moved.connect(lambda v: self.c.set("gain_blue", v))
        self.mode.picked.connect(lambda v: self.c.set("display_mode", v))
        self.overdrive.picked.connect(lambda v: self.c.set("overdrive", v))
        self.low_blue.picked.connect(lambda v: self.c.set("low_blue", v))
        self.preset.picked.connect(lambda v: self.c.set("color_preset", v))
        self.input.picked.connect(lambda v: self.c.set("input_source", v))
        self.c.state_changed.connect(self.sync)
        self.c.profiles_changed.connect(self.rebuild_profiles)
        self.c.status.connect(self.show_status)
        self.c.settings.changed.connect(self._on_setting)

        QShortcut(QKeySequence(Qt.Key_Escape), self, self._escape)
        QShortcut(QKeySequence(PREFERENCES_KEY), self, lambda: self.c.open_settings())
        self._save_pos = QTimer(self, singleShot=True, interval=500)
        self._save_pos.timeout.connect(
            lambda: self.c.settings.set("panel_pos", [self.x(), self.y()]))
        self._apply_density()
        self.rebuild_profiles()
        self.sync()

    def _section(self, *widgets):
        box = QFrame()
        box.setObjectName("Section")
        lay = QVBoxLayout(box)
        for w in widgets:
            lay.addWidget(w)
        return box

    def _fit(self):
        """Resize the window to its content right away, shrinking as well as growing. Hiding a
        row only marks its own section's layout stale, and the outer fixed-size constraint
        re-applies only when the outer layout recomputes, so refresh them innermost first."""
        for lay in reversed(self.card.findChildren(QLayout)):
            lay.activate()
        self.card.layout().activate()
        self.layout().invalidate()
        self.layout().activate()

    def _apply_density(self):
        compact = self.c.settings["compact"]
        pad, gap = (SPACE[3], SPACE[3]) if compact else (SPACE[4], SPACE[4])
        self.outer.setContentsMargins(SPACE[4] if compact else SPACE[5], pad,
                                      SPACE[4] if compact else SPACE[5], pad)
        self.outer.setSpacing(gap)
        for box in self.sections:
            box.layout().setContentsMargins(pad, pad, pad, pad)
            box.layout().setSpacing(gap)

    def _on_setting(self, key):
        if key == "compact":
            self._apply_density()
        if key in ("compact", "hidden_controls"):
            self.sync()  # ends with _fit()
        if key == "panel_position" and self.isVisible() \
                and self.c.settings["panel_position"] == "remembered":
            self._save_pos.start()  # the place it's in now is the one to remember

    # State → widgets
    def sync(self):
        s, maxima = self.c.state, self.c.maxima
        self.model.setText(self.c.model)
        for key, row in (("brightness", self.brightness), ("contrast", self.contrast),
                         ("shadow_control", self.shadow), ("game_color", self.game_color),
                         ("gain_red", self.red), ("gain_green", self.green),
                         ("gain_blue", self.blue)):
            if key in s:
                row.set_value(s[key], maxima.get(key))
        for key in ("display_mode", "overdrive", "low_blue", "color_preset", "input_source"):
            if key in s:
                self.rows[key].set_value(s[key])
        shown = {key: self.c.settings.shown(key) and (key not in self._vendor or key in s)
                 for key in self.rows}
        for key, row in self.rows.items():
            row.setVisible(shown[key])
        self.gains.setVisible(shown["color_preset"]
                              and s.get("color_preset") == ddc.COLOR_PRESET_USER)
        self.sections[0].setVisible(any(shown[k] for k in
                                        ("brightness", "contrast", "shadow_control", "game_color")))
        self.sections[1].setVisible(any(shown[k] for k in
                                        ("display_mode", "overdrive", "low_blue", "color_preset",
                                         "input_source")))
        self._fit()

    def rebuild_profiles(self):
        while self.profile_grid.count():
            self.profile_grid.takeAt(0).widget().deleteLater()
        if not self.c.profiles:
            hint = _label("Save the current settings to switch back in one click.", "Muted")
            hint.setWordWrap(True)
            self.profile_grid.addWidget(hint, 0, 0)
        for i, p in enumerate(self.c.profiles):
            btn = QPushButton(p["name"])
            btn.setObjectName("Profile")
            btn.setCheckable(True)
            btn.setChecked(self.c.active_profile == p["name"])
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            key = self.c.shortcuts.key_text(f"profile-{i + 1}")
            btn.setToolTip(f"Apply {p['name']}" + (f" ({key})" if key else "")
                           + ". Right-click to update, rename or delete.")
            btn.clicked.connect(lambda _=False, n=i: self.c.apply_profile(n))
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, n=i, b=btn: self._profile_menu(n, b, pos))
            self.profile_grid.addWidget(btn, i // 3, i % 3)
        self._fit()

    def _profile_menu(self, index, button, pos):
        name = self.c.profiles[index]["name"]
        menu = QMenu(self)
        menu.addAction(f"Apply {name}", lambda: self.c.apply_profile(index))
        menu.addAction("Update with current settings", lambda: self.c.update_profile(index))
        menu.addAction("Rename…", lambda: self._rename(index))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.c.delete_profile(index))
        menu.exec(button.mapToGlobal(pos))

    def _rename(self, index):
        old = self.c.profiles[index]["name"]
        new, ok = QInputDialog.getText(self, "Rename profile", "Name", text=old)
        if ok and new.strip():
            self.c.rename_profile(index, new.strip())

    def _start_save(self):
        self.save_btn.hide()
        self.name_edit.show()
        self.name_edit.setFocus()

    def _finish_save(self):
        name = self.name_edit.text().strip()
        self._end_save()
        if name:
            self.c.save_profile(name)

    def _end_save(self):
        self.name_edit.clear()
        self.name_edit.hide()
        self.save_btn.show()

    def _escape(self):
        if self.name_edit.isVisible():
            self._end_save()
        else:
            self.hide()

    def show_status(self, text, kind="info", action_text="", callback=None):
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self._status_callback = callback
        self.status_action.setText(action_text)
        self.status_action.setVisible(bool(action_text))
        self._fit()

    def _status_clicked(self):
        if self._status_callback:
            cb, self._status_callback = self._status_callback, None
            self.status_action.hide()
            cb()

    # Showing and hiding
    def toggle(self):
        self.hide() if self.isVisible() else self.popup()

    def popup(self):
        self.c.refresh()  # pick up anything changed with the monitor's buttons
        self._fit()
        saved = self.c.settings["panel_pos"]
        if QGuiApplication.platformName() != "xcb":
            pass  # Wayland: the compositor places windows
        elif self.c.settings["panel_position"] == "remembered" and saved:
            # Where it was last left, pulled back inside the screen it was on if that changed.
            screen = QGuiApplication.screenAt(QPoint(*saved)) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            self.move(min(max(saved[0], area.left()), area.right() - self.width()),
                      min(max(saved[1], area.top()), area.bottom() - self.height()))
        else:
            # Open next to the pointer (the tray icon just clicked), kept inside the work area,
            # so it works wherever the panel or tray lives.
            pos = QCursor.pos()
            screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            x = min(max(pos.x() - self.width() // 2, area.left() + SPACE[3]),
                    area.right() - self.width() - SPACE[3])
            below = pos.y() + SPACE[3]
            y = below if below + self.height() <= area.bottom() else pos.y() - self.height() - SPACE[3]
            self.move(x, max(area.top() + SPACE[3], y))
        self._was_active = False
        self.show()
        self.raise_()
        self.activateWindow()
        # Start keyboard focus on the first control, not the header's Settings button.
        first = next((r for r in self.rows.values() if not r.isHidden()), None)
        if first:
            (first.slider if isinstance(first, SliderRow) else first.combo).setFocus()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active and self.c.settings["close_on_focus_loss"] \
                    and not QApplication.activePopupWidget() and not self._has_child_window():
                self.hide()

    def moveEvent(self, event):
        super().moveEvent(event)
        # Remember where the user dragged it (moves made by popup() happen while hidden).
        if self.isVisible() and self.c.settings["panel_position"] == "remembered":
            self._save_pos.start()

    def _has_child_window(self):
        """Focus moved to something of ours (context menu, rename dialog, settings window),
        not away from the app, so the panel stays open."""
        if self.c.settings_window_visible():
            return True
        return any(w.isVisible() for w in self.findChildren(QMenu) + self.findChildren(QInputDialog))
