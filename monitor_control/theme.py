"""House Style theming from the design-language tokens.

Colors come only from the design-language build output (qt-palette.json, theme-*.qss and
tokens.json), never from literals here. sync-theme.sh copies those files into ./theme so the app
can be packaged; MONITOR_CONTROL_THEME_DIR overrides the location.
"""

import json
import os
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPixmap

HERE = Path(__file__).parent
THEME_DIR = Path(os.environ.get("MONITOR_CONTROL_THEME_DIR", HERE / "theme"))

_tokens = json.loads((THEME_DIR / "tokens.json").read_text())
_palettes = json.loads((THEME_DIR / "qt-palette.json").read_text())

SPACE = {int(k): v for k, v in _tokens["space"].items()}
RADIUS = _tokens["radius"]
DATA_FONT = _tokens["font"]["data"][0]
APP_HEADING_FONT = _tokens["font"]["display-app"][0]
SIZE = _tokens["size"]


def scheme(app):
    """The system's light/dark setting."""
    return "light" if app.styleHints().colorScheme() == Qt.ColorScheme.Light else "dark"


def app_scheme(app, mode="system"):
    """The scheme the app uses: the system's, or a "dark"/"light" override from settings."""
    return scheme(app) if mode == "system" else mode


def color(name, which):
    return _tokens["color"][which][name]


def apply(app, mode="system"):
    which = app_scheme(app, mode)
    pal = QPalette()
    for role_name, hex_value in _palettes[which].items():
        role = getattr(QPalette.ColorRole, role_name, None)
        if role is not None:
            pal.setColor(QPalette.ColorGroup.All, role, QColor(hex_value))
    muted = QColor(color("muted", which))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, muted)
    app.setPalette(pal)
    app.setStyleSheet((THEME_DIR / f"theme-{which}.qss").read_text() + _app_qss(which))


def _app_qss(which):
    c = lambda name: color(name, which)
    return f"""
#Panel {{ background: {c('bg')}; border: 1px solid {c('line')}; border-radius: {RADIUS['card']}px; }}
#Panel QLabel, #Panel QSlider, #Gains {{ background: transparent; }}
#Heading {{ font-family: "{APP_HEADING_FONT}"; font-size: {SIZE['md']}px; font-weight: 600; }}
#Muted, #FieldLabel {{ color: {c('muted')}; font-size: {SIZE['sm']}px; }}
#Value {{ font-family: "{DATA_FONT}"; color: {c('muted')}; font-size: {SIZE['sm']}px; }}
#Section {{ background: {c('surface')}; border: 1px solid {c('line')}; border-radius: {RADIUS['card']}px; }}
#Status[kind="ok"] {{ color: {c('success')}; }}
#Status[kind="error"] {{ color: {c('danger')}; }}
#Status[kind="info"] {{ color: {c('muted')}; }}
QPushButton#Profile:checked {{ background: {c('accent-soft')}; color: {c('accent-text')}; border-color: {c('accent-soft')}; }}
QPushButton#Link {{ background: transparent; border: 0; padding: 0 {SPACE[1]}px; color: {c('accent-text')}; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QPushButton#HeaderButton {{ background: transparent; border: 0; padding: {SPACE[1]}px {SPACE[2]}px; border-radius: {RADIUS['sm']}px; color: {c('muted')}; font-weight: 600; font-size: {SIZE['sm']}px; }}
QPushButton#HeaderButton:hover {{ background: {c('line')}; color: {c('text')}; }}
QPushButton#HeaderButton:focus {{ color: {c('accent-text')}; outline: none; }}
#Handle {{ background: transparent; }}
#SettingsWindow {{ background: {c('bg')}; }}
#SettingsWindow QScrollArea, #SettingsWindow #SettingsBody {{ background: transparent; border: 0; }}
#SettingsWindow QGroupBox QLabel, #SettingsWindow QGroupBox QCheckBox {{ background: transparent; }}
QComboBox {{ min-height: 20px; }}
QComboBox QAbstractItemView {{ background: {c('raised')}; border: 1px solid {c('line')}; selection-background-color: {c('accent-soft')}; selection-color: {c('accent-text')}; }}
"""


def tray_icon(app):
    """Filled monitor glyph in the text color of the system scheme: light on a dark panel,
    dark on a light one. It follows the system, not the app's theme override, because it sits
    on the Plasma panel. Drawn rather than taken from the icon theme, whose glyph disappears
    against a dark panel when the app runs outside Plasma's own Qt."""
    ink = QColor(color("text", scheme(app)))
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size / 24, size / 24)
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)  # overlapping parts union instead of cancelling
        path.addRoundedRect(QRectF(2, 3.5, 20, 13), 2.5, 2.5)   # screen
        path.addRect(QRectF(10.5, 16, 3, 3))                     # neck
        path.addRoundedRect(QRectF(6.5, 18.5, 11, 2.5), 1.25, 1.25)  # foot
        p.fillPath(path.simplified(), ink)
        p.end()
        icon.addPixmap(pm)
    return icon
