@echo off
chcp 65001 >nul
rem 同 start.bat: 先在 65001 下重开一次, 避免 cmd 读文件途中切代码页导致中文行解析错位
if defined A24_CP65001 goto :A24_CP65001_READY
set "A24_CP65001=1"
cmd /c "%~f0" %*
exit /b %ERRORLEVEL%
:A24_CP65001_READY
setlocal EnableExtensions
rem 共用控制台模式(由根 start.bat 置位)下不改窗口标题, 单独运行时保持原标题行为
if not defined A24_SHARED_CONSOLE title A24 前端服务

echo ============================================================
echo   A24 前端服务 - 内部启动器
echo ============================================================
echo   提示: 请通过项目根目录的 start.bat 启动, 不建议直接运行本文件。
echo   本文件只负责启动前端, 不执行 npm install。
echo.

set "ROOT=%~dp0.."
pushd "%ROOT%" >nul

if not exist "%ROOT%\frontend\package.json" goto :BAD_ROOT

where node >nul 2>nul
if errorlevel 1 goto :NO_NODE

if not exist "%ROOT%\frontend\node_modules" goto :NO_DEPS

cd /d "%ROOT%\frontend"
echo [前端] Node : 
node -v
echo [前端] 命令 : npm run dev
echo [前端] 地址 : http://localhost:5173
echo.

call npm run dev

echo.
echo [前端] 进程已退出, 退出码 = %ERRORLEVEL%
popd >nul
if not defined A24_SHARED_CONSOLE pause
exit /b 0

:BAD_ROOT
echo [错误] 未找到 frontend\package.json
echo        请确认本文件位于 <项目根>\scripts\ 下, 且项目结构完整。
popd >nul
if not defined A24_SHARED_CONSOLE pause
exit /b 1

:NO_NODE
echo [错误] 未找到 Node.js / npm
echo        请安装 Node.js 20 LTS 后重试。
popd >nul
if not defined A24_SHARED_CONSOLE pause
exit /b 1

:NO_DEPS
echo [错误] 前端依赖未安装: 未找到 frontend\node_modules
echo        请在项目根目录执行:
echo          cd frontend
echo          npm install
popd >nul
if not defined A24_SHARED_CONSOLE pause
exit /b 1
