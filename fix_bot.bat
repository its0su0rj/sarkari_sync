@echo off
rem One-click: push the Cloudflare bot code, then set up the bot on Cloudflare.
setlocal
cd /d "%~dp0"
set "GIT=git"
where git >nul 2>nul || set "GIT=C:\Program Files\Git\cmd\git.exe"

echo [1/2] Uploading the new code to GitHub...
"%GIT%" rm -q --cached .github/workflows/bot.yml >nul 2>nul
if exist ".github\workflows\bot.yml" del ".github\workflows\bot.yml"
"%GIT%" add -A
"%GIT%" commit -q -m "Move Telegram bot to Cloudflare Workers for instant replies" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01Axw1Fe9q29nFqFToTbus7L"
"%GIT%" pull -q --rebase origin main || goto :fail
"%GIT%" push -q origin HEAD:main || goto :fail
echo Uploaded. GitHub is now rebuilding the website (takes a few minutes).
echo.
echo [2/2] Cloudflare setup
echo If you do not have a Cloudflare account yet, make one now (free):
echo     https://dash.cloudflare.com/sign-up   and verify the email.
pause
powershell -ExecutionPolicy Bypass -File "%~dp0worker\setup.ps1"
pause
exit /b 0

:fail
echo.
echo Upload failed: see the message above.
pause
exit /b 1
