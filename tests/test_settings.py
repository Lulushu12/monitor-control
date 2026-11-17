"""Offline test of app settings against the real panel: python3 -m tests.test_settings."""

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

qt = QApplication(sys.argv)
qt.styleHints().setColorScheme(Qt.ColorScheme.Dark)

from monitor_control import app as mc, ddc, theme  # noqa: E402
from monitor_control.settings import SETTINGS_FILE, Settings  # noqa: E402
from monitor_control.ui import Panel  # noqa: E402

results = []


def height():
    """The panel's real height. Its fixed-size layout resizes the window on the next event
    loop pass, so let that run first."""
    qt.processEvents()
    return p.height()


def bg():
    w = QWidget()
    w.ensurePolished()
    return w.palette().window().color().name()


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


mc.AUTOSTART = Path(os.environ["XDG_CONFIG_HOME"]) / "autostart/monitor-control.desktop"
settings = Settings()
c = mc.Controller(settings, mc.Autostart("monitor-control"))
c.panel = p = Panel(c)
c.state.update({"brightness": 40, "contrast": 70, "shadow_control": 50, "game_color": 10,
                "display_mode": 0, "overdrive": 0, "low_blue": 0, "color_preset": 5,
                "input_source": 0x0F})
p.sync()
p.show()

check("defaults show every supported control", all(not r.isHidden() for r in p.rows.values()))
full_height = height()

settings.set_shown("input_source", False)
settings.set_shown("overdrive", False)
check("hidden controls disappear", p.input.isHidden() and p.overdrive.isHidden())
check("other controls stay", not p.low_blue.isHidden() and not p.mode.isHidden())
shrunk = height()
check("panel shrinks to fit", shrunk < full_height, f"{full_height} -> {shrunk}")
check("hidden list persists to disk",
      set(json.loads(SETTINGS_FILE.read_text())["hidden_controls"]) == {"input_source", "overdrive"})

for key in ("display_mode", "low_blue", "color_preset"):
    settings.set_shown(key, False)
check("a section with nothing left hides itself", p.sections[1].isHidden())
settings.set("hidden_controls", [])
check("showing them again brings the section back", not p.sections[1].isHidden())

del c.state["shadow_control"]
p.sync()
check("unsupported vendor control stays hidden even when enabled", p.shadow.isHidden())
c.state["shadow_control"] = 50
p.sync()

before = height()
settings.set("compact", True)
compact = height()
check("compact layout is shorter", compact < before, f"{before} -> {compact}")
settings.set("compact", False)
check("normal layout restores the height", height() == before)

theme.apply(qt, "light")
light_bg = bg()
theme.apply(qt, "dark")
dark_bg = bg()
check("theme override switches palettes", light_bg == theme.color("bg", "light")
      and dark_bg == theme.color("bg", "dark"), f"{light_bg} / {dark_bg}")
check("tray icon ignores the app override (follows the system)",
      theme.scheme(qt) == "dark")

settings.set("brightness_step", 5)
settings.set("theme", "light")
reloaded = Settings()
check("settings survive a restart", reloaded["brightness_step"] == 5
      and reloaded["theme"] == "light")

SETTINGS_FILE.write_text("{broken")
broken = Settings()
check("broken file falls back to defaults and is kept",
      broken["theme"] == "system" and broken.load_error
      and SETTINGS_FILE.with_suffix(".json.bad").exists(), broken.load_error)

c.autostart.set(True)
check("autostart writes the desktop file", mc.AUTOSTART.exists())
c.open_settings()
check("settings window mirrors autostart", c.settings_window.login.isChecked())
c.autostart.set(False)
check("turning it off elsewhere unticks the window", not c.settings_window.login.isChecked()
      and not mc.AUTOSTART.exists())
check("panel stays open while settings are open", p._has_child_window())
c.settings_window.close()

sys.exit(0 if all(results) else 1)
