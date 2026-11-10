@echo off
rem Launch the agent with the Python on this machine.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py %*
) else (
    where py >nul 2>nul && (py -3 run.py %*) || (python run.py %*)
)
endlocal
