@echo off
rem Has Mozilla sign the Studielogboek extension so it stays installed in normal Firefox.
rem Needs a free addons.mozilla.org account and its API keys:
rem   https://addons.mozilla.org/developers/addon/api/key/
rem "Unlisted" means Mozilla signs it but does NOT publish it in the add-on store.
cd /d "%~dp0"
where npx >nul 2>nul || (echo Node.js is niet gevonden. Installeer Node.js van nodejs.org. & pause & exit /b 1)
echo.
echo Plak je API-sleutels van addons.mozilla.org (Tools - Manage API Keys).
echo Ze worden niet opgeslagen.
echo.
set /p AMO_KEY=JWT issuer (user:12345:67):
set /p AMO_SECRET=JWT secret:
echo.
echo Ondertekenen bij Mozilla, dit duurt meestal 1 tot 5 minuten...
call npx --yes web-ext@8 sign --channel=unlisted --source-dir extension --artifacts-dir web-ext-artifacts --api-key=%AMO_KEY% --api-secret=%AMO_SECRET% --approval-timeout=900000
set AMO_KEY=
set AMO_SECRET=
if errorlevel 1 (echo. & echo Ondertekenen mislukt, zie de melding hierboven. & pause & exit /b 1)
echo.
echo Klaar! Open het .xpi-bestand in web-ext-artifacts met Firefox (slepen naar een Firefox-venster)
echo en klik op "Toevoegen". Daarna blijft de extensie ook na herstarten geinstalleerd.
start "" "%~dp0web-ext-artifacts"
pause
