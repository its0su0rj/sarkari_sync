@echo off
setlocal
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || goto nopy
)
set "VPY=.venv\Scripts\python.exe"
echo Checking packages...
"%VPY%" -m pip install -q --disable-pip-version-check -r requirements.txt || goto fail
if not exist sarkari.db (
    echo No job data yet. Run run.bat first.
    pause
    exit /b 1
)

set "MODE="
if /i "%~1"=="lan" set "MODE=--lan"
start "" http://127.0.0.1:8000
"%VPY%" api.py %MODE%
pause
exit /b 0

:nopy
echo Python was not found. Install it from https://www.python.org/downloads/ and run this again.
pause
exit /b 1

:fail
echo Installing packages failed. Check your internet connection.
pause
exit /b 1
