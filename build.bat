@echo off
rem One-click Windows build: double-click this file.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if errorlevel 1 (
    echo.
    echo Build failed. See the messages above.
) else (
    echo.
    echo Build finished: dist\Gantz.exe
)
pause
