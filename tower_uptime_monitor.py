"""tower_uptime_monitor.py — external Tower /health watcher.

Runs on A6, independent of voice_watcher. Polls Tower WhisperX every N seconds
and logs every state transition (up <-> down) to ``tower_uptime.log`` with the
exact wall-clock timestamp. Also writes ``tower_uptime.json`` as a heartbeat so
supervisor can confirm the monitor itself is alive.

This is forensic instrumentation: when Tower dies overnight we want a precise
local timestamp of the failure independent of Tower's own logs (which usually
stop being written several seconds before the actual hang).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    from discord_client import post_discord_message
except Exception:  # pragma: no cover - notify is best-effort
    post_discord_message = None  # type: ignore[assignment]

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENDPOINT = os.getenv("WHISPER_ENDPOINT", "").strip().rstrip("/")
INTERVAL_SECONDS = int(os.getenv("TOWER_UPTIME_INTERVAL", "60"))
CONNECT_TIMEOUT = float(os.getenv("TOWER_UPTIME_CONNECT_TIMEOUT", "3"))
READ_TIMEOUT = float(os.getenv("TOWER_UPTIME_READ_TIMEOUT", "5"))
LOG_FILE = Path(__file__).parent / "tower_uptime.log"
STATUS_FILE = Path(__file__).parent / "tower_uptime.json"

# Discord notification config. Only notify on a *sustained* outage so 1-min
# blips (WhisperX briefly slow) don't spam. Default: notify after 5 consecutive
# failed probes (5 min at 60s interval).
NOTIFY_CHANNEL = os.getenv("TOWER_UPTIME_NOTIFY_CHANNEL", "1474888067893559360").strip()
SUSTAINED_DOWN_THRESHOLD = int(os.getenv("TOWER_UPTIME_DOWN_THRESHOLD", "5"))

log_format = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
root_log = logging.getLogger()
root_log.setLevel(logging.INFO)
root_log.handlers.clear()
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_format)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
file_handler.setFormatter(log_format)
root_log.addHandler(stream_handler)
root_log.addHandler(file_handler)
log = logging.getLogger(__name__)


def probe() -> tuple[bool, float, str | None]:
    """Probe Tower /health. Returns (healthy, latency_ms, error_message)."""
    start = time.monotonic()
    try:
        resp = requests.get(f"{ENDPOINT}/health", timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        latency_ms = (time.monotonic() - start) * 1000.0
        if resp.status_code == 200:
            return True, latency_ms, None
        return False, latency_ms, f"HTTP {resp.status_code}"
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000.0
        return False, latency_ms, f"{type(exc).__name__}: {exc}"


def write_status(healthy: bool, latency_ms: float, error: str | None, total_checks: int, total_failures: int) -> None:
    STATUS_FILE.write_text(
        json.dumps(
            {
                "last_check": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "endpoint": ENDPOINT,
                "healthy": healthy,
                "latency_ms": round(latency_ms, 1),
                "error": error,
                "interval_seconds": INTERVAL_SECONDS,
                "total_checks": total_checks,
                "total_failures": total_failures,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def notify_discord(message: str) -> None:
    """Best-effort Discord post; swallow errors so the monitor never dies."""
    if post_discord_message is None or not NOTIFY_CHANNEL:
        return
    try:
        post_discord_message(message, NOTIFY_CHANNEL)
        log.info(f"Discord notify sent: {message[:80]}")
    except Exception as exc:
        log.warning(f"Discord notify failed: {exc}")


def main() -> int:
    if not ENDPOINT:
        log.error("WHISPER_ENDPOINT not set; refusing to run uptime monitor")
        return 1

    log.info(f"Tower uptime monitor starting endpoint={ENDPOINT} interval={INTERVAL_SECONDS}s")
    last_state: bool | None = None
    total_checks = 0
    total_failures = 0
    consecutive_down = 0
    down_notified = False
    down_since_iso: str | None = None

    while True:
        healthy, latency_ms, error = probe()
        total_checks += 1
        if not healthy:
            total_failures += 1
            consecutive_down += 1
        else:
            consecutive_down = 0

        if last_state is None:
            log.info(
                f"Initial state: {'UP' if healthy else 'DOWN'} "
                f"latency_ms={latency_ms:.1f}{f' error={error}' if error else ''}"
            )
        elif healthy != last_state:
            if healthy:
                log.warning(f"Tower RECOVERED: now UP latency_ms={latency_ms:.1f}")
                if down_notified:
                    notify_discord(
                        f"✅ **Tower recovered** at {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z "
                        f"(was down since {down_since_iso}Z). WhisperX is back."
                    )
                down_notified = False
                down_since_iso = None
            else:
                log.error(f"Tower DOWN: error={error} latency_ms={latency_ms:.1f}")
                down_since_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Sustained-down notification (avoid spam from 1-min blips)
        if (
            not healthy
            and consecutive_down >= SUSTAINED_DOWN_THRESHOLD
            and not down_notified
        ):
            mins = consecutive_down * INTERVAL_SECONDS // 60
            notify_discord(
                f"🔴 **Tower DOWN ~{mins} min** (since {down_since_iso}Z). "
                f"Last error: {error}. Voice fallback is handling transcriptions locally."
            )
            down_notified = True

        last_state = healthy
        write_status(healthy, latency_ms, error, total_checks, total_failures)

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("Tower uptime monitor stopped by keyboard interrupt")
        sys.exit(0)
