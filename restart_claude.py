"""
restart_claude.py — kills Claude, triggers watchdog relaunch, posts start-session to Discord.
Called by the /restart-session skill as a detached background process.

Usage: python restart_claude.py
"""
import os
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

# Shared Drew_code/.env (one level up from this repo)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)

WATCHDOG_PATH    = Path(__file__).parent / "watchdog.ps1"
LOG_PATH         = Path(__file__).parent / "restart.log"

WAIT_BEFORE_KILL = 12  # seconds for Claude to finish its Discord post before kill
WAIT_AFTER_KILL  = 3   # seconds before calling watchdog


def log(msg: str):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def main():
    log("restart_claude.py started")

    log(f"waiting {WAIT_BEFORE_KILL}s for Claude to finish Discord post")
    time.sleep(WAIT_BEFORE_KILL)

    result = os.system("taskkill /F /IM claude.exe")
    log(f"taskkill exit code: {result}")

    log(f"waiting {WAIT_AFTER_KILL}s before watchdog call")
    time.sleep(WAIT_AFTER_KILL)

    log(f"calling watchdog: {WATCHDOG_PATH}")
    proc = subprocess.run(
        ["powershell", "-NonInteractive", "-File", str(WATCHDOG_PATH)],
        capture_output=True,
        text=True,
    )
    log(f"watchdog exit code: {proc.returncode}")
    if proc.stdout.strip():
        log(f"watchdog stdout: {proc.stdout.strip()}")
    if proc.stderr.strip():
        log(f"watchdog stderr: {proc.stderr.strip()}")

    log("done — watchdog will launch Claude with /start-session as initial prompt")


if __name__ == "__main__":
    main()
