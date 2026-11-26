@echo off
chcp 65001 >nul
title eFootball .dat converter
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    set "PY=py"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo [ERROR] ไม่พบ Python บนเครื่องนี้
        echo ติดตั้งจาก https://www.python.org  ^(ติ๊ก "Add to PATH" ตอนลง^)
        echo.
        pause
        exit /b 1
    )
)

%PY% "%~dp0convert.py" %*

echo.
pause
