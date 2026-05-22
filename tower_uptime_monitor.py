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

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENDPOINT = os.getenv("WHISPER_ENDPOINT", "").strip().rstrip("/")
INTERVAL_SECONDS = int(os.getenv("TOWER_UPTIME_INTERVAL", "60"))
CONNECT_TIMEOUT = float(os.getenv("TOWER_UPTIME_CONNECT_TIMEOUT", "3"))
READ_TIMEOUT = float(os.getenv("TOWER_UPTIME_READ_TIMEOUT", "5"))
LOG_FILE = Path(__file__).parent / "tower_uptime.log"
STATUS_FILE = Path(__file__).parent / "tower_uptime.json"

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


def main() -> int:
    if not ENDPOINT:
        log.error("WHISPER_ENDPOINT not set; refusing to run uptime monitor")
        return 1

    log.info(f"Tower uptime monitor starting endpoint={ENDPOINT} interval={INTERVAL_SECONDS}s")
    last_state: bool | None = None
    total_checks = 0
    total_failures = 0

    while True:
        healthy, latency_ms, error = probe()
        total_checks += 1
        if not healthy:
            total_failures += 1

        if last_state is None:
            # First check after startup — log the initial state explicitly
            log.info(
                f"Initial state: {'UP' if healthy else 'DOWN'} "
                f"latency_ms={latency_ms:.1f}{f' error={error}' if error else ''}"
            )
        elif healthy != last_state:
            if healthy:
                log.warning(f"Tower RECOVERED: now UP latency_ms={latency_ms:.1f}")
            else:
                log.error(f"Tower DOWN: error={error} latency_ms={latency_ms:.1f}")

        last_state = healthy
        write_status(healthy, latency_ms, error, total_checks, total_failures)

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("Tower uptime monitor stopped by keyboard interrupt")
        sys.exit(0)
