"""Talk to the monitor over DDC/CI: standard features through the ddcutil CLI, AOC's vendor
features (Shadow Control, Game Color) as raw DDC/CI packets on /dev/i2c-N.

Every write takes ~0.4 s on the AG241QX, so writes go through a single worker thread that
coalesces them: while one write is in flight, newer values for the same feature replace older
ones, and only the latest value is sent. Dragging a slider therefore never queues up a backlog.
"""

import fcntl
import os
import re
import shutil
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Signal

# VCP feature codes used by the app.
BRIGHTNESS = 0x10
CONTRAST = 0x12
COLOR_PRESET = 0x14
GAIN_RED = 0x16
GAIN_GREEN = 0x18
GAIN_BLUE = 0x1A
INPUT_SOURCE = 0x60
DISPLAY_MODE = 0xDC

# AOC/TPV vendor features. These aren't standard VCP codes: G-Menu (AOC's Windows app) sends
# them as 6-byte DDC/CI payloads "03 F8 E1 <ext> <hi> <lo>" (set) and "01 F8 E1 <ext>" (get).
# Found by decompiling G-Menu 3.34.0 (DDCHelperLib.dll, Zeasn.*.Lib) and confirmed on the
# AG241QX 2026-10-31: reads matched the menu, a test write showed up in the menu.
# Codes are stored as 0xF8E1xx so they never collide with one-byte VCP codes.
TPV_PREFIX = (0xF8, 0xE1)
OVERDRIVE = 0xF8E151       # LEVELS
GAME_COLOR = 0xF8E157      # 0-20
SHADOW_CONTROL = 0xF8E158  # 0-100
LOW_BLUE = 0xF8E159        # LEVELS

READ_CODES = [CONTRAST, COLOR_PRESET, GAIN_RED, GAIN_GREEN, GAIN_BLUE, INPUT_SOURCE, DISPLAY_MODE,
              SHADOW_CONTROL, GAME_COLOR, OVERDRIVE, LOW_BLUE]

# Value tables, mapped by hand against the monitor's own menu (2026-10-31).
COLOR_PRESETS = {0x01: "sRGB", 0x05: "6500 K", 0x06: "7500 K", 0x08: "9300 K", 0x0B: "User"}
COLOR_PRESET_USER = 0x0B
DISPLAY_MODES = {0x00: "Off", 0x0B: "FPS", 0x0C: "RTS", 0x0D: "Racing",
                 0x0E: "Gamer 1", 0x0F: "Gamer 2", 0x10: "Gamer 3"}
INPUT_SOURCES = {0x0F: "DisplayPort", 0x11: "HDMI 1", 0x12: "HDMI 2", 0x03: "DVI", 0x01: "VGA"}
# Overdrive and Low Blue Mode on the AG241QX. G-Menu labels Low Blue values Multimedia/Internet/
# Office/Reading, but this monitor's menu names them Off/Weak/Medium/Strong (user-checked).
LEVELS = {0: "Off", 1: "Weak", 2: "Medium", 3: "Strong"}

DDCUTIL = shutil.which("ddcutil") or "ddcutil"
SPEED_FLAGS = ["--sleep-multiplier", "0.3"]
I2C_SLAVE = 0x0703
DDC_ADDR = 0x37

# One bus transaction at a time from this process (ddcutil runs and raw packets alike).
_bus_lock = threading.Lock()


def is_vendor(code):
    return code > 0xFF


class DdcError(Exception):
    pass


