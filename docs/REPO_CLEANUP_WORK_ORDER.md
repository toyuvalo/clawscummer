# Clawscummer Repo Cleanup Work Order For Claude

Date: 2026-04-08
Scope: Tracked repo state only
Final cleanup requirement: archive this document out of the repo root when the work is complete

## Primary Goal

Keep Clawscummer shippable and understandable by tightening the repo into:
- one clean root master document
- one clear product story
- grouped code by app/runtime responsibility
- no stale build outputs or ambiguous helper scripts in the root

## Current Issues To Fix

1. The root is crowded with launchers, build scripts, platform wrappers, binaries/output folders, GUI/TUI code, and HA integration side-by-side.
2. The README is strong product-facing documentation, but the repo structure underneath needs to match that clarity.
3. `build/`, `dist/`, and `__pycache__/` are present in the repo tree and need intentional tracked-vs-generated handling.
4. The current architecture spans Python app core, GUI, TUI, and HA integration, but the root does not make those boundaries obvious.
5. Local changes in `clawscummer.py`, `clawscummer.ps1`, and `tui.py` indicate active implementation drift that should be reflected in docs if relevant.

## Work Order

### 1. Make The Root Intentional
- Keep `README.md` as the master root document.
- Ensure it links clearly to the actual current entrypoints and build/release paths.
- Reduce root clutter by moving lower-level docs, packaging helpers, or historical artifacts into organized folders.

### 2. Group Code By Responsibility
- Make the repo layout clearer around:
  - app core
  - GUI/webview frontend
  - TUI runtime
  - HA integration
  - packaging/build assets
- Move files into sensible folders where practical without breaking release flows.

### 3. Clean Generated Output Policy
- Decide whether `build/` and `dist/` should be tracked.
- If not intentional, remove them from Git and ignore them.
- Keep only intentional release metadata or fixtures.

### 4. Reconcile Launch And Build Scripts
- Audit the root launcher/build/install scripts across Windows/Linux/macOS.
- Keep only current supported ones in active locations.
- Move historical or redundant wrappers into an archive/tools folder if they remain useful for reference.

### 5. Match Docs To The Actual Product Surface
- Verify the README’s claims about GUI, TUI, account rotation, HA integration, and releases against tracked code structure.
- Add a short architecture section or linked doc if needed so contributors can find the actual implementation boundaries quickly.

### 6. Add Maintenance Guardrails
- Add a lightweight repo policy covering:
  - one root master document
  - generated outputs not tracked by default
  - launch/build scripts kept current and intentional
  - historical material archived or labeled clearly

## Acceptance Criteria
- The root has one strong master document and a cleaner set of top-level files.
- Build outputs are either intentionally tracked or removed.
- The boundaries between core app, GUI, TUI, and HA integration are obvious.
- A contributor can tell how to run and package the project quickly.

## Final Deliverable
- short cleanup report with files moved, removed, rewritten, archived, and any unresolved packaging decisions

## Archive Instruction
- When done, move this file out of the repo root into an archive/docs-history location.
