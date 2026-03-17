#!/usr/bin/env python3
"""
ClawsCummer v1.0 — Multi-account Claude session manager
Seamlessly switch between Claude accounts on rate limit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
    from textual.screen import ModalScreen, Screen
    from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Rule, Static
except ImportError:
    print("\n[ClawsCummer] Required packages not installed. Running: pip install textual rich\n")
    subprocess.run([sys.executable, "-m", "pip", "install", "textual", "rich", "-q"], check=True)
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
    from textual.screen import ModalScreen, Screen
    from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Rule, Static

# ── Paths ─────────────────────────────────────────────────────────────────────
CLAUDE_DIR    = Path.home() / ".claude"
ACCOUNTS_FILE = CLAUDE_DIR / "clawscummer_accounts.json"
CREDS_FILE    = CLAUDE_DIR / ".credentials.json"
PROJECTS_DIR  = CLAUDE_DIR / "projects"
LAUNCH_FILE   = CLAUDE_DIR / "clawscummer_launch.json"
SIGNAL_FILE   = CLAUDE_DIR / "clawscummer_switch.signal"

RATE_LIMIT_PATTERNS = [
    r"rate[\s_-]?limit",
    r"too many requests",
    r'"status":\s*429',
    r"overload",
    r"quota.{0,20}exceed",
    r"usage.{0,20}limit",
    r"claude is currently unavailable",
    r"exceeded.{0,30}limit",
]

LOGO = r"""
   ██████╗██╗      █████╗ ██╗    ██╗███████╗
  ██╔════╝██║     ██╔══██╗██║    ██║██╔════╝
  ██║     ██║     ███████║██║ █╗ ██║███████╗
  ██║     ██║     ██╔══██║██║███╗██║╚════██║
  ╚██████╗███████╗██║  ██║╚███╔███╔╝███████║
   ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝ ╚══════╝
   ██████╗██╗   ██╗███╗   ███╗███╗   ███╗███████╗██████╗
  ██╔════╝██║   ██║████╗ ████║████╗ ████║██╔════╝██╔══██╗
  ██║     ██║   ██║██╔████╔██║██╔████╔██║█████╗  ██████╔╝
  ██║     ██║   ██║██║╚██╔╝██║██║╚██╔╝██║██╔══╝  ██╔══██╗
  ╚██████╗╚██████╔╝██║ ╚═╝ ██║██║ ╚═╝ ██║███████╗██║  ██║
   ╚═════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝
