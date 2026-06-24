"""
restart.py — kills Claude, then triggers the interactive "Discord Watcher"
scheduled task to relaunch it in a VISIBLE window on the logged-in desktop.
Called by the /restart-session skill as a detached background process.

Visibility note: the relaunch goes through `schtasks /Run`, which hands the
launch to the Task Scheduler service. That service runs the task in its own
configured (Interactive) session, so the Claude window appears on A6's desktop
regardless of how hidden/non-interactive THIS process is. The same call works
when triggered remotely from the NUC over SSH (ssh A6 schtasks /Run /TN ...).

Requires an active interactive logon session on A6 — a window cannot render on
a desktop that does not exist.

Usage: python restart.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Shared Drew_code/.env (one level up from this repo)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)

WATCHDOG_PATH    = Path(__file__).parent / "supervisor.ps1"
LOG_PATH         = Path(__file__).parent / "restart.log"
# Interactive scheduled task that launches Claude in a visible window.
# Triggered via `schtasks /Run` so the window lands in the task's own
# (Interactive) desktop session, not in this process's hidden station.
LAUNCH_TASK      = "Discord Watcher"

WAIT_BEFORE_KILL  = 12   # seconds for Claude to finish its Discord post before kill
KILL_VERIFY_TIMEOUT = 15  # seconds to wait for a PID to disappear after taskkill
KILL_RETRIES      = 1     # extra kill attempts for any survivor before giving up
PROCESS_NAME      = "claude.exe"
# Where to shout if the kill fails — the Lane-1/Claude channel Drew watches.
ALERT_CHANNEL     = "1474888067893559360"


def log(msg: str):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def alert(msg: str):
    """Post a loud failure notice to Discord. Never raises — a failed alert must
    not mask the underlying restart failure (which is already logged)."""
    try:
        from discord_client import post_discord_message
        post_discord_message(f"⚠️ restart.py: {msg}", ALERT_CHANNEL)
        log("posted failure alert to Discord")
    except Exception as e:  # noqa: BLE001 — best-effort notification
        log(f"WARNING: could not post Discord alert: {e}")


def get_pids(name: str) -> list[int]:
    """Return PIDs of all processes matching name (by image name)."""
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        fields = [f.strip().strip('"') for f in line.split('","')]
        if len(fields) >= 2 and fields[0].lower() == name.lower():
            try:
                pids.append(int(fields[1].replace('"', "")))
            except ValueError:
                pass
    return pids


def kill_pid(pid: int) -> int:
    """taskkill a single PID by id; returns the taskkill exit code."""
    return os.system(f"taskkill /F /PID {pid}")


def wait_for_pids_gone(pids: list[int], timeout: int) -> list[int]:
    """Poll until the given PIDs are gone. Returns the list still alive at timeout."""
    deadline = time.time() + timeout
    targets = set(pids)
    while time.time() < deadline:
        alive = set(get_pids(PROCESS_NAME)) & targets
        if not alive:
            return []
        time.sleep(1)
    return sorted(set(get_pids(PROCESS_NAME)) & targets)


def main():
    log("restart.py started")

    pids = get_pids(PROCESS_NAME)
    if not pids:
        log(f"WARNING: {PROCESS_NAME} not found before kill — nothing to restart")
        sys.exit(1)

    log(f"confirmed {PROCESS_NAME} running — PIDs {pids} — waiting {WAIT_BEFORE_KILL}s for Discord post")
    time.sleep(WAIT_BEFORE_KILL)

    # Re-snapshot in case PIDs changed during the wait.
    pids = get_pids(PROCESS_NAME)
    log(f"killing {PROCESS_NAME} by PID: {pids}")
    for pid in pids:
        rc = kill_pid(pid)
        log(f"  taskkill /F /PID {pid} exit code: {rc}")

    survivors = wait_for_pids_gone(pids, KILL_VERIFY_TIMEOUT)
    attempt = 0
    while survivors and attempt < KILL_RETRIES:
        attempt += 1
        log(f"RETRY {attempt}: PIDs still alive after kill: {survivors} — killing again")
        for pid in survivors:
            rc = kill_pid(pid)
            log(f"  retry taskkill /F /PID {pid} exit code: {rc}")
        survivors = wait_for_pids_gone(survivors, KILL_VERIFY_TIMEOUT)

    if survivors:
        msg = (
            f"FAILED to kill {PROCESS_NAME} PIDs {survivors} after {KILL_RETRIES + 1} attempts "
            f"(likely higher-integrity/elevated than this killer). Restart ABORTED — "
            f"no relaunch. Manual kill needed."
        )
        log(f"ERROR: {msg}")
        alert(msg)
        sys.exit(1)

    log(f"{PROCESS_NAME} confirmed dead (all PIDs gone) — triggering interactive launch task")

    # Hand the relaunch to Task Scheduler. Because "Discord Watcher" is an
    # Interactive task, the Task Scheduler service opens the Claude window in
    # the logged-in desktop session — visible — no matter that this process is
    # detached/non-interactive. (Watchers are untouched by the kill above and
    # are kept alive independently by the 5-minute Discord PA Watchdog.)
    log(f"schtasks /Run /TN \"{LAUNCH_TASK}\"")
    proc = subprocess.run(
        ["schtasks", "/Run", "/TN", LAUNCH_TASK],
        capture_output=True,
        text=True,
    )
    log(f"schtasks /Run exit code: {proc.returncode}")
    if proc.stdout.strip():
        log(f"schtasks stdout: {proc.stdout.strip()}")
    if proc.stderr.strip():
        log(f"schtasks stderr: {proc.stderr.strip()}")
    if proc.returncode != 0:
        alert(
            f"schtasks /Run '{LAUNCH_TASK}' failed (rc={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()} — Claude may not have relaunched. "
            f"Is A6 logged in?"
        )
        sys.exit(1)

    log("done — interactive task will launch Claude in a visible window")


if __name__ == "__main__":
    main()
