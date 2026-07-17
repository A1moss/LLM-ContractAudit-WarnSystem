@echo off
chcp 65001 >nul
cd /d "D:\学习材料留档\2026暑期社会实践\LLM\LLM-ContractAudit-WarnSystem\frontend"
echo 启动前端服务...
echo 页面地址: http://localhost:5173
echo.
call npm run dev
pause
