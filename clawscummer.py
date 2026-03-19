#!/usr/bin/env python3
"""
ClawsCummer v2.9 — Unified AI Agent Manager
Seamlessly switch between Claude and Gemini CLIs with shared context,
automatic handoff, and plan→execute pipeline.
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
from enum import Enum
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
CLAUDE_DIR      = Path.home() / ".claude"
GEMINI_DIR      = Path.home() / ".gemini"
CLAWSCUMMER_DIR = Path.home() / ".clawscummer"

def _secure_dir(d: Path):
    """Create directory with restrictive permissions (current user only on Windows)."""
    d.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        try:
            import subprocess
            subprocess.run(
                ["icacls", str(d), "/inheritance:r",
                 "/grant:r", f"{os.getlogin()}:(OI)(CI)F"],
                capture_output=True, timeout=5,
            )
        except Exception as e:
            import sys
            print(f"[ClawsCummer] Warning: Could not restrict permissions on {d}: {e}", file=sys.stderr)
ACCOUNTS_FILE   = CLAWSCUMMER_DIR / "accounts.json"
CREDS_FILE      = CLAUDE_DIR / ".credentials.json"
PROJECTS_DIR    = CLAUDE_DIR / "projects"
LAUNCH_FILE     = CLAWSCUMMER_DIR / "launch.json"
SIGNAL_FILE     = CLAWSCUMMER_DIR / "switch.signal"
HANDOFF_FILE    = CLAWSCUMMER_DIR / "handoff.md"
PLAN_FILE       = CLAWSCUMMER_DIR / "plan.md"
SECRETS_FILE    = CLAWSCUMMER_DIR / "secrets.json"
AGENTS_FILE     = CLAWSCUMMER_DIR / "AGENTS.md"

# Old paths (for migration)
OLD_ACCOUNTS_FILE = CLAUDE_DIR / "clawscummer_accounts.json"
OLD_LAUNCH_FILE   = CLAUDE_DIR / "clawscummer_launch.json"

# ── Rate Limit / Context Full Patterns ────────────────────────────────────────
CLAUDE_RATE_PATTERNS = [
    r"(^|\n)\s*(error|⚠|warning).*rate[\s_-]?limit",
    r"(^|\n)\s*you('ve| have| are| been).{0,10}rate[\s_-]?limited",
    r"(^|\n)\s*(error|⚠).*too many requests",
    r"(^|\n)\s*(error|⚠).*quota.{0,10}exceeded",
    r"(^|\n)\s*claude is currently unavailable",
    r"(^|\n)\s*(error|⚠).*429",
]

GEMINI_RATE_PATTERNS = [
    r"(^|\n)\s*(error|⚠|warning).*rate[\s_-]?limit",
    r"(^|\n)\s*you('ve| have| are| been).{0,10}rate[\s_-]?limited",
    r"(^|\n)\s*(error|⚠).*too many requests",
    r"(^|\n)\s*(error|⚠).*quota.{0,10}exceeded",
    r"(^|\n)\s*(error|⚠).*resource.{0,10}exhausted",
    r"(^|\n)\s*(error|⚠).*429",
]

CONTEXT_FULL_PATTERNS = [
    r"(^|\n)\s*(error|sorry).{0,15}this conversation.{0,10}too long",
    r"(^|\n)\s*context (window |limit )?(is |has been )?(exceeded|full)",
    r"(^|\n)\s*(error|sorry).{0,10}turn limit exceeded",
]

# ── MD Scanner Heuristics ─────────────────────────────────────────────────────
INSTRUCTION_SIGNALS = [
    r"(?i)\b(MUST|NEVER|ALWAYS|DO NOT|MANDATORY)\b",
    r"(?i)\b(rules|workflow|instructions|context)\b",
    r"(?i)^#+\s*(rules|setup|context|workflow|config)",
    r"(?i)(use this|follow these|when working)",
]

MD_SKIP_NAMES = {
    "README.md", "CHANGELOG.md", "LICENSE.md", "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md", "SECURITY.md",
}

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

# ── Enums ─────────────────────────────────────────────────────────────────────
class CLIType(str, Enum):
    CLAUDE = "claude"
    GEMINI = "gemini"


class WorkflowMode(str, Enum):
    AUTO         = "auto"
    PLAN_EXECUTE = "plan_execute"
    MANUAL       = "manual"


# ── Data Classes ──────────────────────────────────────────────────────────────
@dataclass
class Account:
    id: str
    label: str
    cli_type: str = "claude"
    email: str = ""
    credentials: dict = field(default_factory=dict)
    last_used: str = ""

    def to_dict(self):  return asdict(self)

    @classmethod
    def from_dict(cls, d):
        if "cli_type" not in d:
            d["cli_type"] = "claude"
        # Filter to known fields only
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Conversation:
    session_id:     str
    project_key:    str
    project_path:   str
    topic:          str
    last_message:   str
    message_count:  int
    last_timestamp: datetime
    is_wip:         bool
    jsonl_path:     Path
    cli_type:       str = "claude"


# ── MD Scanner ────────────────────────────────────────────────────────────────
class MDScanner:
    """Scans a directory for .md files that look like AI instructions."""

    @staticmethod
    def scan(directory: str = ".") -> list[Path]:
        results = []
        dirpath = Path(directory)
        for md in dirpath.glob("*.md"):
            if md.name in MD_SKIP_NAMES:
                continue
            if md.name.upper() == "AGENTS.MD":
                results.append(md)
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
                head = "\n".join(text.splitlines()[:20])
                score = sum(1 for pat in INSTRUCTION_SIGNALS if re.search(pat, head))
                if score >= 2:
                    results.append(md)
            except Exception:
                pass
        return results

    @staticmethod
    def format_hint(paths: list[Path]) -> str:
        """Return a short instruction for the CLI to read detected instruction files."""
        names = [p.name for p in paths if p.name.upper() != "AGENTS.MD"]
        if not names:
            return ""
        return (
            f"\n\nAdditional instruction files detected in working directory: "
            f"{', '.join(names)}. Read them for project-specific instructions."
        )


# ── Context Extractor ─────────────────────────────────────────────────────────
class ContextExtractor:
    """Extracts recent conversation context from CLI session files."""

    @staticmethod
    def _extract_text(content) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "").strip()
        return ""

    @staticmethod
    def extract_claude(max_messages: int = 6) -> list[dict]:
        if not PROJECTS_DIR.exists():
            return []
        newest, newest_time = None, 0
        for proj in PROJECTS_DIR.iterdir():
            if not proj.is_dir():
                continue
            for f in proj.glob("*.jsonl"):
                try:
                    mt = f.stat().st_mtime
                    if mt > newest_time:
                        newest_time, newest = mt, f
                except Exception:
                    pass
        if not newest:
            return []
        try:
            messages = []
            for line in newest.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") in ("user", "assistant"):
                        text = ContextExtractor._extract_text(
                            obj.get("message", {}).get("content", ""))
                        if text:
                            messages.append({"role": obj["type"], "content": text})
                except Exception:
                    continue
            return messages[-max_messages:]
        except Exception:
            return []

    @staticmethod
    def extract_gemini(max_messages: int = 6) -> list[dict]:
        tmp_dir = GEMINI_DIR / "tmp"
        if not tmp_dir.exists():
            return []
        newest, newest_time = None, 0
        for proj in tmp_dir.iterdir():
            chats = proj / "chats"
            if not chats.exists():
                continue
            for f in chats.glob("session_*.json"):
                try:
                    mt = f.stat().st_mtime
                    if mt > newest_time:
                        newest_time, newest = mt, f
                except Exception:
                    pass
        if not newest:
            return []
        try:
            data = json.loads(newest.read_text(encoding="utf-8", errors="replace"))
            messages = []
            for msg in data.get("messages", []):
                msg_type = msg.get("type", "")
                if msg_type in ("user", "gemini"):
                    role = "user" if msg_type == "user" else "assistant"
                    content_parts = msg.get("content", [])
                    if isinstance(content_parts, list):
                        texts = [c.get("text", "") for c in content_parts
                                 if isinstance(c, dict) and "text" in c]
                        text = " ".join(texts).strip()
                    elif isinstance(content_parts, str):
                        text = content_parts.strip()
                    else:
                        text = ""
                    if text:
                        messages.append({"role": role, "content": text})
            return messages[-max_messages:]
        except Exception:
            return []


# ── Handoff Manager ───────────────────────────────────────────────────────────
class HandoffManager:
    """Manages conversation handoff between CLIs."""

    @staticmethod
    def generate(messages: list[dict], from_cli: str, working_dir: str = "") -> str:
        """Write handoff context to file. Returns launch instruction string."""
        if not messages:
            return ""
        lines = [f"# Conversation Handoff from {from_cli.title()}", ""]
        lines.append("Continue this work seamlessly. Here is what was being discussed:")
        lines.append("")

        total = 0
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            content = msg["content"]
            if len(content) > 400:
                content = content[:400] + "..."
            lines.append(f"**{role}:** {content}")
            lines.append("")
            total += len(content)
            if total > 2000:
                lines.append("*(earlier context truncated)*")
                break

        lines.append("---")
        lines.append("Pick up from where the previous assistant left off. Continue the task.")
        if working_dir:
            lines.append(f"Working directory: `{working_dir}`")

        CLAWSCUMMER_DIR.mkdir(parents=True, exist_ok=True)
        HANDOFF_FILE.write_text("\n".join(lines), encoding="utf-8")

        return (
            f"Read the file at {HANDOFF_FILE} for context of the conversation "
            f"you are continuing from {from_cli.title()}, then continue the task."
        )

    @staticmethod
    def cleanup():
        try:
            HANDOFF_FILE.unlink(missing_ok=True)
        except Exception:
            pass


# ── Plan→Execute Manager ──────────────────────────────────────────────────────
class PlanExecuteManager:
    """Manages Gemini plan → Claude execute pipeline."""

    MAX_REPLAN_CYCLES = 2

    @staticmethod
    def check_gemini_available() -> bool:
        try:
            r = subprocess.run(["gemini", "--version"], capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def generate_plan(prompt: str, working_dir: str = ".") -> Optional[str]:
        """Run Gemini in plan mode. Returns plan text or None."""
        cmd = [
            "gemini",
            "--approval-mode", "plan",
            "-p",
            (
                "Create a detailed step-by-step implementation plan for the following task. "
                "Include specific file paths, function names, and code snippets where helpful. "
                f"Working directory: {working_dir}\n\nTask: {prompt}"
            ),
            "--output-format", "text",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180, cwd=working_dir
            )
            if result.returncode == 0 and result.stdout.strip():
                plan = result.stdout.strip()
                PLAN_FILE.write_text(plan, encoding="utf-8")
                return plan
        except Exception:
            pass
        return None

    @staticmethod
    def get_execution_prompt() -> str:
        return (
            f"Read the implementation plan at {PLAN_FILE} and execute it step by step. "
            f"Follow the plan precisely. If you hit a blocker, clearly state what the "
            f"blocker is and stop."
        )


# ── Account Manager ───────────────────────────────────────────────────────────
class AccountManager:
    def __init__(self):
        _secure_dir(CLAWSCUMMER_DIR)
        self._migrate()
        self._bootstrap()

    def _migrate(self):
        """Migrate accounts from old location to new."""
        if OLD_ACCOUNTS_FILE.exists() and not ACCOUNTS_FILE.exists():
            try:
                data = json.loads(OLD_ACCOUNTS_FILE.read_text(encoding="utf-8"))
                for acc in data.get("accounts", []):
                    if "cli_type" not in acc:
                        acc["cli_type"] = "claude"
                if "workflow_mode" not in data:
                    data["workflow_mode"] = "auto"
                ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass

    def _bootstrap(self):
        if ACCOUNTS_FILE.exists():
            return
        data: dict = {"accounts": [], "active_id": None, "workflow_mode": "auto"}
        if CREDS_FILE.exists():
            try:
                creds = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
                email = self._find_email(creds)
                acc = Account(
                    id="claude_1", label="Claude Primary", cli_type="claude",
                    email=email, credentials=creds,
                    last_used=datetime.now(timezone.utc).isoformat(),
                )
                data["accounts"].append(acc.to_dict())
                data["active_id"] = "claude_1"
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
        try:
            return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"accounts": [], "active_id": None, "workflow_mode": "auto"}

    def _save_raw(self, data: dict):
        ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_accounts(self) -> list[Account]:
        return [Account.from_dict(a) for a in self._load_raw().get("accounts", [])]

    def get_active_id(self) -> Optional[str]:
        return self._load_raw().get("active_id")

    def get_active_account(self) -> Optional[Account]:
        active_id = self.get_active_id()
        return next((a for a in self.load_accounts() if a.id == active_id), None)

    def get_workflow_mode(self) -> WorkflowMode:
        raw = self._load_raw().get("workflow_mode", "auto")
        try:
            return WorkflowMode(raw)
        except ValueError:
            return WorkflowMode.AUTO

    def set_workflow_mode(self, mode: WorkflowMode):
        data = self._load_raw()
        data["workflow_mode"] = mode.value
        self._save_raw(data)

    def switch_to(self, account: Account):
        """Swap credentials (Claude only) and set active account."""
        data = self._load_raw()
        accounts = [Account.from_dict(a) for a in data.get("accounts", [])]
        active_id = data.get("active_id")

        # Only swap credentials for Claude accounts
        if account.cli_type == "claude":
            if active_id and CREDS_FILE.exists():
                try:
                    live = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
                    for a in accounts:
                        if a.id == active_id and a.cli_type == "claude":
                            a.credentials = live
                except Exception:
                    pass
            if account.credentials:
                CREDS_FILE.write_text(
                    json.dumps(account.credentials, indent=2), encoding="utf-8"
                )

        account.last_used = datetime.now(timezone.utc).isoformat()
        data["accounts"] = [a.to_dict() for a in accounts]
        data["active_id"] = account.id

        for i, a in enumerate(data["accounts"]):
            if a["id"] == account.id:
                data["accounts"][i] = account.to_dict()

        self._save_raw(data)

    def peek_next(self) -> Optional[Account]:
        """Check if there's another account to rotate to, without switching."""
        accounts = self.load_accounts()
        if len(accounts) <= 1:
            return None
        active_id = self.get_active_id()
        ids = [a.id for a in accounts]
        idx = ids.index(active_id) if active_id in ids else -1
        return accounts[(idx + 1) % len(accounts)]

    def rotate_to_next(self) -> Optional[Account]:
        """Rotate to next account (cross-CLI aware)."""
        accounts = self.load_accounts()
        if len(accounts) <= 1:
            return None
        active_id = self.get_active_id()
        ids = [a.id for a in accounts]
        idx = ids.index(active_id) if active_id in ids else -1
        next_account = accounts[(idx + 1) % len(accounts)]
        self.switch_to(next_account)
        return next_account

    def add_account(self, label: str, cli_type: str = "claude") -> Account:
        data = self._load_raw()
        accounts = [Account.from_dict(a) for a in data.get("accounts", [])]
        prefix = "claude" if cli_type == "claude" else "gemini"
        count = sum(1 for a in accounts if a.cli_type == cli_type) + 1
        new_id = f"{prefix}_{count}"

        creds, email = {}, ""
        if cli_type == "claude" and CREDS_FILE.exists():
            try:
                creds = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
                email = self._find_email(creds)
            except Exception:
                pass

        acc = Account(
            id=new_id, label=label, cli_type=cli_type, email=email,
            credentials=creds, last_used=datetime.now(timezone.utc).isoformat(),
        )
        accounts.append(acc)
        data["accounts"] = [a.to_dict() for a in accounts]
        self._save_raw(data)
        return acc


