@echo off
chcp 65001 >nul
cd /d "%~dp0backend"

echo ========================================
echo   A24 后端服务
echo ========================================
echo.
echo   后端地址: http://localhost:8080/docs
echo.

echo [后端] 启动 uvicorn (venv)...

"%~dp0backend\venv\Scripts\python.exe" -m uvicorn main:app --reload --host 0.0.0.0 --port 8080
pause
