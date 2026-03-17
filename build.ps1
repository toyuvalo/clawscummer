#Requires -Version 5.1
<#
.SYNOPSIS
    Build clawscummer.exe and create a Desktop shortcut.
#>

$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

Write-Host ""
Write-Host "  +------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |  ClawsCummer Builder                     |" -ForegroundColor Cyan
Write-Host "  +------------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# ── [1] Icon ──────────────────────────────────────────────────────────────────
Write-Host "  [1/3] Generating icon..." -ForegroundColor White
python make_icon.py
if ($LASTEXITCODE -ne 0) { Write-Host "  ! Icon generation failed" -ForegroundColor Yellow }

# ── [2] PyInstaller ───────────────────────────────────────────────────────────
Write-Host "  [2/3] Building exe (this takes ~60 seconds)..." -ForegroundColor White

$pyi_args = @(
    "clawscummer.py",
    "--onefile",
    "--console",
    "--icon=clawscummer.ico",
    "--name=clawscummer",
    "--collect-all", "textual",
    "--collect-all", "rich",
    "--hidden-import", "textual",
    "--hidden-import", "textual.app",
    "--hidden-import", "textual._xterm_theme",
    "--hidden-import", "textual.css.query",
    "--hidden-import", "textual.widgets._list_view",
    "--hidden-import", "textual.widgets._list_item",
    "--hidden-import", "textual.widgets._input",
    "--hidden-import", "textual.widgets._button",
    "--hidden-import", "textual.widgets._label",
    "--hidden-import", "textual.widgets._static",
    "--hidden-import", "textual.widgets._rule",
    "--hidden-import", "textual.containers",
    "--hidden-import", "textual.screen",
    "--noconfirm",
    "--clean"
)

pyinstaller @pyi_args 2>&1 | Where-Object { $_ -notmatch "^(INFO|WARNING: Collect)" }

if ($LASTEXITCODE -ne 0) {
    Write-Host "  x Build failed. Check output above." -ForegroundColor Red
    exit 1
}

$exePath = "$ScriptDir\dist\clawscummer.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "  x EXE not found at expected path: $exePath" -ForegroundColor Red
    exit 1
}
Write-Host "  + Built: $exePath" -ForegroundColor Green

# ── [3] Desktop Shortcut ──────────────────────────────────────────────────────
Write-Host "  [3/3] Creating Desktop shortcut..." -ForegroundColor White

try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnkPath = "$desktop\ClawsCummer.lnk"
    $wsh     = New-Object -ComObject WScript.Shell
    $lnk     = $wsh.CreateShortcut($lnkPath)

    $lnk.TargetPath       = $exePath
    $lnk.WorkingDirectory = $ScriptDir
    $lnk.IconLocation     = "$ScriptDir\clawscummer.ico,0"
    $lnk.Description      = "ClawsCummer - Claude Account Manager"
    # Request admin elevation via shortcut
    $lnk.Save()

    # Embed 'run as admin' flag into the shortcut binary
    $bytes = [System.IO.File]::ReadAllBytes($lnkPath)
    $bytes[0x15] = $bytes[0x15] -bor 0x20   # set bit 5 of HotKey flags = RunAsAdministrator
    [System.IO.File]::WriteAllBytes($lnkPath, $bytes)

    Write-Host "  + Shortcut created: $lnkPath" -ForegroundColor Green
} catch {
    Write-Host "  ! Shortcut creation failed: $_" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  +------------------------------------------+" -ForegroundColor Green
Write-Host "  |  Done! Double-click ClawsCummer on your  |" -ForegroundColor Green
Write-Host "  |  Desktop to launch.                      |" -ForegroundColor Green
Write-Host "  +------------------------------------------+" -ForegroundColor Green
Write-Host ""
