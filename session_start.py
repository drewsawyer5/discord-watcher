"""
session_start.py — Posts a session-started ping to Discord.

Session state lives in git/GitHub now (life-org-projects #96): the
start-session skill pulls recent issue activity itself, so this script no
longer reads a handoff file — it just tells Drew a session is spinning up.

Usage:
    python session_start.py
    python session_start.py --dry-run    (print message, don't post)
"""

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from discord_client import send_message

MESSAGE = "🟢 **Claude session starting** — pulling recent activity, summary incoming."


def main():
    dry_run = "--dry-run" in sys.argv

    # Automated one-shot runs (digest / overnight-core / overnight-kickoff,
    # tagged PA_AUTOMATED=1 in their Task Scheduler commands) are not real
    # interactive sessions — skip the "session started" post for them. The
    # always-on watcher's real (re)starts are untagged and still post.
    if os.getenv("PA_AUTOMATED") and not dry_run:
        sys.exit(0)

    if dry_run:
        print("--- DRY RUN ---")
        print(MESSAGE)
        return

    success = send_message(MESSAGE)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
