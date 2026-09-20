@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------------------
rem  cmd 在"读取批处理文件的过程中"切换代码页, 会让之后的中文行解析错位(丢掉 echo
rem  前缀、把中文当成命令执行)。先在 65001 下重开一次本脚本: 子进程打开文件时代码页
rem  已经是 65001, 全程按 UTF-8 读取, 中文横幅/提示就不会再错位。纯 ASCII 的父进程
rem  跳转与退出码都保持原样。
rem ---------------------------------------------------------------------------
if defined A24_CP65001 goto :A24_CP65001_READY
set "A24_CP65001=1"
cmd /c "%~f0" %*
exit /b %ERRORLEVEL%
:A24_CP65001_READY
setlocal EnableExtensions
title A24 合同智能审核系统 (关闭本窗口即停止全部服务)
cd /d "%~dp0"

rem ==========================================================================
rem  A24 合同智能审核系统 —— Windows 本地启动的【唯一对外入口】
rem
rem  职责边界（严格限定为「检查 -> 启动」，不做任何安装/下载/重建）:
rem    · 只检查 项目根 / .env / RAG 资源 / Python / Node 与前后端依赖
rem    · 分别调用 scripts\start_backend.bat 与 scripts\start_frontend.bat
rem    · 两者都在【本窗口内】运行(共用一个控制台窗口), 并打印访问地址
rem    · 任一检查不通过 -> 打印可操作指令并中止（绝不静默失败）
rem  明确【不做】: pip install / npm install / 下载 embedding 模型 /
rem              重建 Chroma 向量库 / 改数据库 / 改任何项目文件
rem  向量库或模型缺失时请先运行一次性准备脚本: prepare.bat
rem  （底层重建命令: cd backend && python -m ai.rag.init_chroma）
rem ==========================================================================

set "ROOT=%~dp0"
set "SK="
set "PY="

echo ============================================================
echo   A24 合同智能审核系统 - Windows 本地启动
echo ============================================================
echo.

rem ---------- 1) 项目根结构 ----------
if not exist "%ROOT%backend\main.py" goto :BAD_ROOT
if not exist "%ROOT%frontend\package.json" goto :BAD_ROOT
if not exist "%ROOT%scripts\start_backend.bat" goto :BAD_ROOT
if not exist "%ROOT%scripts\start_frontend.bat" goto :BAD_ROOT
echo [检查] 项目结构           OK

rem ---------- 2) .env 与 SECRET_KEY ----------
if not exist "%ROOT%.env" goto :NO_ENV
for /f "tokens=1,* delims==" %%a in ('findstr /B /C:"SECRET_KEY=" "%ROOT%.env"') do set "SK=%%b"
if "%SK%"=="" goto :BAD_SECRET
if "%SK%"=="change-me-to-random-string" goto :BAD_SECRET
echo [检查] .env / SECRET_KEY   OK

rem ---------- 3) RAG 资源（只检查，绝不自动重建）----------
if exist "%ROOT%backend\chroma_data\chroma.sqlite3" goto :RAG_BUILT
if exist "%ROOT%backend\ai\rag\resources\contract_templates_source.json" goto :NO_CHROMA
goto :NO_RAG

:RAG_BUILT
echo [检查] 向量库             OK  backend\chroma_data
goto :RAG_MODEL

:NO_CHROMA
echo [错误] 未找到向量库 backend\chroma_data\chroma.sqlite3
echo        start.bat 只做"检查 - 启动", 不会下载模型, 也不会重建向量库。
echo        请先运行一次性准备脚本:
echo          prepare.bat
echo        它会用 backend\ai\rag\resources\contract_templates_source.json 重建向量库,
echo        并真实校验 RAG 是否就绪 (要求 method=rag, fallback=false)。
echo.
pause
exit /b 1

:RAG_MODEL
if exist "%ROOT%models\hf-cache" set "HF_HOME=%ROOT%models\hf-cache"
if defined HF_HOME echo [检查] embedding 模型     OK  models\hf-cache
if not defined HF_HOME echo [警告] 未找到 models\hf-cache, 将尝试系统 HuggingFace 缓存
if not defined HF_HOME echo        若系统缓存也没有, 加载 embedding 会明确报错并中止检索;
if not defined HF_HOME echo        此时请先运行 prepare.bat 准备模型(离线完整包已内置)。

