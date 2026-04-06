# watchdog.ps1 - Ensures discord-watcher processes are always running
# Designed to run via Task Scheduler every 5 minutes
# Logs to: Drew_code/discord-watcher/watchdog.log

$logFile     = "C:\Users\drews\Life Org\Drew_code\discord-watcher\watchdog.log"
$watcherPath = "C:\Users\drews\Life Org\Drew_code\discord-watcher\watcher.py"
$watcherDir  = "C:\Users\drews\Life Org\Drew_code\discord-watcher"
$pythonExe   = "C:\Users\drews\AppData\Local\Programs\Python\Python314\python.exe"
$claudeExe   = "C:\Users\drews\.local\bin\claude.exe"
$claudeDir   = "C:\Users\drews\Life Org"
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

Write-Log "--- watchdog run ---"

# --- Check watcher.py ---
try {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*watcher.py*" }
    if ($procs) {
        $procId = ($procs | Select-Object -First 1).ProcessId
        Write-Log "watcher.py OK - pid $procId"
    } else {
        Write-Log "watcher.py NOT running - restarting"
        Start-Process -FilePath $pythonExe `
            -ArgumentList "`"$watcherPath`"" `
            -WorkingDirectory $watcherDir `
            -WindowStyle Minimized
        Write-Log "watcher.py start issued"
    }
} catch {
    Write-Log "watcher check error - $($_.Exception.Message)"
}

# --- Check Claude --channels session ---
try {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*--channels*" }
    if ($procs) {
        $procId = ($procs | Select-Object -First 1).ProcessId
        Write-Log "claude OK - pid $procId"
    } else {
        Write-Log "claude NOT running - restarting"
        Start-Process -FilePath $claudeExe `
            -ArgumentList "--dangerously-skip-permissions --channels plugin:discord@claude-plugins-official" `
            -WorkingDirectory $claudeDir `
            -WindowStyle Normal
        Write-Log "claude start issued"
    }
} catch {
    Write-Log "claude check error - $($_.Exception.Message)"
}
