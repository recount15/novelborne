@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo    书中织梦 Novelborne - 全量版（完整作品库 + 角色库）
echo ============================================================
echo.

if exist "run_app.py" goto dir_ok
echo [错误] 请把本脚本放在游戏源码根目录（与 run_app.py 同层）。
pause
exit /b 1
:dir_ok

set "PYBIN="
if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYBIN=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PYBIN (
  where python >nul 2>nul && set "PYBIN=python"
)
if not defined PYBIN (
  where py >nul 2>nul && set "PYBIN=py -3"
)
if not defined PYBIN (
  echo [错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH。
  pause
  exit /b 1
)

echo 项目根：%cd%
echo Python：%PYBIN%
echo 浏览器将自动打开 http://127.0.0.1:21601/
echo 关闭本窗口即退出服务。
echo.

"%PYBIN%" run_app.py --port 21601
pause
