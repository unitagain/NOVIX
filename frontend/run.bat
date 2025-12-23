@echo off
REM NOVIX Frontend Startup Script for Windows

echo ========================================
echo   NOVIX Frontend
echo ========================================
echo.

REM Check Node.js installation
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js is not installed or not in PATH.
    echo.
    echo Please install Node.js 18+ from:
    echo   https://nodejs.org/
    echo.
    echo Download the LTS version and run the installer.
    echo.
    pause
    exit /b 1
)

REM Check if node_modules exists
if not exist "node_modules\" (
    echo [1/2] Installing dependencies...
    call npm install
    if errorlevel 1 (
        echo.
        echo ERROR: npm install failed.
        pause
        exit /b 1
    )
)

echo [2/2] Starting development server...
echo.
echo   Frontend: http://localhost:3000
echo   Backend:  http://localhost:8000 (make sure it is running)
echo.

call npm run dev
