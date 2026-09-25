@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title CFD Bot - GitHub Updater

if not exist "logs" mkdir "logs" >nul 2>&1
set "UPDATE_LOG=%CD%\logs\UPDATE.log"
> "%UPDATE_LOG%" echo ============================================================
>>"%UPDATE_LOG%" echo CFD updater log
>>"%UPDATE_LOG%" echo Started: %DATE% %TIME%
>>"%UPDATE_LOG%" echo Updater: %~f0
>>"%UPDATE_LOG%" echo Working directory: %CD%
>>"%UPDATE_LOG%" echo ============================================================
>>"%UPDATE_LOG%" echo.

set "REPO_URL=https://github.com/bxane-dev/cfd-bot.git"
set "BOOTSTRAP_ONLY=0"
if /I "%~1"=="--bootstrap-only" set "BOOTSTRAP_ONLY=1"

call :ensure_git
if errorlevel 1 goto :fail

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo ZIP/non-Git copy detected. Attaching GitHub tracking...
  git init
  if errorlevel 1 goto :fail
  git remote remove origin >nul 2>&1
  call :lock_origin
  if errorlevel 1 goto :fail
  git fetch origin main --depth 1
  if errorlevel 1 goto :fail
  git reset origin/main
  if errorlevel 1 goto :fail
  git branch -M main
  git branch --set-upstream-to=origin/main main >nul 2>&1
  call :lock_origin
  if errorlevel 1 goto :fail
  echo GitHub tracking attached to bxane-dev/cfd-bot.
)

call :ensure_local_excludes
if errorlevel 1 goto :fail

if "%BOOTSTRAP_ONLY%"=="1" exit /b 0

echo.
echo Updating only from bxane-dev/cfd-bot...
call :lock_origin
if errorlevel 1 goto :fail
git fetch origin main --prune
if errorlevel 1 goto :fail

set "DIRTY=0"
for /f "delims=" %%A in ('git status --porcelain') do set "DIRTY=1"
if "!DIRTY!"=="1" (
  echo Local changes detected. Saving them to Git stash...
  git stash push -u -m "Automatic backup before UPDATE.bat"
  if errorlevel 1 goto :fail
)

set "AHEAD=0"
for /f %%A in ('git rev-list --count origin/main..HEAD 2^>nul') do set "AHEAD=%%A"
if not "!AHEAD!"=="0" (
  set "BACKUP_BRANCH=local-backup-%RANDOM%-%RANDOM%"
  git branch "!BACKUP_BRANCH!" HEAD
  if errorlevel 1 goto :fail
  echo Local commits preserved on branch !BACKUP_BRANCH!.
)

git checkout -B main origin/main
if errorlevel 1 goto :fail
git reset --hard origin/main
if errorlevel 1 goto :fail
git branch --set-upstream-to=origin/main main >nul 2>&1

rem Verify the launchers were actually replaced with the current BXANE version.
git checkout origin/main -- START.bat START_LIVE.bat BXANE.txt >nul 2>&1
if errorlevel 1 (
  echo Launcher refresh failed.
  goto :fail
)
findstr /i /c:"@BONE" /c:"BY @BONE" START.bat START_LIVE.bat >nul 2>&1
if not errorlevel 1 (
  echo Legacy BONE banner is still present after update.
  goto :fail
)
findstr /i /c:"BY bxane" START.bat START_LIVE.bat >nul 2>&1
if not errorlevel 1 (
  echo Compact BY banner is still present after update.
  goto :fail
)

echo.
echo Updated successfully from bxane-dev/cfd-bot.
git log -1 --oneline
>>"%UPDATE_LOG%" echo Update completed successfully: %DATE% %TIME%
git log -1 --oneline >>"%UPDATE_LOG%" 2>&1
if "!DIRTY!"=="1" (
  echo Your previous local changes are preserved in Git stash.
  echo Run: git stash list
)
echo.
echo Update finished. Press any key to close this window.
pause >nul
exit /b 0

:ensure_local_excludes
rem Older clones may predate .gitignore. Keep machine-local/runtime files out
rem of "git stash -u" so an active virtualenv is never deleted during update.
if not exist ".git\info" mkdir ".git\info" >nul 2>&1
if not exist ".git\info\exclude" type nul > ".git\info\exclude"

call :exclude_local ".env"
call :exclude_local ".env.*"
call :exclude_local ".venv/"
call :exclude_local "venv/"
call :exclude_local "logs/"
call :exclude_local ".live_acknowledged"
call :exclude_local "__pycache__/"
call :exclude_local "*.py[cod]"
call :exclude_local ".pytest_cache/"
call :exclude_local ".coverage"
call :exclude_local "htmlcov/"
exit /b 0

:exclude_local
findstr /x /l /c:"%~1" ".git\info\exclude" >nul 2>&1
if errorlevel 1 >>".git\info\exclude" echo %~1
exit /b 0

:lock_origin
git remote get-url origin >nul 2>&1
if errorlevel 1 (
  git remote add origin "%REPO_URL%"
  if errorlevel 1 exit /b 1
) else (
  git remote set-url origin "%REPO_URL%"
  if errorlevel 1 exit /b 1
)

set "ORIGIN_URL="
for /f "delims=" %%U in ('git remote get-url origin 2^>nul') do set "ORIGIN_URL=%%U"
if /I not "!ORIGIN_URL!"=="%REPO_URL%" (
  echo Refusing to update: origin is not bxane-dev/cfd-bot.
  exit /b 1
)
exit /b 0

:ensure_git
where git >nul 2>&1
if not errorlevel 1 exit /b 0

echo Git was not found. Attempting to install Git for Windows...
where winget >nul 2>&1
if errorlevel 1 (
  echo Git is required. Install Git for Windows, then run UPDATE.bat again.
  exit /b 1
)
winget install -e --id Git.Git --source winget --accept-package-agreements --accept-source-agreements --silent
if errorlevel 1 exit /b 1
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
if exist "%LocalAppData%\Programs\Git\cmd\git.exe" set "PATH=%LocalAppData%\Programs\Git\cmd;%PATH%"
where git >nul 2>&1
if errorlevel 1 (
  echo Git was installed but this window cannot see it yet.
  echo Close this window and run UPDATE.bat again.
  exit /b 1
)
exit /b 0

:write_diagnostics
>>"%UPDATE_LOG%" echo.
>>"%UPDATE_LOG%" echo ============================================================
>>"%UPDATE_LOG%" echo Diagnostics: %DATE% %TIME%
>>"%UPDATE_LOG%" echo Updater: %~f0
>>"%UPDATE_LOG%" echo Working directory: %CD%
where git >>"%UPDATE_LOG%" 2>&1
git --version >>"%UPDATE_LOG%" 2>&1
git remote -v >>"%UPDATE_LOG%" 2>&1
git status --short --branch >>"%UPDATE_LOG%" 2>&1
git branch -vv >>"%UPDATE_LOG%" 2>&1
git log -1 --oneline >>"%UPDATE_LOG%" 2>&1
>>"%UPDATE_LOG%" echo ============================================================
exit /b 0

:fail
call :write_diagnostics
echo.
echo GitHub update/setup failed. Your .env and ignored logs were not deleted.
echo Update log:
echo   %UPDATE_LOG%
if "%BOOTSTRAP_ONLY%"=="1" exit /b 1
echo.
echo Press any key to close this window.
pause >nul
exit /b 1
