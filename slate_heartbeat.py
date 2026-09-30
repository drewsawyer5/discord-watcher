"""slate_heartbeat.py — register this process as a SLATE Service and heartbeat it (life-org #172).

A daemon thread POSTs ``/api/status/services/<name>/register`` once, then
``/heartbeat`` every INTERVAL_S, with the jobs token. SLATE's stale-Service
alarm then pushes "Service down: <name> on <host>" when the beats stop, and
"Service back" when they resume. A 404 on heartbeat (row retired) re-registers.

Best-effort by construction: every failure is caught and logged once per
outage, the thread is a daemon, and nothing here can stop the host process.
The same file lives in discord-watcher and pa-bot.

Config (.env): SLATE_API_URL (e.g. http://100.113.221.98:8080/api), SLATE_JOBS_TOKEN.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable

import requests

log = logging.getLogger(__name__)

INTERVAL_S = 15.0
TIMEOUT_S = (5, 10)


def start(name: str) -> threading.Thread | None:
    """Start the heartbeat thread; None (and a log line) when SLATE_API_URL is unset or anything goes wrong."""
    try:
        base = os.getenv("SLATE_API_URL", "").strip().rstrip("/")
        if not base:
            log.info("SLATE heartbeat: off (SLATE_API_URL unset)")
            return None
        token = os.getenv("SLATE_JOBS_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        thread = threading.Thread(target=run, args=(name, base, headers), name="slate-heartbeat", daemon=True)
        thread.start()
        log.info(f"SLATE heartbeat: on ({name} -> {base}, every {INTERVAL_S:.0f}s)")
        return thread
    except Exception as exc:  # never take the host process down
        log.warning(f"SLATE heartbeat: could not start: {exc}")
        return None


def run(
    name: str,
    base: str,
    headers: dict[str, str],
    *,
    interval: float = INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
    stop: threading.Event | None = None,
    post: Callable[..., requests.Response] = requests.post,
) -> None:
    """Register, then beat forever (or until `stop`). Swallows every error; logs once per outage."""
    registered = False
    failing = False
    url = f"{base}/status/services/{name}"
    while stop is None or not stop.is_set():
        try:
            if not registered:
                resp = post(f"{url}/register", json={"hostname": socket.gethostname(), "pid": os.getpid()}, headers=headers, timeout=TIMEOUT_S)
                resp.raise_for_status()
                registered = True
            resp = post(f"{url}/heartbeat", json={"details": {}}, headers=headers, timeout=TIMEOUT_S)
            if resp.status_code == 404:
                registered = False  # the row was retired: register again on the next tick
            else:
                resp.raise_for_status()
            if failing:
                log.info("SLATE heartbeat: recovered")
                failing = False
        except Exception as exc:
            if not failing:
                log.warning(f"SLATE heartbeat failing (will keep trying quietly): {exc}")
                failing = True
        sleep(interval)
