@echo off
setlocal
cd /d "%~dp0"
set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" set "VPY=python"
"%VPY%" -c "import secrets;print(secrets.token_urlsafe(32))" > "%TEMP%\em_key.txt" || goto fail
type "%TEMP%\em_key.txt" | clip
echo.
echo Your new STATE_PASSWORD (already copied):
echo.
type "%TEMP%\em_key.txt"
echo.
echo Paste it into GitHub as the STATE_PASSWORD secret, and save a copy somewhere safe.
del "%TEMP%\em_key.txt"
pause
exit /b 0
:fail
echo Python was not found. Run run.bat once first.
pause
exit /b 1
