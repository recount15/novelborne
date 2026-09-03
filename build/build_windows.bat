@echo off
setlocal
chcp 65001 >nul
REM ============================================================
REM  Novelborne v2.0.2 - Windows Web executable build
REM  Output: dist\FateEngine\FateEngine.exe
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

if exist build\pkg rmdir /s /q build\pkg
if exist dist\FateEngine rmdir /s /q dist\FateEngine

REM Use the maintained spec. It collects all lazy engine/API/UI submodules and
REM bundles assets plus the production frontend under the frozen _internal dir.
python -m PyInstaller --noconfirm build\FateEngine.spec ^
  --workpath build\pkg --distpath dist
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed.
  exit /b 1
)

if not exist dist\FateEngine\FateEngine.exe (
  echo [ERROR] Expected executable was not produced.
  exit /b 1
)

echo.
echo Build complete: dist\FateEngine\FateEngine.exe
echo Distribute the complete dist\FateEngine directory, not the EXE alone.
exit /b 0
