@echo off
rem Puts a shortcut to Studielogboek in your Windows Startup folder.
rem To turn it off again, delete "Studielogboek.lnk" from: shell:startup
cd /d "%~dp0"
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\Studielogboek.lnk');" ^
  "$s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%~dp0Studielogboek starten.vbs\"'; $s.WorkingDirectory = '%~dp0'; $s.Save()"
echo Studielogboek start nu automatisch als je inlogt.
pause
