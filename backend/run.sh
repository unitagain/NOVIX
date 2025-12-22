#!/bin/bash
# NOVIX Backend Startup Script for Linux/Mac
# Linux/Mac 启动脚本

echo "Starting NOVIX Backend Server..."
echo "正在启动 NOVIX 后端服务器..."

# Check if virtual environment exists / 检查虚拟环境是否存在
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Creating..."
    echo "虚拟环境不存在，正在创建..."
    python -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
echo "正在安装依赖..."
python -m pip install -r requirements.txt

# Check if .env exists / 检查 .env 是否存在
if [ ! -f ".env" ]; then
    echo "Warning: .env file not found. Copying from .env.example..."
    echo "警告：.env 文件不存在，正在从 .env.example 复制..."
    cp .env.example .env
    echo "Please edit .env file and add your API keys!"
    echo "请编辑 .env 文件并添加你的 API 密钥！"
    echo "Press Enter to continue..."
    read
fi

# Start server / 启动服务器
echo ""
echo "Starting server at http://localhost:8000"
echo "服务器启动于 http://localhost:8000"
echo "API Docs: http://localhost:8000/docs"
echo ""

python -m app.main
