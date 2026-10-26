@echo off
chcp 65001 >nul
rem Moye double-click launcher.
rem 1) run launch.py --check synchronously with python.exe so failures are visible
rem 2) start pythonw.exe (no console) only after the check passes
rem Notes about the exception log / hooks live in launch.py (ASCII-only comments
rem here on purpose: chcp 65001 plus UTF-8 rem lines can shift cmd's read offset
rem and garble later lines).
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "CHECKLOG=%TEMP%\Moye-check.log"

cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [错误] 没找到 %~dp0.venv\Scripts\python.exe
    echo 请先在项目根目录创建虚拟环境并安装依赖：
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo [错误] 没找到 %~dp0.venv\Scripts\pythonw.exe
    echo 虚拟环境不完整，请重建并安装依赖：
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

rem Run the self-check in the foreground; keep its output for error reporting.
"%~dp0.venv\Scripts\python.exe" "%~dp0launch.py" --check > "%CHECKLOG%" 2>&1
if errorlevel 1 (
    echo.
    type "%CHECKLOG%"
    echo.
    echo [错误] 启动自检失败，程序未启动。以上信息已保存到：
    echo     %CHECKLOG%
    echo 常见原因：依赖缺失或版本不对，可运行
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo 或代码存在导入/语法错误，详见 data\startup.log。
    pause
    exit /b 1
)

del "%CHECKLOG%" >nul 2>&1

rem The venv pythonw.exe is only a redirector: it re-execs the base python.exe
rem (console subsystem), which opens an extra black window. Launch the base
rem pythonw.exe (GUI subsystem) directly and point PYTHONPATH at the venv's
rem site-packages so the same dependency versions are used.
set "VENVDIR=%~dp0.venv"
set "PYHOME="
for /f "tokens=1,* delims==" %%A in ('findstr /b /i "home" "%VENVDIR%\pyvenv.cfg"') do set "PYHOME=%%B"
if defined PYHOME if "%PYHOME:~0,1%"==" " set "PYHOME=%PYHOME:~1%"
if not defined PYHOME goto :launch_fallback
if not exist "%PYHOME%\pythonw.exe" goto :launch_fallback

set "VIRTUAL_ENV=%VENVDIR%"
set "PYTHONPATH=%VENVDIR%\Lib\site-packages;%PYTHONPATH%"
start "" "%PYHOME%\pythonw.exe" "%~dp0launch.py"
endlocal
exit /b 0

:launch_fallback
rem Fallback: the venv redirector still works, at the cost of a console window.
start "" "%VENVDIR%\Scripts\pythonw.exe" "%~dp0launch.py"
endlocal
exit /b 0