"""

# ── Data Classes ──────────────────────────────────────────────────────────────
@dataclass
class Account:
    id: str
    label: str
    email: str = ""
    credentials: dict = field(default_factory=dict)
    last_used: str = ""

    def to_dict(self):  return asdict(self)
    @classmethod
    def from_dict(cls, d): return cls(**d)


@dataclass
class Conversation:
    session_id:    str
    project_key:   str
    project_path:  str
    topic:         str
    last_message:  str
    message_count: int
    last_timestamp: datetime
    is_wip:        bool
    jsonl_path:    Path


# ── Account Manager ───────────────────────────────────────────────────────────
class AccountManager:
    def __init__(self):
        self._bootstrap()

    def _bootstrap(self):
        if ACCOUNTS_FILE.exists():
            return
        data: dict = {"accounts": [], "active_id": None}
        if CREDS_FILE.exists():
            try:
                creds = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
                email = self._find_email(creds)
                acc = Account(id="account_1", label="Account 1", email=email,
                              credentials=creds,
                              last_used=datetime.now(timezone.utc).isoformat())
                data["accounts"].append(acc.to_dict())
                data["active_id"] = "account_1"
            except Exception:
                pass
        ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _find_email(creds: dict) -> str:
        for k, v in creds.items():
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if "email" in kk.lower() and isinstance(vv, str):
                        return vv
            if "email" in str(k).lower() and isinstance(v, str):
                return v
        return ""

    def _load_raw(self) -> dict:
        try:   return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        except: return {"accounts": [], "active_id": None}

    def _save_raw(self, data: dict):
        ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_accounts(self) -> list[Account]:
        return [Account.from_dict(a) for a in self._load_raw().get("accounts", [])]

    def get_active_id(self) -> Optional[str]:
        return self._load_raw().get("active_id")

    def get_active_account(self) -> Optional[Account]:
        active_id = self.get_active_id()
        return next((a for a in self.load_accounts() if a.id == active_id), None)

    def switch_to(self, account: Account):
        """Swap .credentials.json to the target account, back-save current."""
        data = self._load_raw()
        accounts = [Account.from_dict(a) for a in data.get("accounts", [])]
        active_id = data.get("active_id")

        # Save current live credentials back to the currently-active account
        if active_id and CREDS_FILE.exists():
            try:
                live = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
                for a in accounts:
                    if a.id == active_id:
                        a.credentials = live
            except Exception:
                pass

        # Write target credentials to disk
        if account.credentials:
            CREDS_FILE.write_text(json.dumps(account.credentials, indent=2), encoding="utf-8")

        account.last_used = datetime.now(timezone.utc).isoformat()
        data["accounts"] = [a.to_dict() for a in accounts]
        data["active_id"] = account.id

        # Merge updated target back in
        for i, a in enumerate(data["accounts"]):
            if a["id"] == account.id:
                data["accounts"][i] = account.to_dict()

        self._save_raw(data)

    def rotate_to_next(self) -> Optional[Account]:
        """Auto-rotate to next account in list."""
        accounts = self.load_accounts()
        if len(accounts) <= 1:
            return None
        active_id = self.get_active_id()
        ids = [a.id for a in accounts]
        idx = ids.index(active_id) if active_id in ids else -1
        next_account = accounts[(idx + 1) % len(accounts)]
        self.switch_to(next_account)
        return next_account

    def add_current_credentials(self, label: str) -> Account:
        data = self._load_raw()
        accounts = [Account.from_dict(a) for a in data.get("accounts", [])]
        new_id = f"account_{len(accounts) + 1}"
        creds = {}
        if CREDS_FILE.exists():
            try: creds = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
            except: pass
        acc = Account(id=new_id, label=label, email=self._find_email(creds),
                      credentials=creds, last_used=datetime.now(timezone.utc).isoformat())
        accounts.append(acc)
        data["accounts"] = [a.to_dict() for a in accounts]
        self._save_raw(data)
        return acc


# ── Conversation Scanner ──────────────────────────────────────────────────────
class ConversationScanner:
    @staticmethod
    def decode_path(key: str) -> str:
        """Best-effort decode: C--Windows-system32 to C:\\Windows\\system32"""
        result = re.sub(r'^([A-Za-z])--', r'\1:\\', key)
        result = result.replace("--", "\\").replace("-", "\\")
        return result

    @staticmethod
    def _extract_text(content) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "").strip()
        return ""

    def _parse(self, path: Path) -> Optional[Conversation]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
            if not raw:
                return None
            messages = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") in ("user", "assistant"):
                        messages.append(obj)
                except Exception:
                    continue

            if len(messages) < 2:
                return None

            # First user message = topic
            topic = ""
            for m in messages:
                if m.get("type") == "user":
                    topic = self._extract_text(m.get("message", {}).get("content", ""))[:90]
                    if topic:
                        break
            if not topic:
                return None

            # Timestamp from last message
            last_ts = None
            for m in reversed(messages):
                ts = m.get("timestamp", "")
                if ts:
                    try:
                        last_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        break
                    except Exception:
                        pass
            if not last_ts:
                last_ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

            # Last message preview
            last_msg = ""
            for m in reversed(messages):
                txt = self._extract_text(m.get("message", {}).get("content", ""))
                if txt:
                    last_msg = txt[:120]
                    break

            # WIP = last message was user (no final reply) or conversation has depth
            last_type = messages[-1].get("type", "")
            is_wip = last_type == "user" or len(messages) >= 6

            project_key = path.parent.name
            return Conversation(
                session_id=path.stem,
                project_key=project_key,
                project_path=self.decode_path(project_key),
                topic=topic,
                last_message=last_msg,
                message_count=len(messages),
                last_timestamp=last_ts,
                is_wip=is_wip,
                jsonl_path=path,
            )
        except Exception:
            return None

    def scan_all(self) -> list[Conversation]:
        if not PROJECTS_DIR.exists():
            return []
        convs = []
        for proj_dir in PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir():
                continue
            for f in proj_dir.glob("*.jsonl"):
                c = self._parse(f)
                if c:
                    convs.append(c)
        convs.sort(key=lambda c: c.last_timestamp, reverse=True)
        return convs

    def get_wip(self, n: int = 3) -> list[Conversation]:
        all_c = self.scan_all()
        wips   = [c for c in all_c if c.is_wip]
        others = [c for c in all_c if not c.is_wip]
        return (wips + others)[:n]


# ── Rate Limit Watcher (standalone mode) ─────────────────────────────────────
def run_watcher(claude_pid: int):
    """
    Run as a background process. Monitors .jsonl files for rate-limit messages.
    When detected: writes SIGNAL_FILE, kills claude process, exits.
    """
    start_time = time.time()
    last_sizes: dict[str, int] = {}

    def check():
        if not PROJECTS_DIR.exists():
            return False
        for proj in PROJECTS_DIR.iterdir():
            if not proj.is_dir():
                continue
            for f in proj.glob("*.jsonl"):
                try:
                    if f.stat().st_mtime < start_time - 5:
                        continue
                    path_str = str(f)
                    size = f.stat().st_size
                    prev = last_sizes.get(path_str, 0)
                    if size > prev:
                        with open(f, "r", encoding="utf-8", errors="replace") as fh:
                            fh.seek(prev)
                            new_text = fh.read().lower()
                        last_sizes[path_str] = size
                        for pat in RATE_LIMIT_PATTERNS:
                            if re.search(pat, new_text):
                                return True
                except Exception:
                    pass
        return False

    while True:
        if check():
            SIGNAL_FILE.write_text("switch")
            subprocess.run(["taskkill", "/F", "/PID", str(claude_pid)],
                           capture_output=True)
            sys.exit(0)
        time.sleep(2)


# ── Launch File Helpers ───────────────────────────────────────────────────────
def write_launch(action: str, session_id="", project_path="", account_id="", prompt=""):
    LAUNCH_FILE.write_text(json.dumps({
        "action": action,
        "session_id": session_id,
        "project_path": project_path,
        "account_id": account_id,
        "prompt": prompt,
    }), encoding="utf-8")


# ── Screens ───────────────────────────────────────────────────────────────────
class AccountSwitchModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, accounts: list[Account], active_id: str, **kw):
        super().__init__(**kw)
        self.accounts  = accounts
        self.active_id = active_id

    def compose(self) -> ComposeResult:
        with Container(id="modal-box"):
            yield Label("⇄  Switch Account", id="modal-title")
            yield Rule()
            with ListView(id="modal-list"):
                for acc in self.accounts:
                    dot = " ●" if acc.id == self.active_id else "  "
                    info = f"  {acc.email}" if acc.email else ""
                    yield ListItem(Label(f"{dot} {acc.label}{info}"), id=f"sw-{acc.id}")
            yield Label("[↑↓] Navigate   [Enter] Select   [Esc] Cancel", id="modal-hint")

    def on_list_view_selected(self, ev: ListView.Selected):
        item_id = ev.item.id or ""
        if item_id.startswith("sw-"):
            acc_id = item_id[3:]
            acc = next((a for a in self.accounts if a.id == acc_id), None)
            self.dismiss(acc)


class AddAccountScreen(Screen):
    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self, am: AccountManager, **kw):
        super().__init__(**kw)
        self.am    = am
        self._step = "name"
        self._label = ""

    def compose(self) -> ComposeResult:
        with Container(id="add-wrap"):
            yield Label("Add New Account", id="add-title")
            yield Rule()
            yield Label("Step 1: Give this account a name", id="add-instr")
            yield Input(placeholder="e.g.  Work,  Alt,  Backup...", id="add-input")
            yield Label("", id="add-status")
            yield Rule()
            yield Label("[Enter] Confirm   [Esc] Back", id="add-hint")

    def on_input_submitted(self, ev: Input.Submitted):
        val = ev.value.strip()
        if not val:
            self.query_one("#add-status", Label).update("  Please enter a name.")
            return
        if self._step == "name":
            self._label = val
            self._step = "auth"
            self.query_one("#add-instr", Label).update(
                f"  Account name: '{val}'\n\n"
                "  Step 2: In a separate terminal run:\n\n"
                "      claude auth logout\n"
                "      claude auth login\n\n"
                "  Log in to your other account, then press Enter here."
            )
            ev.input.value = ""
            ev.input.placeholder = "Press Enter once logged in to new account..."
        elif self._step == "auth":
            acc = self.am.add_current_credentials(self._label)
            self.query_one("#add-status", Label).update(f"  ✓ Saved '{acc.label}'")
            self.app.pop_screen()

    def action_go_back(self):
        self.app.pop_screen()


class LoginScreen(Screen):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("a", "add_account", "Add Account"),
    ]

    def __init__(self, am: AccountManager, **kw):
        super().__init__(**kw)
        self.am = am

    def compose(self) -> ComposeResult:
        accounts  = self.am.load_accounts()
        active_id = self.am.get_active_id()
        with Container(id="login-wrap"):
            yield Static(LOGO, id="logo")
            yield Label("  Multi-account Claude session manager", id="tagline")
            yield Rule()
            yield Label("  Select an account", id="login-sub")
            with ListView(id="login-list"):
                for acc in accounts:
                    dot   = " ●" if acc.id == active_id else "  "
                    email = f"  {acc.email}" if acc.email else ""
                    age   = self._age(acc.last_used)
                    yield ListItem(
                        Label(f"{dot} {acc.label}{email}  {age}"),
                        id=f"li-{acc.id}"
                    )
                yield ListItem(Label("  + Add New Account"), id="li-add")
            yield Rule()
            yield Label("  [↑↓] Navigate   [Enter] Select   [A] Add   [Q] Quit", id="login-hint")

    @staticmethod
    def _age(iso: str) -> str:
        if not iso:
            return ""
        try:
            dt   = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            diff = datetime.now(timezone.utc) - dt
            if diff.days == 0:
                h = diff.seconds // 3600
                return f"({h}h ago)" if h else "(just now)"
            return f"({diff.days}d ago)"
        except Exception:
            return ""

    def on_list_view_selected(self, ev: ListView.Selected):
        iid = ev.item.id or ""
        if iid == "li-add":
            self.action_add_account()
        elif iid.startswith("li-"):
            acc_id = iid[3:]
            acc = next((a for a in self.am.load_accounts() if a.id == acc_id), None)
            if acc:
                self.am.switch_to(acc)
                self.app.switch_screen(MainScreen(self.am))

    def action_add_account(self):
        self.app.push_screen(AddAccountScreen(self.am))

    def action_quit(self):
        write_launch("quit")
        self.app.exit()


class MainScreen(Screen):
    BINDINGS = [
        Binding("ctrl+shift+s", "switch_account", "Switch Account", show=True),
        Binding("1", "pick_1", "Resume 1"),
        Binding("2", "pick_2", "Resume 2"),
        Binding("3", "pick_3", "Resume 3"),
        Binding("n", "new_session", "New"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, am: AccountManager, **kw):
        super().__init__(**kw)
        self.am    = am
        self.scan  = ConversationScanner()
        self._wips: list[Conversation] = []
        self._all:  list[Conversation] = []

    def compose(self) -> ComposeResult:
        self._wips = self.scan.get_wip(3)
        self._all  = self.scan.scan_all()
        acc        = self.am.get_active_account()
        acc_label  = acc.label if acc else "Unknown"

        with Horizontal(id="layout"):
            # ── Left Sidebar ──────────────────────────────────────────────
            with Vertical(id="sidebar"):
                yield Label("  CONVERSATIONS", id="sidebar-header")
                yield Rule()
                with ScrollableContainer(id="sidebar-scroll"):
                    yield ListView(id="hist-list")
                yield Rule()
                yield Button("⇄  Switch Account", id="switch-btn")

            # ── Main Panel ────────────────────────────────────────────────
            with Vertical(id="main-panel"):
                with Horizontal(id="topbar"):
                    yield Label("CLAWSCUMMER", id="topbar-logo")
                    yield Label(f"● {acc_label}", id="acc-label")
                yield Rule()

                yield Label("  ─── RECENT / IN PROGRESS ───", id="wip-header")
                with Container(id="wip-zone"):
                    if self._wips:
                        for i, c in enumerate(self._wips, 1):
                            yield Static(self._card_text(c, i), classes="wip-card", id=f"wip{i}")
                    else:
                        yield Label("  No conversations found.", id="no-wip")

                yield Rule()
                yield Label("  ─── START SOMETHING NEW ───", id="new-header")
                yield Input(
                    placeholder="  What do you want to work on?  [Enter] to launch",
                    id="prompt-input"
                )
                yield Label(
                    "  [1][2][3] Quick resume   [N] New   [Ctrl+Shift+S] Switch account   [Q] Quit",
                    id="main-hint"
                )

        self._fill_sidebar()

    def _card_text(self, c: Conversation, n: int) -> str:
        now  = datetime.now(timezone.utc)
        diff = now - c.last_timestamp
        if diff.days == 0:
            age = f"today  ({diff.seconds // 3600}h ago)"
        elif diff.days == 1:
            age = "yesterday"
        else:
            age = f"{diff.days} days ago"
        topic = (c.topic[:72] + "…") if len(c.topic) > 72 else c.topic
        proj  = c.project_key
        wip   = " [WIP]" if c.is_wip else ""
        return (
            f" [{n}]  {topic}{wip}\n"
            f"       {proj}  •  {age}  •  {c.message_count} messages\n"
            f"       Press [{n}] to resume"
        )

    def _fill_sidebar(self):
        lv    = self.query_one("#hist-list", ListView)
        groups: dict[str, list[Conversation]] = {}
        now   = datetime.now(timezone.utc)
        for c in self._all:
            d = (now - c.last_timestamp).days
            if d == 0:      key = "TODAY"
            elif d == 1:    key = "YESTERDAY"
            elif d <= 7:    key = "THIS WEEK"
            elif d <= 30:   key = "THIS MONTH"
            else:           key = "OLDER"
            groups.setdefault(key, []).append(c)

        for grp, convs in groups.items():
            lv.append(ListItem(Label(f"  ── {grp}"), id=f"grp-{grp}", disabled=True))
            for c in convs:
                short = (c.topic[:34] + "…") if len(c.topic) > 34 else c.topic
                dot   = " ●" if c.is_wip else "  "
                lv.append(ListItem(Label(f"{dot} {short}"), id=f"h-{c.session_id}"))

    # ── Events ────────────────────────────────────────────────────────────────
    def on_list_view_selected(self, ev: ListView.Selected):
        iid = ev.item.id or ""
        if iid.startswith("h-"):
            sid = iid[2:]
            c = next((x for x in self._all if x.session_id == sid), None)
            if c:
                self._launch(c)

    def on_button_pressed(self, ev: Button.Pressed):
        if ev.button.id == "switch-btn":
            self.action_switch_account()

    def on_input_submitted(self, ev: Input.Submitted):
        prompt = ev.value.strip()
        if prompt:
            write_launch("new", prompt=prompt)
            self.app.exit()

    # ── Actions ───────────────────────────────────────────────────────────────
    def action_pick_1(self):
        if self._wips: self._launch(self._wips[0])
    def action_pick_2(self):
        if len(self._wips) >= 2: self._launch(self._wips[1])
    def action_pick_3(self):
        if len(self._wips) >= 3: self._launch(self._wips[2])

    def action_new_session(self):
        write_launch("new")
        self.app.exit()

    def action_quit(self):
        write_launch("quit")
        self.app.exit()

    def action_switch_account(self):
        accounts  = self.am.load_accounts()
        active_id = self.am.get_active_id()

        def on_selected(acc: Optional[Account]):
            if not acc:
                return
            self.am.switch_to(acc)
            self.query_one("#acc-label", Label).update(f"● {acc.label}")

        self.app.push_screen(AccountSwitchModal(accounts, active_id), callback=on_selected)

    def _launch(self, c: Conversation):
        write_launch("resume",
                     session_id=c.session_id,
                     project_path=c.project_path,
                     account_id=self.am.get_active_id() or "")
        self.app.exit()


# ── App ───────────────────────────────────────────────────────────────────────
class ClawsCummerApp(App):
    TITLE = "ClawsCummer"
    BINDINGS = [Binding("ctrl+shift+s", "app_switch", "Switch Account")]

    CSS = """
    Screen { background: #0d1117; color: #e6edf3; }

    /* ── Login ── */
    #login-wrap { align: center middle; padding: 1 2; }
    #logo       { color: #00d4aa; text-align: center; }
    #tagline    { color: #00d4aa; text-align: center; text-style: italic; }
    #login-sub  { color: #8b949e; padding: 0 0 0 2; }
    #login-list {
        background: #161b22; border: solid #30363d;
        height: auto; max-height: 16; width: 64; margin: 1 0;
    }
    #login-list > ListItem          { padding: 0 1; color: #e6edf3; }
    #login-list > ListItem:hover    { background: #1f2937; color: #00d4aa; }
    #login-list > ListItem.-highlighted { background: #1f2937; color: #00d4aa; }
    #login-hint { color: #8b949e; text-align: center; padding: 1 0 0 0; }

    /* ── Add Account ── */
    #add-wrap   { align: center middle; padding: 2; }
    #add-title  { color: #00d4aa; text-style: bold; text-align: center; padding: 0 0 1 0; }
    #add-instr  { color: #e6edf3; padding: 1 0; }
    #add-input  { background: #161b22; border: solid #30363d; width: 60; margin: 1 0; }
    #add-input:focus { border: solid #00d4aa; }
    #add-status { color: #3fb950; }
    #add-hint   { color: #8b949e; text-align: center; }

    /* ── Main Layout ── */
    #layout { height: 100%; }

    /* Sidebar */
    #sidebar       { width: 30; background: #161b22; border-right: solid #21262d; }
    #sidebar-header{ color: #8b949e; text-style: bold; padding: 1 1 0 1; }
    #sidebar-scroll { height: 1fr; }
    #hist-list { background: #161b22; height: auto; }
    #hist-list > ListItem { padding: 0 0; color: #8b949e; }
    #hist-list > ListItem:hover       { background: #1f2937; color: #e6edf3; }
    #hist-list > ListItem.-highlighted { background: #1f2937; color: #00d4aa; }
    #hist-list > ListItem.disabled    { color: #484f58; }
    #switch-btn {
        margin: 1; background: #21262d; color: #8b949e;
        border: solid #30363d; width: 100%;
    }
    #switch-btn:hover { background: #1f2937; border: solid #00d4aa; color: #00d4aa; }

    /* Main panel */
    #main-panel { padding: 1 2; height: 100%; }
    #topbar     { height: 3; align: left middle; }
    #topbar-logo{ color: #00d4aa; text-style: bold; width: 1fr; }
    #acc-label  { color: #3fb950; }

    #wip-header  { color: #8b949e; text-style: bold; padding: 1 0 1 0; }
    #new-header  { color: #8b949e; text-style: bold; padding: 1 0 0 0; }
    #wip-zone    { height: auto; }
    .wip-card {
        background: #161b22; border: solid #30363d;
        padding: 1; margin: 0 0 1 0; color: #e6edf3;
    }
    #no-wip { color: #8b949e; padding: 1; }
    #prompt-input { background: #161b22; border: solid #30363d; margin: 1 0; }
    #prompt-input:focus { border: solid #00d4aa; }
    #main-hint { color: #484f58; }

    /* Rule */
    Rule { color: #21262d; margin: 1 0; }

    /* ── Modal ── */
    AccountSwitchModal { align: center middle; }
    #modal-box {
        background: #161b22; border: solid #00d4aa;
        padding: 2; width: 52; height: auto;
    }
    #modal-title { color: #00d4aa; text-style: bold; text-align: center; padding: 0 0 1 0; }
    #modal-list  { background: #161b22; height: auto; max-height: 14; border: solid #30363d; }
    #modal-list > ListItem { padding: 0 1; color: #e6edf3; }
    #modal-list > ListItem:hover       { background: #1f2937; color: #00d4aa; }
    #modal-list > ListItem.-highlighted { background: #1f2937; color: #00d4aa; }
    #modal-hint { color: #8b949e; text-align: center; padding: 1 0 0 0; }
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.am = AccountManager()

    def on_mount(self):
        self.push_screen(LoginScreen(self.am))

    def action_app_switch(self):
        if isinstance(self.screen, MainScreen):
            self.screen.action_switch_account()


# ── Entry Point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ClawsCummer")
    parser.add_argument("--watch", type=int, metavar="PID",
                        help="Run as rate-limit watcher for given claude PID")
    parser.add_argument("--switch-auto", action="store_true",
                        help="Rotate to next account non-interactively")
    args = parser.parse_args()

    if args.watch:
        run_watcher(args.watch)
        return

    if args.switch_auto:
        am = AccountManager()
        nxt = am.rotate_to_next()
        if nxt:
            print(f"[ClawsCummer] Switched to: {nxt.label}")
        else:
            print("[ClawsCummer] Only one account configured.")
        return

    ClawsCummerApp().run()


if __name__ == "__main__":
    main()
