@echo off
setlocal EnableExtensions
cd /d "%~dp0"
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q *.spec 2>nul
echo Build folders removed.
pause
exit /b 0
