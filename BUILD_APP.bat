@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Build CFD Desk

set "ROOT=%CD%"
set "LOG=%ROOT%\build.log"
> "%LOG%" echo CFD Desk build started %DATE% %TIME%

if exist "BXANE.txt" (
  type "BXANE.txt"
  echo.
)
echo ============================================================
echo                    BUILD CFD DESK
echo ============================================================
echo Builds one Windows CFD Desk app.
echo The Python trading engine is bundled and runs hidden.
echo.
echo Detailed log: "%LOG%"
echo.

rem ------------------------------------------------------------
rem Find Python 3.11+
rem ------------------------------------------------------------
set "PY_EXE="
set "PY_ARGS="

where py.exe >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >>"%LOG%" 2>&1
  if not errorlevel 1 (
    set "PY_EXE=py"
    set "PY_ARGS=-3"
  )
)

if not defined PY_EXE (
  for /f "delims=" %%P in ('where python.exe 2^>nul') do (
    if not defined PY_EXE (
      "%%P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >>"%LOG%" 2>&1
      if not errorlevel 1 set "PY_EXE=%%P"
    )
  )
)

if not defined PY_EXE (
  if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PY_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
  )
)

if not defined PY_EXE (
  echo [ERROR] Python 3.11+ was not found.
  >>"%LOG%" echo ERROR: Python 3.11+ not found.
  goto :fail
)

echo Python: "%PY_EXE%" %PY_ARGS%
>>"%LOG%" echo Python: "%PY_EXE%" %PY_ARGS%

rem ------------------------------------------------------------
rem Find Node/npm
rem ------------------------------------------------------------
set "NPM_EXE="
for /f "delims=" %%N in ('where npm.cmd 2^>nul') do (
  if not defined NPM_EXE set "NPM_EXE=%%N"
)
if not defined NPM_EXE if exist "%ProgramFiles%\nodejs\npm.cmd" set "NPM_EXE=%ProgramFiles%\nodejs\npm.cmd"

if not defined NPM_EXE (
  echo [ERROR] Node.js LTS / npm was not found.
  echo Install Node.js LTS from nodejs.org, then run build.bat again.
  >>"%LOG%" echo ERROR: npm not found.
  goto :fail
)

echo npm: "%NPM_EXE%"
>>"%LOG%" echo npm: "%NPM_EXE%"

rem ------------------------------------------------------------
rem 1. Icon source
rem ------------------------------------------------------------
echo [1/6] Preparing application icon...
if not exist "assets" mkdir "assets"
if not exist "assets\app-icon.b64" (
  echo [ERROR] Missing assets\app-icon.b64
  >>"%LOG%" echo ERROR: assets\app-icon.b64 missing.
  goto :fail
)

powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $b64=(Get-Content -LiteralPath 'assets\app-icon.b64' -Raw).Trim(); [IO.File]::WriteAllBytes((Join-Path (Get-Location) 'assets\app-icon-source.jpg'), [Convert]::FromBase64String($b64))" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Could not decode the application icon.
  goto :fail
)

rem ------------------------------------------------------------
rem 2. Python build venv
rem ------------------------------------------------------------
echo [2/6] Preparing Python build environment...
if not exist ".build-venv\Scripts\python.exe" (
  "%PY_EXE%" %PY_ARGS% -m venv ".build-venv" >>"%LOG%" 2>&1
  if errorlevel 1 (
    echo [ERROR] Could not create .build-venv
    goto :fail
  )
)

set "BUILD_PY=%ROOT%\.build-venv\Scripts\python.exe"
if not exist "%BUILD_PY%" (
  echo [ERROR] Build Python was not created.
  goto :fail
)

"%BUILD_PY%" -m pip install --disable-pip-version-check --upgrade pip >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] pip upgrade failed.
  goto :fail
)

"%BUILD_PY%" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller pillow >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Python build dependencies failed to install.
  goto :fail
)

