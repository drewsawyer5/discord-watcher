"""
discord_client.py — Discord REST API wrapper.

Usage as module:
    from discord_client import send_message
    send_message("hello from Python")

Usage from CLI (for testing or supervisor calls):
    python discord_client.py "your message here"
    python discord_client.py "your message here" <channel_id>
"""

import os
import sys
import requests
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BOT_TOKEN       = os.getenv("DISCORD_BOT_TOKEN", "")
DEFAULT_CHANNEL = os.getenv("GENERAL_CHANNEL_ID", "1474888067893559360")
API_BASE        = "https://discord.com/api/v10"


def send_message(content: str, channel_id: str = DEFAULT_CHANNEL) -> bool:
    if not BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set in .env", file=sys.stderr)
        return False
    resp = requests.post(
        f"{API_BASE}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"content": content},
        timeout=10,
    )
    if resp.ok:
        print(f"Sent: {content!r} | id={resp.json().get('id')}")
        return True
    print(f"ERROR {resp.status_code}: {resp.text}", file=sys.stderr)
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python discord_client.py <message> [channel_id]")
        sys.exit(1)
    msg = sys.argv[1]
    ch  = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CHANNEL
    sys.exit(0 if send_message(msg, ch) else 1)
