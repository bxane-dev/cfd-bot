@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title CFD Bot - GitHub Updater

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

echo.
echo Updated successfully from bxane-dev/cfd-bot.
git log -1 --oneline
if "!DIRTY!"=="1" (
  echo Your previous local changes are preserved in Git stash.
  echo Run: git stash list
)
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

:fail
echo.
echo GitHub update/setup failed. Your .env and ignored logs were not deleted.
exit /b 1
