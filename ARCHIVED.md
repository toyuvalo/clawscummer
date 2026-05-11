# ARCHIVED — 2026-04-27

This repository is **archived** and no longer actively maintained.

- GitHub releases at `toyuvalo/clawscummer` remain available for download.
- No new releases planned.
- Removed from the wiki cross-repo lint map (`/wiki-lint-full` REPO_MAP) so vault drift checks no longer target it.

## Why

ClawsCummer's account-rotation use case is being superseded by tooling built into:
- `dvlce-fresh` (`/webdev/clawscummer` download page links to legacy releases here)
- `ripwave` (newer release pattern)

## Vault context

- Project article: `E:\Claude\wiki\projects\clawscummer.md`
- Web portal article: `E:\Claude\wiki\projects\clawscummer-web-portal.md`
- Historical `/clawscummer` slash command spec: `E:\Claude\_archive\archived-wiki\clawscummer-command.md`

## If reviving

1. Delete this `ARCHIVED.md` and update the vault article (`status: archived` → `status: active`).
2. Re-add `clawscummer → E:\clawscummer\` to `REPO_MAP` in `E:\Claude\.claude\commands\wiki-lint-full.md`.
3. Pull latest, verify Python toolchain, refresh `CHANGELOG`.
4. Run `/wiki-lint-full` to confirm the slug resolves.
