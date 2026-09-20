@echo off
chcp 65001 >nul
setlocal EnableExtensions
title A24 合同智能审核系统 - 首次准备（一次性）
cd /d "%~dp0"

rem ==========================================================================
rem  A24 合同智能审核系统 —— 首次部署准备（一次性，可能需要联网）
rem
rem  职责（与 start.bat 严格分离）:
rem    · 准备 embedding 模型（缺失则下载到 models\hf-cache）
rem    · 建立 / 重建 Chroma 向量库（backend\chroma_data）
rem    · 真实校验 RAG 资源是否就绪（不是只看目录在不在）
rem    · 明确成功 / 失败提示，绝不静默降级
rem  明确【不做】: 启动服务（那是 start.bat 的职责）
rem
rem  用法:
rem    prepare.bat              缺什么补什么
rem    prepare.bat --rebuild    强制重建向量库
rem ==========================================================================

set "ROOT=%~dp0"
set "PY="
set "REBUILD="
set "RC=0"
if /I "%~1"=="--rebuild" set "REBUILD=1"

echo ============================================================
echo   A24 合同智能审核系统 - 首次准备（一次性）
echo ============================================================
echo   本脚本只做"准备"，不启动任何服务；启动请用 start.bat。
echo.

rem 交付包内置模型时，让本次准备也走仓库内缓存，避免误用系统缓存
if exist "%ROOT%models\hf-cache" set "HF_HOME=%ROOT%models\hf-cache"
set "PYTHONIOENCODING=utf-8"

rem ---------- 0) 解释器与后端依赖 ----------
if exist "%ROOT%backend\venv\Scripts\python.exe" set "PY=%ROOT%backend\venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY goto :NO_PYTHON
"%PY%" -c "import fastapi,uvicorn,sqlalchemy" >nul 2>nul
if errorlevel 1 goto :NO_BACKEND_DEPS
echo [检查] 后端环境           OK
if defined HF_HOME echo [检查] HF_HOME            %HF_HOME%
echo.

rem ---------- .env 提示（不阻断资源准备，但会影响最终校验的 LLM 段）----------
if not exist "%ROOT%.env" (
  echo [警告] 未找到 .env。向量库与模型仍可准备，但按下面第 3 步的
  echo        "生产分类" 一段将无法验证。建议先执行:
  echo          copy .env.example .env
  echo        然后在 .env 中填写 SECRET_KEY 与 DEEPSEEK_API_KEY。
  echo.
)

rem ---------- 1) embedding 模型 ----------
echo [步骤 1/3] 准备 embedding 模型
"%PY%" "%ROOT%scripts\prepare_model.py"
if errorlevel 1 goto :MODEL_FAIL
echo.

rem ---------- 2) Chroma 向量库 ----------
echo [步骤 2/3] 准备向量库
if defined REBUILD goto :BUILD_CHROMA
if not exist "%ROOT%backend\chroma_data\chroma.sqlite3" goto :BUILD_CHROMA
echo   已存在向量库 backend\chroma_data，跳过重建。
echo   （如需强制重建: prepare.bat --rebuild）
goto :VERIFY

:BUILD_CHROMA
echo   正在构建向量库（首次约需几分钟）...
pushd "%ROOT%backend"
"%PY%" -m ai.rag.init_chroma
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" goto :CHROMA_FAIL
echo.

rem ---------- 3) 真实校验 RAG ----------
:VERIFY
echo [步骤 3/3] 校验 RAG 交付资源（真实加载模型 + 真实检索 + 生产分类）
"%PY%" "%ROOT%scripts\check_rag_ready.py"
if errorlevel 1 goto :VERIFY_FAIL

echo.
echo ============================================================
echo   准备完成
echo     下一步: 运行 start.bat 启动系统
echo     前端界面 : http://localhost:5173
echo     后端文档 : http://localhost:8080/docs
echo ============================================================
echo.
pause
exit /b 0

rem ====================== 失败分支（均给出可操作指令）======================

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

:MODEL_FAIL
echo [错误] embedding 模型准备失败
echo        RAG 检索依赖该模型，缺少它会静默退化为纯 LLM 分类，
echo        不再走正式 RAG 链路，因此准备流程就此中止。
echo        可选方案见上方 prepare_model.py 打印的说明。
echo        若你拿到的是「离线完整包」，models\hf-cache 应已内置，
echo        请确认交付包是否完整。
echo.
pause
exit /b 1

:CHROMA_FAIL
echo [错误] 向量库构建失败（退出码 = %RC%）
echo        请确认:
echo          1) backend\ai\rag\resources\contract_templates_source.json 是否存在；
echo          2) 磁盘空间是否充足；
echo          3) 上方错误信息中的具体原因。
echo        重新构建: prepare.bat --rebuild
echo.
pause
exit /b 1

:VERIFY_FAIL
echo [错误] RAG 资源校验未通过
echo        上面的 [FAIL] 行即为原因。请勿在未就绪的情况下启动系统:
echo        此时合同分类会退化为 rag-fallback-llm，接口仍会返回结果，
echo        但已不是正式 RAG 链路，属于"看起来正常"的静默失效。
echo.
pause
exit /b 1
