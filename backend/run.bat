@echo off
REM WenShape Backend Startup Script for Windows

REM Ensure working directory is this script's directory (robust for "backend\\run.bat" called from repo root)
cd /d %~dp0

setlocal EnableExtensions

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "PYTHON_CMD=python"
set "PYTHON_ARGS="

REM Pin Windows launcher to Python 3.12 by default
if /I "%OS%"=="Windows_NT" (
  py -3.12 --version >nul 2>&1
  if errorlevel 1 goto :python_missing
  set "PYTHON_CMD=py"
  set "PYTHON_ARGS=-3.12"
)

echo ========================================
echo   WenShape Backend Server
echo ========================================
echo.

REM Check Python installation
"%PYTHON_CMD%" %PYTHON_ARGS% --version >nul 2>&1
if errorlevel 1 goto :python_missing

echo [1/3] Checking dependencies...
"%PYTHON_CMD%" %PYTHON_ARGS% "scripts/check_requirements_installed.py" "requirements.txt" >nul 2>&1
if errorlevel 1 goto :install_deps
echo [OK] Pinned backend dependencies already installed.
goto :deps_ready

:install_deps
echo [INFO] Missing or mismatched packages detected. Installing pinned dependencies...
"%PYTHON_CMD%" %PYTHON_ARGS% -m pip install -r requirements.txt -q --disable-pip-version-check
if errorlevel 1 goto :pip_failed

:deps_ready

REM Check if .env exists
if exist ".env" goto :start_server

:create_env
echo.
echo [!] .env file not found. Creating default backend config (.env)...
echo # Auto-generated on first run> ".env"
echo HOST=127.0.0.1>> ".env"
echo PORT=8000>> ".env"
echo DEBUG=True>> ".env"
echo.>> ".env"
echo # Configure provider API keys below. See .env.example>> ".env"
if errorlevel 1 goto :env_failed
echo [!] Created: %CD%\.env
echo [!] Please edit .env and fill real provider API keys before using writing features.
echo.
goto :start_server

:start_server
REM Start server
echo.
echo [2/3] Starting server...
echo.
if "%PORT%"=="" set "PORT=%WENSHAPE_BACKEND_PORT%"
if "%PORT%"=="" set "PORT=8000"
if "%VITE_DEV_PORT%"=="" set "VITE_DEV_PORT=%WENSHAPE_FRONTEND_PORT%"
if "%VITE_DEV_PORT%"=="" set "VITE_DEV_PORT=3000"
echo   Frontend: http://localhost:%VITE_DEV_PORT%
echo   Backend:  http://localhost:%PORT%
echo   API Docs: http://localhost:%PORT%/docs
echo.

echo [3/3] Server running...
if "%WENSHAPE_AUTO_PORT%"=="" set "WENSHAPE_AUTO_PORT=1"
"%PYTHON_CMD%" %PYTHON_ARGS% -m app.main

pause
exit /b 0

:python_missing
echo ERROR: Python 3.12 is required on Windows and was not found.
echo.
echo Please install Python 3.12 from:
echo   https://www.python.org/downloads/
echo.
echo IMPORTANT:
echo   1) Install Python 3.12
echo   2) Enable "Add python.exe to PATH"
echo   3) Ensure "py -3.12 --version" works
echo.
pause
exit /b 1

:pip_failed
echo.
echo ERROR: Dependency installation failed.
echo This usually means your network/proxy cannot reach PyPI, or some pinned wheel is temporarily unavailable.
echo If dependencies are already installed, rerun backend\\run.bat and the offline checker will skip pip.
echo Otherwise try running: py -3.12 -m pip install -r requirements.txt
echo to see detailed error messages.
pause
exit /b 1

:env_failed
echo [!] Failed to create .env in: %CD%
echo.
pause
exit /b 1
