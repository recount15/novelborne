@echo off
REM ============================================================
REM  书中织梦 · Novelborne — Windows 窗口版（无边框圆角桌面应用）构建
REM  用法：先 pip install -r requirements.txt pyinstaller pywebview，
REM        于项目根运行本脚本
REM  产物：dist\FateEngineWindowed\FateEngineWindowed.exe
REM        双击即出无边框圆角窗口（无控制台黑框）；后端仍监听 0.0.0.0，
REM        手机扫码远程使用与 Web 版一致
REM  与 build_windows.bat（Web 版，起服务开浏览器）二选一或都构建
REM ============================================================
cd /d "%~dp0\.."

call npm --prefix frontend install
if errorlevel 1 exit /b 1
call npm --prefix frontend run build
if errorlevel 1 exit /b 1

if exist build\pkg_win rmdir /s /q build\pkg_win
if exist dist\FateEngineWindowed rmdir /s /q dist\FateEngineWindowed

python -m PyInstaller --noconfirm build\FateEngineWindowed.spec ^
  --workpath build\pkg_win --distpath dist

echo.
echo 构建完成：dist\FateEngineWindowed\FateEngineWindowed.exe
echo 将整个 dist\FateEngineWindowed 文件夹拷贝给使用者即可双击运行。
