@echo off
setlocal
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul || goto nopy

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv || goto nopy
)
set "VPY=.venv\Scripts\python.exe"

echo Installing packages...
"%VPY%" -m pip install -q --disable-pip-version-check -r requirements.txt || goto fail

if "%SR_USER_AGENT%"=="" set "SR_USER_AGENT=ExamMatchBot/1.0"

echo.
echo === 1. Sync ===
"%VPY%" sync.py -v
echo.
echo === 2. Status ===
"%VPY%" sync.py --status
echo.
echo === 3. Export ===
"%VPY%" sync.py --export jobs.json --open-only
"%VPY%" sync.py --export all.json --kinds job,admit_card,result,answer_key,syllabus,admission
echo.
echo Done. Open jobs are in jobs.json, everything in all.json, the log in sync.log
pause
exit /b 0

:nopy
echo.
echo Python was not found. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" in the installer), then double-click run.bat again.
pause
exit /b 1

:fail
echo.
echo Installing packages failed. Check your internet connection and try again.
pause
exit /b 1
