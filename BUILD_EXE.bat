@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Otchetnost UK 728 - Build EXE

echo ============================================================
echo  Otchetnost UK 728 - Build EXE
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

rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q *.spec 2>nul

python -m PyInstaller --noconfirm --clean --windowed --onedir --name "Otchetnost_UK_728" main.py
if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

if not exist "dist\Otchetnost_UK_728\data" mkdir "dist\Otchetnost_UK_728\data"

echo.
echo Done.
echo Program folder: dist\Otchetnost_UK_728\
echo EXE: dist\Otchetnost_UK_728\Otchetnost_UK_728.exe
pause
exit /b 0
