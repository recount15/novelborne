@echo off
setlocal
cd /d "%~dp0\.."
REM Local packaging only. Never launch or terminate an application here.
REM Private asset overrides are deliberately not supported by this public helper.
python build\package_release.py %*
exit /b %ERRORLEVEL%
