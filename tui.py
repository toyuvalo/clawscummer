#!/usr/bin/env python3
"""
ClawsCummer v3.0 — Terminal UI
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static
from textual import on
from rich.text import Text

from clawscummer import (
    AccountManager, ConversationScanner, Conversation, Account,
    CLAUDE_RATE_PATTERNS, CONTEXT_FULL_PATTERNS,
)

# ── CLI helpers ────────────────────────────────────────────────────────────────

CLI_ICON  = {"claude": "C", "gemini": "G", "codex": "X"}
CLI_COLOR = {"claude": "bold bright_blue", "gemini": "bold bright_cyan", "codex": "bold bright_yellow"}
CLI_CMDS  = {
    "claude": {"new": ["claude"],  "resume": ["claude", "--continue"]},
    "gemini": {"new": ["gemini"],  "resume": ["gemini", "--resume", "latest"]},
    "codex":  {"new": ["codex"],   "resume": ["codex", "resume"]},
}

# Known context windows in tokens
CONTEXT_WINDOWS = {"claude": 200_000, "gemini": 1_000_000, "codex": 128_000}


def _run(cmd: list[str], cwd: str) -> int:
    try:
        return subprocess.run(cmd, cwd=cwd).returncode
    except FileNotFoundError:
        print(f"\n\033[31m[ClawsCummer] '{cmd[0]}' not found in PATH\033[0m\n")
        time.sleep(2)
        return 1
    except KeyboardInterrupt:
        return 130


def _was_rate_limited(cli_type: str) -> bool:
    if cli_type == "claude":
        projects = Path.home() / ".claude" / "projects"
        if not projects.exists():
            return False
        dirs = sorted([d for d in projects.iterdir() if d.is_dir()],
                      key=lambda d: d.stat().st_mtime, reverse=True)
        for proj in dirs[:2]:
            for jf in sorted(proj.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:1]:
                try:
                    tail = " ".join(jf.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]).lower()
                    for pat in CLAUDE_RATE_PATTERNS + CONTEXT_FULL_PATTERNS:
                        if re.search(pat, tail):
                            return True
                except Exception:
                    pass
    return False


def _age(ts: datetime) -> str:
    diff = datetime.now(timezone.utc) - ts
    h = int(diff.total_seconds() // 3600)
    if h < 1:
        return "just now"
    if h < 24:
        return f"{h}h ago"
    return f"{h // 24}d ago"


def _estimate_ctx_used(cli_type: str) -> int:
    """Rough token estimate (bytes/4) from most recent session file."""
    try:
        if cli_type == "claude":
            projects = Path.home() / ".claude" / "projects"
            files = sorted(projects.rglob("*.jsonl"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            return files[0].stat().st_size // 4 if files else 0
        if cli_type == "gemini":
            files = sorted((Path.home() / ".gemini" / "tmp").rglob("session_*.json"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            return files[0].stat().st_size // 4 if files else 0
        if cli_type == "codex":
            files = sorted((Path.home() / ".codex" / "sessions").rglob("*.jsonl"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            return files[0].stat().st_size // 4 if files else 0
    except Exception:
        pass
    return 0


def _ctx_label(cli_type: str, used: int) -> tuple[str, str]:
    """Return (text, style) for context remaining display."""
    total = CONTEXT_WINDOWS.get(cli_type, 0)
    if total == 0 or used == 0:
        return "", ""
    remaining = max(0, total - used)
    pct = remaining / total
    used_k = used // 1000
    total_k = total // 1000
    text = f" {remaining//1000}k/{total_k}k"
    if pct > 0.5:
        style = "dim green"
    elif pct > 0.2:
        style = "dim yellow"
    else:
        style = "dim red"
    return text, style


def _copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["clip"], input=text.encode("utf-8"), check=True,
                       capture_output=True)
        return True
    except Exception:
        return False


# ── List item widgets ──────────────────────────────────────────────────────────

class SessionItem(ListItem):
    def __init__(self, conv: Conversation) -> None:
        super().__init__()
        self.conv = conv

    def compose(self) -> ComposeResult:
        t = Text(overflow="ellipsis", no_wrap=True)
        icon = CLI_ICON.get(self.conv.cli_type, "?")
        color = CLI_COLOR.get(self.conv.cli_type, "white")
        t.append(f" {icon} ", style=color)
        topic = self.conv.topic[:60] if self.conv.topic else "(no topic)"
        t.append(topic)
        t.append(f"  {_age(self.conv.last_timestamp)}", style="dim")
        yield Label(t)


class AccountItem(ListItem):
    def __init__(self, acc: Account, active: bool,
                 ctx_used: int = 0) -> None:
        super().__init__()
        self.acc = acc
        self.active = active
        self._ctx_used = ctx_used

    def compose(self) -> ComposeResult:
        t = Text()
        dot = "● " if self.active else "  "
        t.append(dot, style="bright_green" if self.active else "dim")
        icon = CLI_ICON.get(self.acc.cli_type, "?")
        color = CLI_COLOR.get(self.acc.cli_type, "white")
        t.append(f"[{icon}] ", style=color)
        t.append(self.acc.label, style="bold" if self.active else "")
        if self.active:
            t.append("  active", style="dim bright_green")
        ctx_text, ctx_style = _ctx_label(self.acc.cli_type, self._ctx_used)
        if ctx_text:
            t.append(ctx_text, style=ctx_style)
        yield Label(t)


# ── Main App ───────────────────────────────────────────────────────────────────

class ClawsCummerTUI(App):

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        background: #0c0c11;
        color: #e2e8f4;
    }

    #header {
        height: 3;
        background: #0a0a0f;
        border-bottom: solid #1e1e2e;
        padding: 0 2;
        layout: horizontal;
        align: left middle;
        dock: top;
    }
    #logo {
        color: #818cf8;
        text-style: bold;
        width: auto;
        margin-right: 3;
    }
    #active-badge {
        color: #a1a1aa;
        width: auto;
    }

    #body {
        layout: horizontal;
        height: 1fr;
        padding: 1 2 0 2;
    }

    #left-col {
        width: 2fr;
        margin-right: 2;
    }
    #right-col {
        width: 1fr;
    }

    .col-title {
        color: #4f46e5;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }

    ListView {
        background: #111119;
        border: solid #27273a;
        height: auto;
        max-height: 18;
        scrollbar-size: 1 1;
    }
    ListView:focus {
        border: solid #4f46e5;
    }
    ListItem {
        padding: 0 1;
        background: transparent;
        height: 1;
    }
    ListItem:hover {
        background: #1a1a35;
    }
    ListItem.--highlight {
        background: #1e1e35;
    }

    #prompt-bar {
        height: 3;
        background: #0a0a0f;
        border-top: solid #1e1e2e;
        padding: 0 2;
        layout: horizontal;
        align: left middle;
    }
    #prompt-label {
        color: #4f46e5;
        text-style: bold;
        width: auto;
        margin-right: 1;
    }
    #prompt-input {
        background: #111119;
        border: tall #27273a;
        width: 1fr;
        color: #e2e8f4;
    }
    #prompt-input:focus {
        border: tall #4f46e5;
    }

    #hint {
        dock: bottom;
        height: 1;
        background: #111119;
        color: #3f3f56;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("enter",   "launch_new",       "Launch",      show=True),
        Binding("r",       "launch_resume",    "Resume",      show=True),
        Binding("d",       "delete_account",   "Delete acct", show=True),
        Binding("tab",     "focus_next",       "Next pane",   show=True),
        Binding("ctrl+q",  "quit",             "Quit",        show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._am = AccountManager()
        self._scanner = ConversationScanner()
        self._sessions: list[Conversation] = []
        self._accounts: list[Account] = []
        self._ctx_used: dict[str, int] = {}

    # ── Layout ─────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static("CLAWSCUMMER  v3.0", id="logo")
            yield Static("", id="active-badge")
        with Horizontal(id="body"):
            with Vertical(id="left-col"):
                yield Static("Recent Sessions", classes="col-title")
                yield ListView(id="sessions-list")
            with Vertical(id="right-col"):
                yield Static("Accounts", classes="col-title")
                yield ListView(id="accounts-list")
        with Horizontal(id="prompt-bar"):
            yield Static("Quick launch:", id="prompt-label")
            yield Input(placeholder="type a prompt and press Enter to launch...",
                        id="prompt-input")
        yield Static(
            "Enter = launch new   R = resume   D = delete account   Tab = switch pane   Ctrl+Q = quit",
            id="hint",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#sessions-list").focus()
        # Background context estimation
        self.set_interval(30, self._refresh_ctx)
        threading.Thread(target=self._refresh_ctx_bg, daemon=True).start()

    def _refresh_ctx_bg(self) -> None:
        for cli in ("claude", "gemini", "codex"):
            self._ctx_used[cli] = _estimate_ctx_used(cli)
        self.call_from_thread(self._refresh_accounts_list)

    def _refresh_ctx(self) -> None:
        threading.Thread(target=self._refresh_ctx_bg, daemon=True).start()

    def _refresh(self) -> None:
        self._accounts = self._am.load_accounts()
        self._sessions = self._scanner.get_wip(12)

        acc = self._am.get_active_account()
        if acc:
            icon = CLI_ICON.get(acc.cli_type, "?")
            badge = f"[{icon}]  {acc.label}  ·  {acc.cli_type.title()}"
        else:
            badge = "no account"
        self.query_one("#active-badge").update(badge)

        sl = self.query_one("#sessions-list", ListView)
        sl.clear()
        if self._sessions:
            for s in self._sessions:
                sl.append(SessionItem(s))
        else:
            sl.append(ListItem(Label(Text("  (no recent sessions)", style="dim"))))

        self._refresh_accounts_list()

    def _refresh_accounts_list(self) -> None:
        active_id = self._am.get_active_id()
        al = self.query_one("#accounts-list", ListView)
        al.clear()
        for a in self._accounts:
            used = self._ctx_used.get(a.cli_type, 0)
            al.append(AccountItem(a, active=(a.id == active_id), ctx_used=used))

    # ── Launch logic ───────────────────────────────────────────────────────────

    def _launch(self, action: str, conv: Conversation | None = None,
                prompt: str = "") -> None:
        acc = self._am.get_active_account()
        if not acc:
            self.notify("No account selected!", severity="error")
            return

        cwd = os.getcwd()
        if conv and conv.project_path and os.path.isdir(conv.project_path):
            cwd = conv.project_path

        launch_cli = conv.cli_type if (action == "resume" and conv) else acc.cli_type

        if action == "resume" and conv and conv.session_id:
            if launch_cli == "claude":
                cmd = ["claude", "--resume", conv.session_id]
            elif launch_cli == "gemini":
                cmd = ["gemini", "--resume", conv.session_id]
            elif launch_cli == "codex":
                cmd = ["codex", "resume", conv.session_id]
            else:
                cmd = CLI_CMDS.get(launch_cli, {}).get("resume", [launch_cli])
        else:
            cmd = CLI_CMDS.get(launch_cli, {}).get("new", [launch_cli])

        # If a prompt was given, copy to clipboard so user can paste it
        clipped = False
        if prompt:
            clipped = _copy_to_clipboard(prompt)

        with self.suspend():
            w = os.get_terminal_size().columns
            line = "-" * w
            icon = CLI_ICON.get(launch_cli, "?")
            label = f"  {icon}  {acc.label}  ·  {launch_cli.title()}  ·  {cwd}"
            print(f"\033[38;5;61m{line}\033[0m")
            print(f"\033[38;5;61m{label}\033[0m")
            if clipped:
                print(f"\033[33m  Prompt copied to clipboard — paste it once the CLI is ready\033[0m")
            print(f"\033[38;5;61m{line}\033[0m\n")

            _run(cmd, cwd)

            if _was_rate_limited(acc.cli_type):
                nxt = self._am.peek_next()
                if nxt:
                    nxt_icon = CLI_ICON.get(nxt.cli_type, "?")
                    print(f"\n\033[33m[ClawsCummer] Rate limit detected.\033[0m")
                    print(f"\033[33mNext account: [{nxt_icon}] {nxt.label}\033[0m")
                    ans = input("Switch and continue? [Y/n] ").strip().lower()
                    if ans != "n":
                        self._am.rotate_to_next()
                        acc2 = self._am.get_active_account()
                        cmd2 = CLI_CMDS.get(acc2.cli_type, {}).get("new", [acc2.cli_type])
                        print(f"\033[32m[ClawsCummer] Switched to {acc2.label}. Launching...\033[0m\n")
                        _run(cmd2, cwd)
                else:
                    print(f"\n\033[33m[ClawsCummer] Rate limit detected. No other accounts available.\033[0m")
                    input("Press Enter to return to launcher...")

        self._refresh()

    # ── Actions ────────────────────────────────────────────────────────────────

    def action_launch_new(self) -> None:
        # If prompt bar is focused, let Input handle it
        focused = self.focused
        if focused and focused.id == "prompt-input":
            return
        self._launch("new")

    def action_launch_resume(self) -> None:
        sl = self.query_one("#sessions-list", ListView)
        item = sl.highlighted_child
        conv = item.conv if isinstance(item, SessionItem) else None
        self._launch("resume", conv)

    def action_delete_account(self) -> None:
        al = self.query_one("#accounts-list", ListView)
        item = al.highlighted_child
        if not isinstance(item, AccountItem):
            return
        acc = item.acc
        active_id = self._am.get_active_id()
        if acc.id == active_id:
            self.notify("Cannot delete active account. Switch first.", severity="warning")
            return
        self._am.delete_account(acc.id)
        self._accounts = self._am.load_accounts()
        self.notify(f"Deleted: {acc.label}")
        self._refresh()

    # ── Prompt bar ─────────────────────────────────────────────────────────────

    @on(Input.Submitted, "#prompt-input")
    def on_prompt_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        event.input.clear()
        if prompt:
            self._launch("new", prompt=prompt)

    # ── Click handlers ─────────────────────────────────────────────────────────

    @on(ListView.Selected, "#sessions-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            self._launch("resume", event.item.conv)

    @on(ListView.Selected, "#accounts-list")
    def on_account_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, AccountItem):
            acc = event.item.acc
            self._am.switch_to(acc)
            self._refresh()
            self.notify(f"Active: [{CLI_ICON.get(acc.cli_type,'?')}] {acc.label}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ClawsCummerTUI().run()
