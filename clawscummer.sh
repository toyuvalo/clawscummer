#!/usr/bin/env bash
# ClawsCummer — Multi-account Claude session manager
# Launcher for Linux and macOS

set -e

# Resolve symlinks so SCRIPT_DIR always points to the real script location
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

# Find Python 3
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null && python --version 2>&1 | grep -q "Python 3"; then
    PYTHON=python
else
    echo "[ClawsCummer] Error: Python 3 not found. Install Python 3.8+ first."
    exit 1
fi

# Auto-install required packages if missing
if ! "$PYTHON" -c "import textual" 2>/dev/null; then
    echo "[ClawsCummer] Installing required packages (textual, rich)..."
    "$PYTHON" -m pip install textual rich -q
fi

exec "$PYTHON" "$SCRIPT_DIR/clawscummer.py" --tui "$@"
