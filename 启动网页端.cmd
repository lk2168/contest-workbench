@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================
echo    大学生竞赛工作台 - 网页端
echo ============================================
echo.

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" (
  echo [错误] 没找到 Python。请先安装 Python 3.10+ ^(https://www.python.org/downloads/^)
  echo        安装时记得勾选 "Add Python to PATH"。
  pause
  exit /b 1
)

%PY% -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [提示] 缺少依赖，正在安装 ^(走清华镜像^)...
  %PY% -m pip install fastapi "uvicorn[standard]" python-multipart requests PyYAML pypdf python-docx numpy matplotlib -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo 正在启动，稍等几秒会自动打开浏览器...
start "" http://127.0.0.1:8765
%PY% cli.py web --port 8765
pause
