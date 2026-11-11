@echo off
setlocal EnableExtensions EnableDelayedExpansion
title PES Reroll Farm - Web Launcher
cd /d "%~dp0"

rem Do NOT taskkill python.exe here (would kill other projects).
rem Each click picks a free port and starts a new isolated dashboard.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [X] Python not found in PATH.
    pause
    exit /b 1
)

set "PORT="
for /f %%P in ('python pick_web_port.py 2^>nul') do set "PORT=%%P"
if not defined PORT (
    echo [X] No free port in 8080-8199.
    pause
    exit /b 1
)

set "HOST_PORT=%PORT%"
set "PORT=%PORT%"
set "PYTHONUNBUFFERED=1"

echo ===================================================
echo   PES Reroll Farm   http://localhost:%PORT%
echo   Click this .bat again to open another dashboard
echo ===================================================
echo.

start "" "http://localhost:%PORT%"
python server.py

endlocal
