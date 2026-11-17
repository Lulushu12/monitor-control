"""Live test of the login race: PowerDevil registers the monitor after the app starts.

python3 -m tests.test_brightness_race   (changes brightness by 5 and restores it)
"""

import os
import subprocess
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

qt = QCoreApplication(sys.argv)

from monitor_control import kde  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def monitor_brightness():
    out = subprocess.run(["ddcutil", "--bus", "2", "--brief", "getvcp", "10"],
                         capture_output=True, text=True).stdout.split()
    return int(out[3])


# Start with PowerDevil "not ready yet": it answers with no displays, like at login.
real_busctl = kde._busctl
kde._busctl = lambda *a, **k: None if "DisplaysDBusNames" in a else real_busctl(*a, **k)
b = kde.PowerDevilBrightness()
check("no display while PowerDevil isn't ready", b.path is None)
seen = []
b.changed.connect(seen.append)

# PowerDevil finishes starting and announces the monitor.
kde._busctl = real_busctl
b._on_display_added("display1")
check("display found when PowerDevil announces it", b.path is not None, str(b.path))
check("current brightness reported on discovery", bool(seen), str(seen))

# Without the announcement: the retry before use also finds it.
kde._busctl = lambda *a, **k: None if "DisplaysDBusNames" in a else real_busctl(*a, **k)
b2 = kde.PowerDevilBrightness()
kde._busctl = real_busctl
b2._last_try = 0  # pretend the retry interval has passed
check("retry before use finds a late display", b2.available)

original = monitor_brightness()
target = original + 5 if original <= 95 else original - 5
b.set(target)
end = time.time() + 6
while monitor_brightness() != target and time.time() < end:
    wait(300)
check("brightness change reaches the monitor", monitor_brightness() == target, f"{original} -> {target}")
b.set(original)
end = time.time() + 6
while monitor_brightness() != original and time.time() < end:
    wait(300)
check("brightness restored", monitor_brightness() == original, str(original))
sys.exit(0 if all(results) else 1)
