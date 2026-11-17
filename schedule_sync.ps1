# Choose (or change, or stop) how often pc_sync.bat runs. Run it again any time to change it.
$ErrorActionPreference = 'Stop'
$name = 'ExamMatch sync'
$bat = Join-Path $PSScriptRoot 'pc_sync.bat'

function Read-Number($prompt, $min, $max) {
    while ($true) {
        $v = Read-Host $prompt
        $n = 0
        if ([int]::TryParse($v, [ref]$n) -and $n -ge $min -and $n -le $max) { return $n }
        Write-Host "  Please type a number from $min to $max."
    }
}
function Read-Time {
    $v = Read-Host 'At what time? e.g. 21:00 or 9pm (press Enter for 9 PM)'
    if ([string]::IsNullOrWhiteSpace($v)) { return [datetime]::Today.AddHours(21) }
    $v = $v.Trim().ToLower() -replace '\s', ''
    if ($v -match '^(\d{1,2})(?::(\d{2}))?(am|pm)?$') {
        $h = [int]$Matches[1]; $m = if ($Matches[2]) { [int]$Matches[2] } else { 0 }
        if ($Matches[3] -eq 'pm' -and $h -lt 12) { $h += 12 }
        if ($Matches[3] -eq 'am' -and $h -eq 12) { $h = 0 }
        if ($h -le 23 -and $m -le 59) { return [datetime]::Today.AddHours($h).AddMinutes($m) }
    }
    Write-Host '  Could not read that time; using 9 PM.'
    return [datetime]::Today.AddHours(21)
}

$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
Write-Host ''
if ($existing) {
    $info = Get-ScheduledTaskInfo -TaskName $name
    Write-Host "Current schedule is active. Next run: $($info.NextRunTime)   Last run: $($info.LastRunTime)"
} else {
    Write-Host 'No automatic updates are scheduled yet.'
}
Write-Host ''
Write-Host 'How often should ExamMatch fetch new exams?'
Write-Host '  1) Every 6 hours'
Write-Host '  2) Every 12 hours'
Write-Host '  3) Once a day'
Write-Host '  4) Every 2 days'
Write-Host '  5) Every N hours (you choose, 3 to 23)'
Write-Host '  6) Every N days (you choose, 1 to 7)'
Write-Host '  7) Stop automatic updates'
Write-Host '  8) Leave it as it is'
$choice = Read-Number 'Type a number and press Enter' 1 8

if ($choice -eq 8) { exit 0 }
if ($choice -eq 7) {
    if ($existing) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; Write-Host 'Stopped. Run this again to turn it back on.' }
    else { Write-Host 'Nothing to stop.' }
    exit 0
}

switch ($choice) {
    1 { $hours = 6 }
    2 { $hours = 12 }
    3 { $days = 1 }
    4 { $days = 2 }
    5 { $hours = Read-Number 'Every how many hours? (3 to 23)' 3 23 }
    6 { $days = Read-Number 'Every how many days? (1 to 7)' 1 7 }
}

if ($hours) {
    # start at the next full hour, then repeat
    $start = (Get-Date).Date.AddHours((Get-Date).Hour + 1)
    try {
        $trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours $hours)
    } catch {
        $trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours $hours) `
                   -RepetitionDuration (New-TimeSpan -Days 3650)
    }
    $text = "every $hours hours, starting $($start.ToString('h:mm tt'))"
} else {
    $at = Read-Time
    $trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $days -At $at
    $text = if ($days -eq 1) { "every day at $($at.ToString('h:mm tt'))" } else { "every $days days at $($at.ToString('h:mm tt'))" }
}

$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$bat`" auto" -WorkingDirectory $PSScriptRoot
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Fetch new exams from sarkariresult.com and upload them to the ExamMatch website' -Force | Out-Null

Write-Host ''
Write-Host "Done: ExamMatch will fetch new exams $text."
Write-Host 'If the PC is off at that time, it runs as soon as the PC is on again.'
Write-Host 'A black window appears for a few minutes while it runs; you can ignore it.'
Write-Host 'Run schedule_sync.bat again any time to change or stop this.'
