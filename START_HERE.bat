@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Otchetnost UK 728 - Start

echo ============================================================
echo  Otchetnost UK 728 - Start
echo ============================================================
echo.

call "%~dp0tools\setup_env.bat"
if errorlevel 1 exit /b 1

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Cannot activate .venv
    pause
    exit /b 1
)

echo Starting program...
python main.py
if errorlevel 1 (
    echo.
    echo Program closed with an error.
    pause
    exit /b 1
)
exit /b 0