# ── Conversation Scanner ──────────────────────────────────────────────────────
class ConversationScanner:

    @staticmethod
    def decode_path(key: str) -> str:
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

    def _parse_claude(self, path: Path) -> Optional[Conversation]:
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

            topic = ""
            for m in messages:
                if m.get("type") == "user":
                    topic = self._extract_text(m.get("message", {}).get("content", ""))[:90]
                    if topic:
                        break
            if not topic:
                return None

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

            last_msg = ""
            for m in reversed(messages):
                txt = self._extract_text(m.get("message", {}).get("content", ""))
                if txt:
                    last_msg = txt[:120]
                    break

            last_type = messages[-1].get("type", "")
            is_wip = last_type == "user" or len(messages) >= 6

            return Conversation(
                session_id=path.stem,
                project_key=path.parent.name,
                project_path=self.decode_path(path.parent.name),
                topic=topic,
                last_message=last_msg,
                message_count=len(messages),
                last_timestamp=last_ts,
                is_wip=is_wip,
                jsonl_path=path,
                cli_type="claude",
            )
        except Exception:
            return None

    def _parse_gemini(self, path: Path) -> Optional[Conversation]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            conv_msgs = [m for m in data.get("messages", [])
                         if m.get("type") in ("user", "gemini")]
            if len(conv_msgs) < 2:
                return None

            topic = ""
            for m in conv_msgs:
                if m.get("type") == "user":
                    cp = m.get("content", [])
                    if isinstance(cp, list):
                        for c in cp:
                            if isinstance(c, dict) and "text" in c:
                                topic = c["text"][:90]
                                break
                    elif isinstance(cp, str):
                        topic = cp[:90]
                    if topic:
                        break
            if not topic:
                return None

            last_updated = data.get("lastUpdated", "")
            try:
                last_ts = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            except Exception:
                last_ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

            last_msg = ""
            for m in reversed(conv_msgs):
                cp = m.get("content", [])
                if isinstance(cp, list):
                    for c in cp:
                        if isinstance(c, dict) and "text" in c:
                            last_msg = c["text"][:120]
                            break
                elif isinstance(cp, str):
                    last_msg = cp[:120]
                if last_msg:
                    break

            project_key = path.parent.parent.name
            return Conversation(
                session_id=data.get("sessionId", path.stem),
                project_key=f"gemini:{project_key}",
                project_path=project_key,
                topic=topic,
                last_message=last_msg,
                message_count=len(conv_msgs),
                last_timestamp=last_ts,
                is_wip=conv_msgs[-1].get("type") == "user",
                jsonl_path=path,
                cli_type="gemini",
            )
        except Exception:
            return None

    def scan_all(self) -> list[Conversation]:
        convs = []
        # Claude sessions
        if PROJECTS_DIR.exists():
            for proj_dir in PROJECTS_DIR.iterdir():
                if not proj_dir.is_dir():
                    continue
                for f in proj_dir.glob("*.jsonl"):
                    c = self._parse_claude(f)
                    if c:
                        convs.append(c)
        # Gemini sessions
        gemini_tmp = GEMINI_DIR / "tmp"
        if gemini_tmp.exists():
            for proj_dir in gemini_tmp.iterdir():
                chats_dir = proj_dir / "chats"
                if not chats_dir.exists():
                    continue
                for f in chats_dir.glob("session_*.json"):
                    c = self._parse_gemini(f)
                    if c:
                        convs.append(c)
        convs.sort(key=lambda c: c.last_timestamp, reverse=True)
        return convs

    def get_wip(self, n: int = 3) -> list[Conversation]:
        all_c = self.scan_all()
        wips   = [c for c in all_c if c.is_wip]
        others = [c for c in all_c if not c.is_wip]
        return (wips + others)[:n]


