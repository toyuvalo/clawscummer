#!/usr/bin/env bash
# ClawsCummer build script for Linux and macOS
# Produces a standalone binary in dist/

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  +------------------------------------------+"
echo "  |  ClawsCummer Builder (Linux/macOS)       |"
echo "  +------------------------------------------+"
echo ""

# Find Python
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "  ! Python not found."
    exit 1
fi

# ── [1] Install build dependencies ────────────────────────────────────────────
echo "  [1/2] Installing build dependencies..."
"$PYTHON" -m pip install pyinstaller textual rich --quiet --upgrade
echo "  + Dependencies ready"

# ── [2] Build with PyInstaller ────────────────────────────────────────────────
echo "  [2/2] Building binary (this takes ~60 seconds)..."

"$PYTHON" -m PyInstaller clawscummer.py \
    --onefile \
    --console \
    --name clawscummer \
    --collect-all textual \
    --collect-all rich \
    --hidden-import textual \
    --hidden-import textual.app \
    --hidden-import textual._xterm_theme \
    --hidden-import textual.css.query \
    --hidden-import textual.widgets._list_view \
    --hidden-import textual.widgets._list_item \
    --hidden-import textual.widgets._input \
    --hidden-import textual.widgets._button \
    --hidden-import textual.widgets._label \
    --hidden-import textual.widgets._static \
    --hidden-import textual.widgets._rule \
    --hidden-import textual.containers \
    --hidden-import textual.screen \
    --noconfirm \
    --clean \
    2>&1 | grep -v "^INFO" | grep -v "^WARNING: Collect"

echo ""
echo "  +------------------------------------------+"
echo "  |  Done! Binary at: dist/clawscummer       |"
echo "  +------------------------------------------+"
echo ""
