# Monitor Control

A tray panel for the AOC AG241QX's settings over DDC/CI, so the button menu isn't needed.

```bash
./monitor-control            # start (tray icon); click it or press Meta+Alt+M for the panel
./monitor-control brightness +10
./monitor-control profile Night
```

- **Panel:** brightness, contrast, shadow control, game color, game mode, overdrive, low blue
  light, color preset (RGB gains under User), input.
- **Profiles:** "Save current settings…", then click to apply. Right-click to update, rename or
  delete. Apply, update, delete and import can all be undone from the status line.
  Stored in `~/.config/monitor-control/profiles.json`; Import/Export in the tray menu.
- **Shortcuts** (rebind in System Settings → Shortcuts → Monitor Control): Meta+Alt+M panel,
  Meta+Ctrl+PgUp/PgDown brightness, Meta+Alt+1…9 profiles.
- **Moving the panel:** drag it by its header. Settings can make it reopen where you left it.
- **Settings** (header button, tray menu, or Ctrl+Shift+,): which controls show, where the panel
  opens, close-on-click-elsewhere, compact layout, theme (system/dark/light), brightness shortcut
  step, profile on-screen messages, a link to KDE's shortcut editor, and start at login. Changes
  apply and save immediately to `~/.config/monitor-control/settings.json`.
- **Start at login** is off until you tick it (tray menu or Settings; they stay in sync).

## How it works

- Brightness goes through PowerDevil (it already drives this monitor over DDC), so Plasma's
  brightness slider, keys and OSD stay in sync. Everything else is written with `ddcutil`.
- DDC writes take ~0.4 s; a background writer coalesces them so sliders never lag behind.
- Runs on XWayland by default so the panel can open beside the tray icon
  (`QT_QPA_PLATFORM=wayland` to opt out; KWin then places it).
- Colors come from the House Style build (`./sync-theme.sh` after changing tokens).

## Mapped values

Display mode (0xDC): 00 Off, 0B FPS, 0C RTS, 0D Racing, 0E–10 Gamer 1–3 (checked against the
monitor menu 2026-10-31).

Shadow Control and Game Color aren't standard VCP codes. AOC's G-Menu 3.34.0 sends them as
vendor DDC/CI packets, found by decompiling it (`DDCHelperLib.dll`, `Zeasn.*.Lib`) and confirmed
on this monitor (reads matched the menu; a test write appeared in the menu):

| Feature | Set payload | Get payload | Range |
|---|---|---|---|
| Overdrive | `03 F8 E1 51 hi lo` | `01 F8 E1 51` | 0–3: Off, Weak, Medium, Strong |
| Game Color | `03 F8 E1 57 hi lo` | `01 F8 E1 57` | 0–20 |
| Shadow Control | `03 F8 E1 58 hi lo` | `01 F8 E1 58` | 0–100 |
| Low Blue Mode | `03 F8 E1 59 hi lo` | `01 F8 E1 59` | 0–3: Off, Weak, Medium, Strong |

Replies are `02 rc F8 ext maxHi maxLo curHi curLo`. ddcutil can't send these, so `ddc.py` writes
the packets to `/dev/i2c-N` itself. G-Menu names the Low Blue values Multimedia/Internet/Office/
Reading; on the AG241QX the menu calls them Off/Weak/Medium/Strong (checked against the menu).

`python3 -m tests.test_live` runs a live test against the monitor (restores what it changes);
`python3 -m tests.test_settings` checks the app settings offline.
