@echo off
chcp 65001 >nul
cd /d "%~dp0frontend"

echo ========================================
echo   A24 — 启动前端服务
echo ========================================
echo.
echo [前端] 启动 Vite 开发服务器...
echo [前端] http://localhost:5173
echo.

npm run dev
pause
