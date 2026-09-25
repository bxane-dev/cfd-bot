@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CFD Bot - Capital.com LIVE

if not exist "logs" mkdir "logs" >nul 2>&1
set "START_LOG=%CD%\logs\START_LIVE.log"
> "%START_LOG%" echo ============================================================
>>"%START_LOG%" echo CFD LIVE launcher log
>>"%START_LOG%" echo Started: %DATE% %TIME%
>>"%START_LOG%" echo Launcher: %~f0
>>"%START_LOG%" echo Working directory: %CD%
>>"%START_LOG%" echo ============================================================
where git >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%G in ('git rev-parse --short HEAD 2^>nul') do >>"%START_LOG%" echo Git commit: %%G
  for /f "delims=" %%B in ('git branch --show-current 2^>nul') do >>"%START_LOG%" echo Git branch: %%B
)
>>"%START_LOG%" echo.
chcp 65001 >nul 2>&1
call :ensure_bxane_banner
if errorlevel 1 goto :fail
>>"%START_LOG%" echo Banner file: %CD%\BXANE.txt
if exist "BXANE.txt" (
  >>"%START_LOG%" echo --- BXANE.txt ---
  type "BXANE.txt" >>"%START_LOG%" 2>&1
  >>"%START_LOG%" echo --- end BXANE.txt ---
) else (
  >>"%START_LOG%" echo ERROR: BXANE.txt missing after ensure_bxane_banner
)
call :sticky_banner

if not exist ".git" (
  echo ZIP/non-Git copy detected. Attaching GitHub tracking...
  call UPDATE.bat --bootstrap-only >>"%START_LOG%" 2>&1
  if errorlevel 1 (
    echo Warning: GitHub tracking could not be attached. The bot can still start.
  ) else (
    echo GitHub tracking is ready. Future git pull commands will work.
  )
  echo.
)

set "VENV_PY=.venv\Scripts\python.exe"
set "PY_KIND="
set "PY_EXE="
set "PY_ARGS="

echo [1/6] Checking Python...

rem Prefer an already-working project virtual environment.
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    echo Using existing virtual environment.
    goto :dependencies
  )
  echo Existing .venv is unusable or older than Python 3.11. Rebuilding it...
)

call :detect_python
if not errorlevel 1 goto :python_found

echo.
echo Python 3.11+ was not found.
echo Attempting to install Python 3.12 automatically...
echo.

where winget >nul 2>&1
if errorlevel 1 goto :no_winget

winget install -e --id Python.Python.3.12 --scope user --source winget --accept-package-agreements --accept-source-agreements --silent
if errorlevel 1 (
  echo.
  echo Automatic Python installation failed.
  goto :manual_install
)

echo.
echo Python installation completed. Detecting it...
call :detect_python
if not errorlevel 1 goto :python_found

if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  set "PY_KIND=exe"
  set "PY_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
  goto :python_found
)

echo.
echo Python appears to be installed, but this window cannot detect it yet.
echo Close this window, double-click START_LIVE.bat again, and it should continue.
pause
exit /b 0

:no_winget
echo WinGet is not available on this Windows installation.
echo Downloading the official Python 3.12 installer instead...
set "PY_INSTALLER=%TEMP%\cfd_bot_python312.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile '%PY_INSTALLER%'; Start-Process -FilePath '%PY_INSTALLER%' -ArgumentList '/quiet InstallAllUsers=0 PrependPath=0 Include_pip=1 Include_test=0' -Wait"
if errorlevel 1 (
  echo.
  echo Automatic Python download or installation failed.
  goto :manual_install
)
if exist "%PY_INSTALLER%" del /q "%PY_INSTALLER%" >nul 2>&1
call :detect_python
if not errorlevel 1 goto :python_found
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  set "PY_KIND=exe"
  set "PY_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
  goto :python_found
)
echo Python installation completed, but this window could not detect it.
echo Close this window and double-click START_LIVE.bat again.
pause
exit /b 0

:manual_install
echo.
echo Install Python 3.12, then run START_LIVE.bat again.
echo Official package ID: Python.Python.3.12
echo.
echo You can install it from a new Command Prompt with:
echo   winget install -e --id Python.Python.3.12 --scope user
echo.
echo Or install Python 3.12 manually and enable "Add python.exe to PATH".
goto :fail

:python_found
echo Found Python:
>>"%START_LOG%" echo Python kind: %PY_KIND%
>>"%START_LOG%" echo Python executable: %PY_EXE%
>>"%START_LOG%" echo Python args: %PY_ARGS%
if "%PY_KIND%"=="launcher" (
  %PY_EXE% %PY_ARGS% --version
) else (
  "%PY_EXE%" --version
)

echo [2/6] Preparing virtual environment...
if exist ".venv" rmdir /s /q ".venv"

if "%PY_KIND%"=="launcher" (
  %PY_EXE% %PY_ARGS% -m venv .venv
  if errorlevel 1 %PY_EXE% %PY_ARGS% -m venv .venv >>"%START_LOG%" 2>&1
) else (
  "%PY_EXE%" -m venv .venv
  if errorlevel 1 "%PY_EXE%" -m venv .venv >>"%START_LOG%" 2>&1
)
if errorlevel 1 (
  echo Could not create .venv.
  goto :fail
)

