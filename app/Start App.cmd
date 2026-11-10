@echo off
setlocal
cd /d "%~dp0"
if exist "..\.venv\Scripts\python.exe" (
    "..\.venv\Scripts\python.exe" server.py
) else (
    python server.py
)
if errorlevel 1 (
    echo.
    echo See app\README.md for setup and troubleshooting.
    pause
)
