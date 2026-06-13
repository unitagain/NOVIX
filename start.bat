@echo off
setlocal
chcp 65001 >nul
echo ========================================
echo    WenShape One-Click Start
echo ========================================
echo.

py -3.12 --version >nul 2>&1
if errorlevel 1 goto :python_missing

py -3.12 "%~dp0start.py"
exit /b 0

:python_missing
echo [Error] Python 3.12 not found.
echo Please install Python 3.12 and ensure "py -3.12 --version" works.
echo Download: https://www.python.org/downloads/
echo.
pause
exit /b 1
