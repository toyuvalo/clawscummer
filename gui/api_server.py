"""
ClawsCummer HTTP API Server — enables remote prompt execution via HA/Tailscale.
Runs as a daemon thread alongside the pywebview GUI on port 7855.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from gui.terminal import TerminalManager


class _Handler(BaseHTTPRequestHandler):
    """HTTP request handler for ClawsCummer API."""

    bridge = None  # Set by start_server()
    _session_lock = threading.Lock()

    def log_message(self, format, *args):
        """Suppress default stderr logging."""
        pass

    # ── CORS headers ──────────────────────────────────────────────────────
    def _send_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')

    def _json_response(self, status: int, data: dict):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self._send_cors()
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    # ── GET endpoints ─────────────────────────────────────────────────────
    def do_GET(self):
        if self.path == '/api/status':
            self._handle_status()
        elif self.path == '/api/response':
            self._handle_get_response()
        elif self.path == '/api/health':
            self._json_response(200, {"ok": True, "version": "2.9"})
        else:
            self._json_response(404, {"error": "Not found"})

    def _handle_status(self):
        if not self.bridge:
            self._json_response(503, {"error": "Bridge not ready"})
            return
        alive = self.bridge._terminal.is_alive()
        self._json_response(200, {
            "status": "running" if alive else "idle",
            "cli_type": self.bridge._current_cli_type,
        })

    def _handle_get_response(self):
        if not self.bridge:
            self._json_response(503, {"error": "Bridge not ready"})
            return
        try:
            result = json.loads(self.bridge.get_last_response())
            self._json_response(200, result)
        except Exception as e:
            self._json_response(500, {"error": str(e)})

    # ── POST endpoints ────────────────────────────────────────────────────
    def do_POST(self):
        if self.path == '/api/prompt':
            self._handle_prompt()
        elif self.path == '/api/kill':
            self._handle_kill()
        else:
            self._json_response(404, {"error": "Not found"})

    def _handle_prompt(self):
        """Send a prompt and block until response is ready (up to 120s)."""
        if not self.bridge:
            self._json_response(503, {"error": "Bridge not ready"})
            return

        if not self._session_lock.acquire(blocking=False):
            self._json_response(429, {"error": "A prompt is already being processed"})
            return

        try:
            # Parse request
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length)) if content_length else {}
            prompt = body.get('prompt', '').strip()
            timeout = min(body.get('timeout', 120), 300)  # Max 5 min

            if not prompt:
                self._json_response(400, {"error": "Missing 'prompt' field"})
                return

            # Kill any existing session and launch new one
            self.bridge._terminal.kill_session()
            time.sleep(0.5)

            result = json.loads(self.bridge.launch_session('new', '', '', prompt))
            if not result.get('ok'):
                self._json_response(500, {"error": result.get('error', 'Launch failed')})
                return

            # Wait for the prompt to actually be sent to Claude
            # (prompt detection + Ink settle + PTY write takes ~3-4s)
            self.bridge._terminal.wait_for_prompt(timeout=30)
            time.sleep(3.5)  # Let prompt get sent and Claude start processing

            # NOW snapshot — this captures the greeting, not our response
            try:
                old_resp = json.loads(self.bridge.get_last_response())
                old_text = old_resp.get('text', '') if old_resp.get('ok') else ''
            except Exception:
                old_text = ''

            # Poll for a NEW response (different from the greeting)
            start = time.time()
            response_text = None

            while time.time() - start < timeout:
                time.sleep(1.0)
                try:
                    resp = json.loads(self.bridge.get_last_response())
                    if resp.get('ok') and resp.get('text'):
                        if resp['text'] != old_text:
                            response_text = resp['text']
                            break
                except Exception:
                    continue

            if response_text:
                self._json_response(200, {
                    "ok": True,
                    "text": response_text,
                    "elapsed": round(time.time() - start, 1),
                })
            else:
                self._json_response(504, {
                    "ok": False,
                    "error": f"No response after {timeout}s",
                })

        except json.JSONDecodeError:
            self._json_response(400, {"error": "Invalid JSON body"})
        except Exception as e:
            self._json_response(500, {"error": str(e)})
        finally:
            self._session_lock.release()

    def _handle_kill(self):
        if self.bridge:
            self.bridge._terminal.kill_session()
        self._json_response(200, {"ok": True})


def start_server(bridge, port: int = 7855) -> HTTPServer:
    """Start the API server as a daemon thread. Returns the HTTPServer instance."""
    _Handler.bridge = bridge
    server = HTTPServer(('0.0.0.0', port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="APIServer")
    thread.start()
    import sys
    print(f"[ClawsCummer API] Listening on http://0.0.0.0:{port}", flush=True)
    return server