# ── Launch File Helpers ───────────────────────────────────────────────────────
def write_launch(action: str, session_id="", project_path="", account_id="",
                 prompt="", workflow_mode=""):
    CLAWSCUMMER_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_FILE.write_text(json.dumps({
        "action": action,
        "session_id": session_id,
        "project_path": project_path,
        "account_id": account_id,
        "prompt": prompt,
        "workflow_mode": workflow_mode,
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
            yield Label("Switch Account", id="modal-title")
            yield Rule()
            with ListView(id="modal-list"):
                for acc in self.accounts:
                    dot = " *" if acc.id == self.active_id else "  "
                    cli_tag = "C" if acc.cli_type == "claude" else "G"
                    info = f"  {acc.email}" if acc.email else ""
                    yield ListItem(
                        Label(f"{dot} [{cli_tag}] {acc.label}{info}"),
                        id=f"sw-{acc.id}",
                    )
            yield Label("[Up/Down] Navigate   [Enter] Select   [Esc] Cancel", id="modal-hint")

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
        self.am       = am
        self._step     = "type"
        self._cli_type = ""
        self._label    = ""

    def compose(self) -> ComposeResult:
        with Container(id="add-wrap"):
            yield Label("Add New Account", id="add-title")
            yield Rule()
            yield Label("Step 1: Select CLI type", id="add-instr")
            with ListView(id="add-type-list"):
                yield ListItem(Label("  Claude  --  Anthropic Claude Code"), id="type-claude")
                yield ListItem(Label("  Gemini  --  Google Gemini CLI"), id="type-gemini")
            yield Input(placeholder="", id="add-input")
            yield Label("", id="add-status")
            yield Rule()
            yield Label("[Enter] Confirm   [Esc] Back", id="add-hint")

    def on_mount(self):
        self.query_one("#add-input", Input).display = False

    def on_list_view_selected(self, ev: ListView.Selected):
        if self._step != "type":
            return
        iid = ev.item.id or ""
        if iid == "type-claude":
            self._cli_type = "claude"
        elif iid == "type-gemini":
            self._cli_type = "gemini"
        else:
            return

        self._step = "name"
        self.query_one("#add-type-list", ListView).display = False
        inp = self.query_one("#add-input", Input)
        inp.display = True
        inp.placeholder = "e.g.  Primary,  Backup,  Work..."
        inp.focus()
        self.query_one("#add-instr", Label).update(
            f"Step 2: Name this {self._cli_type.title()} account"
        )

    def on_input_submitted(self, ev: Input.Submitted):
        val = ev.value.strip()
        if not val:
            self.query_one("#add-status", Label).update("  Please enter a name.")
            return

        if self._step == "name":
            self._label = val
            if self._cli_type == "gemini":
                acc = self.am.add_account(self._label, "gemini")
                self.query_one("#add-status", Label).update(
                    f"  Added Gemini account '{acc.label}'"
                )
                self.app.pop_screen()
            else:
                self._step = "auth"
                self.query_one("#add-instr", Label).update(
                    f"  Account name: '{val}'\n\n"
                    "  Step 3: In a separate terminal run:\n\n"
                    "      claude auth logout\n"
                    "      claude auth login\n\n"
                    "  Log in to your other account, then press Enter here."
                )
                ev.input.value = ""
                ev.input.placeholder = "Press Enter once logged in to new account..."
        elif self._step == "auth":
            acc = self.am.add_account(self._label, "claude")
            self.query_one("#add-status", Label).update(
                f"  Saved '{acc.label}'"
            )
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
            yield Label("  Unified AI Agent Manager", id="tagline")
            yield Rule()
            yield Label("  Select an account", id="login-sub")
            with ListView(id="login-list"):
                for acc in accounts:
                    dot   = " *" if acc.id == active_id else "  "
                    email = f"  {acc.email}" if acc.email else ""
                    age   = self._age(acc.last_used)
                    cli_tag = "[bold #818cf8]C[/]" if acc.cli_type == "claude" else "[bold #4ecdc4]G[/]"
                    yield ListItem(
                        Label(f"{dot} {cli_tag} {acc.label}{email}  {age}"),
                        id=f"li-{acc.id}",
                    )
                yield ListItem(Label("  + Add New Account"), id="li-add")
            yield Rule()
            yield Label(
                "  [Up/Down] Navigate   [Enter] Select   [A] Add   [Q] Quit",
                id="login-hint",
            )
        yield Footer()

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
        Binding("m", "cycle_mode", "Mode"),
        Binding("1", "pick_1", "Resume 1"),
        Binding("2", "pick_2", "Resume 2"),
        Binding("3", "pick_3", "Resume 3"),
        Binding("n", "new_session", "New"),
        Binding("q", "quit", "Quit"),
    ]

    MODE_LABELS = {
        WorkflowMode.AUTO:         "AUTO",
        WorkflowMode.PLAN_EXECUTE: "PLAN>EXEC",
        WorkflowMode.MANUAL:       "MANUAL",
    }

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
        cli_type   = acc.cli_type if acc else "claude"
        mode       = self.am.get_workflow_mode()

        with Horizontal(id="layout"):
            # ── Left Sidebar ──────────────────────────────────────────────
            with Vertical(id="sidebar"):
                yield Label("  SESSIONS", id="sidebar-header")
                yield Rule()
                with ScrollableContainer(id="sidebar-scroll"):
                    yield ListView(id="hist-list")
                yield Rule()
                yield Button("Switch Account", id="switch-btn")

            # ── Main Panel ────────────────────────────────────────────────
            with Vertical(id="main-panel"):
                with Horizontal(id="topbar"):
                    yield Label("CLAWSCUMMER", id="topbar-logo")
                    yield Label(
                        f"{'C' if cli_type == 'claude' else 'G'} {acc_label}",
                        id="acc-badge",
                        classes=f"cli-badge-{cli_type}",
                    )
                    yield Label(
                        self.MODE_LABELS[mode],
                        id="mode-badge",
                    )

                with ScrollableContainer(id="content-area"):
                    yield Label("CONTINUE WHERE YOU LEFT OFF", id="wip-header")
                    with Container(id="wip-zone"):
                        if self._wips:
                            for i, c in enumerate(self._wips, 1):
                                yield Static(
                                    self._card_text(c, i),
                                    classes="wip-card",
                                    id=f"wip{i}",
                                )
                        else:
                            yield Label(
                                "  No recent conversations found.", id="no-wip"
                            )

                    yield Label("START SOMETHING NEW", id="new-header")
                    yield Input(
                        placeholder="What do you want to work on?   press Enter to launch",
                        id="prompt-input",
                    )
                    yield Label(
                        "  1/2/3 resume   n new   m mode   ctrl+shift+s switch   q quit",
                        id="main-hint",
                    )

        self._fill_sidebar()
        yield Footer()

    def _card_text(self, c: Conversation, n: int) -> str:
        now  = datetime.now(timezone.utc)
        diff = now - c.last_timestamp
        if diff.days == 0:
            h   = diff.seconds // 3600
            age = f"{h}h ago" if h else "just now"
        elif diff.days == 1:
            age = "yesterday"
        else:
            age = f"{diff.days}d ago"

        topic = (c.topic[:74] + "...") if len(c.topic) > 74 else c.topic
        proj  = c.project_key
        wip   = "  [bold #34d399]WIP[/]" if c.is_wip else ""
        cli   = "[bold #818cf8]C[/]" if c.cli_type == "claude" else "[bold #4ecdc4]G[/]"

        return (
            f" [bold #818cf8] {n} [/]  {cli} [bold #e2e8f4]{topic}[/]{wip}\n"
            f"     [#52525b]{proj}[/]  [#3f3f56]|[/]  [#52525b]{age}[/]"
            f"  [#3f3f56]|[/]  [#52525b]{c.message_count} msgs[/]\n"
            f"     [#3f3f56]press [{n}] to resume[/]"
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
            lv.append(ListItem(Label(f"  -- {grp}"), id=f"grp-{grp}", disabled=True))
            for c in convs:
                short = (c.topic[:30] + "...") if len(c.topic) > 30 else c.topic
                dot   = " *" if c.is_wip else "  "
                cli   = "C" if c.cli_type == "claude" else "G"
                lv.append(ListItem(
                    Label(f"{dot}{cli} {short}"),
                    id=f"h-{c.cli_type}-{c.session_id}",
                ))

    # ── Events ────────────────────────────────────────────────────────────────
    def on_list_view_selected(self, ev: ListView.Selected):
        iid = ev.item.id or ""
        if iid.startswith("h-"):
            # Format: h-claude-<session_id> or h-gemini-<session_id>
            parts = iid.split("-", 2)
            if len(parts) == 3:
                cli_type, sid = parts[1], parts[2]
                c = next(
                    (x for x in self._all
                     if x.session_id == sid and x.cli_type == cli_type),
                    None,
                )
                if c:
                    self._launch(c)

    def on_button_pressed(self, ev: Button.Pressed):
        if ev.button.id == "switch-btn":
            self.action_switch_account()

    def on_input_submitted(self, ev: Input.Submitted):
        prompt = ev.value.strip()
        if prompt:
            mode = self.am.get_workflow_mode()
            write_launch("new", prompt=prompt, workflow_mode=mode.value)
            self.app.exit()

    # ── Actions ───────────────────────────────────────────────────────────────
    def action_pick_1(self):
        if self._wips:
            self._launch(self._wips[0])

    def action_pick_2(self):
        if len(self._wips) >= 2:
            self._launch(self._wips[1])

    def action_pick_3(self):
        if len(self._wips) >= 3:
            self._launch(self._wips[2])

    def action_new_session(self):
        mode = self.am.get_workflow_mode()
        write_launch("new", workflow_mode=mode.value)
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
            cli_type = acc.cli_type
            self.query_one("#acc-badge", Label).update(
                f"{'C' if cli_type == 'claude' else 'G'} {acc.label}"
            )
            badge = self.query_one("#acc-badge", Label)
            badge.remove_class("cli-badge-claude", "cli-badge-gemini")
            badge.add_class(f"cli-badge-{cli_type}")

        self.app.push_screen(
            AccountSwitchModal(accounts, active_id), callback=on_selected
        )

    def action_cycle_mode(self):
        modes = list(WorkflowMode)
        cur   = self.am.get_workflow_mode()
        idx   = modes.index(cur)
        nxt   = modes[(idx + 1) % len(modes)]
        self.am.set_workflow_mode(nxt)
        self.query_one("#mode-badge", Label).update(self.MODE_LABELS[nxt])

    def _launch(self, c: Conversation):
        mode = self.am.get_workflow_mode()
        write_launch(
            "resume",
            session_id=c.session_id,
            project_path=c.project_path,
            account_id=self.am.get_active_id() or "",
            workflow_mode=mode.value,
        )
        self.app.exit()


# ── App ───────────────────────────────────────────────────────────────────────
class ClawsCummerApp(App):
    TITLE = "ClawsCummer"
    BINDINGS = [Binding("ctrl+shift+s", "app_switch", "Switch Account")]

    CSS = """
    /* =================================================
       ClawsCummer v2.9  –  Unified AI Agent Manager
       Palette: zinc-950 base, indigo=Claude, teal=Gemini
       ================================================= */

    Screen { background: #0c0c11; color: #e2e8f4; }

    /* ── LOGIN ──────────────────────────────────── */
    #login-wrap {
        align: center middle;
        padding: 1 4;
    }
    #logo {
        color: #818cf8;
        text-align: center;
        padding: 1 0 0 0;
    }
    #tagline {
        color: #6366f1;
        text-align: center;
        text-style: italic bold;
        padding: 0 0 1 0;
    }
    #login-sub {
        color: #52525b;
        text-align: center;
        padding: 0 0 1 0;
    }
    #login-list {
        background: #111119;
        border: round #27273a;
        height: auto;
        max-height: 20;
        width: 62;
        margin: 0 0 1 0;
    }
    #login-list > ListItem {
        padding: 1 2;
        color: #a1a1aa;
        background: #111119;
    }
    #login-list > ListItem:hover {
        background: #18182a;
        color: #c4b5fd;
    }
    #login-list > ListItem.-highlighted {
        background: #1e1e35;
        color: #a5b4fc;
    }
    #login-hint { color: #3f3f56; text-align: center; padding: 1 0; }

    /* ── ADD ACCOUNT ────────────────────────────── */
    #add-wrap  { align: center middle; padding: 2 4; }
    #add-title {
        color: #818cf8; text-style: bold;
        text-align: center; padding: 0 0 1 0;
    }
    #add-instr  { color: #a1a1aa; padding: 1 0; width: 60; }
    #add-input  {
        background: #111119; border: round #27273a;
        width: 60; margin: 1 0; color: #e2e8f4;
    }
    #add-input:focus  { border: round #6366f1; background: #14142a; }
    #add-status { color: #34d399; padding: 0 0 1 0; }
    #add-hint   { color: #3f3f56; text-align: center; }
    #add-type-list {
        background: #111119;
        border: round #27273a;
        height: auto;
        max-height: 8;
        width: 58;
        margin: 1 0;
    }
    #add-type-list > ListItem {
        padding: 1 2;
        color: #a1a1aa;
        background: #111119;
    }
    #add-type-list > ListItem:hover {
        background: #18182a;
        color: #c4b5fd;
    }
    #add-type-list > ListItem.-highlighted {
        background: #1e1e35;
        color: #a5b4fc;
    }

    /* ── MAIN LAYOUT ────────────────────────────── */
    #layout { height: 100%; }

    /* Nav sidebar */
    #sidebar {
        width: 28;
        background: #0a0a0f;
        border-right: solid #18182a;
    }
    #sidebar-header {
        color: #3f3f56;
        text-style: bold;
        padding: 1 2 0 2;
    }
    #sidebar-scroll { height: 1fr; background: #0a0a0f; }
    #hist-list { background: #0a0a0f; height: auto; }
    #hist-list > ListItem {
        padding: 0 1; color: #52525b; background: #0a0a0f;
    }
    #hist-list > ListItem:hover {
        background: #14142a; color: #c4b5fd;
    }
    #hist-list > ListItem.-highlighted {
        background: #1a1a30; color: #818cf8;
    }
    #switch-btn {
        margin: 1 1 1 1;
        background: #111119;
        color: #52525b;
        border: round #27273a;
        width: 100%;
    }
    #switch-btn:hover {
        background: #1a1a2e; color: #a5b4fc; border: round #4f46e5;
    }

    /* Top nav bar */
    #topbar {
        background: #0a0a0f;
        border-bottom: solid #18182a;
        height: 3;
        align: left middle;
        padding: 0 2;
    }
    #topbar-logo {
        color: #818cf8; text-style: bold; width: 1fr; padding: 0 1;
    }

    /* CLI type badges */
    .cli-badge-claude {
        color: #818cf8;
        background: #1e1e35;
        border: round #4f46e5;
        padding: 0 2;
    }
    .cli-badge-gemini {
        color: #4ecdc4;
        background: #0d1f1d;
        border: round #14534d;
        padding: 0 2;
    }

    /* Mode badge */
    #mode-badge {
        color: #f59e0b;
        background: #1f1a0d;
        border: round #78350f;
        padding: 0 2;
        margin: 0 0 0 1;
    }

    /* Scrollable content area */
    #content-area { padding: 2 3; height: 1fr; overflow-y: auto; }

    #wip-header {
        color: #3f3f56; text-style: bold; padding: 0 0 1 0;
    }
    #new-header {
        color: #3f3f56; text-style: bold; padding: 2 0 1 0;
    }
    #wip-zone { height: auto; margin: 0 0 1 0; }

    .wip-card {
        background: #111119;
        border: round #27273a;
        padding: 1 2;
        margin: 0 0 1 0;
        color: #e2e8f4;
    }
    .wip-card:hover {
        background: #14142a;
        border: round #4f46e5;
    }

    #no-wip { color: #3f3f56; padding: 2 0; }

    #prompt-input {
        background: #111119;
        border: round #27273a;
        color: #e2e8f4;
        padding: 0 1;
        margin: 0 0 0 0;
    }
    #prompt-input:focus {
        border: round #6366f1;
        background: #14142a;
    }
    #main-hint { color: #27273a; padding: 0 0 1 0; }

    /* Rules / dividers */
    Rule { color: #18182a; margin: 1 0; }

    /* ── MODAL ──────────────────────────────────── */
    AccountSwitchModal { align: center middle; }
    #modal-box {
        background: #111119;
        border: round #4f46e5;
        padding: 2 3;
        width: 56; height: auto;
    }
    #modal-title {
        color: #818cf8; text-style: bold;
        text-align: center; padding: 0 0 1 0;
    }
    #modal-list {
        background: #111119;
        height: auto; max-height: 16;
        border: round #27273a;
        margin: 1 0;
    }
    #modal-list > ListItem { padding: 1 2; color: #a1a1aa; }
    #modal-list > ListItem:hover       { background: #18182a; color: #c4b5fd; }
    #modal-list > ListItem.-highlighted{ background: #1e1e35; color: #818cf8; }
    #modal-hint { color: #3f3f56; text-align: center; padding: 1 0 0 0; }

    /* ── FOOTER ─────────────────────────────────── */
    Footer {
        background: #0a0a0f;
        color: #3f3f56;
        border-top: solid #18182a;
    }
    Footer > .footer--key { color: #52525b; }
    Footer > .footer--highlight { background: #18182a; color: #818cf8; }
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.am = AccountManager()

    def on_mount(self):
        self.push_screen(LoginScreen(self.am))

    def action_app_switch(self):
        if isinstance(self.screen, MainScreen):
            self.screen.action_switch_account()


# ── CLI Launch Loop ───────────────────────────────────────────────────────────
def _launch_cli_loop():
    """Read launch file, run the selected CLI with auto-switch on rate limit."""
    if not LAUNCH_FILE.exists():
        return
    try:
        launch = json.loads(LAUNCH_FILE.read_text(encoding="utf-8"))
        LAUNCH_FILE.unlink(missing_ok=True)
    except Exception:
        return

    if launch.get("action") == "quit":
        return

    is_resume    = launch.get("action") == "resume"
    project_path = launch.get("project_path", "")
    prompt       = launch.get("prompt", "")
    wf_mode      = launch.get("workflow_mode", "auto")
    original_dir = os.getcwd()

    if is_resume and project_path and os.path.isdir(project_path):
        os.chdir(project_path)

    am = AccountManager()

    # ── Plan→Execute: generate plan with Gemini first ─────────────────────
    if wf_mode == "plan_execute" and prompt and not is_resume:
        if PlanExecuteManager.check_gemini_available():
            print("\n  [ClawsCummer] Planning with Gemini...\n")
            plan = PlanExecuteManager.generate_plan(prompt, os.getcwd())
            if plan:
                plan_preview = plan[:200].replace("\n", " ")
                print(f"  [ClawsCummer] Plan ready ({len(plan)} chars)")
                print(f"  [ClawsCummer] Preview: {plan_preview}...\n")
                # Switch to a Claude account for execution
                accounts = am.load_accounts()
                claude_acc = next(
                    (a for a in accounts if a.cli_type == "claude"), None
                )
                if claude_acc:
                    am.switch_to(claude_acc)
                prompt = PlanExecuteManager.get_execution_prompt()
            else:
                print("  [ClawsCummer] Plan generation failed. Using direct mode.\n")
        else:
            print("  [ClawsCummer] Gemini not available. Using direct mode.\n")

    # ── Scan for additional instruction MDs ───────────────────────────────
    md_hint = MDScanner.format_hint(MDScanner.scan(os.getcwd()))

    handoff_prompt = ""
    replan_count   = 0

    for attempt in range(10):
        acc      = am.get_active_account()
        label    = acc.label if acc else "Unknown"
        cli_type = acc.cli_type if acc else "claude"
        cli_name = "Claude" if cli_type == "claude" else "Gemini"

        print(f"\n  +------------------------------------------+")
        print(f"  |  ClawsCummer  --  {label:<22} |")
        print(f"  |  CLI: {cli_name:<36} |")
        print(f"  +------------------------------------------+\n")

        # ── Build command ─────────────────────────────────────────────────
        if cli_type == "claude":
            cmd = ["claude"]
            if handoff_prompt:
                cmd.append(handoff_prompt + md_hint)
            elif prompt and attempt == 0:
                cmd.append(prompt + md_hint)
            elif is_resume or attempt > 0:
                cmd.append("--continue")
        else:  # gemini
            cmd = ["gemini"]
            if handoff_prompt:
                cmd.extend(["-i", handoff_prompt + md_hint])
            elif prompt and attempt == 0:
                cmd.extend(["-i", prompt + md_hint])
            elif is_resume:
                cmd.extend(["--resume", "latest"])

        # Clear one-shot values
        handoff_prompt = ""
        one_shot_prompt = prompt if attempt == 0 else ""

        proc = subprocess.Popen(cmd)

        # ── Rate limit / context full watcher ─────────────────────────────
        trigger_event = threading.Event()
        watch_start   = time.time()
        _sizes: dict[str, int] = {}

        patterns = (
            (CLAUDE_RATE_PATTERNS if cli_type == "claude" else GEMINI_RATE_PATTERNS)
            + CONTEXT_FULL_PATTERNS
        )

        def _watch(cli_t=cli_type, pats=patterns):
            if cli_t == "claude":
                _watch_claude(pats)
            else:
                _watch_gemini(pats)

        def _watch_claude(pats):
            if not PROJECTS_DIR.exists():
                return
            while not trigger_event.is_set():
                try:
                    for proj in PROJECTS_DIR.iterdir():
                        if not proj.is_dir():
                            continue
                        for f in proj.glob("*.jsonl"):
                            try:
                                if f.stat().st_mtime < watch_start - 5:
                                    continue
                                key  = str(f)
                                size = f.stat().st_size
                                prev = _sizes.get(key, 0)
                                if size > prev:
                                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                                        fh.seek(prev)
                                        new_text = fh.read().lower()
                                    _sizes[key] = size
                                    for pat in pats:
                                        if re.search(pat, new_text):
                                            trigger_event.set()
                                            return
                            except Exception:
                                pass
                except Exception:
                    pass
                trigger_event.wait(2)

        def _watch_gemini(pats):
            gemini_tmp = GEMINI_DIR / "tmp"
            while not trigger_event.is_set():
                try:
                    if gemini_tmp.exists():
                        for proj in gemini_tmp.iterdir():
                            chats = proj / "chats"
                            if not chats.exists():
                                continue
                            for f in chats.glob("session_*.json"):
                                try:
                                    if f.stat().st_mtime < watch_start - 5:
                                        continue
                                    key  = str(f)
                                    size = f.stat().st_size
                                    prev = _sizes.get(key, 0)
                                    if size > prev:
                                        with open(f, "r", encoding="utf-8", errors="replace") as fh:
                                            fh.seek(prev)
                                            new_text = fh.read().lower()
                                        _sizes[key] = size
                                        for pat in pats:
                                            if re.search(pat, new_text):
                                                trigger_event.set()
                                                return
                                except Exception:
                                    pass
                except Exception:
                    pass
                trigger_event.wait(2)

        threading.Thread(target=_watch, daemon=True, name="RateLimitWatch").start()

        # ── Wait for process or trigger ───────────────────────────────────
        while proc.poll() is None:
            if trigger_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
                break
            time.sleep(0.5)

        if trigger_event.is_set():
            print("\n  [ClawsCummer] Rate limit / context full detected -- switching...\n")

            # Extract context from current session
            if cli_type == "claude":
                messages = ContextExtractor.extract_claude()
            else:
                messages = ContextExtractor.extract_gemini()

            prev_cli_type = cli_type
            nxt = am.rotate_to_next()

            if nxt:
                print(f"  [ClawsCummer] Switched to: {nxt.label} ({nxt.cli_type})\n")

                if nxt.cli_type != prev_cli_type:
                    # Cross-CLI switch: generate handoff
                    instruction = HandoffManager.generate(
                        messages, prev_cli_type, os.getcwd()
                    )
                    if instruction:
                        handoff_prompt = instruction
                    is_resume = False
                else:
                    # Same CLI type: use native resume
                    is_resume = True
            else:
                print("  [ClawsCummer] No other accounts. Add more via the TUI.\n")
                break

            time.sleep(1)
            continue

        break  # Normal exit

    HandoffManager.cleanup()
    os.chdir(original_dir)


# ── Entry Point ───────────────────────────────────────────────────────────────
def _request_uac():
    try:
        import ctypes
        params = " ".join(f'"{a}"' for a in sys.argv)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    except Exception:
        pass
    sys.exit(0)


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return True


def main():
    parser = argparse.ArgumentParser(description="ClawsCummer v2.9")
    parser.add_argument("--watch", type=int, metavar="PID",
                        help="Run as rate-limit watcher (internal use)")
    parser.add_argument("--switch-auto", action="store_true",
                        help="Rotate to next account non-interactively")
    parser.add_argument("--tui", action="store_true",
                        help="Use terminal TUI instead of GUI")
    args = parser.parse_args()

    if args.watch:
        # Legacy watcher mode
        from pathlib import Path
        start_time = time.time()
        last_sizes: dict[str, int] = {}
        all_patterns = CLAUDE_RATE_PATTERNS + GEMINI_RATE_PATTERNS + CONTEXT_FULL_PATTERNS

        def check():
            for search_dir in [PROJECTS_DIR, GEMINI_DIR / "tmp"]:
                if not search_dir.exists():
                    continue
                for proj in search_dir.iterdir():
                    if not proj.is_dir():
                        continue
                    globs = ["*.jsonl"] if "claude" in str(search_dir) else []
                    chats = proj / "chats"
                    if chats.exists():
                        globs.append("session_*.json")
                    for pattern in (["*.jsonl"] if search_dir == PROJECTS_DIR else []):
                        for f in proj.glob(pattern):
                            try:
                                if f.stat().st_mtime < start_time - 5:
                                    continue
                                key = str(f)
                                size = f.stat().st_size
                                prev = last_sizes.get(key, 0)
                                if size > prev:
                                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                                        fh.seek(prev)
                                        new_text = fh.read().lower()
                                    last_sizes[key] = size
                                    for pat in all_patterns:
                                        if re.search(pat, new_text):
                                            return True
                            except Exception:
                                pass
            return False

        while True:
            if check():
                SIGNAL_FILE.write_text("switch")
                subprocess.run(["taskkill", "/F", "/PID", str(args.watch)],
                               capture_output=True)
                sys.exit(0)
            time.sleep(2)
        return

    if args.switch_auto:
        am = AccountManager()
        nxt = am.rotate_to_next()
        print(
            f"[ClawsCummer] Switched to: {nxt.label} ({nxt.cli_type})" if nxt
            else "[ClawsCummer] Only one account configured."
        )
        return

    # GUI mode (default) or TUI mode (--tui)
    if args.tui:
        # Legacy TUI mode
        if sys.platform == "win32" and not _is_admin():
            _request_uac()
            return
        ClawsCummerApp().run()
        _launch_cli_loop()
    else:
        # GUI mode — pywebview with embedded terminal
        try:
            from gui.app import main as gui_main
            gui_main()
        except ImportError as e:
            print(f"  [ClawsCummer] GUI dependencies missing: {e}")
            print(f"  [ClawsCummer] Install with: pip install pywebview pywinpty websockets")
            print(f"  [ClawsCummer] Falling back to TUI mode...\n")
            if sys.platform == "win32" and not _is_admin():
                _request_uac()
                return
            ClawsCummerApp().run()
            _launch_cli_loop()


if __name__ == "__main__":
    main()
