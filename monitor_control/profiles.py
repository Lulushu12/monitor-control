"""Saved profiles, stored as plain JSON so they can be edited, exported and backed up.

~/.config/monitor-control/profiles.json:
    {"version": 1, "profiles": [{"name": "Night", "settings": {"brightness": 10, ...}}]}

Settings keys: brightness (percent), contrast, shadow_control, game_color, color_preset,
display_mode, overdrive, low_blue, and gain_red / gain_green / gain_blue when the color preset is
User. Input source is never part of a profile: switching inputs from a profile would blank the
screen without warning.
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "monitor-control"
PROFILES_FILE = CONFIG_DIR / "profiles.json"
SETTING_KEYS = ("brightness", "contrast", "shadow_control", "game_color", "color_preset",
                "display_mode", "overdrive", "low_blue", "gain_red", "gain_green", "gain_blue")
MAX_SHORTCUT_PROFILES = 9


class ProfileError(Exception):
    pass


def parse(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProfileError(f"Not valid JSON (line {e.lineno}).") from None
    items = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ProfileError('Expected an object with a "profiles" list.')
    profiles = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise ProfileError("Every profile needs a name.")
        settings = {k: int(v) for k, v in (item.get("settings") or {}).items() if k in SETTING_KEYS}
        profiles.append({"name": str(item["name"]).strip(), "settings": settings})
    return profiles


def load():
    if not PROFILES_FILE.exists():
        return []
    return parse(PROFILES_FILE.read_text())


def dump(profiles):
    return json.dumps({"version": 1, "profiles": profiles}, indent=2) + "\n"


def save(profiles, path=PROFILES_FILE):
    """Write atomically, so a crash mid-write never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump(profiles))
    tmp.replace(path)
