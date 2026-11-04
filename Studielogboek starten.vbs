' Starts the Studielogboek tray app without a console window.
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root & "\companion"
sh.Run """" & root & "\.venv\Scripts\pythonw.exe"" -m studielogboek", 0, False
