@echo off
chcp 65001 >nul
cd /d "%~dp0frontend"

echo ========================================
echo   A24 前端服务
echo ========================================
echo.
echo   前端地址: http://localhost:5173
echo.

npm run dev
pause
