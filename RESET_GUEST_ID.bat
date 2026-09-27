@echo off
chcp 65001 >nul
title eFootball Guest ID Manager [Reset / Backup]
cd /d "%~dp0"

echo ======================================================================
echo           eFootball Guest ID Reset & Backup Tool
echo ======================================================================
echo.

:: 1. Detect device
set ADB_DEV=
for /f "tokens=1" %%a in ('adb devices ^| findstr /r "device$"') do (
    if not "%%a"=="List" (
        set ADB_DEV=%%a
        goto :found_dev
    )
)

adb connect 127.0.0.1:5557 >nul 2>&1
adb connect 127.0.0.1:16384 >nul 2>&1

for /f "tokens=1" %%a in ('adb devices ^| findstr /r "device$"') do (
    if not "%%a"=="List" (
        set ADB_DEV=%%a
        goto :found_dev
    )
)

:found_dev
if "%ADB_DEV%"=="" (
    echo [ERROR] Cannot reach emulator via ADB!
    pause
    exit /b 1
)

echo [*] Target Emulator: %ADB_DEV%
echo [*] Stopping eFootball...
adb -s %ADB_DEV% shell am force-stop jp.konami.pesam >nul 2>&1

:: 2. Auto Backup Current ID before reset
set TIMESTAMP=%date:~10,4%%date:~4,2%%date:~7,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set TIMESTAMP=%TIMESTAMP: =0%
set BACKUP_DIR=saved_accounts\%TIMESTAMP%

mkdir "%BACKUP_DIR%" >nul 2>&1
echo [*] Backing up current ID to %BACKUP_DIR%...
adb -s %ADB_DEV% shell "su -c 'cat /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat'" > "%BACKUP_DIR%\online_user_id_data.dat" 2>nul

for %%F in ("%BACKUP_DIR%\online_user_id_data.dat") do (
    if %%~zF LSS 10 (
        rmdir /s /q "%BACKUP_DIR%" >nul 2>&1
        echo [*] No existing ID to backup or backup was empty.
    ) else (
        echo [OK] Backed up successfully!
        type "%BACKUP_DIR%\online_user_id_data.dat"
        echo.
    )
)

:: 3. Clear Auth & System data
echo.
echo [*] Removing ID data from emulator...
adb -s %ADB_DEV% shell "su -c 'rm -f /data/user/0/jp.konami.pesam/files/SaveData/AUTH/* /data/user/0/jp.konami.pesam/files/SaveData/SYSTEM/*'"

echo.
echo ======================================================================
echo  [SUCCESS] Guest ID reset complete!
echo  Game resources (2GB+) are preserved.
echo  Run START_MONITOR.bat and open the game to create a new Guest ID.
echo ======================================================================
echo.
pause
