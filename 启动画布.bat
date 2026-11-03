@echo off
rem Keep this file pure ASCII: cmd misreads UTF-8 lines in .bat files.
rem Chinese messages live in bin\msg\*.txt and are printed with "type".
chcp 65001 >nul
title QingZi Canvas One-Key - keep this window open
set "HERE=%~dp0"
if "%HERE:~0,2%"=="\\" goto network
cd /d "%HERE%"
set "UV_CACHE_DIR=%HERE%runtime\uv-cache"
set "UV_PYTHON_INSTALL_DIR=%HERE%runtime\python"
set "UV_LINK_MODE=copy"
set "PYTHONUTF8=1"
if exist "data\onekey\mirror.flag" set "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
if not exist "bin\uv.exe" goto nouv

if exist "runtime\app-venv\Scripts\python.exe" goto deps
type "bin\msg\first.txt"
"bin\uv.exe" venv "runtime\app-venv" --python 3.12
if errorlevel 1 goto fail
:deps
"bin\uv.exe" pip install --python "runtime\app-venv\Scripts\python.exe" -q -r "canvas\requirements.txt"
if errorlevel 1 goto fail
"runtime\app-venv\Scripts\python.exe" launcher\main.py %*
goto end

:network
pushd "%HERE%" 2>nul && (type "bin\msg\network.txt" & popd) || echo Please copy this folder to a local disk, e.g. D:\canvas, then run it again.
pause
goto end

:nouv
type "bin\msg\nouv.txt" 2>nul || echo bin\uv.exe not found. Please fully extract the zip first.
pause
goto end

:fail
type "bin\msg\fail.txt"
pause
:end
