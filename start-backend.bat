@echo off
chcp 65001 >nul
cd /d "%~dp0backend"

echo ========================================
echo   A24 — 启动后端服务
echo ========================================
echo.

call venv\Scriptsctivate
echo [后端] venv 已激活，启动 uvicorn...
echo [后端] http://localhost:8080/docs
echo.

uvicorn main:app --reload --host 0.0.0.0 --port 8080
pause
