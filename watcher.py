import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from faster_whisper import WhisperModel

# Force UTF-8 output on Windows so emoji in log entries don't crash the terminal
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Paths — override with .env vars if needed
INBOX_DIR = Path(os.getenv("DISCORD_INBOX", r"C:\Users\drews\.claude\channels\discord\inbox"))
LOG_BASE = Path(os.getenv("INBOX_LOG_DIR", r"C:\Users\drews\Life Org\MD-AI\00 - Inbox\logs"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


def transcribe(ogg_path: Path) -> str:
    segments, _ = model.transcribe(str(ogg_path), language="en")
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
    txt_path = ogg_path.with_suffix(".txt")
    if txt_path.exists():
        log.info(f"Already transcribed: {ogg_path.name}")
        return
    log.info(f"Transcribing: {ogg_path.name}")
    try:
        transcript = transcribe(ogg_path)
        txt_path.write_text(transcript, encoding="utf-8")
        log.info(f"Wrote: {txt_path.name}")
        append_to_log(transcript, ogg_path.name)
    except Exception as e:
        log.error(f"Failed to transcribe {ogg_path.name}: {e}")


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

    # Catch up on any existing untranscribed files
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