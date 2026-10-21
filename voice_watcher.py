import os
import sys
import json
import time
import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone
import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import env_keys
import discord_voice
import slate_beat
try:
    from discord_client import post_discord_message
except Exception:  # pragma: no cover - Discord notify is best-effort
    post_discord_message = None  # type: ignore[assignment]

env_keys.load(  # only the keys this process uses, never the whole shared .env (#387)
    "DISCORD_INBOX", "INBOX_LOG_DIR", "INBOX_MAX_AGE_HOURS", "FALLBACK_NOTIFY_CHANNEL", "FALLBACK_NOTIFY_COOLDOWN",
    "WHISPER_MODEL", "WHISPER_ENDPOINT", "WHISPER_TIMEOUT", "WHISPER_DIARIZE", "WHISPER_LOCAL_PREWARM",
    "SLATE_API_URL", "SLATE_JOBS_TOKEN",
)

_processing_lock = threading.Lock()
_in_progress: set[str] = set()

# Force UTF-8 output on Windows so emoji in log entries don't crash the terminal
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Paths — override with .env vars if needed
INBOX_DIR = Path(os.getenv("DISCORD_INBOX", r"C:\Users\drews\.claude\channels\discord\inbox"))
LOG_BASE = Path(os.getenv("INBOX_LOG_DIR", r"C:\Users\drews\Life Org\Obsidian\7 - MD-AI\00 - Inbox\logs"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_ENDPOINT = os.getenv("WHISPER_ENDPOINT", "").strip().rstrip("/")
WHISPER_TIMEOUT = int(os.getenv("WHISPER_TIMEOUT", "600"))
WHISPER_DIARIZE = os.getenv("WHISPER_DIARIZE", "").strip().lower() in {"1", "true", "yes"}
WHISPER_LOCAL_PREWARM = os.getenv("WHISPER_LOCAL_PREWARM", "1").strip().lower() in {"1", "true", "yes"}
STATUS_FILE = Path(__file__).parent / "status.json"
WATCHER_LOG = Path(__file__).parent / "voice_watcher.log"
SOAK_LOG = Path(__file__).parent / "whisperx_soak.jsonl"
HEARTBEAT_INTERVAL = 300  # seconds (5 minutes)
# SLATE's Services table hears from us every minute; SLATE pushes after 3 min of silence (#347, Comms Service).
SLATE_BEAT_INTERVAL = 60
SLATE_SERVICE = "voice-watcher"
SLATE_ALLOWANCE_S = 90  # the Status page's "up" allowance: one beat plus slack
INBOX_MAX_AGE_HOURS = int(os.getenv("INBOX_MAX_AGE_HOURS", "72"))
CLEANUP_INTERVAL = 3600  # seconds (1 hour)
# Written next to an .ogg while it is being transcribed; the no-rewhisper hook reads it (#351).
MARKER_SUFFIX = ".transcribing"

# Discord notification config — single channel for fallback alerts
FALLBACK_NOTIFY_CHANNEL = os.getenv("FALLBACK_NOTIFY_CHANNEL", "1474888067893559360").strip()
FALLBACK_NOTIFY_COOLDOWN = int(os.getenv("FALLBACK_NOTIFY_COOLDOWN", "300"))  # seconds
_last_fallback_notify_ts = 0.0
_last_via = "tower"  # tracks transition for recovery notifications
_notify_lock = threading.Lock()

log_format = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
root_log = logging.getLogger()
root_log.setLevel(logging.INFO)
root_log.handlers.clear()
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_format)
file_handler = RotatingFileHandler(WATCHER_LOG, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
file_handler.setFormatter(log_format)
root_log.addHandler(stream_handler)
root_log.addHandler(file_handler)
log = logging.getLogger(__name__)

files_transcribed = 0
files_via_local = 0
files_failed = 0


def write_heartbeat():
    tower_state = discord_voice.get_tower_state()
    STATUS_FILE.write_text(
        json.dumps({
            "last_seen": datetime.now().isoformat(timespec="seconds"),
            "files_transcribed": files_transcribed,
            "files_via_local": files_via_local,
            "files_failed": files_failed,
            "watching": str(INBOX_DIR),
            "model": WHISPER_ENDPOINT if WHISPER_ENDPOINT else WHISPER_MODEL,
            "mode": "fallback" if WHISPER_ENDPOINT else "local",
            "current_via": _last_via,
            "tower_healthy": tower_state.get("healthy"),
            "tower_last_error": tower_state.get("last_error"),
            **({"timeout": WHISPER_TIMEOUT, "diarize": WHISPER_DIARIZE} if WHISPER_ENDPOINT else {}),
        }, indent=2),
        encoding="utf-8",
    )


def make_slate_beat():
    """The SLATE client for `voice-watcher` (SLATE_API_URL + SLATE_JOBS_TOKEN), or None when SLATE is not configured."""
    return slate_beat.from_env(SLATE_SERVICE, interval_s=SLATE_ALLOWANCE_S, details=_slate_details)


def _slate_details():
    return {
        "files_transcribed": files_transcribed,
        "files_via_local": files_via_local,
        "files_failed": files_failed,
        "current_via": _last_via,
    }


def heartbeat_loop(slate=None, sleep=time.sleep):
    """Beat SLATE every SLATE_BEAT_INTERVAL and write status.json every HEARTBEAT_INTERVAL.

    SLATE answering 410 (retired) stops the SLATE beat for good; the local status file carries on.
    """
    every = HEARTBEAT_INTERVAL // SLATE_BEAT_INTERVAL
    tick = 0
    while True:
        if tick % every == 0:
            try:
                write_heartbeat()
            except Exception as exc:
                log.warning(f"status.json write failed: {exc}")
        if slate is not None:
            try:
                if slate.beat() == "retired":
                    slate = None
            except Exception as exc:  # slate_beat swallows its own errors; this is belt and braces
                log.warning(f"SLATE beat raised: {exc}")
        tick += 1
        sleep(SLATE_BEAT_INTERVAL)


def cleanup_old_inbox_files():
    """Delete .ogg and .txt files in the inbox older than INBOX_MAX_AGE_HOURS."""
    cutoff = time.time() - INBOX_MAX_AGE_HOURS * 3600
    removed = 0
    for ext in ("*.ogg", "*.txt"):
        for f in INBOX_DIR.glob(ext):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception as e:
                log.warning(f"Could not delete {f.name}: {e}")
    if removed:
        log.info(f"Inbox cleanup: removed {removed} files older than {INBOX_MAX_AGE_HOURS}h")


def cleanup_stale_markers():
    """Remove `.transcribing` markers left by a previous run (#351): nothing is transcribing at startup."""
    for f in INBOX_DIR.glob(f"*{MARKER_SUFFIX}"):
        try:
            f.unlink()
            log.info(f"Removed stale marker {f.name}")
        except Exception as e:
            log.warning(f"Could not delete {f.name}: {e}")


def cleanup_loop():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        cleanup_old_inbox_files()


def _write_soak_row(row: dict):
    SOAK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SOAK_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _notify_fallback(via: str, filename: str, error: str | None) -> None:
    """Post a Discord notice when transcription falls back to local or fails.

    Cooldown'd so a long Tower outage doesn't spam the channel.
    """
    global _last_fallback_notify_ts, _last_via
    if post_discord_message is None or not FALLBACK_NOTIFY_CHANNEL:
        return

    if via == "local" and not WHISPER_ENDPOINT:
        # No Tower configured — local IS the primary path, not a fallback.
        # Stay quiet; only genuine failures below are worth a ping.
        with _notify_lock:
            _last_via = via
        return

    with _notify_lock:
        previous = _last_via
        _last_via = via
        now_ts = time.monotonic()
        should_notify = False
        message = None

        if via == "tower" and previous in {"local", "failed"}:
            message = f"✅ Tower WhisperX back online — switched back to remote (file: `{filename}`)."
            should_notify = True
        elif via == "local" and previous != "local":
            message = (
                f"⚠️ Tower WhisperX unreachable — using local faster-whisper for `{filename}`. "
                f"Reason: {error or 'unknown'}"
            )
            should_notify = True
        elif via == "local" and now_ts - _last_fallback_notify_ts > FALLBACK_NOTIFY_COOLDOWN:
            message = f"⚠️ Still on local faster-whisper. Latest: `{filename}`."
            should_notify = True
        elif via == "failed":
            message = (
                f"🔴 Voice transcription FAILED for `{filename}` — both Tower and local "
                f"refused. Manual review needed.\n```\n{(error or '')[:400]}\n```"
            )
            should_notify = True

        if should_notify and message:
            _last_fallback_notify_ts = now_ts

    if should_notify and message:
        try:
            post_discord_message(message, FALLBACK_NOTIFY_CHANNEL)
        except Exception as exc:
            log.warning(f"Failed to post fallback notification to Discord: {exc}")


def _record_soak(ogg_path: Path, result: dict) -> None:
    """Append one row per transcription attempt to the soak log."""
    try:
        file_size = ogg_path.stat().st_size
    except OSError:
        file_size = None

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filename": ogg_path.name,
        "file_size": file_size,
        "via": result.get("via"),
        "latency_sec": round((result.get("latency_ms") or 0) / 1000.0, 3),
        "char_count": len(result.get("text") or ""),
        "used_endpoint": result.get("used_endpoint"),
        "error": (result.get("error") or "")[:500] or None,
    }
    SOAK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SOAK_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def get_log_path() -> Path:
    now = datetime.now()
    log_path = LOG_BASE / now.strftime("%Y") / now.strftime("%m") / now.strftime("%Y-%m-%d.md")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def append_to_log(transcript: str, source_file: str, via: str = "tower"):
    log_path = get_log_path()
    timestamp = datetime.now().strftime("%H:%M")
    suffix = " ⚠️ via local" if via == "local" else ""
    entry = f"🎤 [{timestamp}] {transcript} *(voice: {source_file}{suffix})*\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)
    log.info(f"Logged to {log_path}")


def process_ogg(ogg_path: Path):
    global files_transcribed, files_via_local, files_failed
    key = os.path.normcase(str(ogg_path.resolve()))
    with _processing_lock:
        if key in _in_progress:
            log.info(f"Already in progress: {ogg_path.name}")
            return
        txt_path = ogg_path.with_suffix(".txt")
        if txt_path.exists():
            log.info(f"Already transcribed: {ogg_path.name}")
            return
        _in_progress.add(key)
    log.info(f"Transcribing: {ogg_path.name}")
    marker = ogg_path.with_suffix(MARKER_SUFFIX)
    try:
        try:
            marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        except Exception as exc:
            log.warning(f"Could not write {marker.name}: {exc}")
        result = discord_voice.transcribe_with_fallback(ogg_path)
        via = result.get("via", "failed")
        latency_ms = result.get("latency_ms", 0)
        _record_soak(ogg_path, result)

        if via == "failed":
            files_failed += 1
            log.error(
                f"Transcription FAILED file={ogg_path.name} latency_ms={latency_ms} "
                f"error={result.get('error')}"
            )
            _notify_fallback("failed", ogg_path.name, result.get("error"))
            return

        transcript = (result.get("text") or "").strip()
        txt_path.write_text(transcript, encoding="utf-8")
        files_transcribed += 1
        if via == "local":
            files_via_local += 1
        log.info(
            f"Transcribed file={ogg_path.name} via={via} latency_ms={latency_ms} "
            f"chars={len(transcript)}"
        )
        append_to_log(transcript, ogg_path.name, via=via)
        _notify_fallback(via, ogg_path.name, result.get("error"))
        write_heartbeat()
    finally:
        try:
            marker.unlink(missing_ok=True)
        except Exception as exc:
            log.warning(f"Could not remove {marker.name}: {exc}")
        with _processing_lock:
            _in_progress.discard(key)


def _handle_event_safely(path: Path) -> None:
    """Process a file event without ever letting an exception escape into watchdog.

    Watchdog's Observer thread can stop dispatching events if a handler raises.
    All exceptions get logged and swallowed here so the Observer keeps running.
    """
    try:
        time.sleep(1)  # ensure file is fully written before reading
        process_ogg(path)
    except Exception as exc:  # pragma: no cover - defensive guard
        log.exception(f"process_ogg crashed for {path.name}: {exc}")


class OggHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".ogg"):
            _handle_event_safely(Path(event.src_path))

    def on_moved(self, event):
        if not event.is_directory and event.dest_path.endswith(".ogg"):
            _handle_event_safely(Path(event.dest_path))


