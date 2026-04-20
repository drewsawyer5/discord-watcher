"""
restart_claude.py — kills Claude, triggers watchdog relaunch, posts start-session to Discord.
Called by the /restart-session skill as a detached background process.

Usage: python restart_claude.py
"""
import os
import subprocess
import time
import requests
from pathlib import Path

from dotenv import load_dotenv

# Shared Drew_code/.env (one level up from this repo)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)

WATCHDOG_PATH     = Path(__file__).parent / "watchdog.ps1"
LOG_PATH          = Path(__file__).parent / "restart.log"

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CHANNEL_ID        = os.getenv("INGEST_CHANNEL_ID", "1474888067893559360")
DISCORD_API_BASE  = "https://discord.com/api/v10"

WAIT_AFTER_KILL      = 3   # seconds before calling watchdog
WAIT_FOR_CLAUDE_BOOT = 25  # seconds for Claude to start + MCP to connect


def log(msg: str):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def post_discord_message(content: str):
    if not DISCORD_BOT_TOKEN:
        log("ERROR: DISCORD_BOT_TOKEN not set — cannot post start-session")
        return
    resp = requests.post(
        f"{DISCORD_API_BASE}/channels/{CHANNEL_ID}/messages",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"content": content},
        timeout=10,
    )
    resp.raise_for_status()
    log(f"discord post OK — status {resp.status_code}")


def main():
    log("restart_claude.py started")

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

    log(f"waiting {WAIT_FOR_CLAUDE_BOOT}s for Claude to boot")
    time.sleep(WAIT_FOR_CLAUDE_BOOT)

    log("posting start-session to Discord")
    try:
        post_discord_message("/start-session")
        log("done")
    except Exception as e:
        log(f"ERROR posting to Discord: {e}")


if __name__ == "__main__":
    main()
