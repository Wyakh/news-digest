@echo off
REM News Digest launcher
REM Starts the tray controls and local UI using shared config/launch.json.

cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\python.exe" (
    echo Create the venv and install requirements first. See README.md.
    pause
    exit /b 1
)
echo News Digest: right-click its icon beside the clock, including hidden icons.
"%~dp0venv\Scripts\python.exe" -c "from launch_config import load_launch_settings; print('Open: ' + load_launch_settings()[2])"
if not exist "%~dp0NewsDigest.exe" (
    echo Build the project launcher first:
    echo powershell.exe -NoProfile -ExecutionPolicy Bypass -File launcher\build-launcher.ps1
    pause
    exit /b 1
)
start "" "%~dp0NewsDigest.exe"
exit /b %ERRORLEVEL%
