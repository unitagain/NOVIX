@echo off
REM NOVIX Frontend Startup Script for Windows

chcp 65001 >nul

echo Starting NOVIX Frontend...
echo 正在启动 NOVIX 前端...

REM Check if node_modules exists
if not exist "node_modules\" (
    echo Installing dependencies...
    echo 正在安装依赖...
    npm install
)

echo.
echo Starting development server at http://localhost:3000
echo 开发服务器启动于 http://localhost:3000
echo.
echo Make sure backend is running at http://localhost:8000
echo 请确保后端服务运行在 http://localhost:8000
echo.

npm run dev
