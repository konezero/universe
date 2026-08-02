@echo off
setlocal
set "UNIVERSE_ROOT=%~dp0..\.."
if not defined UNIVERSE_PYTHON set "UNIVERSE_PYTHON=python"
if exist "%UNIVERSE_ROOT%\runtime\python\python.exe" set "UNIVERSE_PYTHON=%UNIVERSE_ROOT%\runtime\python\python.exe"
cd /d "%UNIVERSE_ROOT%"
"%UNIVERSE_PYTHON%" tools\universe_server.py tray --start-service
exit /b %ERRORLEVEL%