rem ---------- 4) 后端运行环境 ----------
if exist "%ROOT%backend\venv\Scripts\python.exe" set "PY=%ROOT%backend\venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY goto :NO_PYTHON
"%PY%" -c "import fastapi,uvicorn,sqlalchemy" >nul 2>nul
if errorlevel 1 goto :NO_BACKEND_DEPS
echo [检查] 后端环境           OK  %PY%

rem ---------- 5) 前端运行环境 ----------
where node >nul 2>nul
if errorlevel 1 goto :NO_NODE
if not exist "%ROOT%frontend\node_modules" goto :NO_FRONTEND_DEPS
echo [检查] 前端环境           OK  node + frontend\node_modules

rem ---------- 6) 启动 ----------
rem 单窗口模式：用 start /b 在【本窗口的控制台】里启动两个服务，不再各开一个新窗口。
rem   · 两个服务进程仍独立常驻(uvicorn / vite), 本脚本结束后继续运行;
rem   · 关闭本窗口 = 系统向该控制台所有进程发 CTRL_CLOSE, 两个服务一起停止, 不残留;
rem   · 置 A24_SHARED_CONSOLE 让子脚本知道"共用控制台", 从而不再改本窗口标题、
rem     也不再 pause(避免抢占本窗口的按键与画面)。单独运行子脚本时行为不变。
set "A24_SHARED_CONSOLE=1"
echo.
echo [启动] 正在本窗口内启动后端服务(8080) ...
start /b "" cmd /c ""%ROOT%scripts\start_backend.bat""
timeout /t 3 /nobreak >nul
echo [启动] 正在本窗口内启动前端服务(5173) ...
start /b "" cmd /c ""%ROOT%scripts\start_frontend.bat""
rem 前端 Vite 启动时会清屏(clearScreen), 共用同一个窗口时会把上面的检查/日志一起清掉,
rem 所以这里等它清完屏再打印收尾提示(窗口标题里的停止方式不受清屏影响)。
timeout /t 3 /nobreak >nul
echo.
echo ============================================================
echo   已启动完成
echo     前端界面 : http://localhost:5173
echo     后端文档 : http://localhost:8080/docs
echo     健康检查 : http://localhost:8080/api/health
echo   服务已在本窗口内后台运行, 上方会持续输出前后端日志。
echo   关闭本窗口即可停止全部服务(后端 + 前端)。
echo ============================================================
echo.
pause
exit /b 0

rem ====================== 失败分支（均给出可操作指令）======================

:BAD_ROOT
echo [错误] 项目结构不完整
echo        需要存在: backend\main.py, frontend\package.json,
echo                  scripts\start_backend.bat, scripts\start_frontend.bat
echo        请在项目根目录运行 start.bat。
echo.
pause
exit /b 1

:NO_ENV
echo [错误] 未找到 .env 配置文件
echo        请先执行:  copy .env.example .env
echo        然后填写 SECRET_KEY, 见下一条提示。
echo.
pause
exit /b 1

:BAD_SECRET
echo [错误] .env 中的 SECRET_KEY 未设置, 或仍是占位串
echo        后端启动会直接失败, 必须先设置 SECRET_KEY。
echo        生成方法:  python -c "import secrets;print(secrets.token_urlsafe(32))"
echo        把生成结果填到 .env 的 SECRET_KEY= 后面, 然后重新双击 start.bat。
echo.
pause
exit /b 1

:NO_RAG
echo [错误] RAG 资源缺失
echo        既没有预构建向量库 backend\chroma_data\chroma.sqlite3
echo        也没有建库源 backend\ai\rag\resources\contract_templates_source.json
echo        说明交付包不完整: 只剩纯 LLM 零样本分类, 已不是正式 RAG 链路。
echo        请重新获取完整交付包 (离线完整包已内置模型与向量库)。
echo.
pause
exit /b 1

:NO_PYTHON
echo [错误] 未找到 Python 解释器
echo        推荐在项目根目录执行:
echo          python -m venv backend\venv
echo          backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
echo.
pause
exit /b 1

:NO_BACKEND_DEPS
echo [错误] 后端依赖未安装或不完整
echo        请在项目根目录执行:
echo          backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
echo.
pause
exit /b 1

:NO_NODE
echo [错误] 未找到 Node.js / npm
echo        请安装 Node.js 20 LTS 后重试。
echo.
pause
exit /b 1

:NO_FRONTEND_DEPS
echo [错误] 前端依赖未安装: 未找到 frontend\node_modules
echo        请在项目根目录执行:
echo          cd frontend
echo          npm install
echo.
pause
exit /b 1
