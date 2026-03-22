<p align="center">
  <img src="clawscummer_thumb.png" alt="ClawsCummer" width="200">
</p>

<h1 align="center">ClawsCummer</h1>

<p align="center">Multi-account Claude &amp; Gemini session manager. Hit a rate limit and it rotates to the next account instantly — no interruption, no context loss.</p>

<p align="center"><a href="https://webdev.dvlce.ca/clawscummer">Project page →</a></p>

---

When you're running long autonomous coding sessions, rate limits kill flow. ClawsCummer treats your collection of Claude and Gemini accounts as a pool, rotates between them automatically the moment one is hit, and preserves context so the incoming session picks up exactly where the previous one left off.

## Features

- **Auto account rotation** — monitors for rate limits in real time, switches to the next account silently, resumes the task without dropping context
- **Session browser** — browse, search, and resume all past conversations across every account from one interface
- **Plan → Execute pipeline** — Gemini drafts a structured step-by-step plan; Claude executes it. Two models, each doing what they're best at
- **Cross-CLI handoff** — when switching accounts or models, auto-generates a context summary and injects it into the new session
- **AGENTS.md scanner** — scans the working directory tree for `AGENTS.md` instruction files and injects them into the session context automatically
- **Home Assistant integration** — optional `ha_integration/` module syncs session state and agent activity to HA sensors for dashboards and automations
- **Windows GUI** — native window via pywebview + xterm.js embedded terminal
- **Linux / macOS TUI** — full-featured terminal UI via [Textual](https://github.com/Textualize/textual)
- **PyInstaller binaries** — standalone `.exe` and Linux binaries, no Python install needed on the target machine

## Install

### From source

```bash
git clone https://github.com/toyuvalo/clawscummer
cd clawscummer
bash install.sh          # Linux / macOS
.\install.ps1            # Windows (PowerShell)
```

### Standalone binary (no Python required)

Download from [Releases](https://github.com/toyuvalo/clawscummer/releases):

| Platform | File |
|----------|------|
| Windows  | `clawscummer-windows.exe` |
| Linux    | `clawscummer-linux` |
| macOS    | `clawscummer-macos` |

## Requirements

- [Claude Code](https://claude.ai/code) and/or [Gemini CLI](https://github.com/google-gemini/gemini-cli) installed and on your PATH
- Python 3.8+ (if running from source)

## Adding Accounts

1. Launch ClawsCummer
2. Select **+ Add New Account**
3. Choose **Claude** or **Gemini** and follow the auth prompts
4. Credentials stored in `~/.clawscummer/accounts.json`

## Configuration

Copy `secrets.json.example` to `secrets.json` and fill in any optional API keys or HA connection details before first launch.

## Workflow Modes

| Mode | Behaviour |
|------|-----------|
| `AUTO` | Launches the active account's CLI directly |
| `PLAN→EXEC` | Gemini plans, Claude executes |
| `MANUAL` | AUTO with automatic rotation disabled |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `1` / `2` / `3` | Resume recent conversation |
| `n` | New session |
| `m` | Cycle workflow mode |
| `Ctrl+Shift+S` | Switch active account |
| `q` | Quit |

## Building from Source

```bash
bash build.sh        # Linux / macOS → dist/clawscummer
.\build.ps1          # Windows → dist\clawscummer.exe
```

Binaries are built automatically via GitHub Actions on every version tag.

## Related

- [webdev.dvlce.ca/clawscummer](https://webdev.dvlce.ca/clawscummer) — project page

## License

MIT with [Commons Clause](https://commonsclause.com/) — free to use, modify, and share. Commercial resale not permitted.
