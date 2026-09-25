@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build CFD Desk

if exist "BXANE.txt" (
  type "BXANE.txt"
  echo.
)
echo ============================================================
echo                    BUILD CFD DESK
echo ============================================================
echo This builds one visible desktop app with the CFD engine
echo bundled as a hidden background executable.
echo.

set "PY_CMD="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY_CMD=py -3"
)

if not defined PY_CMD (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY_CMD=python"
  )
)

if not defined PY_CMD (
  echo Python 3.11+ was not found.
  where winget >nul 2>&1
  if errorlevel 1 goto :python_missing
  echo Installing Python 3.12...
  winget install -e --id Python.Python.3.12 --scope user --source winget --accept-package-agreements --accept-source-agreements --silent
  if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PY_CMD="%LocalAppData%\Programs\Python\Python312\python.exe""
  )
)

if not defined PY_CMD goto :python_missing

where npm >nul 2>&1
if errorlevel 1 (
  echo Node.js/npm was not found.
  where winget >nul 2>&1
  if errorlevel 1 goto :node_missing
  echo Installing Node.js LTS...
  winget install -e --id OpenJS.NodeJS.LTS --source winget --accept-package-agreements --accept-source-agreements --silent
  if exist "%ProgramFiles%\nodejs\npm.cmd" set "PATH=%ProgramFiles%\nodejs;%PATH%"
)
where npm >nul 2>&1
if errorlevel 1 goto :node_missing

echo [1/6] Preparing application icon source...
if not exist "assets" mkdir "assets"
if not exist "assets\app-icon.b64" (
  echo Missing assets\app-icon.b64.
  goto :fail
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $b64=(Get-Content -LiteralPath 'assets\app-icon.b64' -Raw).Trim(); [IO.File]::WriteAllBytes((Join-Path (Get-Location) 'assets\app-icon-source.jpg'), [Convert]::FromBase64String($b64))"
if errorlevel 1 goto :fail

echo [2/6] Preparing Python build environment...
if not exist ".build-venv\Scripts\python.exe" (
  %PY_CMD% -m venv .build-venv
  if errorlevel 1 goto :fail
)
set "BUILD_PY=.build-venv\Scripts\python.exe"
"%BUILD_PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"%BUILD_PY%" -m pip install -r requirements.txt pyinstaller pillow
if errorlevel 1 goto :fail

echo [3/6] Generating Windows icon...
"%BUILD_PY%" -c "from PIL import Image; p=r'assets\app-icon-source.jpg'; out=r'assets\app-icon.ico'; img=Image.open(p).convert('RGBA').resize((256,256), Image.Resampling.LANCZOS); img.save(out, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if errorlevel 1 goto :fail
if not exist "assets\app-icon.ico" (
  echo Icon generation failed.
  goto :fail
)

echo [4/6] Compiling hidden CFD engine...
if exist "build\engine" rmdir /s /q "build\engine"
if exist "build\pyi-work" rmdir /s /q "build\pyi-work"
if exist "build\pyi-spec" rmdir /s /q "build\pyi-spec"
mkdir "build\engine" >nul 2>&1
mkdir "build\pyi-work" >nul 2>&1
mkdir "build\pyi-spec" >nul 2>&1

"%BUILD_PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name cfd-engine ^
  --distpath "%CD%\build\engine" ^
  --workpath "%CD%\build\pyi-work" ^
  --specpath "%CD%\build\pyi-spec" ^
  --paths "%CD%\app" ^
  --collect-submodules app ^
  --collect-submodules broker ^
  --collect-submodules strategy ^
  --collect-submodules research ^
  --hidden-import desk ^
  --hidden-import instruments ^
  --hidden-import main ^
  --hidden-import memory ^
  --hidden-import news ^
  --hidden-import predict ^
  --hidden-import risk ^
  --hidden-import streamers ^
  --add-data "%CD%\web;web" ^
  --add-data "%CD%\config.yaml;." ^
  --add-data "%CD%\.env.example;." ^
  --add-data "%CD%\BXANE.txt;." ^
  "%CD%\desktop\backend_entry.py"
if errorlevel 1 goto :fail

if not exist "build\engine\cfd-engine.exe" (
  echo Hidden engine build did not produce cfd-engine.exe.
  goto :fail
)

echo [5/6] Installing desktop build dependencies...
call npm install
if errorlevel 1 goto :fail

echo [6/6] Building Windows app...
if exist "release" rmdir /s /q "release"
call npm run dist:win
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo BUILD COMPLETE
echo ============================================================
echo Output folder:
echo   %CD%\release
echo.
echo You will get:
echo   - a normal CFD Desk installer
echo   - a portable single EXE
echo.
echo The installed/portable app shows only CFD Desk.
echo The Python CFD engine is bundled and runs hidden in background.
echo The supplied CFD artwork is used for the Windows app icon.
echo.
start "" explorer "%CD%\release"
exit /b 0

:python_missing
echo.
echo Python 3.11+ is required to BUILD the app.
echo Install Python 3.12 and run BUILD_APP.bat again.
pause
exit /b 1

:node_missing
echo.
echo Node.js LTS with npm is required to BUILD the app.
echo Install Node.js LTS and run BUILD_APP.bat again.
pause
exit /b 1

:fail
echo.
echo BUILD FAILED.
echo Review the error above.
pause
exit /b 1
