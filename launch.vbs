Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\toyuv\clawscummer"
WshShell.Run "cmd /c python clawscummer.py", 1, False
