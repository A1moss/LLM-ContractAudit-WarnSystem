#!/usr/bin/env bash
# A24 一键启动脚本 (bash 版)
# 适用于 VS Code 终端 (git-bash) / 手动双击
# 用法：双击 start.sh 或在终端里 bash start.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "========================================"
echo "  A24 合同智能审核系统"
echo "========================================"
echo ""
echo "  后端: http://localhost:8080/docs"
echo "  前端: http://localhost:5173"
echo ""

# 启动后端（新窗口）
echo "[1/2] 启动后端 (port 8080)..."
cmd //c start "A24 后端" cmd //k "cd /d $BACKEND_DIR && venv\\Scripts\\activate && uvicorn main:app --reload --host 0.0.0.0 --port 8080"

sleep 2

# 启动前端（新窗口）
echo "[2/2] 启动前端 (port 5173)..."
cmd //c start "A24 前端" cmd //k "cd /d $FRONTEND_DIR && npm run dev"

echo ""
echo "  全部启动完成！"
echo "  关闭弹窗即可停止服务"
echo "========================================"
