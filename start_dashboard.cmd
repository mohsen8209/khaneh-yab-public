@echo off
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
start "خانه‌یاب عمومی" http://127.0.0.1:8766
"%PYTHON%" -m divar_scout dashboard
