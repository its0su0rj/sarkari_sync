@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run run.bat once first.
    pause
    exit /b 1
)
set "PROFILE=%~1"
if "%PROFILE%"=="" set "PROFILE=profile.json"
if not exist "%PROFILE%" (
    copy /y profile.example.json profile.json >nul
    echo Created profile.json from the example. Edit it with your details, then run match.bat again.
    notepad profile.json
    exit /b 0
)
".venv\Scripts\python.exe" eligibility.py "%PROFILE%" --out matches.json
pause