if not exist "%VENV_PY%" (
  echo Virtual environment was not created correctly.
  goto :fail
)

:dependencies
echo [3/6] Checking dependencies...
"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo pip is missing from the virtual environment. Repairing it...
  "%VENV_PY%" -m ensurepip --upgrade
  if errorlevel 1 "%VENV_PY%" -m ensurepip --upgrade >>"%START_LOG%" 2>&1
  if errorlevel 1 (
    echo pip repair failed. Rebuilding the virtual environment...
    call :detect_python
    if errorlevel 1 goto :fail
    if exist ".venv" rmdir /s /q ".venv"
    if "%PY_KIND%"=="launcher" (
      %PY_EXE% %PY_ARGS% -m venv .venv
    ) else (
      "%PY_EXE%" -m venv .venv
    )
    if errorlevel 1 goto :fail
  )
  "%VENV_PY%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo pip is still unavailable after repair/rebuild.
    goto :fail
  )
)
"%VENV_PY%" -c "import pandas,yaml,dotenv,optuna" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies...
  "%VENV_PY%" -m pip install --upgrade pip
  if errorlevel 1 "%VENV_PY%" -m pip install --upgrade pip >>"%START_LOG%" 2>&1
  if errorlevel 1 goto :fail
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 "%VENV_PY%" -m pip install -r requirements.txt >>"%START_LOG%" 2>&1
  if errorlevel 1 goto :fail
)

echo [4/6] Checking configuration...
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env from .env.example.
)

"%VENV_PY%" scripts\check_login.py
if errorlevel 1 (
  "%VENV_PY%" scripts\check_login.py >>"%START_LOG%" 2>&1
  echo.
  echo Fill CAPITAL_EMAIL, CAPITAL_API_KEY, and CAPITAL_API_PASSWORD in .env.
  echo These credentials must work with your Capital.com LIVE environment.
  start "" notepad ".env"
  pause
  "%VENV_PY%" scripts\check_login.py
  if errorlevel 1 (
    "%VENV_PY%" scripts\check_login.py >>"%START_LOG%" 2>&1
    set "FAIL_REASON=Capital.com credential/login check failed"
    goto :fail
  )
)

"%VENV_PY%" -c "import app.auto,app.desk,app.web_app" >nul 2>&1
if errorlevel 1 (
  echo Startup import check failed.
  "%VENV_PY%" -c "import app.auto,app.desk,app.web_app" >>"%START_LOG%" 2>&1
  set "FAIL_REASON=Startup import check failed"
  goto :fail
)

echo [5/6] LIVE trading acknowledgement...
echo.
echo ============================================================
echo                    CAPITAL.COM LIVE
echo ============================================================
echo This launcher sends orders to the LIVE Capital.com endpoint.
echo It uses the current settings in config.yaml.
echo.
"%VENV_PY%" -c "import yaml; cfg=yaml.safe_load(open('config.yaml', encoding='utf-8')); r=cfg.get('risk',{}); a=cfg.get('account',{}); print('Risk per trade:', r.get('risk_per_trade_pct'), 'pct'); print('Requested leverage:', str(a.get('requested_leverage', 'broker')) + ':1'); print('Portfolio allocation cap:', r.get('max_portfolio_allocation_pct', 100), 'pct'); print('Daily loss limit:', ('ON (' + str(r.get('max_daily_loss_pct')) + ' pct)') if r.get('daily_loss_enabled', True) else 'OFF'); print('Max open positions:', r.get('max_open_positions')); print('Max index positions:', r.get('max_index_positions'))"
echo.

if not exist ".live_acknowledged" (
  echo First LIVE launch on this copy.
  echo Future launches will not ask again once you acknowledge below.
  choice /C YN /N /M "Acknowledge real-money LIVE trading and continue? [Y/N] "
  if errorlevel 2 (
    echo.
    echo Live start cancelled.
    pause
    exit /b 0
  )
  >".live_acknowledged" echo acknowledged
  echo LIVE acknowledgement saved locally.
) else (
  echo LIVE acknowledgement already saved locally - starting without another prompt.
)

echo.
set "CFD_START_LOG=%START_LOG%"
echo [6/6] Starting CFD bot in LIVE mode...
echo Dashboard opens automatically with a one-run control token.
echo Automatic tuning is skipped at startup.
echo.
call :reset_scroll_region
"%VENV_PY%" -u scripts\sticky_console.py --mode live
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo LIVE bot stopped with exit code %EXIT_CODE%.
  set "FAIL_REASON=LIVE CFD bot exited with code %EXIT_CODE%"
  goto :fail
)

echo.
echo LIVE bot stopped normally.
call :reset_scroll_region
pause
exit /b 0

:detect_python
set "PY_KIND="
set "PY_EXE="
set "PY_ARGS="

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_KIND=launcher"
  set "PY_EXE=py"
  set "PY_ARGS=-3"
  exit /b 0
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_KIND=exe"
  set "PY_EXE=python"
  exit /b 0
)

