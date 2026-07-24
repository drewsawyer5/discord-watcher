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

# --- process_ingest.py: MOVED TO NUC (2026-06-22, Step 2 dedup) ---
# Ingest now runs SOLELY on the NUC (discord-watcher.service). A6 must NOT
# poll the ingest channel too — both polling 1474888214639546631 caused every
# #inbox message to be double-processed into the shared (Syncthing) vault.
# Do NOT re-enable here. See [[Monitoring & Logs (NUC Consolidation)]] Step 2.
Write-Log "process_ingest.py SKIPPED on A6 - owned by NUC (dedup 2026-06-22)"

# --- tower_uptime_monitor.py: DISABLED (2026-07-24, Tower hardware dead) ---
# The Tower died completely; WHISPER_ENDPOINT is commented out in Drew_code/.env
# and the monitor refuses to start without it (5-min refuse-and-exit loop here).
# Re-enable this block when a WhisperX host exists again (Tower rebuild).
# See vault: 5 - Storage/05 - Raw Ingests/Notes/2026-07-24-context-tower-death-inventory.md
Write-Log "tower_uptime_monitor.py SKIPPED - Tower dead 2026-07-24, no WHISPER_ENDPOINT"

# --- Check Claude --channels session ---
try {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*--channels*" }
    if ($procs) {
        $procId = ($procs | Select-Object -First 1).ProcessId
        Write-Log "claude OK - pid $procId"
    } else {
        Write-Log "claude NOT running - restarting via interactive task"
        # Launch through the Interactive "Discord Watcher" task, NOT Start-Process.
        # This supervisor runs under an S4U (non-interactive, session-0) principal,
        # so any window it spawns directly would be invisible. schtasks /Run hands
        # the launch to the Task Scheduler service, which runs "Discord Watcher" in
        # its configured Interactive session -> visible window on the logged-in desktop.
        schtasks /Run /TN "Discord Watcher" | Out-Null
        Write-Log "claude start issued via interactive task 'Discord Watcher' (visible window; rc=$LASTEXITCODE)"
    }
} catch {
    Write-Log "claude check error - $($_.Exception.Message)"
}
