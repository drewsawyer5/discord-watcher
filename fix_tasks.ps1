# Run this once as Administrator to fix both Task Scheduler tasks

# Fix 1: "Discord Watcher" — use cmd.exe /c to handle the space in "Life Org"
$action1 = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"C:\Users\drews\Life Org\Drew_code\discord-watcher\start.bat`""
Set-ScheduledTask -TaskName "Discord Watcher" -Action $action1
Write-Host "Discord Watcher task fixed"

# Fix 2: "Discord PA Watchdog" — re-point to supervisor.ps1 and enable
$action2 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"C:\Users\drews\Life Org\Drew_code\discord-watcher\supervisor.ps1`""
Set-ScheduledTask -TaskName "Discord PA Watchdog" -Action $action2
Enable-ScheduledTask -TaskName "Discord PA Watchdog"
Write-Host "Discord PA Watchdog task fixed and enabled"

# Trigger both now to bring watchers up immediately
Start-ScheduledTask -TaskName "Discord Watcher"
Write-Host "Discord Watcher started"
Start-ScheduledTask -TaskName "Discord PA Watchdog"
Write-Host "Discord PA Watchdog started"

Write-Host "`nDone. Check watchdog.log in ~30 seconds to confirm watchers are running."
