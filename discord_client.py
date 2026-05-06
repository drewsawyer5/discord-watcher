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
import logging
import requests
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BOT_TOKEN       = os.getenv("DISCORD_BOT_TOKEN", "")
INGEST_CHANNEL_ID = os.getenv("INGEST_CHANNEL_ID", "1474888067893559360")
DEFAULT_CHANNEL = os.getenv("GENERAL_CHANNEL_ID", "")
API_BASE        = "https://discord.com/api/v10"
log = logging.getLogger(__name__)


def _discord_headers() -> dict:
    return {"Authorization": f"Bot {BOT_TOKEN}"}


def discord_get(endpoint: str) -> list | dict:
    resp = requests.get(
        f"{API_BASE}{endpoint}",
        headers=_discord_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def discord_post(endpoint: str, payload: dict) -> dict:
    resp = requests.post(
        f"{API_BASE}{endpoint}",
        headers={**_discord_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def discord_get_message(message_id: str) -> dict | None:
    """Fetch a single message by ID for retry processing."""
    try:
        return discord_get(f"/channels/{INGEST_CHANNEL_ID}/messages/{message_id}")
    except Exception as e:
        log.warning(f"Could not fetch message {message_id}: {e}")
        return None


def post_discord_reply(content: str, reference_message_id: str):
    discord_post(
        f"/channels/{INGEST_CHANNEL_ID}/messages",
        {
            "content": content,
            "message_reference": {"message_id": reference_message_id},
        },
    )


def post_discord_message(content: str, channel_id: str | None = None):
    """Post a standalone message to a Discord channel (no reply reference)."""
    discord_post(f"/channels/{channel_id or INGEST_CHANNEL_ID}/messages", {"content": content})


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