def main():
    log.info(f"Watching {INBOX_DIR} for .ogg files")
    if WHISPER_ENDPOINT:
        log.info(
            f"Transcription mode: fallback (Tower first, local backup). "
            f"endpoint={WHISPER_ENDPOINT} timeout={WHISPER_TIMEOUT} diarize={WHISPER_DIARIZE}"
        )
    else:
        log.info(f"Transcription mode: local-only model={WHISPER_MODEL}")

    if WHISPER_LOCAL_PREWARM:
        try:
            log.info(f"Pre-warming local faster-whisper model: {WHISPER_MODEL}")
            discord_voice.warm_local_model(WHISPER_MODEL)
        except Exception as exc:
            log.warning(f"Local model pre-warm failed (will retry on demand): {exc}")

    write_heartbeat()
    slate = make_slate_beat()
    log.info(f"SLATE beat: {'on, ' + SLATE_SERVICE + ' every ' + str(SLATE_BEAT_INTERVAL) + 's' if slate else 'off (SLATE_API_URL unset)'}")
    threading.Thread(target=heartbeat_loop, kwargs={"slate": slate}, daemon=True).start()
    threading.Thread(target=cleanup_loop, daemon=True).start()

    cleanup_old_inbox_files()
    cleanup_stale_markers()
    for ogg in sorted(INBOX_DIR.glob("*.ogg")):
        _handle_event_safely(ogg)

    observer = Observer()
    observer.schedule(OggHandler(), str(INBOX_DIR), recursive=False)
    observer.start()
    log.info("Watcher running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(5)
            if not observer.is_alive():
                log.error("Observer thread is no longer alive — exiting so supervisor restarts us")
                break
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
