@echo off
chcp 65001 >nul
title Build eFootball Account Injector
cd /d "%~dp0"

echo ======================================================================
echo   Building eFootball Account Injector  (one-file .exe)
echo ======================================================================
echo.

:: 1) ensure PyInstaller
python -c "import PyInstaller" 2>nul
if %errorlevel% neq 0 (
    echo [*] Installing PyInstaller...
    python -m pip install --upgrade pyinstaller
)

:: 2) build  (--noconsole = no black console window; windowed GUI only)
echo [*] Building...
python -m PyInstaller --onefile --noconsole --clean --name eFootballInjector ^
    --distpath dist --workpath build --specpath build app.py

if %errorlevel% neq 0 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo ======================================================================
echo  Done!  ->  dist\eFootballInjector.exe
echo.
echo  Tip: put an adb.exe next to the exe if adb is not on PATH
echo       (the app also auto-detects MuMu / LDPlayer / Nox / MEmu adb).
echo ======================================================================
pause
