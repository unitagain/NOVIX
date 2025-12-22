@echo off
REM NOVIX Backend Startup Script for Windows
REM Windows 启动脚本

chcp 65001 >nul

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo Starting NOVIX Backend Server...
echo 正在启动 NOVIX 后端服务器...

REM Check if virtual environment exists / 检查虚拟环境是否存在
if not exist "venv\" (
    echo Virtual environment not found. Creating...
    echo 虚拟环境不存在，正在创建...
    python -m venv venv
    call venv\Scripts\activate
) else (
    call venv\Scripts\activate
)

echo Installing dependencies...
echo 正在安装依赖...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Please check the error above.
    echo 依赖安装失败，请检查上方错误信息。
    pause
    exit /b 1
)

REM Check if .env exists / 检查 .env 是否存在
if not exist ".env" (
    echo Warning: .env file not found. Copying from .env.example...
    echo 警告：.env 文件不存在，正在从 .env.example 复制...
    copy .env.example .env
    echo Please edit .env file and add your API keys!
    echo 请编辑 .env 文件并添加你的 API 密钥！
    pause
)

REM Start server / 启动服务器
echo.
echo Starting server at http://localhost:8000
echo 服务器启动于 http://localhost:8000
echo API Docs: http://localhost:8000/docs
echo.

python -m app.main

pause
