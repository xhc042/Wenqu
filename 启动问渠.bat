@echo off
cd /d "%~dp0"
python manage_server.py start
if %errorlevel% equ 0 (
    start http://127.0.0.1:8765
    echo Server started at http://127.0.0.1:8765
) else (
    echo Failed to start server
)
pause
