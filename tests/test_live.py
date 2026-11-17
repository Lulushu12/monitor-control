"""Live test against the real monitor: python3 -m tests.test_live (restores contrast after)."""

import os
import sys
import tempfile
import time

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from monitor_control import app as mc, ddc, profiles  # noqa: E402

qt = QApplication(sys.argv)
results = []


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def until(cond, timeout=15000):
    end = time.time() + timeout / 1000
    while time.time() < end:
        if cond():
            return True
        wait(100)
    return False


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def contrast():
    return ddc.read_values(c.bus, [ddc.CONTRAST])[ddc.CONTRAST][0]


c = mc.Controller()
statuses = []
c.status.connect(lambda text, kind, action, cb: statuses.append((text, kind, action, cb)))
c.connect_monitor()
check("connects and reads state", until(lambda: "contrast" in c.state), str(c.state))
original = c.state["contrast"]

# Simulate a slider drag: 20 values in quick succession, only the last should matter.
t0 = time.time()
for v in range(41, 61):
    c.set("contrast", v)
idle = []
c.writer.idle.connect(lambda: idle.append(1))
until(lambda: idle)
elapsed = time.time() - t0
check("slider drag coalesces to the final value", contrast() == 60, f"{elapsed:.1f}s for 20 moves")
check("drag finishes fast (not 20 queued writes)", elapsed < 3, f"{elapsed:.1f}s")

c.save_profile("Test")
check("profile saved to disk", profiles.load()[0]["settings"].get("contrast") == 60)
c.set("contrast", 75)
idle.clear(); until(lambda: idle)
c.apply_profile(0)
idle.clear(); until(lambda: idle)
check("apply profile sets the monitor", contrast() == 60)
undo = statuses[-1][3]
check("apply offers undo", statuses[-1][2] == "Undo" and callable(undo))
undo()
idle.clear(); until(lambda: idle)
check("undo restores the previous value", contrast() == 75)

c.delete_profile(0)
check("delete removes profile", profiles.load() == [])
statuses[-1][3]()
check("undo delete brings it back", [p["name"] for p in profiles.load()] == ["Test"])

export = os.path.join(os.environ["XDG_CONFIG_HOME"], "export.json")
c.export_profiles(export)
c.delete_profile(0)
c.import_profiles(export)
check("export then import round-trips", [p["name"] for p in c.profiles] == ["Test"])
bad = os.path.join(os.environ["XDG_CONFIG_HOME"], "bad.json")
open(bad, "w").write("{nope")
c.import_profiles(bad)
check("bad import reports an error and changes nothing",
      statuses[-1][1] == "error" and len(c.profiles) == 1, statuses[-1][0])

c.set("contrast", original)
idle.clear(); until(lambda: idle)
check("contrast restored", contrast() == original, str(original))

# AOC vendor features (raw DDC/CI, not ddcutil).
check("shadow control and game color read", "shadow_control" in c.state and "game_color" in c.state,
      f"shadow {c.state.get('shadow_control')}/{c.maxima.get('shadow_control')}, "
      f"game color {c.state.get('game_color')}/{c.maxima.get('game_color')}")
check("vendor maxima match the manual", c.maxima.get("shadow_control") == 100
      and c.maxima.get("game_color") == 20)
orig_shadow, orig_gc = c.state["shadow_control"], c.state["game_color"]
t0 = time.time()
for v in range(orig_shadow, orig_shadow + 11):
    c.set("shadow_control", v)
c.set("game_color", min(20, orig_gc + 2))
idle.clear(); until(lambda: idle)
shadow_now = ddc.read_vendor(c.bus, ddc.SHADOW_CONTROL)[0]
gc_now = ddc.read_vendor(c.bus, ddc.GAME_COLOR)[0]
check("shadow drag coalesces to the final value", shadow_now == orig_shadow + 10,
      f"{shadow_now}, {time.time() - t0:.1f}s")
check("game color write lands", gc_now == min(20, orig_gc + 2), str(gc_now))
c.save_profile("Vendor")
c.set("shadow_control", orig_shadow); c.set("game_color", orig_gc)
idle.clear(); until(lambda: idle)
saved = next(p for p in c.profiles if p["name"] == "Vendor")["settings"]
check("profile stores vendor values", saved.get("shadow_control") == orig_shadow + 10
      and saved.get("game_color") == min(20, orig_gc + 2), str(saved))
c.apply_profile([p["name"] for p in c.profiles].index("Vendor"))
# Poll the monitor instead of waiting for the writer's idle signal: a queued idle from the
# previous batch can arrive after idle.clear() and end the wait early.
check("profile restores vendor values",
      until(lambda: ddc.read_vendor(c.bus, ddc.SHADOW_CONTROL)[0] == orig_shadow + 10, 10000))
c.set("shadow_control", orig_shadow); c.set("game_color", orig_gc)
check("vendor values restored", until(lambda: (ddc.read_vendor(c.bus, ddc.SHADOW_CONTROL)[0],
                                               ddc.read_vendor(c.bus, ddc.GAME_COLOR)[0])
                                      == (orig_shadow, orig_gc), 10000), f"{orig_shadow}, {orig_gc}")
# Overdrive and Low Blue Mode: four levels each, on the same vendor channel.
check("overdrive and low blue read", c.maxima.get("overdrive") == 3 and c.maxima.get("low_blue") == 3,
      f"overdrive {c.state.get('overdrive')}, low blue {c.state.get('low_blue')}")
orig_od, orig_lb = c.state["overdrive"], c.state["low_blue"]
c.set("overdrive", 2); c.set("low_blue", 1)
check("overdrive and low blue writes land",
      until(lambda: (ddc.read_vendor(c.bus, ddc.OVERDRIVE)[0], ddc.read_vendor(c.bus, ddc.LOW_BLUE)[0])
            == (2, 1), 10000))
c.set("overdrive", orig_od); c.set("low_blue", orig_lb)
check("overdrive and low blue restored",
      until(lambda: (ddc.read_vendor(c.bus, ddc.OVERDRIVE)[0], ddc.read_vendor(c.bus, ddc.LOW_BLUE)[0])
            == (orig_od, orig_lb), 10000), f"{orig_od}, {orig_lb}")

c.writer.stop()
sys.exit(0 if all(results) else 1)
