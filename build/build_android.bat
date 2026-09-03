@echo off
setlocal
cd /d "%~dp0\..\frontend"

call npm install
if errorlevel 1 exit /b 1

if not exist node_modules\@capacitor\cli (
  call npm install --save-dev @capacitor/cli @capacitor/core
  if errorlevel 1 exit /b 1
)
if not exist node_modules\@capacitor\android (
  call npm install @capacitor/android
  if errorlevel 1 exit /b 1
)

set VITE_PLATFORM=android
call npm run build
if errorlevel 1 exit /b 1

if not exist android (
  call npx cap add android
  if errorlevel 1 exit /b 1
)
call npx cap sync android
if errorlevel 1 exit /b 1
call npx cap open android
