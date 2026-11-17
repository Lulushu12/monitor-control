"""App settings (not monitor settings), stored as JSON next to the profiles.

~/.config/monitor-control/settings.json holds every setting. Unknown keys are ignored and missing
ones fall back to DEFAULTS, so files from older or newer versions keep working.
"""

import json
import shutil

from PySide6.QtCore import QObject, Signal

from .profiles import CONFIG_DIR

SETTINGS_FILE = CONFIG_DIR / "settings.json"

# Panel controls, in panel order: key → label. Settings hide them by key.
CONTROLS = {
    "brightness": "Brightness", "contrast": "Contrast", "shadow_control": "Shadow control",
    "game_color": "Game color", "display_mode": "Game mode", "overdrive": "Overdrive",
    "low_blue": "Low blue light", "color_preset": "Color", "input_source": "Input",
}

DEFAULTS = {
    "hidden_controls": [],          # keys from CONTROLS; new controls show by default
    "panel_position": "pointer",    # "pointer" | "remembered"
    "panel_pos": None,              # [x, y] of the last place the panel was left
    "close_on_focus_loss": True,
    "compact": False,
    "theme": "system",              # "system" | "dark" | "light"
    "brightness_step": 10,          # percent per shortcut press
    "profile_osd": True,            # on-screen indicator when a shortcut applies a profile
    "settings_geometry": None,      # settings window size and position (base64)
}


class Settings(QObject):
    changed = Signal(str)  # key

    def __init__(self, path=SETTINGS_FILE):
        super().__init__()
        self.path = path
        self.values = dict(DEFAULTS)
        self.load_error = None
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, dict):
                    raise ValueError("expected a JSON object")
            except (OSError, ValueError) as e:
                # Keep the broken file for inspection; start from defaults.
                backup = path.with_suffix(".json.bad")
                shutil.copyfile(path, backup)
                self.load_error = f"Settings file was unreadable ({e}); kept it as {backup.name}."
            else:
                self.values.update({k: v for k, v in data.items() if k in DEFAULTS})

    def __getitem__(self, key):
        return self.values[key]

    def set(self, key, value):
        if self.values.get(key) == value:
            return
        self.values[key] = value
        self._save()
        self.changed.emit(key)

    def shown(self, control):
        return control not in self.values["hidden_controls"]

    def set_shown(self, control, shown):
        hidden = [c for c in self.values["hidden_controls"] if c != control]
        if not shown:
            hidden.append(control)
        self.set("hidden_controls", hidden)

    def _save(self):
        """Atomic write, so a crash mid-write never leaves a half-written file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, indent=2) + "\n")
        tmp.replace(self.path)
