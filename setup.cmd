@echo off
rem Double-click this file to set up the GHL CLI on Windows.
rem Runs setup.ps1 without changing your PowerShell execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%
