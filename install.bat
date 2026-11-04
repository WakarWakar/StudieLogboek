@echo off
rem Sets up Studielogboek: a private Python environment with its dependencies.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ is niet gevonden. Installeer Python van python.org en probeer opnieuw. & pause & exit /b 1)
if not exist .venv (
    echo Python-omgeving aanmaken...
    python -m venv .venv || (pause & exit /b 1)
)
echo Onderdelen installeren...
.venv\Scripts\python -m pip install --quiet --disable-pip-version-check -r requirements.txt || (pause & exit /b 1)
echo.
echo Klaar. Start Studielogboek met "Studielogboek starten.vbs".
echo Automatisch starten met Windows: dubbelklik "autostart aanzetten.bat".
pause
