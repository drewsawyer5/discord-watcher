"""
session_start.py — Posts a session-started summary to Discord.

Reads the last 'Worked on' items and 'Planned next' from session-context.md,
then sends a brief Discord message so Drew sees what was last active.

Usage:
    python session_start.py
    python session_start.py --dry-run    (print message, don't post)
"""

import sys
import re
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from discord_client import send_message

SESSION_CONTEXT = Path(r"C:\Users\drews\Life Org\Obsidian\7 - MD-AI\session-context.md")
LAST_N_ITEMS = 3


SECTION_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]*:$")


def parse_section(text: str, header: str) -> list[str]:
    """Return bullet lines under `header:` until the next section header or ## heading."""
    lines, capturing = [], False
    for line in text.splitlines():
        if re.match(rf"^{re.escape(header)}:", line, re.IGNORECASE):
            capturing = True
            continue
        if capturing:
            stripped = line.strip()
            if line.startswith("##") or SECTION_HEADER.match(line):
                break
            if stripped.startswith("- "):
                lines.append(stripped[2:])
    return lines


def parse_planned_next(text: str) -> str:
    """Return the primary goal line (> ⭐ ...) from Planned next section."""
    in_section = False
    for line in text.splitlines():
        if re.match(r"^Planned next:", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if stripped.startswith(">"):
                return stripped.lstrip(">").strip()
            if stripped.startswith("##"):
                break
    return ""


def build_message(worked_on: list[str], planned_next: str) -> str:
    parts = ["🟢 **Session started.**"]

    if worked_on:
        recent = worked_on[-LAST_N_ITEMS:]
        items = "\n".join(f"• {item}" for item in recent)
        parts.append(f"\n**Last worked on:**\n{items}")
    else:
        parts.append("\n_No recent work logged._")

    if planned_next:
        parts.append(f"\n**Planned next:** {planned_next}")

    parts.append("\nAnything to pick up, or new direction?")
    return "\n".join(parts)


def main():
    dry_run = "--dry-run" in sys.argv

    if not SESSION_CONTEXT.exists():
        print(f"ERROR: session-context.md not found at {SESSION_CONTEXT}", file=sys.stderr)
        sys.exit(1)

    text = SESSION_CONTEXT.read_text(encoding="utf-8")
    worked_on = parse_section(text, "Worked on")
    planned_next = parse_planned_next(text)
    message = build_message(worked_on, planned_next)

    if dry_run:
        sys.stdout.reconfigure(encoding="utf-8")
        print("--- DRY RUN ---")
        print(message)
        return

    success = send_message(message)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