rem ------------------------------------------------------------
rem 3. Generate real Windows ICO
rem ------------------------------------------------------------
echo [3/6] Generating Windows icon...
"%BUILD_PY%" -c "from PIL import Image; p=r'assets\app-icon-source.jpg'; out=r'assets\app-icon.ico'; im=Image.open(p).convert('RGBA'); im.thumbnail((256,256), Image.Resampling.LANCZOS); canvas=Image.new('RGBA',(256,256),(0,0,0,0)); canvas.alpha_composite(im,((256-im.width)//2,(256-im.height)//2)); canvas.save(out,format='ICO',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Windows icon generation failed.
  goto :fail
)
if not exist "assets\app-icon.ico" (
  echo [ERROR] assets\app-icon.ico was not created.
  goto :fail
)

rem ------------------------------------------------------------
rem 4. Build hidden CFD engine
rem ------------------------------------------------------------
echo [4/6] Building hidden CFD engine...
if exist "build\engine" rmdir /s /q "build\engine"
if exist "build\pyi-work" rmdir /s /q "build\pyi-work"
if exist "build\pyi-spec" rmdir /s /q "build\pyi-spec"
mkdir "build\engine" >>"%LOG%" 2>&1
mkdir "build\pyi-work" >>"%LOG%" 2>&1
mkdir "build\pyi-spec" >>"%LOG%" 2>&1

"%BUILD_PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --noupx ^
  --name cfd-engine ^
  --icon "%ROOT%\assets\app-icon.ico" ^
  --distpath "%ROOT%\build\engine" ^
  --workpath "%ROOT%\build\pyi-work" ^
  --specpath "%ROOT%\build\pyi-spec" ^
  --paths "%ROOT%\app" ^
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
  --add-data "%ROOT%\web;web" ^
  --add-data "%ROOT%\config.yaml;." ^
  --add-data "%ROOT%\.env.example;." ^
  --add-data "%ROOT%\BXANE.txt;." ^
  "%ROOT%\desktop\backend_entry.py" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Hidden CFD engine build failed.
  goto :fail
)

if not exist "build\engine\cfd-engine.exe" (
  echo [ERROR] cfd-engine.exe was not produced.
  >>"%LOG%" echo ERROR: build\engine\cfd-engine.exe missing.
  goto :fail
)

rem ------------------------------------------------------------
rem 5. Electron dependencies
rem ------------------------------------------------------------
echo [5/6] Installing desktop dependencies...
call "%NPM_EXE%" install --no-audit --no-fund >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] npm install failed.
  goto :fail
)

rem ------------------------------------------------------------
rem 6. Windows package
rem ------------------------------------------------------------
echo [6/6] Building Windows installer and portable EXE...
if exist "release" rmdir /s /q "release"
call "%NPM_EXE%" run dist:win >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] electron-builder failed.
  goto :fail
)

if not exist "release" (
  echo [ERROR] release folder was not created.
  goto :fail
)

echo.
echo ============================================================
echo BUILD COMPLETE
echo ============================================================
echo Output:
echo   %ROOT%\release
echo.
echo - CFD Desk installer
echo - CFD Desk portable EXE
echo - hidden bundled CFD engine
echo.
>>"%LOG%" echo BUILD SUCCESS %DATE% %TIME%
start "" explorer "%ROOT%\release"
exit /b 0

:fail
set "BUILD_CODE=%ERRORLEVEL%"
if "%BUILD_CODE%"=="0" set "BUILD_CODE=1"
echo.
echo ============================================================
echo BUILD FAILED
echo ============================================================
echo The window will stay open.
echo Full log:
echo   %LOG%
echo.
echo Last 35 log lines:
echo ------------------------------------------------------------
powershell.exe -NoLogo -NoProfile -Command "if (Test-Path -LiteralPath $env:LOG) { Get-Content -LiteralPath $env:LOG -Tail 35 }" 2>nul
echo ------------------------------------------------------------
echo.
pause
exit /b %BUILD_CODE%
