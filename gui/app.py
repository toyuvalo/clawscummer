"""
ClawsCummer v2.9 — GUI Application
Main entry point for the pywebview GUI with embedded terminal.
"""
from __future__ import annotations

import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview

from gui.terminal import TerminalManager
from gui.bridge import Api
from gui.api_server import start_server as start_api_server


def main():
    # Create terminal manager
    terminal = TerminalManager()

    # Create API bridge
    api = Api(terminal=terminal)

    # Start HTTP API server for remote access (HA, Tailscale, etc.)
    api_port = int(os.environ.get("CLAWSCUMMER_API_PORT", "7855"))
    api_server = start_api_server(api, port=api_port)

    # Resolve path to HTML
    gui_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(gui_dir, "assets", "index.html")

    # Create window
    window = webview.create_window(
        "ClawsCummer v2.9",
        url=html_path,
        js_api=api,
        width=1100,
        height=750,
        min_size=(800, 500),
        background_color="#0c0c11",
        text_select=True,
    )

    # Give the API a reference to the window for evaluate_js calls
    api.set_window(window)

    # Debug mode: set CLAWSCUMMER_DEBUG=1 to enable right-click → Inspect
    webview.start(debug=os.environ.get("CLAWSCUMMER_DEBUG") == "1")

    # Cleanup
    api_server.shutdown()
    terminal.stop()


if __name__ == "__main__":
    main()
