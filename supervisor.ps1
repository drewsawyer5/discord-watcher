# supervisor.ps1 - Ensures discord-watcher processes are always running
# Designed to run via Task Scheduler every 5 minutes
# Logs to: Drew_code/discord-watcher/watchdog.log

$logFile     = "C:\Users\drews\Life Org\Drew_code\discord-watcher\watchdog.log"
$watcherDir  = "C:\Users\drews\Life Org\Drew_code\discord-watcher"
$maxLogLines = 300

function Write-Log {
    param($msg)
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts $msg"
    Add-Content -Path $logFile -Value $line
}

# Trim log if it gets long
if (Test-Path $logFile) {
    $lines = Get-Content $logFile
    if ($lines.Count -gt $maxLogLines) {
        $lines | Select-Object -Last $maxLogLines | Set-Content $logFile
    }
}

Write-Log "--- supervisor run ---"

# --- Check voice_watcher.py ---
try {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*voice_watcher.py*" }
    if ($procs) {
        $procId = ($procs | Select-Object -First 1).ProcessId
        Write-Log "voice_watcher.py OK - pid $procId"
    } else {
        Write-Log "voice_watcher.py NOT running - restarting"
        Start-Process "cmd.exe" -ArgumentList "/c `"$watcherDir\launch_watcher.bat`"" -WorkingDirectory $watcherDir -WindowStyle Hidden
        Write-Log "voice_watcher.py start issued"
    }
} catch {
    Write-Log "voice_watcher check error - $($_.Exception.Message)"
}

# --- Check process_ingest.py ---
try {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*process_ingest.py*" }
    if ($procs) {
        $procId = ($procs | Select-Object -First 1).ProcessId
        Write-Log "process_ingest.py OK - pid $procId"
    } else {
        Write-Log "process_ingest.py NOT running - restarting"
        Start-Process "cmd.exe" -ArgumentList "/c `"$watcherDir\launch_ingest.bat`"" -WorkingDirectory $watcherDir -WindowStyle Hidden
        Write-Log "process_ingest.py start issued"
    }
} catch {
    Write-Log "process_ingest check error - $($_.Exception.Message)"
}

# --- Check Claude --channels session ---
try {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*--channels*" }
    if ($procs) {
        $procId = ($procs | Select-Object -First 1).ProcessId
        Write-Log "claude OK - pid $procId"
    } else {
        Write-Log "claude NOT running - restarting"
        Start-Process "cmd.exe" -ArgumentList "/c `"$watcherDir\launch_claude.bat`"" -WorkingDirectory $watcherDir -WindowStyle Hidden
        Write-Log "claude start issued"
    }
} catch {
    Write-Log "claude check error - $($_.Exception.Message)"
}