python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_KIND=exe"
  set "PY_EXE=python3"
  exit /b 0
)

for %%V in (314 313 312 311) do (
  if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
    "%LocalAppData%\Programs\Python\Python%%V\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PY_KIND=exe"
      set "PY_EXE=%LocalAppData%\Programs\Python\Python%%V\python.exe"
      exit /b 0
    )
  )
)

for %%V in (314 313 312 311) do (
  if exist "%ProgramFiles%\Python%%V\python.exe" (
    "%ProgramFiles%\Python%%V\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
      set "PY_KIND=exe"
      set "PY_EXE=%ProgramFiles%\Python%%V\python.exe"
      exit /b 0
    )
  )
)

exit /b 1

:ensure_bxane_banner
set "BXANE_B64=IF9fX19fXyAgICAgICAgICAgIF9fX19fX18gIF8gICAgICAgIF9fX19fX18KKCAgX19fIFwgfFwgICAgIC98KCAgX19fICApKCAoICAgIC98KCAgX19fXyBcCnwgKCAgICkgKSggXCAgIC8gKXwgKCAgICkgfHwgIFwgICggfHwgKCAgICBcLwp8IChfXy8gLyAgXCAoXykgLyB8IChfX18pIHx8ICAgXCB8IHx8IChfXwp8ICBfXyAoICAgICkgXyAoICB8ICBfX18gIHx8IChcIFwpIHx8ICBfXykKfCAoICBcIFwgIC8gKCApIFwgfCAoICAgKSB8fCB8IFwgICB8fCAoCnwgKV9fXykgKSggLyAgIFwgKXwgKSAgICggfHwgKSAgXCAgfHwgKF9fX18vXAp8LyBcX19fLyB8LyAgICAgXHx8LyAgICAgXHx8LyAgICApXykoX19fX19fXy8KICAgICAgICAgICAgICAgICAgICAgICAgIGJ4YW5lCg=="
set "BXANE_BANNER_PATH=%~dp0BXANE.txt"
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [IO.File]::WriteAllBytes($env:BXANE_BANNER_PATH,[Convert]::FromBase64String($env:BXANE_B64))" >>"%START_LOG%" 2>&1
if errorlevel 1 (
  set "FAIL_REASON=Could not restore BXANE banner"
  exit /b 1
)
exit /b 0


:sticky_banner
set "BXANE_STICKY=0"
set "ESC="
for /F "delims=" %%E in ('echo prompt $E^| cmd') do set "ESC=%%E"

rem Keep BXANE fixed in rows 1-11. Only the log area from row 12 down scrolls.
set "BXANE_ANSI=0"
if defined WT_SESSION set "BXANE_ANSI=1"
if defined ANSICON set "BXANE_ANSI=1"
if /I "%ConEmuANSI%"=="ON" set "BXANE_ANSI=1"
if defined TERM set "BXANE_ANSI=1"

if "%BXANE_ANSI%"=="1" if defined ESC (
  <nul set /p "=%ESC%[2J%ESC%[H"
  if exist "%~dp0BXANE.txt" (
    type "%~dp0BXANE.txt"
  ) else (
    call :print_bxane_ascii
  )
  <nul set /p "=                    %ESC%]8;;https://guns.lol/bxane%ESC%\https://guns.lol/bxane%ESC%]8;;%ESC%\"
  echo.
  <nul set /p "=%ESC%[12;r%ESC%[12;1H"
  set "BXANE_STICKY=1"
  exit /b 0
)

rem Fallback for classic CMD hosts that do not expose ANSI capability.
if exist "%~dp0BXANE.txt" (
  type "%~dp0BXANE.txt"
) else (
  call :print_bxane_ascii
)
echo                     https://guns.lol/bxane
echo.
exit /b 0

:reset_scroll_region
if "%BXANE_STICKY%"=="1" if defined ESC (
  <nul set /p "=%ESC%[r%ESC%[999;1H"
)
exit /b 0

:fail
if not defined FAIL_REASON set "FAIL_REASON=Launcher/setup step failed; see preceding log entries"
>>"%START_LOG%" echo.
>>"%START_LOG%" echo ============================================================
>>"%START_LOG%" echo FAILURE: %FAIL_REASON%
>>"%START_LOG%" echo Time: %DATE% %TIME%
>>"%START_LOG%" echo Launcher: %~f0
>>"%START_LOG%" echo Working directory: %CD%
>>"%START_LOG%" echo ============================================================
echo.
echo Startup failed: %FAIL_REASON%
echo Failure log:
echo   %START_LOG%
echo.
echo Last 30 log lines:
echo ------------------------------------------------------------
powershell.exe -NoLogo -NoProfile -Command "if (Test-Path -LiteralPath $env:START_LOG) { Get-Content -LiteralPath $env:START_LOG -Tail 30 }" 2>nul
echo ------------------------------------------------------------
call :reset_scroll_region
echo.
echo Press any key to close.
pause >nul
exit /b 1
