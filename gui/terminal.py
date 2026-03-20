"""
Terminal manager — PTY bridge for embedded terminal.
Spawns CLI processes in a ConPTY and bridges I/O to xterm.js
via pywebview's evaluate_js() — no WebSocket, no ports.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from typing import Callable, Optional

from winpty import PTY


class TerminalManager:
    """Manages a PTY process and bridges it to xterm.js via pywebview."""

    # Debug log file for PTY output diagnosis
    _DEBUG_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pty_debug.log")

    def __init__(
        self,
        on_rate_limit: Optional[Callable[[], None]] = None,
        rate_patterns: Optional[list[str]] = None,
    ):
        self._on_rate_limit = on_rate_limit
        self._rate_patterns = rate_patterns or []
        self._rate_cooldown = 0  # timestamp of last rate limit fire
        self._output_buffer = ""
        self._output_lock = threading.Lock()
        self._flush_thread: Optional[threading.Thread] = None
        self._pty: Optional[PTY] = None
        self._process_handle = None
        self._job_handle = None  # Windows Job Object for process tree cleanup
        self._read_thread: Optional[threading.Thread] = None
        self._alive = False
        self._cols = 120
        self._rows = 30
        self._window = None  # pywebview window reference
        self._prompt_event = threading.Event()  # Fires when CLI prompt appears
        self._recent_output = ""  # Last ~500 chars for prompt detection
        self._recent_lock = threading.Lock()
        self._debug_log = None
        self._auth_detected = False  # Prevent repeated auth notifications
        self._perm_approved = False  # Prevent repeated permission auto-approvals

    def set_window(self, window):
        """Set the pywebview window for evaluate_js calls."""
        self._window = window

    def pty_input(self, data: str):
        """Called from JS when user types in xterm.js."""
        if self._pty and self._alive:
            self._pty.write(data)

    def pty_resize(self, cols: int, rows: int):
        """Called from JS when terminal resizes."""
        self._cols = cols
        self._rows = rows
        if self._pty and self._alive:
            try:
                self._pty.set_size(cols, rows)
            except Exception:
                pass

    def start_session(self, cmd: list[str], cwd: str = "."):
        """Spawn a CLI process in a PTY."""
        self.kill_session()

        self._pty = PTY(self._cols, self._rows)

        # Resolve full executable path — winpty needs absolute path on Windows
        exe = shutil.which(cmd[0])
        if not exe:
            for p in [
                os.path.expanduser(f"~/AppData/Roaming/npm/{cmd[0]}.cmd"),
                os.path.expanduser(f"~/AppData/Local/npm/{cmd[0]}.cmd"),
            ]:
                if os.path.isfile(p):
                    exe = p
                    break
            if not exe:
                self._push_output_immediate(f"\x1b[31mError: '{cmd[0]}' not found in PATH\x1b[0m\r\n")
                return

        def _quote(s):
            """Quote an argument for Windows command line."""
            s = s.replace('"', '""')
            if s.endswith('\\'):
                s += '\\'
            return f'"{s}"'

        # For .cmd/.bat files, spawn via cmd.exe
        # Wrap entire command in outer quotes for cmd.exe /c parsing
        if exe.upper().endswith(('.CMD', '.BAT')):
            inner = _quote(exe)
            if len(cmd) > 1:
                inner += " " + " ".join(_quote(a) for a in cmd[1:])
            cmdline = f'cmd.exe /c "{inner}"'
            spawn_exe = "cmd.exe"
        else:
            spawn_exe = exe
            cmdline = " ".join(_quote(a) for a in cmd)

        try:
            self._process_handle = self._pty.spawn(spawn_exe, cmdline=cmdline, cwd=cwd)
        except Exception as e:
            self._push_output_immediate(f"\x1b[31mFailed to spawn: {e}\x1b[0m\r\n")
            return

        # #9 fix: Create Job Object to track entire process tree
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            self._job_handle = kernel32.CreateJobObjectW(None, None)
            if self._job_handle:
                # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
                class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD),
                    ]
                class IO_COUNTERS(ctypes.Structure):
                    _fields_ = [("ReadOperationCount", ctypes.c_uint64)] * 6
                class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t),
                    ]
                info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = 0x2000
                kernel32.SetInformationJobObject(
                    self._job_handle, 9,
                    ctypes.byref(info), ctypes.sizeof(info)
                )
                kernel32.AssignProcessToJobObject(
                    self._job_handle, int(self._process_handle)
                )
        except Exception:
            pass  # Job Object is best-effort; fallback to TerminateProcess

        self._alive = True
        self._has_output = False
        self._auth_detected = False
        self._perm_approved = False
        self._prompt_event.clear()
        with self._recent_lock:
            self._recent_output = ""

        # Open debug log
        try:
            self._debug_log = open(self._DEBUG_LOG, "w", encoding="utf-8", errors="replace")
            self._debug_log.write(f"=== PTY session started: {cmdline} ===\n")
            self._debug_log.flush()
        except Exception:
            self._debug_log = None

        # Start output flush loop and read thread
        self._start_flush_loop()
        self._read_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="PTYRead"
        )
        self._read_thread.start()

    def _push_output(self, data: str):
        """Buffer terminal output and flush at ~30fps via pywebview."""
        with self._output_lock:
            self._output_buffer += data

    def _start_flush_loop(self):
        """Start the output flush timer thread."""
        if self._flush_thread and self._flush_thread.is_alive():
            return
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="OutputFlush"
        )
        self._flush_thread.start()

    def _flush_loop(self):
        """Flush buffered output to xterm.js at ~30fps."""
        while self._alive:
            time.sleep(0.033)  # ~30fps
            with self._output_lock:
                buf = self._output_buffer
                self._output_buffer = ""
            if buf and self._window:
                escaped = json.dumps(buf)
                try:
                    self._window.evaluate_js(f"window.termWrite({escaped})")
                except Exception:
                    # #6 fix: Window likely destroyed — stop flushing
                    self._alive = False
                    break

    def _push_output_immediate(self, data: str):
        """Push directly to xterm.js — for error messages, not PTY stream."""
        if self._window:
            escaped = json.dumps(data)
            try:
                self._window.evaluate_js(f"window.termWrite({escaped})")
            except Exception:
                pass

    def _push_status(self, event: str, text: str = ""):
        """Push a status event to the frontend."""
        if self._window:
            msg = json.dumps({"event": event, "text": text})
            try:
                self._window.evaluate_js(f"window.handleStatus({msg})")
            except Exception:
                pass

    def _read_loop(self):
        """Read from PTY and push to xterm.js + monitor for rate limits."""
        rate_buffer = ""
        empty_count = 0
        while self._alive and self._pty:
            try:
                data = self._pty.read(blocking=False)
                if not data:
                    empty_count += 1
                    if empty_count > 100:
                        # Check if process actually exited
                        if self._process_handle:
                            import ctypes
                            ret = ctypes.windll.kernel32.WaitForSingleObject(
                                int(self._process_handle), 0
                            )
                            if ret == 0:  # WAIT_OBJECT_0 = process exited
                                break
                        empty_count = 0
                    time.sleep(0.01)
                    continue
                empty_count = 0
                self._has_output = True

                # Track recent output for prompt detection
                with self._recent_lock:
                    self._recent_output += data
                    if len(self._recent_output) > 1000:
                        self._recent_output = self._recent_output[-500:]
                    # Comprehensive ANSI strip for prompt detection
                    clean = self._strip_ansi(self._recent_output)
                    if re.search(r'❯', clean) or re.search(r'\?\s*for\s*shortcuts', clean) or \
                       re.search(r'^\s*>\s*$', clean, re.MULTILINE):  # Codex prompt
                        if not self._prompt_event.is_set():
                            import sys
                            print(f"[CC-PROMPT] Detected CLI prompt ready", flush=True)
                        self._prompt_event.set()

                # Debug log raw output
                if self._debug_log:
                    try:
                        clean = self._strip_ansi(data)
                        if clean.strip():
                            self._debug_log.write(f"[RAW] {repr(data[:200])}\n")
                            self._debug_log.write(f"[CLN] {clean.strip()[:200]}\n")
                            self._debug_log.flush()
                    except Exception:
                        pass

                # Push to xterm.js
                self._push_output(data)

                # Rate limit monitoring
                clean = self._strip_ansi(data).lower()
                rate_buffer += clean
                if len(rate_buffer) > 10000:
                    rate_buffer = rate_buffer[-5000:]

                now = time.time()
                if now - self._rate_cooldown > 30:  # 30s cooldown
                    for pat in self._rate_patterns:
                        if re.search(pat, rate_buffer, re.MULTILINE):
                            if self._on_rate_limit:
                                import sys
                                print(f"[CC-RATE] Pattern matched: {pat}", flush=True)
                                print(f"[CC-RATE] Context: {repr(clean[-300:])}", flush=True)
                                self._rate_cooldown = now
                                self._on_rate_limit()
                            rate_buffer = ""
                            break

                # Auto-approve ALL permission/trust/confirmation prompts
                # Use accumulated recent output (not per-chunk) so split text is detected
                with self._recent_lock:
                    recent_clean = self._strip_ansi(self._recent_output).lower()

                # Workspace trust prompts — always safe to approve
                if not self._perm_approved and \
                   'trust' in recent_clean and ('contents' in recent_clean or 'directory' in recent_clean or 'folder' in recent_clean or 'workspace' in recent_clean):
                    self._pty.write("y\r")
                    self._perm_approved = True  # Only prevents re-triggering trust, NOT write prompts
                    with self._recent_lock:
                        self._recent_output = ""
                    import sys
                    print(f"[CC-PERM] Auto-approved workspace trust", flush=True)
                # Read-only tool prompts — auto-approve (always checked, not gated by _perm_approved)
                elif re.search(r'(allow|permit).{0,30}(read|glob|grep|search|list|view|cat)', recent_clean):
                    self._pty.write("y\r")
                    with self._recent_lock:
                        self._recent_output = ""
                    import sys
                    print(f"[CC-PERM] Auto-approved read tool", flush=True)
                # Write/edit/execute/delete prompts — surface to UI (always checked)
                elif re.search(r'(allow|permit|approve).{0,30}(write|edit|create|delete|execute|bash|run|install|remove)', recent_clean):
                    perm_text = recent_clean.strip()[-300:]
                    self._push_status("permission_request", perm_text)
                    with self._recent_lock:
                        self._recent_output = ""
                    import sys
                    print(f"[CC-PERM] Write permission → UI: {perm_text[:80]}", flush=True)
                # Generic y/n with read/trust context — auto-approve
                elif re.search(r'y/n|yes/no', recent_clean) and \
                     re.search(r'(read|trust|parent|access|continue)', recent_clean):
                    self._pty.write("y\r")
                    with self._recent_lock:
                        self._recent_output = ""
                    import sys
                    print(f"[CC-PERM] Auto-approved read/trust y/n", flush=True)
                # Generic y/n with write context — surface to UI
                elif re.search(r'y/n|yes/no', recent_clean) and \
                     re.search(r'(write|edit|delete|execute|overwrite|remove)', recent_clean):
                    perm_text = recent_clean.strip()[-300:]
                    self._push_status("permission_request", perm_text)
                    with self._recent_lock:
                        self._recent_output = ""
                    import sys
                    print(f"[CC-PERM] Write y/n → UI: {perm_text[:80]}", flush=True)

                # Auth detection — check if CLI is prompting for login
                if not self._auth_detected:
                    # Very specific patterns to avoid false positives from prompt text
                    auth_patterns = [
                        r'claude\s+auth\s+login',
                        r'codex\s+login\b',
                        r'not\s+logged\s+in.*run\s',
                        r'please\s+(run|execute).*login',
                        r'session\s+expired.*re-?auth',
                        r'open\s+this\s+url\s+in\s+your\s+browser',
                        r'enter\s+(the\s+)?code\s+shown',
                        r'device\s+code.*https?://',
                    ]
                    for pat in auth_patterns:
                        if re.search(pat, clean, re.IGNORECASE):
                            self._auth_detected = True
                            self._push_status("auth_required",
                                f"CLI needs authentication. Check the terminal panel.")
                            import sys
                            print(f"[CC-AUTH] Auth prompt detected: {pat}", flush=True)
                            break

            except Exception:
                if self._alive:
                    time.sleep(0.05)
                    continue
                break

        # Process ended
        self._alive = False
        self._push_status("session_ended")

    def kill_session(self):
        """Kill the current PTY process and entire process tree."""
        self._alive = False
        self._prompt_event.set()  # Unblock any waiting PromptSend thread
        if self._pty:
            try:
                self._pty.write("\x03")  # Ctrl+C
                time.sleep(0.2)
            except Exception:
                pass
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                # #9 fix: Close Job Object first — kills entire process tree
                if self._job_handle:
                    kernel32.CloseHandle(self._job_handle)
                    self._job_handle = None
                elif self._process_handle:
                    # Fallback: kill root process only
                    kernel32.TerminateProcess(int(self._process_handle), 1)
                if self._process_handle:
                    kernel32.CloseHandle(int(self._process_handle))
            except Exception:
                pass
            self._pty = None
            self._process_handle = None

        if self._read_thread and self._read_thread.is_alive():
            self._read_thread.join(timeout=2)
        self._read_thread = None

        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=1)
        self._flush_thread = None

    def is_alive(self) -> bool:
        return self._alive

    def has_output(self) -> bool:
        """Returns True if any output has been received from the PTY."""
        return self._has_output

    _has_output = False

    @staticmethod
    def _strip_ansi(s: str) -> str:
        """Comprehensive ANSI/control sequence stripping."""
        s = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', s)   # CSI
        s = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', s)  # OSC
        s = re.sub(r'\x1b[PX^_][^\x1b]*\x1b\\', '', s)  # DCS/PM/APC
        s = re.sub(r'\x1b[>=<()#][0-9]*', '', s)         # Mode chars
        s = re.sub(r'\x1b[a-zA-Z]', '', s)               # ESC+letter
        s = re.sub(r'\[\?[0-9;]*[a-zA-Z]', '', s)        # Orphan private modes
        s = s.replace('\r', '').replace('\x07', '')
        s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
        return s

    def wait_for_prompt(self, timeout: float = 30.0) -> bool:
        """Block until the CLI prompt appears in the output stream."""
        return self._prompt_event.wait(timeout=timeout)

    def set_rate_patterns(self, patterns: list[str]):
        self._rate_patterns = patterns

    def write(self, data: str):
        if self._pty and self._alive:
            self._pty.write(data)

    def notify_switch_start(self, text: str):
        self._push_status("switching", text)

    def notify_switch_done(self, text: str):
        self._push_status("switched", text)

    def notify_rate_limit_ask(self, text: str, cwd: str = "", md_hint: str = ""):
        """Show rate limit banner with Switch/Dismiss buttons."""
        self._push_status("rate_limit_ask", text)

    def dump_js_debug(self):
        """Dump JS-side debug log to the debug log file."""
        if self._window and self._debug_log:
            try:
                js_log = self._window.evaluate_js("window.getDebugLog ? window.getDebugLog() : ''")
                if js_log:
                    self._debug_log.write("\n=== JS DEBUG LOG ===\n")
                    self._debug_log.write(js_log)
                    self._debug_log.write("\n=== END JS DEBUG ===\n")
                    self._debug_log.flush()
            except Exception:
                pass

    def stop(self):
        self.dump_js_debug()
        if self._debug_log:
            try:
                self._debug_log.close()
            except Exception:
                pass
            self._debug_log = None
        self.kill_session()
