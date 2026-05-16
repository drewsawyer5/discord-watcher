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
from dotenv import load_dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from faster_whisper import WhisperModel

load_dotenv(Path(__file__).parent.parent / ".env")

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
STATUS_FILE = Path(__file__).parent / "status.json"
WATCHER_LOG = Path(__file__).parent / "voice_watcher.log"
SOAK_LOG = Path(__file__).parent / "whisperx_soak.jsonl"
HEARTBEAT_INTERVAL = 300  # seconds (5 minutes)
INBOX_MAX_AGE_HOURS = int(os.getenv("INBOX_MAX_AGE_HOURS", "72"))
CLEANUP_INTERVAL = 3600  # seconds (1 hour)

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

if WHISPER_ENDPOINT:
    model = None
else:
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

# Seed the decoder with expected names and topic vocabulary so Whisper biases
# toward recognizing them correctly. Keep under ~50 words; only affects first segment.
_INITIAL_PROMPT = (
    "Casual conversation between Drew and Sydney. "
    "Topics: Obsidian, Discord, Claude, Resy, Tasker, AutoInput, Boston, Somerville, "
    "Eevee, TCG Pocket, browser-harness, reservations, wine bar."
)

files_transcribed = 0


def write_heartbeat():
    STATUS_FILE.write_text(
        json.dumps({
            "last_seen": datetime.now().isoformat(timespec="seconds"),
            "files_transcribed": files_transcribed,
            "watching": str(INBOX_DIR),
            "model": WHISPER_ENDPOINT if WHISPER_ENDPOINT else WHISPER_MODEL,
            "mode": "remote" if WHISPER_ENDPOINT else "local",
            **({"timeout": WHISPER_TIMEOUT, "diarize": WHISPER_DIARIZE} if WHISPER_ENDPOINT else {}),
        }, indent=2),
        encoding="utf-8",
    )


def heartbeat_loop():
    while True:
        write_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


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


def cleanup_loop():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        cleanup_old_inbox_files()


def _write_soak_row(row: dict):
    SOAK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SOAK_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _remote_outcome(exc: Exception | None, status_code: int | None) -> str:
    if exc is not None:
        if isinstance(exc, requests.Timeout):
            return "TIMEOUT"
        if isinstance(exc, requests.ConnectionError):
            return "CONNECTION_ERROR"
        if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
            return "MALFORMED_RESPONSE"
        if isinstance(exc, requests.HTTPError) and status_code is not None:
            if 500 <= status_code <= 599:
                return "HTTP_ERROR_5XX"
            if 400 <= status_code <= 499:
                return "HTTP_ERROR_4XX"
        return "UNKNOWN_ERROR"
    if status_code is not None:
        if 500 <= status_code <= 599:
            return "HTTP_ERROR_5XX"
        if 400 <= status_code <= 499:
            return "HTTP_ERROR_4XX"
    return "UNKNOWN_ERROR"


def _transcribe_remote(ogg_path: Path) -> str:
    start = time.monotonic()
    status_code = None
    log.info(f"Remote POST start file={ogg_path.name} endpoint={WHISPER_ENDPOINT} timeout={WHISPER_TIMEOUT} diarize={WHISPER_DIARIZE}")
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filename": ogg_path.name,
        "file_size": ogg_path.stat().st_size,
    }
    try:
        post_data = {"diarize": "true"} if WHISPER_DIARIZE else None
        with ogg_path.open("rb") as f:
            resp = requests.post(
                f"{WHISPER_ENDPOINT}/transcribe",
                files={"file": (ogg_path.name, f, "audio/ogg")},
                data=post_data,
                timeout=WHISPER_TIMEOUT,
            )
        status_code = resp.status_code
        resp.raise_for_status()
        data = resp.json()
        transcript = data.get("text")
        if transcript is None:
            segments = data.get("segments", [])
            transcript = " ".join(seg.get("text", "").strip() for seg in segments).strip()
        if not isinstance(transcript, str):
            raise ValueError("response text is not a string")
        latency = time.monotonic() - start
        row.update({
            "latency_sec": round(latency, 3),
            "http_status": status_code,
            "outcome": "SUCCESS",
            "char_count": len(transcript),
        })
        _write_soak_row(row)
        log.info(f"Remote POST success file={ogg_path.name} status={status_code} latency_sec={latency:.3f} chars={len(transcript)}")
        return transcript
    except Exception as exc:
        latency = time.monotonic() - start
        error_text = str(exc)
        response_text = getattr(locals().get("resp", None), "text", "")
        if response_text:
            error_text = response_text
        row.update({
            "latency_sec": round(latency, 3),
            "http_status": status_code,
            "outcome": _remote_outcome(exc, status_code),
            "error": error_text[:500],
        })
        _write_soak_row(row)
        log.error(f"Remote POST failed file={ogg_path.name} outcome={row['outcome']} status={status_code} latency_sec={latency:.3f} error={row['error']}")
        raise


def _transcribe_local(ogg_path: Path) -> str:
    segments, _ = model.transcribe(
        str(ogg_path),
        language="en",
        condition_on_previous_text=False,
        initial_prompt=_INITIAL_PROMPT,
        vad_filter=True,
        no_speech_threshold=0.4,
        compression_ratio_threshold=2.1,
        log_prob_threshold=-0.8,
        beam_size=5,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe(ogg_path: Path) -> str:
    if WHISPER_ENDPOINT:
        return _transcribe_remote(ogg_path)
    return _transcribe_local(ogg_path)


def get_log_path() -> Path:
    now = datetime.now()
    log_path = LOG_BASE / now.strftime("%Y") / now.strftime("%m") / now.strftime("%Y-%m-%d.md")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def append_to_log(transcript: str, source_file: str):
    log_path = get_log_path()
    timestamp = datetime.now().strftime("%H:%M")
    entry = f"🎤 [{timestamp}] {transcript} *(voice: {source_file})*\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)
    log.info(f"Logged to {log_path}")


def process_ogg(ogg_path: Path):
    global files_transcribed
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
    try:
        transcript = transcribe(ogg_path)
        txt_path.write_text(transcript, encoding="utf-8")
        files_transcribed += 1
        log.info(f"Wrote: {txt_path.name}")
        append_to_log(transcript, ogg_path.name)
        write_heartbeat()  # update immediately after each transcription
    except Exception as e:
        log.error(f"Failed to transcribe {ogg_path.name}: {e}")
        raise
    finally:
        with _processing_lock:
            _in_progress.discard(key)


class OggHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".ogg"):
            time.sleep(1)  # ensure file is fully written before reading
            process_ogg(Path(event.src_path))

    def on_moved(self, event):
        if not event.is_directory and event.dest_path.endswith(".ogg"):
            time.sleep(1)
            process_ogg(Path(event.dest_path))


def main():
    log.info(f"Watching {INBOX_DIR} for .ogg files")
    if WHISPER_ENDPOINT:
        log.info(f"Transcription mode: remote endpoint={WHISPER_ENDPOINT} timeout={WHISPER_TIMEOUT} diarize={WHISPER_DIARIZE}")
    else:
        log.info(f"Transcription mode: local model={WHISPER_MODEL}")

    # Write initial heartbeat and start background threads
    write_heartbeat()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=cleanup_loop, daemon=True).start()

    # Clean up old files on startup, then catch up on any existing untranscribed files
    cleanup_old_inbox_files()
    for ogg in sorted(INBOX_DIR.glob("*.ogg")):
        process_ogg(ogg)

    observer = Observer()
    observer.schedule(OggHandler(), str(INBOX_DIR), recursive=False)
    observer.start()
    log.info("Watcher running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
