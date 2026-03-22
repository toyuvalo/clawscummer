#Requires -Version 5.1
# ClawsCummer v2.9 — launcher shim
# Runs the Textual TUI from wherever you call it.

$ScriptDir = $PSScriptRoot
python "$ScriptDir\tui.py" @args
