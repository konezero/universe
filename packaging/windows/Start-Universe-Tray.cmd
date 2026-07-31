@echo off
setlocal
set "UNIVERSE_ROOT=%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0Universe-Tray.ps1" -UniverseRoot "%UNIVERSE_ROOT%" -StartService
exit /b %ERRORLEVEL%
