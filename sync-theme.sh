#!/usr/bin/env bash
# Copy the House Style build output into the app. Run after changing design-language tokens
# (python3 palette.py && python3 build.py in ux-ranking/design-language).
set -euo pipefail
src="${1:-$HOME/Claude Code/ux-ranking/design-language}"
dst="$(dirname "$0")/monitor_control/theme"
mkdir -p "$dst"
cp "$src/tokens.json" "$src/dist/qt-palette.json" "$src/dist/theme-dark.qss" "$src/dist/theme-light.qss" "$dst/"
echo "Theme copied from $src"
