import os
import sys
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from faster_whisper import WhisperModel

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
STATUS_FILE = Path(__file__).parent / "status.json"
HEARTBEAT_INTERVAL = 300  # seconds (5 minutes)
INBOX_MAX_AGE_HOURS = int(os.getenv("INBOX_MAX_AGE_HOURS", "72"))
CLEANUP_INTERVAL = 3600  # seconds (1 hour)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

files_transcribed = 0


def write_heartbeat():
    STATUS_FILE.write_text(
        json.dumps({
            "last_seen": datetime.now().isoformat(timespec="seconds"),
            "files_transcribed": files_transcribed,
            "watching": str(INBOX_DIR),
            "model": WHISPER_MODEL,
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


def transcribe(ogg_path: Path) -> str:
    segments, _ = model.transcribe(str(ogg_path), language="en", condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


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
    key = str(ogg_path)
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
    finally:
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
    log.info(f"Whisper model: {WHISPER_MODEL}")

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