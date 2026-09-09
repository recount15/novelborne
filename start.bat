@echo off
rem One-click build and start for Novelborne (double-click me).
rem Checks Python deps and Node.js, builds the frontend, then launches run_app.py.
rem This script never installs anything by itself.
cd /d "%~dp0"
python setup_and_run.py
pause
