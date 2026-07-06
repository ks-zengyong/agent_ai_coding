@echo off
REM ===========================================================================
REM  TUI Coding Agent - Windows 一键启动脚本
REM  双击即可运行；自动定位项目根目录、检查 Python、按需下载 vendor 依赖
REM ===========================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

REM --- 切换到脚本所在目录（即项目根目录）---
cd /d "%~dp0"

echo ============================================================
echo  TUI Coding Agent  -  Windows Launcher
echo  Project: %CD%
echo ============================================================
echo.

REM --- 探测 Python 解释器 ---
set "PY="
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PY=py"
    )
)

if "!PY!"=="" (
    echo [ERROR] 未找到 Python 解释器。
    echo 请安装 Python 3.11+ 并将其加入 PATH：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo [1/3] Python: !PY!
!PY! --version
echo.

REM --- 校验项目入口存在 ---
if not exist "src\main.py" (
    echo [ERROR] 未找到 src\main.py，请确认 start.bat 位于项目根目录。
    echo.
    pause
    exit /b 1
)

REM --- 检查 vendor 依赖是否就绪 ---
set "VENDOR_READY=0"
if exist "src\_vendor\httpx" set "VENDOR_READY=1"
if exist "src\_vendor\rich" set "VENDOR_READY=1"
if exist "src\_vendor\prompt_toolkit" set "VENDOR_READY=1"

if "!VENDOR_READY!"=="0" (
    echo [2/3] src\_vendor\ 未就绪，正在下载运行时依赖（httpx / rich / prompt_toolkit ...）
    echo       首次运行需联网，请耐心等待...
    echo.
    !PY! scripts\vendor_deps.py
    if !errorlevel! neq 0 (
        echo.
        echo [ERROR] vendor 依赖下载失败。请检查网络或手动执行：
        echo         !PY! scripts\vendor_deps.py
        echo.
        pause
        exit /b 1
    )
) else (
    echo [2/3] vendor 依赖已就绪。
)
echo.

REM --- 启动 TUI Agent ---
echo [3/3] 启动 TUI Coding Agent ...
echo ------------------------------------------------------------
!PY! -m src.main --project-dir .
set "EXITCODE=%errorlevel%"

echo.
echo ------------------------------------------------------------
echo 程序已退出（退出码 %EXITCODE%）。
pause
endlocal
exit /b %EXITCODE%
