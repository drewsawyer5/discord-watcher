from __future__ import annotations

import re


ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")
ANSI_SINGLE_RE = re.compile(r"\x1b[@-Z\\-_]")
WORKING_LINE_RE = re.compile(r"^[•◦]\s+.*(?:esc to interrupt|Working|Booting MCP server).*$", re.IGNORECASE)
READY_HINTS = (
    "Use /skills to list available skills",
    "Improve documentation in @filename",
    "Implement {feature}",
)
PROMPT_MARKERS = ("›", "â€º")
WORKING_MARKERS = ("esc to interrupt", "Booting MCP server")
READY_STATUS_MARKERS = (
    "gpt-5.5 default",
    "gpt-5.4 default",
    "gpt-5.3 default",
)
UI_LINE_MARKERS = (
    "OpenAI Codex",
    "YOLO mode",
    "/model to change",
    "Tip:",
    "PROMPT:",
    "===== ",
    "directory:",
    "gpt-5.5 default",
    "tab to queue message",
    "context left",
    "Heads up, you have less than",
    "Run /status",
    "esc to interr",
    "breakdown.",
    "Approaching rate limits",
    "Switch to gpt-5.4-mini",
    "Press enter to confirm",
)
UI_TRUNCATE_MARKERS = (
    "Approaching rate limits",
    "Switch to gpt-5.4-mini",
    "Press enter to confirm",
)


def strip_terminal_controls(raw: str) -> str:
    """Remove common terminal control sequences from Codex TUI output.

    Args:
        raw: Raw PTY output.

    Returns:
        Text with ANSI/OSC escape sequences removed and line endings normalized.
    """
    text = ANSI_OSC_RE.sub("", raw)
    text = ANSI_CSI_RE.sub("", text)
    text = ANSI_SINGLE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def is_ready_screen(raw: str) -> bool:
    """Return true when Codex appears ready for another prompt.

    Args:
        raw: Raw or cleaned terminal text.

    Returns:
        True if the screen contains the Codex input prompt hints and no active working marker.
    """
    text = strip_terminal_controls(raw)
    latest_ready = max(
        [text.rfind(hint) for hint in READY_HINTS + READY_STATUS_MARKERS],
        default=-1,
    )
    latest_prompt = max([text.rfind(marker) for marker in PROMPT_MARKERS], default=-1)
    latest_working = max([text.rfind(marker) for marker in WORKING_MARKERS], default=-1)
    return latest_ready >= 0 and latest_prompt >= 0 and latest_ready > latest_working


def extract_turn_text(raw: str) -> str:
    """Extract a readable rough turn slice from raw Codex TUI output.

    Args:
        raw: Raw PTY output collected after prompt submission.

    Returns:
        Minimally cleaned text suitable for an MVP Discord reply.
    """
    cleaned = strip_terminal_controls(raw)
    lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = _clean_output_line(line)
        if not stripped:
            continue
        if any(char in stripped for char in "╭╮╰╯─│"):
            continue
        if stripped.startswith(PROMPT_MARKERS):
            continue
        if WORKING_LINE_RE.match(stripped):
            continue
        if "Working" in stripped and "esc to interrupt" in stripped:
            continue
        if any(marker in stripped for marker in UI_LINE_MARKERS):
            continue
        if any(hint in stripped for hint in READY_HINTS):
            continue
        lines.append(stripped)
    return "\n".join(_dedupe_consecutive(lines)).strip()


def _clean_output_line(line: str) -> str:
    stripped = line.strip().strip("â€¢â—¦•◦").strip()
    for marker in UI_TRUNCATE_MARKERS:
        if marker in stripped:
            stripped = stripped.split(marker, 1)[0].strip()
    return stripped


def _dedupe_consecutive(lines: list[str]) -> list[str]:
    deduped: list[str] = []
    for line in lines:
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)
    return deduped
