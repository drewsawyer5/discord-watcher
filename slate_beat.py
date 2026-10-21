"""slate_beat.py - beat a Comms Service into SLATE's Services table (life-org #347).

SLATE pushes Drew's phone when a Comms Service goes quiet (voice-watcher after 3 min, break-glass after 8 min).
Two callers:

- voice_watcher.py runs `SlateBeat.run` in a daemon thread, one beat every 60 s.
- supervisor.ps1 runs `python slate_beat.py break-glass --interval-s 420 --pid <claude pid>` once per 5-min run,
  only while the Discord Claude session is alive.

The protocol (SLATE #346): POST /status/services/<name>/heartbeat. 404 = SLATE doesn't know the name yet, so
register and beat again. 410 = the name is retired (a Tombstone): stop for good, never register. Every other
failure is logged and swallowed; nothing here may take the host process down.

Config (two keys of Drew_code/.env, read by env_keys): SLATE_API_URL (e.g. https://sawyer-industries.com/api), SLATE_JOBS_TOKEN.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TIMEOUT_S = (5, 10)
# Cloudflare blocks the default python-requests agent (error 1010), so every call names itself.
USER_AGENT = "discord-watcher-slate-beat/1.0 (+life-org#347)"
EXIT_OK, EXIT_ERROR, EXIT_RETIRED = 0, 1, 3
STATE_DIR = Path(__file__).parent


class SlateBeat:
    """One Comms Service's heartbeat client. `beat()` returns "ok", "registered", "retired" or "error"."""

    def __init__(
        self,
        name: str,
        base: str,
        token: str,
        *,
        interval_s: float,
        details: Callable[[], Mapping[str, object]] = dict,
        post: Callable[..., requests.Response] = requests.post,
        hostname: str | None = None,
        pid: int | None = None,
    ) -> None:
        self.name = name
        self.base = base.rstrip("/")
        self.interval_s = interval_s
        self.retired = False
        self._details = details
        self._post = post
        self._hostname = hostname or socket.gethostname()
        self._pid = pid if pid is not None else os.getpid()
        self._headers = {"User-Agent": USER_AGENT}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._failing = False

    def _url(self, action: str) -> str:
        return f"{self.base}/status/services/{self.name}/{action}"

    def _body(self) -> dict[str, object]:
        extra: Mapping[str, object] = {}
        try:
            extra = self._details()
        except Exception as exc:  # a broken details source must not stop the beat
            log.warning(f"SLATE beat {self.name}: details failed: {exc}")
        # interval_s is the Status page's allowance (SLATE's `stale_after`); the push threshold lives in SLATE.
        return {"details": {"interval_s": self.interval_s, "kind": self.name, **extra}}

    def _retire(self) -> str:
        self.retired = True
        log.warning(f"SLATE beat {self.name}: SLATE says this Service is retired (410); beating stops for good")
        return "retired"

    def beat(self) -> str:
        """Send one heartbeat, registering first when SLATE has never heard of the name."""
        if self.retired:
            return "retired"
        try:
            resp = self._post(self._url("heartbeat"), json=self._body(), headers=self._headers, timeout=TIMEOUT_S)
            outcome = "ok"
            if resp.status_code == 404:
                reg = self._post(
                    self._url("register"),
                    json={"hostname": self._hostname, "pid": self._pid},
                    headers=self._headers,
                    timeout=TIMEOUT_S,
                )
                if reg.status_code == 410:
                    return self._retire()
                reg.raise_for_status()
                resp = self._post(self._url("heartbeat"), json=self._body(), headers=self._headers, timeout=TIMEOUT_S)
                outcome = "registered"
            if resp.status_code == 410:
                return self._retire()
            resp.raise_for_status()
        except Exception as exc:  # network trouble or a 5xx: report, never raise
            if not self._failing:
                log.warning(f"SLATE beat {self.name} failing (will keep trying quietly): {exc}")
                self._failing = True
            return "error"
        if self._failing:
            log.info(f"SLATE beat {self.name}: recovered")
            self._failing = False
        return outcome

    def run(self, *, every_s: float, sleep: Callable[[float], None] = time.sleep) -> None:
        """Beat every `every_s` until SLATE retires the name. Meant for a daemon thread."""
        while self.beat() != "retired":
            sleep(every_s)


def from_env(name: str, *, interval_s: float, env: Mapping[str, str] | None = None, **kw: object) -> SlateBeat | None:
    """A client configured from SLATE_API_URL + SLATE_JOBS_TOKEN, or None when SLATE_API_URL is unset."""
    env = os.environ if env is None else env
    base = env.get("SLATE_API_URL", "").strip().rstrip("/")
    if not base:
        return None
    return SlateBeat(name, base, env.get("SLATE_JOBS_TOKEN", "").strip(), interval_s=interval_s, **kw)  # type: ignore[arg-type]


def beat_once(beat: SlateBeat, *, state_dir: Path = STATE_DIR) -> int:
    """One beat for a run-once caller. A 410 leaves `<name>.retired` in `state_dir` so later runs send nothing."""
    marker = state_dir / f"{beat.name}.retired"
    if marker.exists():
        return EXIT_RETIRED
    outcome = beat.beat()
    if outcome == "retired":
        try:
            marker.write_text("SLATE answered 410: this Service is retired. Delete this file to beat again.\n")
        except OSError as exc:
            log.warning(f"could not write {marker}: {exc}")
        return EXIT_RETIRED
    return EXIT_ERROR if outcome == "error" else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """CLI: one beat for `name` (the supervisor's Break-glass beat). Exit 0 ok, 1 error, 3 retired."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--interval-s", type=float, required=True)
    parser.add_argument("--pid", type=int, default=None, help="the watched process's pid, sent on register")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import env_keys

    env_keys.load("SLATE_API_URL", "SLATE_JOBS_TOKEN")  # only these, never the whole shared .env (#387)
    beat = from_env(args.name, interval_s=args.interval_s, pid=args.pid)
    if beat is None:
        log.warning("SLATE_API_URL unset: no beat sent")
        return EXIT_ERROR
    code = beat_once(beat)
    print({EXIT_OK: "ok", EXIT_ERROR: "error", EXIT_RETIRED: "retired"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
