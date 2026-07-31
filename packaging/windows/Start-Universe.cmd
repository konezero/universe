@echo off
setlocal
set "UNIVERSE_ROOT=%~dp0..\.."
pushd "%UNIVERSE_ROOT%"
python tools\universe_server.py start --open-ui
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
