@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart_backend.ps1"
exit /b %ERRORLEVEL%
