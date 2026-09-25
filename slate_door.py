"""slate_door.py — the #inbox lane as a thin client of SLATE's knowledge door (#161, ADR 0012/0013).

Every drop process_ingest.py handles (URL, text, PDF, image, voice) is ALSO
posted to ``POST /api/knowledge/sources`` with the service token, origin
``ingest-lane``, so the wiki filing and the SLATE Source land from one drop
until the Discord lane retires at P4. Best-effort by design: SLATE down or
refusing never blocks the wiki path — the capture is logged and skipped.

Config (.env): SLATE_API_URL (e.g. http://100.113.221.98:8080/api — A6 over
Tailscale from the NUC), SLATE_JOBS_TOKEN (service token; loopback on A6 needs none).
"""

from __future__ import annotations

import logging
import mimetypes
import os

import requests

log = logging.getLogger(__name__)

ORIGIN = "ingest-lane"
TIMEOUT_S = 60


def configured() -> bool:
    """True when a SLATE API URL is set — the lane stays wiki-only without one."""
    return bool(os.getenv("SLATE_API_URL", "").strip())


def _base_url() -> str:
    return os.getenv("SLATE_API_URL", "").strip().rstrip("/")


def _headers() -> dict[str, str]:
    token = os.getenv("SLATE_JOBS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _post(*, json_body: dict | None = None, files: dict | None = None, data: dict | None = None) -> dict | None:
    if not configured():
        return None
    url = f"{_base_url()}/knowledge/sources"
    try:
        resp = requests.post(url, json=json_body, files=files, data=data, headers=_headers(), timeout=TIMEOUT_S)
    except requests.RequestException as e:
        log.warning(f"SLATE door unreachable ({url}): {e}")
        return None
    if resp.status_code not in (200, 201):
        log.warning(f"SLATE door refused ({resp.status_code}): {resp.text[:200]}")
        return None
    try:
        source = resp.json()
    except ValueError:
        log.warning("SLATE door returned non-JSON")
        return None
    log.info(f"  [slate] {describe(source)}")
    return source


def capture_url(url: str, name: str | None = None) -> dict | None:
    """A URL becomes a `url` Source (SLATE fetches and stores the snapshot itself)."""
    body: dict = {"url": url, "origin": ORIGIN}
    if name:
        body["name"] = name[:300]
    return _post(json_body=body)


def capture_text(text: str, name: str | None = None, kind: str | None = None) -> dict | None:
    """Pasted text or a transcript becomes a `note` Source."""
    if not text or not text.strip():
        return None
    body: dict = {"text": text, "origin": ORIGIN}
    if name:
        body["name"] = name[:300]
    if kind:
        body["kind"] = kind
    return _post(json_body=body)


def capture_file(filename: str, data: bytes, mime: str | None = None, kind: str | None = None) -> dict | None:
    """Bytes (PDF, image, audio) become a Source of the detected kind; the door dedups by content hash."""
    if not data:
        return None
    mime = mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    fields = {"origin": ORIGIN}
    if kind:
        fields["kind"] = kind
    return _post(files={"file": (filename, data, mime)}, data=fields)


def describe(source: dict | None) -> str:
    """One short clause for logs and the Discord reply: `SLATE source #7` or the duplicate it points at."""
    if not source:
        return ""
    if source.get("duplicate_of") is not None:
        return f"SLATE source #{source['duplicate_of']} (already captured)"
    return f"SLATE source #{source.get('id')}"


def reply_suffix(source: dict | None) -> str:
    """What to append to the lane's Discord reply so Drew sees the Source id beside the wiki page."""
    text = describe(source)
    return f"\n**SLATE:** {text}" if text else ""
