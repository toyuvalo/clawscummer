"""
JS<->Python API bridge for pywebview.
Exposes Python methods callable from JavaScript in the GUI.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clawscummer import (
    AccountManager, ConversationScanner, ContextExtractor,
    HandoffManager, MDScanner, PlanExecuteManager,
    WorkflowMode,
    CLAUDE_RATE_PATTERNS, GEMINI_RATE_PATTERNS, CONTEXT_FULL_PATTERNS,
)
from gui.terminal import TerminalManager

# Expose WorkflowMode values for the frontend dropdown
WORKFLOW_MODES = [
    {"value": "auto", "label": "AUTO"},
    {"value": "plan_execute", "label": "PLAN > EXEC"},
    {"value": "manual", "label": "MANUAL"},
]


class Api:
    """pywebview js_api — called from JavaScript in the GUI."""

    def __init__(self, terminal: TerminalManager, window=None):
        self._terminal = terminal
        self._window = window
        self._am = AccountManager()
        self._scanner = ConversationScanner()
        self._current_cli_type = "claude"
        self._current_cwd = os.getcwd()
        self._switch_lock = threading.Lock()
        self._launch_lock = threading.Lock()
        self._session_start_time = 0  # timestamp when current session launched

    def set_window(self, window):
        self._window = window
        self._terminal.set_window(window)

    # ── Account Management ────────────────────────────────────────────────────
    def get_accounts(self) -> str:
        accounts = self._am.load_accounts()
        active_id = self._am.get_active_id()
        return json.dumps({
            "accounts": [a.to_dict() for a in accounts],
            "active_id": active_id,
        })

    def get_active_account(self) -> str:
        acc = self._am.get_active_account()
        return json.dumps(acc.to_dict() if acc else {})

    def switch_account(self, account_id: str) -> str:
        accounts = self._am.load_accounts()
        acc = next((a for a in accounts if a.id == account_id), None)
        if acc:
            self._am.switch_to(acc)
            self._current_cli_type = acc.cli_type
            return json.dumps({"ok": True, "label": acc.label, "cli_type": acc.cli_type})
        return json.dumps({"ok": False})

    def add_account(self, label: str, cli_type: str) -> str:
        # Verify the CLI is installed
        cli_cmd = {"claude": "claude", "gemini": "gemini", "codex": "codex"}.get(cli_type, cli_type)
        if not shutil.which(cli_cmd):
            return json.dumps({"ok": False, "error": f"'{cli_cmd}' not found in PATH. Install it first."})
        acc = self._am.add_account(label, cli_type)
        return json.dumps({"ok": True, "id": acc.id, "label": acc.label})

    # ── Workflow Mode ─────────────────────────────────────────────────────────
    def get_workflow_mode(self) -> str:
        return self._am.get_workflow_mode().value

    def get_workflow_modes(self) -> str:
        return json.dumps(WORKFLOW_MODES)

    def set_workflow_mode(self, mode: str) -> str:
        try:
            wm = WorkflowMode(mode)
            self._am.set_workflow_mode(wm)
            return json.dumps({"ok": True})
        except ValueError:
            return json.dumps({"ok": False})

    # ── Conversations ─────────────────────────────────────────────────────────
    def get_conversations(self) -> str:
        convs = self._scanner.get_wip(5)
        return json.dumps([
            {
                "session_id": c.session_id,
                "project_key": c.project_key,
                "project_path": c.project_path,
                "topic": c.topic,
                "last_message": c.last_message,
                "message_count": c.message_count,
                "last_timestamp": c.last_timestamp.isoformat(),
                "is_wip": c.is_wip,
                "cli_type": c.cli_type,
            }
            for c in convs
        ])

    # ── PTY I/O (called from JS) ─────────────────────────────────────────────
    def pty_input(self, data: str):
        self._terminal.pty_input(data)

    def pty_resize(self, cols: int, rows: int):
        self._terminal.pty_resize(cols, rows)

    # ── Session Launch ────────────────────────────────────────────────────────
    def launch_session(self, action: str, session_id: str = "",
                       project_path: str = "", prompt: str = "") -> str:
        if not self._launch_lock.acquire(blocking=False):
            return json.dumps({"ok": False, "error": "Launch already in progress"})
        try:
            return self._do_launch(action, session_id, project_path, prompt)
        finally:
            self._launch_lock.release()

    def _do_launch(self, action: str, session_id: str, project_path: str,
                   prompt: str) -> str:
        acc = self._am.get_active_account()
        if not acc:
            return json.dumps({"ok": False, "error": "No active account"})

        cli_type = acc.cli_type
        self._current_cli_type = cli_type

        # Set working directory
        cwd = self._current_cwd
        if action == "resume" and project_path and os.path.isdir(project_path):
            cwd = project_path

        # Scan for additional MD instructions
        md_hint = MDScanner.format_hint(MDScanner.scan(cwd))

        # Set up rate limit monitoring
        rate_map = {"claude": CLAUDE_RATE_PATTERNS, "gemini": GEMINI_RATE_PATTERNS,
                    "codex": CLAUDE_RATE_PATTERNS}
        patterns = rate_map.get(cli_type, CLAUDE_RATE_PATTERNS) + CONTEXT_FULL_PATTERNS
        self._terminal.set_rate_patterns(patterns)

        # Handle Plan→Execute mode
        mode = self._am.get_workflow_mode()
        if mode == WorkflowMode.PLAN_EXECUTE and prompt and action == "new":
            return self._launch_plan_execute(prompt, cwd, md_hint, cli_type)

        # Build command
        cmd = self._build_cmd(cli_type, action, prompt, md_hint)

        # Launch in PTY
        self._terminal._on_rate_limit = lambda: self._handle_rate_limit(cwd, md_hint)
        self._session_start_time = time.time()
        self._terminal.start_session(cmd, cwd)

        # Send prompt via PTY input after CLI prompt appears
        if prompt and action == "new":
            full_prompt = prompt + md_hint
            def _send_prompt():
                if self._terminal.wait_for_prompt(timeout=30):
                    time.sleep(1.0)  # Let Ink initialize input handler
                    if not self._terminal.is_alive():
                        return
                    # Write raw text (NO bracketed paste — Ink doesn't support it)
                    self._terminal.write(full_prompt)
                    time.sleep(0.3)
                    if self._terminal.is_alive():
                        self._terminal.write("\r")
                    import sys
                    print(f"[CC-PROMPT] Sent prompt ({len(full_prompt)} chars) + Enter", flush=True)
            threading.Thread(target=_send_prompt, daemon=True, name="PromptSend").start()

            # Notify frontend that auto-prompt was sent (so it discards the echo)
            if self._window:
                self._window.evaluate_js("window.autoPromptSent && window.autoPromptSent()")

        return json.dumps({"ok": True, "cli_type": cli_type})

    def _build_cmd(self, cli_type: str, action: str, prompt: str = "",
                   md_hint: str = "") -> list[str]:
        if cli_type == "claude":
            # Pre-approve read tools only — writes still prompt
            cmd = ["claude", "--allowedTools", "Read,Glob,Grep,Bash(git:*)"]
            if action == "resume":
                cmd.append("--continue")
        elif cli_type == "codex":
            # untrusted: prompt for everything except reads
            cmd = ["codex", "-a", "untrusted"]
            if action == "resume":
                cmd = ["codex", "resume", "--last"]
        else:  # gemini
            # default: prompt for all tool use (reads auto-approved by our PTY detection)
            cmd = ["gemini", "--approval-mode", "default"]
            if action == "resume":
                cmd.extend(["--resume", "latest"])
        return cmd

    def _launch_plan_execute(self, prompt: str, cwd: str, md_hint: str,
                             cli_type: str) -> str:
        """Run Gemini plan phase, then launch Claude for execution.
        Note: Called while _launch_lock is held by launch_session.
        The thread runs WITHOUT re-acquiring — the lock is released
        by launch_session's finally block before the thread needs it.
        """
        threading.Thread(
            target=self.__run_plan_execute,
            args=(prompt, cwd, md_hint, cli_type),
            daemon=True, name="PlanExec"
        ).start()
        return json.dumps({"ok": True, "mode": "plan_execute"})

    def __run_plan_execute(self, prompt: str, cwd: str, md_hint: str,
                           cli_type: str):
        """Inner plan-execute logic (runs under _launch_lock)."""
        self._terminal.notify_switch_start("Planning with Gemini...")

        if PlanExecuteManager.check_gemini_available():
            plan = PlanExecuteManager.generate_plan(prompt, cwd)
            if plan:
                exec_prompt = PlanExecuteManager.get_execution_prompt()
                # Switch to Claude for execution
                accounts = self._am.load_accounts()
                claude_acc = next(
                    (a for a in accounts if a.cli_type == "claude"), None
                )
                if claude_acc:
                    self._am.switch_to(claude_acc)
                    self._current_cli_type = "claude"

                cmd = ["claude", exec_prompt + md_hint]
                patterns = CLAUDE_RATE_PATTERNS + CONTEXT_FULL_PATTERNS
                self._terminal.set_rate_patterns(patterns)
                self._terminal._on_rate_limit = (
                    lambda: self._handle_rate_limit(cwd, md_hint)
                )
                self._terminal.start_session(cmd, cwd)
                self._terminal.notify_switch_done("Plan ready. Executing with Claude...")
                if self._window:
                    self._window.evaluate_js("loadAccounts()")
                return

        # Fallback: direct launch
        self._terminal.notify_switch_done("Plan failed. Using direct mode.")
        cmd = self._build_cmd(cli_type, "new", prompt, md_hint)
        rate_map = {"claude": CLAUDE_RATE_PATTERNS, "gemini": GEMINI_RATE_PATTERNS,
                    "codex": CLAUDE_RATE_PATTERNS}
        patterns = rate_map.get(cli_type, CLAUDE_RATE_PATTERNS) + CONTEXT_FULL_PATTERNS
        self._terminal.set_rate_patterns(patterns)
        self._terminal._on_rate_limit = (
            lambda: self._handle_rate_limit(cwd, md_hint)
        )
        self._terminal.start_session(cmd, cwd)

    def _handle_rate_limit(self, cwd: str, md_hint: str):
        """Called when rate limit / context full is detected in the PTY stream.

        Shows a confirmation banner instead of auto-switching.
        The terminal stays open — user decides what to do.
        """
        if not self._switch_lock.acquire(blocking=False):
            return
        try:
            prev_cli_type = self._current_cli_type
            nxt = self._am.peek_next()

            if nxt:
                self._terminal.notify_rate_limit_ask(
                    f"Rate limit detected on {prev_cli_type.title()}. Switch to {nxt.label}?",
                    cwd, md_hint
                )
            else:
                self._terminal.notify_rate_limit_ask(
                    "Rate limit detected. No other accounts available.",
                    cwd, md_hint
                )
        finally:
            self._switch_lock.release()

    def confirm_switch(self) -> str:
        """Called from JS when user clicks 'Switch' on the rate limit banner."""
        if not self._switch_lock.acquire(blocking=False):
            return json.dumps({"ok": False, "error": "Switch already in progress"})

        try:
            return self._do_switch()
        finally:
            self._switch_lock.release()

    def _do_switch(self) -> str:
        cwd = self._current_cwd
        prev_cli_type = self._current_cli_type

        # Extract context while current session is still alive
        if prev_cli_type == "claude":
            messages = ContextExtractor.extract_claude()
        else:
            messages = ContextExtractor.extract_gemini()

        # Kill and switch
        self._terminal.kill_session()
        time.sleep(0.5)

        nxt = self._am.rotate_to_next()
        if not nxt:
            return json.dumps({"ok": False, "error": "No other accounts"})

        self._current_cli_type = nxt.cli_type
        self._terminal.notify_switch_start(f"Switching to {nxt.label}...")

        try:
            md_hint = MDScanner.format_hint(MDScanner.scan(cwd))

            handoff_prompt = None
            if nxt.cli_type != prev_cli_type:
                instruction = HandoffManager.generate(messages, prev_cli_type, cwd)
                handoff_prompt = instruction + md_hint if instruction else None
                cmd = self._build_cmd(nxt.cli_type, "new")
            else:
                cmd = self._build_cmd(nxt.cli_type, "resume")

            patterns = (
                (CLAUDE_RATE_PATTERNS if nxt.cli_type == "claude"
                 else GEMINI_RATE_PATTERNS)
                + CONTEXT_FULL_PATTERNS
            )
            self._terminal.set_rate_patterns(patterns)
            self._terminal._on_rate_limit = (
                lambda: self._handle_rate_limit(cwd, md_hint)
            )

            self._session_start_time = time.time()
            self._terminal.start_session(cmd, cwd)

            # Inject handoff context via PTY after CLI is ready
            if handoff_prompt:
                def _send_handoff():
                    if self._terminal.wait_for_prompt(timeout=30):
                        time.sleep(1.0)
                        if self._terminal.is_alive():
                            self._terminal.write(handoff_prompt)
                            time.sleep(0.3)
                        if self._terminal.is_alive():
                            self._terminal.write("\r")
                        import sys
                        print(f"[CC-HANDOFF] Sent context ({len(handoff_prompt)} chars)", flush=True)
                threading.Thread(target=_send_handoff, daemon=True, name="HandoffSend").start()

            self._terminal.notify_switch_done(
                f"Switched to {nxt.label} ({nxt.cli_type.title()})"
            )

            if self._window:
                self._window.evaluate_js("loadAccounts()")

            return json.dumps({"ok": True})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})
        finally:
            HandoffManager.cleanup()

    def dismiss_rate_limit(self) -> str:
        """Called from JS when user dismisses the rate limit banner."""
        self._terminal.notify_switch_done("Dismissed. Session continues.")
        return json.dumps({"ok": True})

    def get_response_for_prompt(self, prompt_text: str) -> str:
        """Get the assistant response that follows a specific user prompt."""
        try:
            cli_type = self._current_cli_type
            if cli_type == "claude":
                text = self._read_response_for_prompt(prompt_text)
            elif cli_type == "gemini":
                text = self._read_gemini_response_for_prompt(prompt_text)
            else:
                text = None  # codex: TODO SQLite reader

            if text:
                return json.dumps({"ok": True, "text": text})
            return json.dumps({"ok": False, "error": "No response yet"})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    def _read_response_for_prompt(self, prompt_text: str) -> Optional[str]:
        """Find the assistant response that follows a specific user prompt."""
        from pathlib import Path
        projects_dir = Path.home() / ".claude" / "projects"
        if not projects_dir.exists():
            return None

        # Find project dir — check ALL dirs, use most recently modified
        best_dir = None
        best_time = 0
        for proj in projects_dir.iterdir():
            if not proj.is_dir():
                continue
            try:
                mt = proj.stat().st_mtime
                if mt > best_time:
                    best_time = mt
                    best_dir = proj
            except Exception:
                pass
        if not best_dir:
            return None

        # Find newest .jsonl after session start
        jsonl_files = list(best_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None
        session_files = [f for f in jsonl_files if f.stat().st_mtime >= self._session_start_time - 5]
        newest = max(session_files or jsonl_files, key=lambda f: f.stat().st_mtime)

        # Read messages
        messages = []
        for line in newest.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("type") in ("user", "assistant"):
                    content = obj.get("message", {}).get("content", "")
                    text = ContextExtractor._extract_text(content)
                    if text:
                        messages.append({"role": obj["type"], "text": text})
            except Exception:
                continue

        # Find the response that follows our specific prompt (substring match)
        prompt_clean = prompt_text.strip()[:40].lower()
        for i in range(len(messages) - 1, 0, -1):
            if messages[i]["role"] == "assistant" and messages[i - 1]["role"] == "user":
                if prompt_clean in messages[i - 1]["text"].lower():
                    return messages[i]["text"]

        return None

    def _read_gemini_response_for_prompt(self, prompt_text: str) -> Optional[str]:
        """Find the Gemini response that follows a specific user prompt."""
        from pathlib import Path
        projects_file = Path.home() / ".gemini" / "projects.json"
        if not projects_file.exists():
            return None
        try:
            data = json.loads(projects_file.read_text(encoding="utf-8"))
            slug = data.get("projects", {}).get(self._current_cwd.lower())
        except Exception:
            return None
        if not slug:
            return None

        chats_dir = Path.home() / ".gemini" / "tmp" / slug / "chats"
        if not chats_dir.exists():
            return None

        session_files = list(chats_dir.glob("session-*.json"))
        if not session_files:
            return None

        # Prefer files from the current session
        recent = [f for f in session_files if f.stat().st_mtime >= self._session_start_time - 5]
        newest = max(recent or session_files, key=lambda f: f.stat().st_mtime)

        try:
            messages = json.loads(newest.read_text(encoding="utf-8", errors="replace")).get("messages", [])
        except Exception:
            return None

        prompt_clean = prompt_text.strip()[:40].lower()
        for i in range(len(messages) - 1, 0, -1):
            msg = messages[i]
            prev = messages[i - 1]
            if msg.get("type") == "gemini" and prev.get("type") == "user":
                user_content = prev.get("content", [])
                user_text = " ".join(c.get("text", "") for c in user_content) \
                    if isinstance(user_content, list) else str(user_content)
                if prompt_clean in user_text.lower():
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        return content
        return None

    def get_last_response(self) -> str:
        """Read the last assistant message from Claude's conversation file.
        Called from JS when a turn completes — gives clean text without Ink rendering artifacts.
        Scoped to the current working directory's project folder."""
        try:
            cli_type = self._current_cli_type
            if cli_type == "claude":
                text = self._read_last_claude_response()
            elif cli_type == "gemini":
                text = self._read_gemini_response_for_prompt("")  # empty = last response
            else:
                text = None

            if text:
                return json.dumps({"ok": True, "text": text})
            return json.dumps({"ok": False, "error": "No assistant message found"})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    def _read_last_claude_response(self) -> Optional[str]:
        """Read last assistant message from the current project's conversation files."""
        from pathlib import Path
        projects_dir = Path.home() / ".claude" / "projects"
        if not projects_dir.exists():
            return None

        # Find the project dir matching our cwd
        cwd = self._current_cwd.replace("\\", "-").replace(":", "-").replace("/", "-")
        # Try to find matching project directory
        best_dir = None
        for proj in projects_dir.iterdir():
            if not proj.is_dir():
                continue
            # Match by checking if project key decodes to our cwd
            if proj.name.lower().replace("--", "-") == cwd.lower() or \
               proj.name.lower() in cwd.lower() or cwd.lower() in proj.name.lower():
                best_dir = proj
                break

        # Fallback: use the most recently modified project dir
        if not best_dir:
            dirs = [d for d in projects_dir.iterdir() if d.is_dir()]
            if dirs:
                best_dir = max(dirs, key=lambda d: d.stat().st_mtime)

        if not best_dir:
            return None

        # Find the most recently modified .jsonl file created AFTER session start
        jsonl_files = list(best_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None

        # Prefer files modified after the current session started
        session_files = [f for f in jsonl_files if f.stat().st_mtime >= self._session_start_time - 5]
        newest = max(session_files or jsonl_files, key=lambda f: f.stat().st_mtime)

        # Read messages and find the last assistant response AFTER a user message
        messages = []
        for line in newest.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("type") in ("user", "assistant"):
                    content = obj.get("message", {}).get("content", "")
                    text = ContextExtractor._extract_text(content)
                    if text:
                        messages.append({"role": obj["type"], "text": text})
            except Exception:
                continue

        # Walk backwards: find last assistant message that follows a user message
        # (skips Claude's auto-greeting which has no preceding user message)
        for i in range(len(messages) - 1, 0, -1):
            if messages[i]["role"] == "assistant" and messages[i - 1]["role"] == "user":
                return messages[i]["text"]

        # Fallback: return the very last assistant message
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                return msg["text"]

        return None

    def run_auth(self, cli_type: str) -> str:
        """Run auth command — launches in a separate visible console window
        so OAuth redirect works properly (PTY can mangle callback URLs)."""
        import subprocess

        if cli_type == "claude":
            cmd = ["claude", "auth", "login"]
        elif cli_type == "codex":
            cmd = ["codex", "login"]
        elif cli_type == "gemini":
            cmd = ["gemini", "/auth"]
        else:
            return json.dumps({"ok": False, "error": f"Unknown CLI type: {cli_type}"})

        try:
            # Open in a new visible console window so OAuth redirect works
            subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=self._current_cwd,
            )
            return json.dumps({"ok": True, "cmd": " ".join(cmd)})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    def import_creds(self, label: str, cli_type: str, creds_json: str) -> str:
        """Import credentials from a JSON string (user pastes or picks a file)."""
        try:
            creds = json.loads(creds_json)
            acc = self._am.add_account(label, cli_type)
            # Save creds to per-account file
            from pathlib import Path
            creds_dir = Path.home() / ".clawscummer" / "creds"
            creds_dir.mkdir(parents=True, exist_ok=True)
            (creds_dir / f"{acc.id}.json").write_text(
                json.dumps(creds, indent=2), encoding="utf-8"
            )
            return json.dumps({"ok": True, "id": acc.id, "label": acc.label})
        except json.JSONDecodeError:
            return json.dumps({"ok": False, "error": "Invalid JSON"})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    def pick_creds_file(self) -> str:
        """Open a file picker for credentials JSON file."""
        import webview
        if self._window:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=('JSON files (*.json)',),
            )
            if result and len(result) > 0:
                try:
                    content = open(result[0], 'r', encoding='utf-8').read()
                    # Validate it's JSON
                    json.loads(content)
                    return json.dumps({"ok": True, "content": content, "path": result[0]})
                except Exception as e:
                    return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": False, "error": "No file selected"})

    def kill_session(self) -> str:
        self._terminal.kill_session()
        return json.dumps({"ok": True})
