@echo off
REM v2.1.1 Release Package Builder
echo ========================================
echo Novelborne v2.1.1 Release Package
echo ========================================
echo.

REM Step 1: Check if build exists
echo [1/5] Checking build...
if not exist "dist\FateEngine\FateEngine.exe" (
    echo ERROR: FateEngine.exe not found! Please run build\build_windows.bat first
    pause
    exit /b 1
)
echo OK: FateEngine.exe found

REM Step 2: Test the executable
echo.
echo [2/5] Testing executable...
start /wait dist\FateEngine\FateEngine.exe --test 2>nul
timeout /t 5 /nobreak >nul
tasklist /FI "IMAGENAME eq FateEngine.exe" 2>nul | find /I /N "FateEngine.exe">nul
if "%ERRORLEVEL%"=="0" (
    echo OK: FateEngine.exe is running
    taskkill /F /IM FateEngine.exe >nul 2>&1
) else (
    echo WARNING: Cannot verify if FateEngine.exe started correctly
)

REM Step 3: Create release directory
echo.
echo [3/5] Creating release package...
set RELEASE_DIR=Novelborne-v2.1.1-windows
if exist "%RELEASE_DIR%" rmdir /s /q "%RELEASE_DIR%"
mkdir "%RELEASE_DIR%"

REM Step 4: Copy files
echo Copying executable and dependencies...
xcopy /E /I /Y "dist\FateEngine\*" "%RELEASE_DIR%\" >nul

REM Copy essential docs
echo Copying documentation...
copy /Y "README.md" "%RELEASE_DIR%\README.txt" >nul
copy /Y "LICENSE" "%RELEASE_DIR%\LICENSE.txt" >nul 2>nul
copy /Y "docs\USER_MANUAL.md" "%RELEASE_DIR%\USER_MANUAL.txt" >nul 2>nul
copy /Y "docs\FEATURES.md" "%RELEASE_DIR%\FEATURES.txt" >nul
copy /Y "docs\RELEASE_NOTES_v2.1.1.md" "%RELEASE_DIR%\RELEASE_NOTES.txt" >nul

REM Create quick start guide
echo Creating quick start guide...
(
echo Novelborne v2.1.1 - Quick Start
echo ================================
echo.
echo 1. Double-click FateEngine.exe to start
echo 2. Browser will open automatically to http://127.0.0.1:8000
echo 3. If browser doesn't open, manually open the URL above
echo.
echo Troubleshooting:
echo - If port 8000 is in use, edit the config or use --port option
echo - Check firewall if cannot access
echo.
echo Documentation:
echo - USER_MANUAL.txt: Complete user guide
echo - FEATURES.txt: All features explained
echo - RELEASE_NOTES.txt: What's new in v2.1.1
echo.
echo Support:
echo - GitHub: https://github.com/recount15/novelborne
echo - Issues: https://github.com/recount15/novelborne/issues
) > "%RELEASE_DIR%\START_HERE.txt"

echo OK: Files copied to %RELEASE_DIR%\

REM Step 5: Create 7z archive with maximum compression
echo.
echo [4/5] Compressing with 7-Zip (maximum compression)...
if not exist "C:\Program Files\7-Zip\7z.exe" (
    echo WARNING: 7-Zip not found at C:\Program Files\7-Zip\7z.exe
    echo Please install 7-Zip or compress manually
    echo Release directory ready at: %RELEASE_DIR%\
    pause
    exit /b 0
)

"C:\Program Files\7-Zip\7z.exe" a -t7z -m0=lzma2 -mx=9 -mfb=273 -md=256m -ms=on "Novelborne-v2.1.1-windows.7z" "%RELEASE_DIR%\" >nul
if %ERRORLEVEL% EQU 0 (
    echo OK: Archive created successfully
    dir /b "Novelborne-v2.1.1-windows.7z" | findstr "7z"
    for %%I in ("Novelborne-v2.1.1-windows.7z") do echo Size: %%~zI bytes
) else (
    echo ERROR: 7-Zip compression failed
    pause
    exit /b 1
)

REM Step 6: Cleanup
echo.
echo [5/5] Cleaning up...
rmdir /s /q "%RELEASE_DIR%"
echo OK: Temporary files removed

echo.
echo ========================================
echo Package created successfully!
echo ========================================
echo.
echo File: Novelborne-v2.1.1-windows.7z
dir "Novelborne-v2.1.1-windows.7z"
echo.
echo Next steps:
echo 1. Test the package on a clean Windows machine
echo 2. Upload to GitHub Release
echo 3. Upload to Gitee Release
echo.
pause
