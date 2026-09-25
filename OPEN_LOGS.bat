@echo off
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs" >nul 2>&1
start "" explorer "%CD%\logs"
exit /b 0
