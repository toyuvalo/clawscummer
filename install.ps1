#Requires -Version 5.1
<#
.SYNOPSIS
    ClawsCummer installer — sets up dependencies and adds to PATH.
#>

$ScriptDir  = $PSScriptRoot
$ProfileDir = Split-Path $PROFILE -Parent

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   ClawsCummer Installer                  ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Check Python ──────────────────────────────────────────────────────────────
Write-Host "  [1/4] Checking Python..." -ForegroundColor White
$pyVersion = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✗ Python not found. Install Python 3.8+ from https://python.org" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ $pyVersion" -ForegroundColor Green

# ── Install Python packages ───────────────────────────────────────────────────
Write-Host "  [2/4] Installing Python packages (textual, rich)..." -ForegroundColor White
python -m pip install textual rich --quiet --upgrade
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✗ pip install failed. Try running as Administrator." -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ Packages installed" -ForegroundColor Green

# ── Add to PATH via PowerShell Profile ───────────────────────────────────────
Write-Host "  [3/4] Setting up 'clawscummer' command..." -ForegroundColor White

# Create profile dir if needed
if (-not (Test-Path $ProfileDir)) {
    New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
}

# Create or update profile
if (-not (Test-Path $PROFILE)) {
    New-Item -ItemType File -Path $PROFILE -Force | Out-Null
}

$alias = "function clawscummer { powershell -NoProfile -ExecutionPolicy Bypass -File `"$ScriptDir\clawscummer.ps1`" }"
$profileContent = Get-Content $PROFILE -Raw -ErrorAction SilentlyContinue

if ($profileContent -notlike "*clawscummer.ps1*") {
    Add-Content -Path $PROFILE -Value "`n# ClawsCummer`n$alias"
    Write-Host "  ✓ Added 'clawscummer' to PowerShell profile" -ForegroundColor Green
} else {
    Write-Host "  ✓ 'clawscummer' already in profile" -ForegroundColor DarkGreen
}

# Also add to PATH permanently so it works from CMD/other shells
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$ScriptDir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$userPath;$ScriptDir", "User")
    Write-Host "  ✓ Added to system PATH" -ForegroundColor Green
} else {
    Write-Host "  ✓ Already in system PATH" -ForegroundColor DarkGreen
}

# ── Create Desktop Shortcut ───────────────────────────────────────────────────
Write-Host "  [4/4] Creating Desktop shortcut..." -ForegroundColor White
try {
    $desktop    = [Environment]::GetFolderPath("Desktop")
    $shortcut   = "$desktop\ClawsCummer.lnk"
    $wsh        = New-Object -ComObject WScript.Shell
    $lnk        = $wsh.CreateShortcut($shortcut)
    $lnk.TargetPath       = "powershell.exe"
    $lnk.Arguments        = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptDir\clawscummer.ps1`""
    $lnk.WorkingDirectory = $ScriptDir
    $lnk.IconLocation     = "powershell.exe,0"
    $lnk.Description      = "ClawsCummer — Claude Account Manager"
    $lnk.Save()
    Write-Host "  ✓ Shortcut created on Desktop" -ForegroundColor Green
} catch {
    Write-Host "  ! Could not create shortcut (non-critical)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║   Installation complete!                 ║" -ForegroundColor Green
Write-Host "  ║                                          ║" -ForegroundColor Green
Write-Host "  ║   Usage:                                 ║" -ForegroundColor Green
Write-Host "  ║     • Type: clawscummer                  ║" -ForegroundColor Green
Write-Host "  ║     • Or double-click Desktop shortcut   ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Restart your terminal for 'clawscummer' command to work." -ForegroundColor DarkGray
Write-Host ""
