#Requires -Version 5.1
<#
.SYNOPSIS
    ClawsCummer - Multi-account Claude session manager launcher
.DESCRIPTION
    Elevates to admin, presents account/session picker, launches Claude,
    and auto-switches accounts on rate limit detection.
#>

param()

# ── Elevation ─────────────────────────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" `
        -Verb RunAs
    exit
}

# ── Paths ─────────────────────────────────────────────────────────────────────
$ScriptDir   = $PSScriptRoot
$ClaudeDir   = "$env:USERPROFILE\.claude"
$LaunchFile  = "$ClaudeDir\clawscummer_launch.json"
$SignalFile  = "$ClaudeDir\clawscummer_switch.signal"
$PythonScript= "$ScriptDir\clawscummer.py"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Banner {
    param([string]$Msg, [string]$Color = "Cyan")
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor DarkGray
    Write-Host "  ║  $($Msg.PadRight(42))║" -ForegroundColor $Color
    Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor DarkGray
    Write-Host ""
}

function Get-LaunchData {
    if (Test-Path $LaunchFile) {
        try { return Get-Content $LaunchFile -Raw | ConvertFrom-Json }
        catch { return $null }
    }
    return $null
}

function Remove-StaleFiles {
    Remove-Item $LaunchFile  -Force -ErrorAction SilentlyContinue
    Remove-Item $SignalFile  -Force -ErrorAction SilentlyContinue
}

function Invoke-ClaudeSession {
    param(
        [string]$Action,
        [string]$ProjectPath,
        [string]$SessionId,
        [string]$Prompt,
        [bool]  $IsResume
    )

    # Switch to project directory if resuming
    $originalDir = $PWD.Path
    if ($IsResume -and $ProjectPath -and (Test-Path $ProjectPath)) {
        Set-Location $ProjectPath
    }

    $attemptCount = 0
    $maxAttempts  = 10   # Safety cap on auto-switches

    while ($attemptCount -lt $maxAttempts) {
        $attemptCount++

        # Determine claude command
        if ($IsResume -and $attemptCount -eq 1) {
            $claudeArgs = @("--continue")
        } elseif ($attemptCount -gt 1) {
            # After a switch, always continue
            $claudeArgs = @("--continue")
        } elseif ($Prompt) {
            # New session with initial prompt — pipe it in
            $claudeArgs = @()
        } else {
            $claudeArgs = @()
        }

        # Build current account info
        $accountInfo = ""
        if (Test-Path "$ClaudeDir\clawscummer_accounts.json") {
            try {
                $accountsData = Get-Content "$ClaudeDir\clawscummer_accounts.json" -Raw | ConvertFrom-Json
                $activeId     = $accountsData.active_id
                $activeAcc    = $accountsData.accounts | Where-Object { $_.id -eq $activeId } | Select-Object -First 1
                if ($activeAcc) { $accountInfo = $activeAcc.label }
            } catch {}
        }
        if ($accountInfo) {
            Write-Banner "Account: $accountInfo" "Green"
        }

        # Start watcher in background (hidden window)
        $watcherJob = $null

        # Launch claude process — inherit current console so it's fully interactive
        if ($Prompt -and $attemptCount -eq 1) {
            # Feed initial prompt via stdin
            $claudeProc = Start-Process -FilePath "claude" `
                -ArgumentList $claudeArgs `
                -PassThru -NoNewWindow
        } else {
            $claudeProc = Start-Process -FilePath "claude" `
                -ArgumentList $claudeArgs `
                -PassThru -NoNewWindow
        }

        if (-not $claudeProc) {
            Write-Host "[ClawsCummer] Failed to start claude. Is it installed and on PATH?" -ForegroundColor Red
            break
        }

        # Start rate-limit watcher as hidden background process
        $claudePid   = $claudeProc.Id
        $watcherProc = Start-Process -FilePath "python" `
            -ArgumentList "`"$PythonScript`" --watch $claudePid" `
            -PassThru -WindowStyle Hidden -ErrorAction SilentlyContinue

        # Wait for claude to finish
        $claudeProc.WaitForExit()

        # Stop watcher
        if ($watcherProc -and -not $watcherProc.HasExited) {
            Stop-Process -Id $watcherProc.Id -Force -ErrorAction SilentlyContinue
        }

        # Check for rate-limit signal
        if (Test-Path $SignalFile) {
            Remove-Item $SignalFile -Force -ErrorAction SilentlyContinue
            Write-Banner "Rate limit detected — switching account..." "Yellow"

            # Rotate to next account
            python "$PythonScript" --switch-auto
            Start-Sleep -Milliseconds 800

            Write-Banner "Resuming conversation on new account..." "Cyan"
            $IsResume = $true
            continue   # Loop → restart claude --continue
        }

        # Normal exit
        break
    }

    Set-Location $originalDir
}

# ── Main ──────────────────────────────────────────────────────────────────────
Remove-StaleFiles

# Clear screen and show a clean header
Clear-Host
Write-Host "  Starting ClawsCummer..." -ForegroundColor DarkCyan

# Run the Python TUI
python "$PythonScript"

# Read what the user chose
$launch = Get-LaunchData

if (-not $launch -or $launch.action -eq "quit") {
    Write-Host ""
    Write-Host "  Goodbye." -ForegroundColor DarkGray
    exit
}

Remove-StaleFiles

switch ($launch.action) {
    "resume" {
        Write-Banner "Resuming previous session..." "Cyan"
        Invoke-ClaudeSession `
            -Action      "resume" `
            -ProjectPath $launch.project_path `
            -SessionId   $launch.session_id `
            -Prompt      "" `
            -IsResume    $true
    }
    "new" {
        Write-Banner "Starting new session..." "Cyan"
        Invoke-ClaudeSession `
            -Action      "new" `
            -ProjectPath "" `
            -SessionId   "" `
            -Prompt      $launch.prompt `
            -IsResume    $false
    }
    default {
        Write-Host "  Unknown action: $($launch.action)" -ForegroundColor Red
    }
}
