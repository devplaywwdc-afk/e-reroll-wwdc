@echo off
chcp 65001 >nul
title eFootball Traffic Monitor [Charles-Like Web UI]
cd /d "%~dp0"

echo ======================================================================
echo           eFootball Live Traffic Monitor - Launcher
echo ======================================================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH!
    echo Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

echo [*] Starting Traffic Monitor Server...
echo [*] Web UI will open at http://127.0.0.1:8770
echo [*] Press Ctrl+C in this window to stop.
echo.

python monitor\run.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Monitor stopped with error code %errorlevel%.
    pause
)
