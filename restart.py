"""
restart.py — kills Claude, triggers supervisor relaunch, posts start-session to Discord.
Called by the /restart-session skill as a detached background process.

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

WAIT_BEFORE_KILL  = 12   # seconds for Claude to finish its Discord post before kill
KILL_VERIFY_TIMEOUT = 15  # seconds to wait for claude.exe to disappear after taskkill
PROCESS_NAME      = "claude.exe"


def log(msg: str):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def is_process_running(name: str) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
        capture_output=True, text=True,
    )
    return name.lower() in result.stdout.lower()


def wait_for_death(name: str, timeout: int) -> bool:
    """Poll until process is gone. Returns True if confirmed dead within timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_running(name):
            return True
        time.sleep(1)
    return False


def main():
    log("restart.py started")

    if not is_process_running(PROCESS_NAME):
        log(f"WARNING: {PROCESS_NAME} not found before kill — nothing to restart")
        sys.exit(1)

    log(f"confirmed {PROCESS_NAME} is running — waiting {WAIT_BEFORE_KILL}s for Discord post")
    time.sleep(WAIT_BEFORE_KILL)

    result = os.system(f"taskkill /F /IM {PROCESS_NAME}")
    log(f"taskkill exit code: {result}")

    log(f"verifying {PROCESS_NAME} is dead (up to {KILL_VERIFY_TIMEOUT}s)...")
    if not wait_for_death(PROCESS_NAME, KILL_VERIFY_TIMEOUT):
        log(f"ERROR: {PROCESS_NAME} still running after {KILL_VERIFY_TIMEOUT}s — aborting restart")
        sys.exit(1)
    log(f"{PROCESS_NAME} confirmed dead — proceeding to supervisor")

    log(f"calling supervisor: {WATCHDOG_PATH}")
    proc = subprocess.run(
        ["powershell", "-NonInteractive", "-File", str(WATCHDOG_PATH)],
        capture_output=True,
        text=True,
    )
    log(f"supervisor exit code: {proc.returncode}")
    if proc.stdout.strip():
        log(f"supervisor stdout: {proc.stdout.strip()}")
    if proc.stderr.strip():
        log(f"supervisor stderr: {proc.stderr.strip()}")

    log("done — supervisor will launch Claude")


if __name__ == "__main__":
    main()
