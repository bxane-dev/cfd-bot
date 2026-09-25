@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CFD Desk Builder

call BUILD_APP.bat
set "BUILD_EXIT=%ERRORLEVEL%"

echo.
if not "%BUILD_EXIT%"=="0" (
  echo ============================================================
  echo CFD DESK BUILD FAILED - exit code %BUILD_EXIT%
  echo ============================================================
  echo.
  echo The builder did not complete successfully.
  echo Check:
  echo   %CD%\build.log
  echo.
  if exist "%CD%\build.log" (
    echo Last 25 log lines:
    echo ------------------------------------------------------------
    powershell.exe -NoLogo -NoProfile -Command "Get-Content -LiteralPath '%CD%\build.log' -Tail 25" 2>nul
    echo ------------------------------------------------------------
  )
) else (
  echo CFD Desk build finished successfully.
  echo Output: %CD%\release
)

echo.
echo Press any key to close this window.
pause >nul
exit /b %BUILD_EXIT%
