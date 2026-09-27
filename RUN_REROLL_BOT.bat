@echo off
chcp 65001 >nul
title eFootball Reroll Bot Engine
cd /d "%~dp0"

echo ======================================================================
echo           eFootball Guest ID Reroll Bot & Card Detector
echo ======================================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH!
    pause
    exit /b 1
)

python reroll_bot.py

if %errorlevel% neq 0 (
    echo.
    echo [!] Program exited with code %errorlevel%.
    pause
)
