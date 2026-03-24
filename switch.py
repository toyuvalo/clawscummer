#!/usr/bin/env python3
"""
ClawsCummer — Interactive account switcher.
Run standalone or called by AI CLIs via /clawscummer command.

Usage:
  python switch.py              # interactive
  python switch.py --list       # print accounts as numbered list, exit
  python switch.py --select N   # switch to account N (1-based), exit
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Force UTF-8 output on Windows so unicode chars don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from clawscummer import AccountManager

CLI_ICON = {"claude": "C", "gemini": "G", "codex": "X"}


def _list(am: AccountManager) -> list:
    accounts = am.load_accounts()
    active_id = am.get_active_id()
    for i, a in enumerate(accounts):
        dot = "●" if a.id == active_id else " "
        icon = CLI_ICON.get(a.cli_type, "?")
        active_tag = "  ← active" if a.id == active_id else ""
        print(f"  {i + 1}. {dot} [{icon}] {a.label}{active_tag}")
    return accounts


def main():
    am = AccountManager()

    if "--list" in sys.argv:
        _list(am)
        return

    if "--select" in sys.argv:
        try:
            idx = int(sys.argv[sys.argv.index("--select") + 1]) - 1
            accounts = am.load_accounts()
            acc = accounts[idx]
            am.switch_to(acc)
            icon = CLI_ICON.get(acc.cli_type, "?")
            print(f"Switched to [{icon}] {acc.label} ({acc.cli_type})")
        except (ValueError, IndexError):
            print("Invalid account number.", file=sys.stderr)
            sys.exit(1)
        return

    # ── Interactive mode ───────────────────────────────────────────────────────
    print()
    print("  ╔══ ClawsCummer ══ Switch Account ══╗")
    print()
    accounts = _list(am)
    print()

    if not accounts:
        print("  No accounts configured. Run: clawscummer")
        return

    try:
        raw = input("  Select account number (Enter to cancel): ").strip()
        if not raw:
            print("  Cancelled.")
            return
        idx = int(raw) - 1
        if not (0 <= idx < len(accounts)):
            raise IndexError
        acc = accounts[idx]
        am.switch_to(acc)
        icon = CLI_ICON.get(acc.cli_type, "?")
        print()
        print(f"  ✓ Switched to [{icon}] {acc.label} ({acc.cli_type})")
        print()
        print("  Next session will launch on this account.")
        print("  To switch CLI mid-session: exit this chat and run `clawscummer`.")
        print()
    except ValueError:
        print("  Invalid input.")
    except IndexError:
        print("  Account number out of range.")
    except KeyboardInterrupt:
        print("\n  Cancelled.")


if __name__ == "__main__":
    main()
