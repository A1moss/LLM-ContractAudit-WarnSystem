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
if not defined A24_SHARED_CONSOLE title A24 后端服务

echo ============================================================
echo   A24 后端服务 - 内部启动器
echo ============================================================
echo   提示: 请通过项目根目录的 start.bat 启动, 不建议直接运行本文件。
echo   本文件只负责启动后端, 不做依赖安装 / 不下载模型 / 不重建向量库。
echo.

set "ROOT=%~dp0.."
pushd "%ROOT%" >nul

if not exist "%ROOT%\backend\main.py" goto :BAD_ROOT

set "PY="
if exist "%ROOT%\backend\venv\Scripts\python.exe" set "PY=%ROOT%\backend\venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
if not defined PY goto :NO_PYTHON

rem 交付包内置的 embedding 模型: 存在则把 HF_HOME 指向它, 避免运行期联网 / 找不到模型。
rem 加载逻辑本身不变, 模型仍按 repo id "shibing624/text2vec-base-chinese" 解析。
set "HF_HOME="
if exist "%ROOT%\models\hf-cache" set "HF_HOME=%ROOT%\models\hf-cache"

cd /d "%ROOT%\backend"
echo [后端] 解释器 : %PY%
if defined HF_HOME echo [后端] HF_HOME : %HF_HOME%
if not defined HF_HOME echo [后端] HF_HOME : 未设置, 使用系统 HuggingFace 缓存
echo [后端] 命令   : uvicorn main:app --reload --host 0.0.0.0 --port 8080
echo [后端] 地址   : http://localhost:8080/docs
echo.

"%PY%" -m uvicorn main:app --reload --host 0.0.0.0 --port 8080

echo.
echo [后端] 进程已退出, 退出码 = %ERRORLEVEL%
popd >nul
if not defined A24_SHARED_CONSOLE pause
exit /b 0

:BAD_ROOT
echo [错误] 未找到 backend\main.py
echo        请确认本文件位于 <项目根>\scripts\ 下, 且项目结构完整。
popd >nul
if not defined A24_SHARED_CONSOLE pause
exit /b 1

:NO_PYTHON
echo [错误] 未找到 Python 解释器
echo        推荐在项目根目录执行:
echo          python -m venv backend\venv
echo          backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
popd >nul
if not defined A24_SHARED_CONSOLE pause
exit /b 1
