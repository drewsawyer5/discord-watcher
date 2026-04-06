# watchdog.ps1 — Ensures discord-watcher processes are always running
# Designed to run via Task Scheduler every 5 minutes
# Logs to: Drew_code/discord-watcher/watchdog.log

$logFile     = "C:\Users\drews\Life Org\Drew_code\discord-watcher\watchdog.log"
$watcherPath = "C:\Users\drews\Life Org\Drew_code\discord-watcher\watcher.py"
$watcherDir  = "C:\Users\drews\Life Org\Drew_code\discord-watcher"
$pythonExe   = "C:\Users\drews\AppData\Local\Programs\Python\Python314\python.exe"
$claudeDir   = "C:\Users\drews\Life Org"
$maxLogLines = 300

function Write-Log {
    param($msg)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $logFile -Value $line
}

# Trim log if it gets long
if (Test-Path $logFile) {
    $lines = Get-Content $logFile
    if ($lines.Count -gt $maxLogLines) {
        $lines | Select-Object -Last $maxLogLines | Set-Content $logFile
    }
}

# --- Check watcher.py ---
$watcherProc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*watcher.py*" }
if ($watcherProc) {
    Write-Log "watcher.py OK (PID $($watcherProc.ProcessId))"
} else {
    Write-Log "watcher.py NOT running — restarting"
    Start-Process -FilePath $pythonExe `
        -ArgumentList "`"$watcherPath`"" `
        -WorkingDirectory $watcherDir `
        -WindowStyle Minimized
}

# --- Check Claude --channels session ---
$claudeProc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*--channels*" }
if ($claudeProc) {
    Write-Log "claude OK (PID $($claudeProc.ProcessId))"
} else {
    Write-Log "claude NOT running — restarting"
    Start-Process -FilePath "claude" `
        -ArgumentList "--dangerously-skip-permissions --channels plugin:discord@claude-plugins-official" `
        -WorkingDirectory $claudeDir `
        -WindowStyle Normal
}
