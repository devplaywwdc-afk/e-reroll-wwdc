@echo off
chcp 65001 >nul
title eFootball Swap Setup [One-Time Injection]
cd /d "%~dp0"

echo ======================================================================
echo       eFootball Root Swap Setup (One-Time Installation)
echo ======================================================================
echo.
echo This script will push the patched base APK and Frida Gadget into
echo MuMu Player's /data/app/ directory via Root.
echo.

:: 1. Find or connect to emulator device
set ADB_DEV=
for /f "tokens=1" %%a in ('adb devices ^| findstr /r "device$"') do (
    if not "%%a"=="List" (
        set ADB_DEV=%%a
        goto :device_ready
    )
)

echo [*] Connecting to MuMu Player via ADB...
adb connect 127.0.0.1:5557 >nul 2>&1
adb connect 127.0.0.1:16384 >nul 2>&1

for /f "tokens=1" %%a in ('adb devices ^| findstr /r "device$"') do (
    if not "%%a"=="List" (
        set ADB_DEV=%%a
        goto :device_ready
    )
)

:device_ready
if "%ADB_DEV%"=="" (
    echo.
    echo [ERROR] Cannot reach emulator via ADB!
    echo Make sure MuMu Player is running and Root permission is enabled.
    pause
    exit /b 1
)

echo [OK] Target Emulator Device: %ADB_DEV%

:: 2. Push Files to /data/local/tmp
echo.
echo [*] Pushing files to emulator (/data/local/tmp)...
adb -s %ADB_DEV% push pesam_base.final2.apk /data/local/tmp/pesam_base.final2.apk
if %errorlevel% neq 0 ( echo [ERROR] Failed to push pesam_base.final2.apk & pause & exit /b 1 )

adb -s %ADB_DEV% push libfrida-gadget.so /data/local/tmp/libfrida-gadget.so
if %errorlevel% neq 0 ( echo [ERROR] Failed to push libfrida-gadget.so & pause & exit /b 1 )

adb -s %ADB_DEV% push gadget.config.json /data/local/tmp/libfrida-gadget.config.so
if %errorlevel% neq 0 ( echo [ERROR] Failed to push gadget.config.json & pause & exit /b 1 )

adb -s %ADB_DEV% push do_swap.sh /data/local/tmp/do_swap.sh
if %errorlevel% neq 0 ( echo [ERROR] Failed to push do_swap.sh & pause & exit /b 1 )

:: 3. Execute Swap Script via su
echo.
echo [*] Executing do_swap.sh via root...
adb -s %ADB_DEV% shell su -c "sh /data/local/tmp/do_swap.sh"

echo.
echo ======================================================================
echo Setup finished! You can now run START_MONITOR.bat to sniff traffic.
echo ======================================================================
pause
