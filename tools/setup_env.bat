@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
title Setup environment - Otchetnost UK 728

if not exist "data" mkdir "data" >nul 2>nul
set "LOG_FILE=%CD%\data\setup_env_log.txt"

if not exist "main.py" (
    echo ERROR: main.py not found. Unpack the ZIP first and run BAT from the program folder.
    pause
    exit /b 1
)
if not exist "requirements.txt" (
    echo ERROR: requirements.txt not found. Unpack the ZIP first and run BAT from the program folder.
    pause
    exit /b 1
)

>"%LOG_FILE%" echo [%DATE% %TIME%] START setup in %CD%
echo Preparing environment...

call :find_python
if not defined PY_CMD call :install_python
if not defined PY_CMD goto no_python

echo Python command: %PY_CMD%
>>"%LOG_FILE%" echo Python command: %PY_CMD%

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    %PY_CMD% -m venv .venv
    if errorlevel 1 goto fail
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto fail

python -m pip install --upgrade pip
if errorlevel 1 goto fail

python -m pip install -r requirements.txt
if errorlevel 1 goto fail

>>"%LOG_FILE%" echo [%DATE% %TIME%] OK setup complete
exit /b 0

:find_python
set "PY_CMD="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
    exit /b 0
)
python -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=python"
    exit /b 0
)
python3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=python3"
    exit /b 0
)
exit /b 0

:install_python
echo Python not found. Trying winget install...
where winget >nul 2>nul
if errorlevel 1 exit /b 0
winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 0
set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
call :find_python
exit /b 0

:no_python
echo.
echo ERROR: Python was not found and automatic installation did not work.
echo Install Python 3.12 manually from python.org and enable Add python.exe to PATH.
echo Then close this window and run START_HERE.bat again.
pause
exit /b 1

:fail
echo.
echo Setup failed. See data\setup_env_log.txt
pause
exit /b 1
