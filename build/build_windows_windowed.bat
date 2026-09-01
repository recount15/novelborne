@echo off
setlocal
chcp 65001 >nul
REM ============================================================
REM  Novelborne v2.0.2 - Windows windowed build (pywebview/WebView2)
REM  Output: dist\FateEngineWindowed\FateEngineWindowed.exe
REM ============================================================
cd /d "%~dp0\.."

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm not found. Install Node.js 18+ and reopen the terminal.
  exit /b 1
)

call npm --prefix frontend install --no-audit --no-fund
if errorlevel 1 exit /b 1
call npm --prefix frontend run build
if errorlevel 1 exit /b 1

if exist build\pkg_win rmdir /s /q build\pkg_win
if exist dist\FateEngineWindowed rmdir /s /q dist\FateEngineWindowed

python -m PyInstaller --noconfirm build\FateEngineWindowed.spec ^
  --workpath build\pkg_win --distpath dist
if errorlevel 1 (
  echo [ERROR] PyInstaller windowed build failed.
  exit /b 1
)

if not exist dist\FateEngineWindowed\FateEngineWindowed.exe (
  echo [ERROR] Expected windowed executable was not produced.
  exit /b 1
)

echo.
echo Build complete: dist\FateEngineWindowed\FateEngineWindowed.exe
echo Distribute the complete dist\FateEngineWindowed directory, not the EXE alone.
exit /b 0