def _run(args, timeout=15):
    try:
        with _bus_lock:
            out = subprocess.run([DDCUTIL, *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise DdcError("ddcutil isn't installed.") from None
    except subprocess.TimeoutExpired:
        raise DdcError("The monitor didn't answer in time.") from None
    if out.returncode != 0:
        raise DdcError((out.stderr or out.stdout).strip().splitlines()[-1] if (out.stderr or out.stdout).strip()
                       else f"ddcutil exited with code {out.returncode}")
    return out.stdout


def detect():
    """Return (i2c bus number, model name) of the first DDC-capable display."""
    text = _run(["detect", "--brief"], timeout=30)
    bus = re.search(r"/dev/i2c-(\d+)", text)
    if not bus:
        raise DdcError("No monitor with DDC/CI found. Check that DDC/CI is on in the monitor menu.")
    model = re.search(r"Monitor:\s+[^:]*:([^:]+):", text)
    return int(bus.group(1)), (model.group(1).strip() if model else "Monitor")


def _xor(seed, data):
    for b in data:
        seed ^= b
    return seed


def _vendor_transaction(bus, payload, reply_len=0):
    """Send one DDC/CI packet (51 8n payload chk) and optionally read the reply payload."""
    msg = bytes([0x51, 0x80 | len(payload)]) + bytes(payload)
    try:
        with _bus_lock:
            fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
            try:
                fcntl.ioctl(fd, I2C_SLAVE, DDC_ADDR)
                os.write(fd, msg + bytes([_xor(0x6E, msg)]))
                time.sleep(0.1)  # G-Menu waits 100 ms after every write
                if not reply_len:
                    return None
                reply = os.read(fd, reply_len + 3)
            finally:
                os.close(fd)
    except OSError as e:
        raise DdcError(f"I2C error on /dev/i2c-{bus}: {e.strerror}") from None
    n = reply[1] & 0x7F
    if reply[0] != 0x6E or not reply[1] & 0x80 or n > reply_len or _xor(0x50, reply[:n + 3]):
        raise DdcError("The monitor sent a garbled reply.")
    return reply[2:2 + n]


def read_vendor(bus, code):
    """(current, max) of a vendor feature. Reply payload: 02 rc F8 ext maxHi maxLo curHi curLo."""
    body = _vendor_transaction(bus, [0x01, *TPV_PREFIX, code & 0xFF], reply_len=8)
    if len(body) < 8 or body[0] != 0x02 or body[1] != 0 or body[3] != code & 0xFF:
        raise DdcError("The monitor doesn't support this feature.")
    return body[6] << 8 | body[7], body[4] << 8 | body[5]


def write_vendor(bus, code, value):
    _vendor_transaction(bus, [0x03, *TPV_PREFIX, code & 0xFF, value >> 8 & 0xFF, value & 0xFF])


def read_values(bus, codes=READ_CODES):
    """Read current values. Returns {code: (current, max or None)}.

    Vendor features the monitor doesn't answer are left out, so the UI hides them."""
    values = {}
    for code in (c for c in codes if is_vendor(c)):
        try:
            values[code] = read_vendor(bus, code)
        except DdcError:
            pass
    codes = [c for c in codes if not is_vendor(c)]
    if not codes:
        return values
    text = _run(["--bus", str(bus), "--brief", *SPEED_FLAGS, "getvcp", *[f"{c:02X}" for c in codes]])
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != "VCP":
            continue
        code, kind = int(parts[1], 16), parts[2]
        if kind == "C":
            values[code] = (int(parts[3]), int(parts[4]))
        elif kind in ("SNC", "CNC"):
            # Non-continuous: the last byte (sl) is the current value.
            values[code] = (int(parts[-1].lstrip("x"), 16), None)
    return values


def write_value(bus, code, value):
    if is_vendor(code):
        return write_vendor(bus, code, value)
    _run(["--bus", str(bus), "--noverify", *SPEED_FLAGS, "setvcp", f"{code:02X}", str(value)])


class DdcWriter(QObject):
    """Coalescing background writer. Emits failed(message) when a write doesn't land."""

    failed = Signal(str)
    idle = Signal()

    def __init__(self, bus):
        super().__init__()
        self.bus = bus
        self._pending = {}
        self._cond = threading.Condition()
        self._stop = False
        threading.Thread(target=self._loop, name="ddc-writer", daemon=True).start()

    def set(self, code, value):
        with self._cond:
            # Re-inserting moves the code to the end, so features are written in the order the
            # user last touched them.
            self._pending.pop(code, None)
            self._pending[code] = int(value)
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify()

    def _loop(self):
        while True:
            with self._cond:
                while not self._pending and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                code = next(iter(self._pending))
                value = self._pending.pop(code)
            try:
                write_value(self.bus, code, value)
            except DdcError as e:
                self.failed.emit(str(e))
            with self._cond:
                empty = not self._pending
            if empty:
                self.idle.emit()
