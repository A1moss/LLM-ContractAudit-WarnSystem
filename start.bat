@echo off
chcp 65001 >nul
title A24 合同审核系统 — 一键启动

cd /d "%~dp0"

echo ========================================
echo   A24 合同智能审核系统
echo ========================================
echo.
echo   后端: http://localhost:8080/docs
echo   前端: http://localhost:5173
echo.
echo   正在启动后端服务...
echo.

start "A24 后端" cmd /k ""%~dp0start-backend.bat""

echo   等待后端就绪 (3秒)...
timeout /t 3 /nobreak >nul

echo   正在启动前端服务...
start "A24 前端" cmd /k ""%~dp0start-frontend.bat""

echo.
echo   全部启动完成! 关闭弹出的两个窗口即可停止服务.
echo ========================================
pause
