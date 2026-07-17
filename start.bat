@echo off
chcp 65001 >nul
title A24 合同审核系统 — 一键启动

cd /d "D:\学习材料留档\2026暑期社会实践\LLM\LLM-ContractAudit-WarnSystem"

echo ========================================
echo   A24 合同智能审核系统
echo ========================================
echo.
echo   启动后端服务 (port 8080)...
echo   启动前端服务 (port 5173)...
echo.

REM 启动后端
start "A24 后端" cmd /k "cd /d backend && venv\Scripts\activate && uvicorn main:app --reload --host 0.0.0.0 --port 8080"

REM 等后端先起来
timeout /t 3 /nobreak >nul

REM 启动前端
start "A24 前端" cmd /k "cd /d frontend && npm run dev"

echo.
echo   后端: http://localhost:8080/docs
echo   前端: http://localhost:5173
echo.
echo   关闭本窗口即可停止所有服务
echo ========================================
pause
