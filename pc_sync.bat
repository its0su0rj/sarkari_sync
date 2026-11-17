@echo off
rem Fetch new exams on this PC (sarkariresult.com blocks GitHub), upload them, and republish the website.
rem Run by Windows Task Scheduler on the schedule you pick in schedule_sync.bat, or double-click to run now.
setlocal
cd /d "%~dp0"
set "LOG=%~dp0pc_sync.log"
set "REPO=its0su0rj/sarkari_sync"
echo ==== %date% %time% >> "%LOG%"

set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" (set "MSG=Python environment missing: run run.bat once first." & goto fail)

set "GH=gh"
where gh >nul 2>nul || set "GH=C:\Program Files\GitHub CLI\gh.exe"

set "PWFILE=%USERPROFILE%\Documents\exammatch_STATE_PASSWORD.txt"
if not exist "%PWFILE%" (set "MSG=Password file not found: %PWFILE%" & goto fail)
set /p STATE_PASSWORD=<"%PWFILE%"

set "GITHUB_TOKEN="
for /f "usebackq delims=" %%t in (`call "%GH%" auth token 2^>nul`) do set "GITHUB_TOKEN=%%t"
if "%GITHUB_TOKEN%"=="" (set "MSG=GitHub CLI is not logged in: run  gh auth login" & goto fail)
set "GITHUB_REPOSITORY=%REPO%"

echo [1/3] Fetching new exams from sarkariresult.com (a few minutes, it goes slowly on purpose)...
"%VPY%" -m pip install -q --disable-pip-version-check -r requirements.txt >> "%LOG%" 2>&1
"%VPY%" sync.py >> "%LOG%" 2>&1
if errorlevel 1 echo       The sync reported a problem; uploading the data it has. Details in pc_sync.log
echo [2/3] Uploading the encrypted job data to GitHub...
"%VPY%" state.py push jobsdb sarkari.db >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=Upload failed. See pc_sync.log" & goto fail)
echo [3/3] Asking GitHub to rebuild the website...
"%GH%" workflow run sync.yml -R %REPO% >> "%LOG%" 2>&1
if errorlevel 1 (set "MSG=Could not start the website rebuild. See pc_sync.log" & goto fail)
echo done >> "%LOG%"
echo.
echo Done. The website updates in about 5 minutes.
if /i not "%~1"=="auto" pause
exit /b 0

:fail
echo ERROR: %MSG%
echo ERROR: %MSG% >> "%LOG%"
if /i not "%~1"=="auto" pause
exit /b 1
