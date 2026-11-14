@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_frontend.ps1"
exit /b %ERRORLEVEL%
