# One-time setup of the ExamMatch Telegram bot on Cloudflare Workers (free).
# Run from the project folder in the VS Code terminal:   powershell -ExecutionPolicy Bypass -File worker\setup.ps1
# You will be asked to: approve Cloudflare in your browser, maybe pick a workers.dev name, and paste the bot token.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$worker = $PSScriptRoot
$docs = Join-Path $env:USERPROFILE 'Documents'
Set-Location $worker

function Step($n, $text) { Write-Host ""; Write-Host "[$n/7] $text" -ForegroundColor Cyan }
function New-Secret { -join ((1..40) | ForEach-Object { '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'[(Get-Random -Maximum 62)] }) }

Step 1 'Checking Node.js'
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing Node.js (needed once)...' -ForegroundColor Yellow
    winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Host 'Node.js could not be installed automatically. Install it from nodejs.org, then run this again.'
        exit 1
    }
}
node --version
npm install --no-audit --no-fund --silent

Step 2 'Logging in to Cloudflare (a browser window opens: click Allow)'
npx wrangler login

Step 3 'Creating the subscriber store'
$toml = Get-Content wrangler.toml -Raw
if ($toml -match 'REPLACE_WITH_KV_ID') {
    $out = (npx wrangler kv namespace create exammatch-subscribers 2>&1 | Out-String)
    $m = [regex]::Match($out, '[0-9a-f]{32}')
    if (-not $m.Success) {
        $list = (npx wrangler kv namespace list 2>&1 | Out-String)
        $m = [regex]::Match($list, '"id":\s*"([0-9a-f]{32})"[^}]*"title":\s*"[^"]*exammatch-subscribers')
        if ($m.Success) { $m = $m.Groups[1] }
    }
    if (-not $m.Success) { Write-Host $out; throw 'Could not create or find the KV namespace' }
    $toml = $toml -replace 'REPLACE_WITH_KV_ID', $m.Value
    Set-Content wrangler.toml $toml -NoNewline
    Write-Host "Store id: $($m.Value)"
} else { Write-Host 'Already set up.' }

Step 4 'Publishing the bot (if asked for a workers.dev name, type a short one, e.g. exammatch)'
npx wrangler deploy
if ($LASTEXITCODE -ne 0) { throw 'Deploy failed (see above)' }
$deploy = (npx wrangler deploy 2>&1 | Out-String)
$url = [regex]::Match($deploy, 'https://[a-z0-9.-]+\.workers\.dev').Value
if (-not $url) { throw 'Could not find the workers.dev address in the output above' }
Write-Host "Bot address: $url" -ForegroundColor Green

Step 5 'Saving secrets'
$tokenSecure = Read-Host 'Paste the Telegram bot token from BotFather (hidden)' -AsSecureString
$token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokenSecure))
$token = $token.Trim()
$me = Invoke-RestMethod "https://api.telegram.org/bot$token/getMe"
Write-Host "Token belongs to @$($me.result.username)"
$webhookSecret = New-Secret
$adminToken = New-Secret
Set-Content (Join-Path $docs 'exammatch_ADMIN_TOKEN.txt') $adminToken -Encoding ascii
$token | npx wrangler secret put TELEGRAM_BOT_TOKEN
$webhookSecret | npx wrangler secret put WEBHOOK_SECRET
$adminToken | npx wrangler secret put ADMIN_TOKEN

Step 6 'Moving existing subscribers from the old bot'
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$env:ADMIN_TOKEN = $adminToken
& $py -m pip install -q --disable-pip-version-check requests cryptography
& $py (Join-Path $worker 'migrate_subscribers.py') $url
Remove-Item Env:ADMIN_TOKEN

Step 7 'Connecting Telegram to the bot'
$body = @{ url = "$url/telegram"; secret_token = $webhookSecret; allowed_updates = @('message'); drop_pending_updates = $false } | ConvertTo-Json
$r = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/setWebhook" -ContentType 'application/json' -Body $body
Write-Host "Telegram: $($r.description)"
$info = (Invoke-RestMethod "https://api.telegram.org/bot$token/getWebhookInfo").result
Write-Host "Webhook: $($info.url)   waiting messages: $($info.pending_update_count)"
$token = $null

Write-Host ''
Write-Host "Done. The bot now answers instantly from $url" -ForegroundColor Green
Write-Host "Admin token saved in $docs\exammatch_ADMIN_TOKEN.txt (keep it; copy it to your safe note)."
Write-Host "Check: $url/admin/stats needs the admin token; or just send /matches to the bot."
