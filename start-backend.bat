@echo off
chcp 65001 >nul
cd /d "D:\学习材料留档\2026暑期社会实践\LLM\LLM-ContractAudit-WarnSystem\backend"
call venv\Scripts\activate.bat
echo 启动后端服务...
echo API 文档: http://localhost:8080/docs
echo.
uvicorn main:app --reload --host 0.0.0.0 --port 8080
pause
