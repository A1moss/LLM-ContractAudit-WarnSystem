@echo off
chcp 65001 >nul
cd /d "%~dp0..\frontend"
echo 启动前端服务...
echo 页面地址: http://localhost:5173
echo.
call npm run dev
pause
