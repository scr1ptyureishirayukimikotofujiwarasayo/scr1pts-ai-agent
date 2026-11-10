@echo off
rem Remove API keys, sessions and caches before sharing the harness.
rem Usage: clean.bat [-y] [--dry-run] [--keep-config] [--keep-mcp]
setlocal
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo Python was not found. Install Python 3.10+ or create .venv first.
    exit /b 1
)

%PY% -m agent.cleanup %*
exit /b %errorlevel%
