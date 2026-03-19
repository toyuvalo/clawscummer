<h1 align="center">ClawsCummer</h1>

<p align="center">
  <img src="clawscummer_thumb.png" width="100" alt="ClawsCummer TUI" />
</p>

**Multi-account Claude & Gemini session manager.** Seamlessly switch accounts on rate limit, browse past conversations, and pick up exactly where you left off.

---

## Features

- **Auto account rotation** — detects rate limits and context-full errors in real time, switches to your next account, and resumes the conversation automatically
- **Session browser** — lists all past Claude and Gemini conversations grouped by recency; resume any session in one keypress
- **Multi-CLI support** — manages both Claude Code and Gemini CLI accounts side by side
- **Cross-CLI handoff** — when switching between Claude and Gemini, generates a context summary so the new CLI can pick up seamlessly
- **Plan → Execute pipeline** — Gemini drafts a step-by-step implementation plan, Claude executes it
- **Instruction file scanner** — detects `AGENTS.md` and other instruction files in your working directory and passes them to the CLI automatically
- **Windows GUI** — embedded terminal via pywebview + xterm.js (Windows)
- **Linux / macOS TUI** — full-featured terminal UI via [Textual](https://github.com/Textualize/textual)

---

## Installation

### Linux / macOS

```bash
git clone https://github.com/toyuvalo/clawscummer
cd clawscummer
bash install.sh
```

Then run with:

```bash
clawscummer
```

### Windows

```powershell
git clone https://github.com/toyuvalo/clawscummer
cd clawscummer
.\install.ps1
```

Then run with:

```powershell
clawscummer
```

Or double-click the **ClawsCummer** shortcut created on your Desktop.

---

## Standalone Binaries

Pre-built binaries for Linux, macOS, and Windows are available on the [Releases](https://github.com/toyuvalo/clawscummer/releases) page — no Python required.

| Platform | File |
|----------|------|
| Linux    | `clawscummer-linux` |
| macOS    | `clawscummer-macos` |
| Windows  | `clawscummer-windows.exe` |

---

## Requirements

- Python 3.8+
- [Claude Code](https://claude.ai/code) and/or [Gemini CLI](https://github.com/google-gemini/gemini-cli) installed and on your PATH
- `textual`, `rich` (auto-installed on first run)

Optional (Windows GUI mode):
- `pywebview`, `pywinpty`, `websockets`

---

## Adding Accounts

1. Launch ClawsCummer — you'll land on the account picker
2. Select **+ Add New Account**
3. Choose **Claude** or **Gemini**
4. For Claude: log in to your second account in a separate terminal (`claude auth logout && claude auth login`), then press Enter to save
5. For Gemini: just give it a label — Gemini handles its own auth separately

ClawsCummer stores account credentials in `~/.clawscummer/accounts.json` (restricted permissions).

---

## Workflow Modes

Press `m` on the main screen to cycle through modes:

| Mode | Behaviour |
|------|-----------|
| `AUTO` | Launches the active account's CLI directly |
| `PLAN>EXEC` | Gemini generates an implementation plan first; Claude executes it |
| `MANUAL` | Same as AUTO, but disables automatic rate-limit switching |

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1` / `2` / `3` | Resume recent conversation |
| `n` | New session |
| `m` | Cycle workflow mode |
| `Ctrl+Shift+S` | Switch active account |
| `q` | Quit |

---

## Building from Source

**Linux / macOS**
```bash
bash build.sh
# → dist/clawscummer
```

**Windows**
```powershell
.\build.ps1
# → dist\clawscummer.exe
```

Binaries are built automatically for all platforms via GitHub Actions on every version tag push.

---

## Home Assistant Integration

The `ha_integration/` directory contains example YAML for controlling ClawsCummer via Home Assistant automations and Lovelace cards (useful for Tailscale-connected setups).

---

## License

MIT License with [Commons Clause](https://commonsclause.com/) — free to use, modify, and share. Commercial resale is not permitted.

See [LICENSE](LICENSE) for full terms.
