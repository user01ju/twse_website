@echo off
REM Local FORCE_REBUILD wrapper. Chinese output is handled by Python (UTF-8).
REM Usage:
REM   rebuild.bat              rebuild latest trading day
REM   rebuild.bat 2026-06-18   rebuild a specific date
REM   rebuild.bat 20260618     same (YYYYMMDD accepted)

cd /d "%~dp0"

set "PYTHONUTF8=1"
set "FORCE_REBUILD=true"

python rebuild_local.py %*

echo.
echo [EXIT %ERRORLEVEL%] 0=done, 2=no full report (see messages above)
