#!/usr/bin/env bash
# ClawsCummer installer for Linux and macOS

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║   ClawsCummer Installer                  ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""

# ── [1] Check Python ──────────────────────────────────────────────────────────
echo "  [1/3] Checking Python..."
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null && python --version 2>&1 | grep -q "Python 3"; then
    PYTHON=python
else
    echo "  ✗ Python 3 not found. Install Python 3.8+ first."
    exit 1
fi
echo "  ✓ $($PYTHON --version)"

# ── [2] Install Python packages ───────────────────────────────────────────────
echo "  [2/3] Installing Python packages (textual, rich)..."
"$PYTHON" -m pip install textual rich --quiet --upgrade
echo "  ✓ Packages installed"

# ── [3] Install clawscummer command ──────────────────────────────────────────
echo "  [3/3] Setting up 'clawscummer' command..."
chmod +x "$SCRIPT_DIR/clawscummer.sh"

INSTALL_DIR=""
if [[ -w /usr/local/bin ]]; then
    INSTALL_DIR="/usr/local/bin"
elif [[ -d "$HOME/.local/bin" ]] || mkdir -p "$HOME/.local/bin" 2>/dev/null; then
    INSTALL_DIR="$HOME/.local/bin"
else
    echo "  ✗ Cannot find a writable bin directory."
    exit 1
fi

ln -sf "$SCRIPT_DIR/clawscummer.sh" "$INSTALL_DIR/clawscummer"
echo "  ✓ Installed to $INSTALL_DIR/clawscummer"

if [[ "$INSTALL_DIR" == "$HOME/.local/bin" ]]; then
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        echo ""
        echo "  Note: Add ~/.local/bin to your PATH by adding this to ~/.bashrc or ~/.zshrc:"
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
fi

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║   Installation complete!                 ║"
echo "  ║                                          ║"
echo "  ║   Usage: clawscummer                     ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""
