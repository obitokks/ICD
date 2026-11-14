@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Open http://127.0.0.1:8765
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto fail
exit /b 0
:fail
pause
exit /b 1
