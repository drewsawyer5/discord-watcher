#!/usr/bin/env python3
"""
ingest.py — Background ingest processor for Drew's PA system.

Polls #inbox on Discord for:
  - URLs       → fetches content → Gemini Flash → wiki page
  - Voice msgs → faster-whisper (local) → Gemini Flash → wiki note

No Claude session required. LLM provider is configured via LLM_PROVIDER in .env:
  gemini         — Gemini Flash via OpenAI-compatible endpoint (default)
  openai_compat  — Any OpenAI-compatible API (Ollama, OpenRouter, etc.) via LLM_BASE_URL
"""

import os
import sys
import json
import time
import re
import base64
import logging
import logging.handlers
import tempfile
import requests
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI
from faster_whisper import WhisperModel

# Load shared Drew_code/.env (one level up from this repo)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log_file = Path(__file__).parent / "ingest.log"
logging.getLogger().addHandler(
    logging.handlers.RotatingFileHandler(_log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (all overridable via .env)
# ---------------------------------------------------------------------------
DISCORD_BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN", "")
INGEST_CHANNEL_ID  = os.getenv("INGEST_CHANNEL_ID", "1474888067893559360")
DREW_USER_ID       = os.getenv("DREW_USER_ID", "")      # optional: only process messages from this Discord user ID
VAULT_PATH         = Path(os.getenv("VAULT_PATH", r"C:\Users\drews\Life Org\Obsidian"))
POLL_INTERVAL      = int(os.getenv("INGEST_POLL_INTERVAL", "30"))
STATE_FILE         = Path(__file__).parent / "ingest_state.json"

LLM_PROVIDER  = os.getenv("LLM_PROVIDER", "gemini")
LLM_API_KEY   = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY", "")  # fall back to GEMINI_API_KEY
LLM_MODEL     = os.getenv("LLM_MODEL") or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
LLM_BASE_URL  = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")

INGEST_INSTRUCTIONS_PATH = Path(r"C:\Users\drews\Life Org\MD-AI\01 - CLAUDE\Skills\ingest.md")
SCHEMA_PATH              = Path(r"C:\Users\drews\Life Org\Obsidian\6 - Wiki Hub\_schema.md")

# PDF drop folder — drop PDFs here to ingest without Discord's 10MB limit
# Accessible from phone via Obsidian sync. Processed files move to drop/done/.
PDF_DROP_DIR  = VAULT_PATH / "5 - Storage" / "05 - Raw Ingests" / "pdfs" / "drop"
PDF_DROP_DONE = PDF_DROP_DIR / "done"

URL_RE = re.compile(r'https?://[^\s>]+')
AUDIO_EXTENSIONS = {'.ogg', '.mp3', '.mp4', '.wav', '.m4a', '.webm'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
IMAGE_MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
              '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
PDF_EXTENSIONS = {'.pdf'}

# ---------------------------------------------------------------------------
# Whisper model (lazy-loaded on first voice message)
# ---------------------------------------------------------------------------
_whisper_model: WhisperModel | None = None


def get_whisper_model() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        model_size = os.getenv("WHISPER_MODEL", "base")
        log.info(f"Loading Whisper model: {model_size}")
        _whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _whisper_model

# ---------------------------------------------------------------------------
# LLM client (provider-agnostic via OpenAI SDK + base_url)
# ---------------------------------------------------------------------------
_llm_client: OpenAI | None = None


def get_llm_client() -> OpenAI:
    global _llm_client
    if _llm_client is None:
        _llm_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, max_retries=6)
    return _llm_client


def call_llm(system: str, user: str) -> str:
    resp = get_llm_client().chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def call_llm_with_image(system: str, user_text: str, image_b64: str, mime_type: str = "image/jpeg") -> str:
    resp = get_llm_client().chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                {"type": "text", "text": user_text},
            ]},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# Raw storage helpers
# ---------------------------------------------------------------------------
def _slug(s: str, max_len: int = 50) -> str:
    s = re.sub(r'^https?://', '', s)  # strip URL scheme
    return re.sub(r'[^\w-]', '-', s)[:max_len].strip('-').lower()


def _fm_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v)
    if any(c in s for c in ':#[]{}>|'):
        return f'"{s}"'
    return s


def _raw_dir(content_type: str) -> Path:
    d = VAULT_PATH / "5 - Storage" / "05 - Raw Ingests" / content_type
    d.mkdir(parents=True, exist_ok=True)
    return d


def vault_rel(path: Path) -> str:
    return str(path.relative_to(VAULT_PATH)).replace("\\", "/")


def write_raw_md(content_type: str, slug: str, body: str, extra_fm: dict | None = None) -> Path:
    """Write frontmatter + body to 5 - Storage/05 - Raw Ingests/{type}/YYYY-MM-DD-{slug}.md."""
    today = datetime.now().strftime("%Y-%m-%d")
    raw_path = _raw_dir(content_type) / f"{today}-{_slug(slug)}.md"
    fm = {"date": today, "type": content_type, "processed": False}
    if extra_fm:
        fm.update(extra_fm)
    lines = ["---"] + [f"{k}: {_fm_val(v)}" for k, v in fm.items()] + ["---", "", body.strip()]
    raw_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  [raw] {vault_rel(raw_path)}")
    return raw_path


def write_raw_binary(content_type: str, filename: str, data: bytes) -> Path:
    """Save binary file to 5 - Storage/05 - Raw Ingests/{type}/{filename}."""
    today = datetime.now().strftime("%Y-%m-%d")
    raw_path = _raw_dir(content_type) / f"{today}-{filename}"
    raw_path.write_bytes(data)
    log.info(f"  [raw-bin] {vault_rel(raw_path)}")
    return raw_path


# ---------------------------------------------------------------------------
# State — tracks last seen message ID so restarts don't re-process
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if "failed_ids" not in state:
            state["failed_ids"] = {}  # migrate old state
        if "processed_drop_files" not in state:
            state["processed_drop_files"] = []  # migrate old state
        return state
    return {"last_message_id": None, "processed_ids": [], "failed_ids": {}, "processed_drop_files": []}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Discord REST helpers
# ---------------------------------------------------------------------------
def _discord_headers() -> dict:
    return {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}


def discord_get(endpoint: str) -> list | dict:
    resp = requests.get(
        f"https://discord.com/api/v10{endpoint}",
        headers=_discord_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def discord_post(endpoint: str, payload: dict) -> dict:
    resp = requests.post(
        f"https://discord.com/api/v10{endpoint}",
        headers={**_discord_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def discord_get_message(message_id: str) -> dict | None:
    """Fetch a single message by ID for retry processing."""
    try:
        return discord_get(f"/channels/{INGEST_CHANNEL_ID}/messages/{message_id}")
    except Exception as e:
        log.warning(f"Could not fetch message {message_id}: {e}")
        return None


def fetch_new_messages(after_id: str | None) -> list[dict]:
    params = "?limit=20"
    if after_id:
        params += f"&after={after_id}"
    messages = discord_get(f"/channels/{INGEST_CHANNEL_ID}/messages{params}")
    # Discord returns newest-first; reverse for chronological processing
    return list(reversed(messages))


def post_discord_reply(content: str, reference_message_id: str):
    discord_post(
        f"/channels/{INGEST_CHANNEL_ID}/messages",
        {
            "content": content,
            "message_reference": {"message_id": reference_message_id},
        },
    )


def post_discord_message(content: str, channel_id: str | None = None):
    """Post a standalone message to a Discord channel (no reply reference)."""
    discord_post(f"/channels/{channel_id or INGEST_CHANNEL_ID}/messages", {"content": content})


# Digest notification — /digest writes this file; we pick it up and send via bot
DIGEST_NOTIFY_FILE = VAULT_PATH.parent / "MD-AI" / "digest-notify-pending.md"

# Retry queue — items that failed to fully process (fetch blocked or LLM down)
RETRY_QUEUE_FILE = VAULT_PATH / "6 - Wiki Hub" / "_retry_queue.md"


def _in_retry_queue(source: str) -> bool:
    """Check if this source already has an entry in the retry queue (prevents duplicates)."""
    if not RETRY_QUEUE_FILE.exists():
        return False
    return source in RETRY_QUEUE_FILE.read_text(encoding="utf-8")


def write_retry_queue_entry(fail_type: str, source: str, raw_path: str = ""):
    """Append a failed ingest entry to _retry_queue.md. Skips if source already present."""
    if _in_retry_queue(source):
        return
    if not RETRY_QUEUE_FILE.exists():
        RETRY_QUEUE_FILE.write_text(
            "# Ingest Retry Queue\n\n"
            "> lm_failed = LLM was down; raw content is saved and re-processable.\n"
            "> fetch_failed = URL was blocked (403/paywall); no content saved. Needs manual handling.\n"
            "> /digest reads this file in Step 0 and either retries or surfaces to Drew.\n\n"
            "| Date | Type | Source | Raw File |\n"
            "|---|---|---|---|\n",
            encoding="utf-8",
        )
    today = datetime.now().strftime("%Y-%m-%d")
    with RETRY_QUEUE_FILE.open("a", encoding="utf-8") as f:
        f.write(f"| {today} | {fail_type} | {source} | {raw_path} |\n")
    log.info(f"[retry_queue] {fail_type}: {source}")


def check_digest_notify():
    """Send pending digest notification if one was written by /digest, then delete it."""
    if not DIGEST_NOTIFY_FILE.exists():
        return
    try:
        text = DIGEST_NOTIFY_FILE.read_text(encoding="utf-8").strip()
        if text:
            post_discord_message(text)
            log.info("Sent pending digest notification to Discord")
        DIGEST_NOTIFY_FILE.unlink()
    except Exception as e:
        log.error(f"Failed to send digest notification: {e}")


# ---------------------------------------------------------------------------
# URL content fetch
# ---------------------------------------------------------------------------
def fetch_url_content(url: str) -> str:
    """Fetch URL and extract main text via trafilatura. Returns full text (no cap — callers cap for LLM)."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
            if text:
                log.info(f"trafilatura extracted {len(text)} chars from {url}")
                return text
        # Fallback: regex strip if trafilatura returns nothing
        log.warning(f"trafilatura returned empty for {url} — falling back to regex strip")
        resp = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 (compatible; DrewPA/1.0)"})
        resp.raise_for_status()
        text = re.sub(r'<[^>]+>', ' ', resp.text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        log.warning(f"Fetch failed for {url}: {e}")
        return f"[Fetch failed: {e}]"


# ---------------------------------------------------------------------------
# System prompt (loaded once, cached)
# ---------------------------------------------------------------------------
_system_prompt: str | None = None


def get_system_prompt(force_reload: bool = False) -> str:
    global _system_prompt
    if _system_prompt is not None and not force_reload:
        return _system_prompt

    ingest_md = INGEST_INSTRUCTIONS_PATH.read_text(encoding="utf-8")
    schema_md = SCHEMA_PATH.read_text(encoding="utf-8")
    today = datetime.now().strftime("%Y-%m-%d")

    _system_prompt = f"""You are an ingest processor for Drew's personal knowledge wiki.
Today's date: {today}
Vault root: C:\\Users\\drews\\Life Org\\Obsidian

## Ingest Instructions
{ingest_md}

## Wiki Schema
{schema_md}

## Output Format
Return a single JSON object:
{{
  "type": "article | paper | list_item | note | image | youtube | unsupported",
  "title": "human-readable title",
  "files": [
    {{
      "path": "path relative to vault root",
      "content": "full content to write OR single line to append",
      "mode": "create | append"
    }}
  ],
  "discord_reply": "**Ingested:** ...\\n**Type:** ...\\n**Filed:** ...\\n**Summary:** ..."
}}

Rules:
- mode "create": write full content to file (overwrite if exists)
- mode "append": append content as a new line at end of file
- Always include _log.md append. The file already has a header row — append exactly one bare data row with NO extra formatting, NO table headers, NO blank lines before or after it:
  | YYYY-MM-DD | {{type}} | {{title}} | {{source_url or "attachment" or "text"}} | {{wiki_page_path}} |
  Example: | 2026-04-09 | article | My Title | https://example.com | 6 - Wiki Hub/Sources/My-Title.md |
  Types: article, paper, list_item, note, image, voice
- Do NOT touch _index.md — /digest owns and rebuilds that file
- For list items: append one line to the correct Lists/ page; set mode "append"
- For list pages that may not exist: set mode "create" with full page content including the Queue header, then the one item
- For image: describe what's in the image, write a note to 6 - Wiki Hub/ in the appropriate section, include _log.md append as usual
- For youtube: set files=[] and discord_reply="Queued for YouTube ingestion (not yet built)."
- For unsupported (non-image attachment, unknown file type): set files=[] and discord_reply="Unsupported file type — drop via Claude session."
- discord_reply must be 4 lines or fewer
- If the user message contains "Raw file: [[path]]", add "- **Raw:** [[path]]" to the ## Metadata section of all wiki pages being created (not to _log.md)
- NEVER include paths under "5 - Storage/05 - Raw Ingests/" in the files[] array — those files are already written by the system before your call and must not be overwritten
"""
    return _system_prompt


def get_existing_lists_context() -> str:
    """Tell the LLM which list files already exist so it uses append, not create."""
    lists_dir = VAULT_PATH / "6 - Wiki Hub" / "Lists"
    if not lists_dir.exists():
        return ""
    existing = sorted(f.name for f in lists_dir.glob("*.md"))
    if not existing:
        return ""
    return f"\nExisting list files (MUST use mode 'append' for these, never 'create'): {', '.join(existing)}"


# ---------------------------------------------------------------------------
# Core ingest — shared write logic
# ---------------------------------------------------------------------------
_RAW_INGEST_PREFIX = "5 - Storage/05 - Raw Ingests/"


def _apply_ingest_result(result: dict, message_id: str, label: str):
    """Write files and post reply from a parsed LLM result. Shared by URL and voice paths."""
    ingest_type = result.get("type", "unknown")

    if ingest_type == "youtube":
        queue_path = VAULT_PATH / "5 - Storage/05 - Raw Ingests/YouTube Queue.md"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        with queue_path.open("a", encoding="utf-8") as f:
            f.write(f"- [ ] {today} — {label}\n")
        log.info("Queued YouTube URL")
        post_discord_reply(result.get("discord_reply", "Queued for YouTube ingestion."), message_id)
        return

    if ingest_type == "unsupported":
        post_discord_reply(result.get("discord_reply", "Unsupported content type."), message_id)
        return

    for op in result.get("files", []):
        rel_path = op.get("path", "")
        file_content = op.get("content", "")
        mode = op.get("mode", "create")

        if not rel_path or not file_content:
            log.warning(f"Skipping file op with missing path or content: {op}")
            continue

        # Raw ingest files are written by Python before the LLM call — never let LLM overwrite them
        if rel_path.startswith(_RAW_INGEST_PREFIX):
            log.warning(f"Skipping LLM file op targeting raw ingest path (already written by system): {rel_path}")
            continue

        path = VAULT_PATH / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append":
            with path.open("a", encoding="utf-8") as f:
                f.write(file_content.rstrip() + "\n")
        else:
            path.write_text(file_content, encoding="utf-8")

        log.info(f"  [{mode}] {rel_path}")

    post_discord_reply(result.get("discord_reply", f"Ingested: {label}"), message_id)
    log.info(f"Done: {result.get('title', label)}")


LLM_URL_CAP = 25_000  # chars passed to LLM for URL content


def run_ingest(url: str, message_id: str, drew_context: str = "") -> bool:
    log.info(f"Ingesting URL: {url}")
    content = fetch_url_content(url)
    # Raw gets full text; LLM gets capped version
    raw_path = write_raw_md("urls", url, content, extra_fm={"url": url})
    raw_rel = vault_rel(raw_path)

    # Fetch failed permanently (403, paywall, etc.) — skip LLM, log for manual recovery
    if content.startswith("[Fetch failed:"):
        write_retry_queue_entry("fetch_failed", url, raw_rel)
        log.info(f"Fetch failed for {url} — logged to retry queue, skipping LLM")
        post_discord_reply(f"⚠️ Could not fetch <{url}> (blocked/403) — logged to retry queue for manual handling.", message_id)
        return True  # Not a retryable LLM failure — don't add to failed_ids

    llm_content = content[:LLM_URL_CAP]
    truncated = len(content) > LLM_URL_CAP
    truncation_note = f"\n\n[Content truncated at {LLM_URL_CAP} chars — full text in raw file]" if truncated else ""
    context_note = f"\nDrew's note: {drew_context}" if drew_context else ""
    try:
        raw = call_llm(
            get_system_prompt(),
            f"URL: {url}\nRaw file: [[{raw_rel}]]{context_note}{get_existing_lists_context()}\n\nFetched content:{truncation_note}\n{llm_content}",
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for {url}: {e}")
        write_retry_queue_entry("lm_failed", url, raw_rel)
        post_discord_reply(f"⚠️ Ingest failed for <{url}>: LLM error — will retry next cycle", message_id)
        return False
    _apply_ingest_result(result, message_id, url)
    return True


# ---------------------------------------------------------------------------
# Voice ingest
# ---------------------------------------------------------------------------
def transcribe_attachment(att: dict) -> str:
    """Download an audio attachment from Discord and transcribe with faster-whisper."""
    cdn_url = att["url"]
    suffix = Path(att.get("filename", "audio.ogg")).suffix or ".ogg"
    log.info(f"Downloading voice attachment: {att.get('filename')}")

    resp = requests.get(cdn_url, headers=_discord_headers(), timeout=30)
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = Path(tmp.name)

    try:
        segments, _ = get_whisper_model().transcribe(str(tmp_path), language="en", condition_on_previous_text=False)
        transcript = " ".join(s.text.strip() for s in segments).strip()
        log.info(f"Transcribed ({len(transcript)} chars): {transcript[:80]}...")
        return transcript
    finally:
        tmp_path.unlink(missing_ok=True)


def run_ingest_voice(att: dict, message_id: str) -> bool:
    try:
        transcript = transcribe_attachment(att)
    except Exception as e:
        log.error(f"Transcription failed: {e}")
        post_discord_reply(f"⚠️ Voice transcription failed: {e}", message_id)
        return False

    if not transcript:
        post_discord_reply("⚠️ Voice message was empty or couldn't be transcribed.", message_id)
        return True  # not an LLM failure — don't re-queue

    filename = att.get("filename", "voice.ogg")
    raw_path = write_raw_md("voice", Path(filename).stem, transcript, extra_fm={"filename": filename})
    raw_rel = vault_rel(raw_path)

    try:
        raw = call_llm(
            get_system_prompt(),
            f"Content type: voice transcript (Drew spoke this into #inbox)\nRaw file: [[{raw_rel}]]{get_existing_lists_context()}\n\nTranscript:\n{transcript}",
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for voice transcript: {e}")
        write_retry_queue_entry("lm_failed", f"voice:{filename}", raw_rel)
        post_discord_reply(f"⚠️ Voice ingest failed: LLM error — will retry next cycle", message_id)
        return False

    _apply_ingest_result(result, message_id, f"voice: {filename}")
    return True


# ---------------------------------------------------------------------------
# Text drop ingest
# ---------------------------------------------------------------------------
def run_ingest_text(content: str, message_id: str) -> bool:
    log.info(f"Ingesting text drop: {content[:80]}")
    raw_path = write_raw_md("text", content[:40], content)
    raw_rel = vault_rel(raw_path)
    try:
        raw = call_llm(
            get_system_prompt(),
            f"Content type: text drop (Drew typed this into #inbox)\nRaw file: [[{raw_rel}]]{get_existing_lists_context()}\n\nText:\n{content}",
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for text drop: {e}")
        write_retry_queue_entry("lm_failed", f"text:{content[:60]}", raw_rel)
        post_discord_reply("⚠️ Text ingest failed: LLM error — will retry next cycle", message_id)
        return False
    _apply_ingest_result(result, message_id, f"text: {content[:40]}")
    return True


# ---------------------------------------------------------------------------
# Image ingest
# ---------------------------------------------------------------------------
def run_ingest_image(att: dict, message_id: str, drew_context: str = "") -> bool:
    filename = att.get("filename", "image.jpg")
    suffix = Path(filename).suffix.lower()
    mime_type = IMAGE_MIME.get(suffix, "image/jpeg")

    log.info(f"Downloading image: {filename}")
    try:
        resp = requests.get(att["url"], headers=_discord_headers(), timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"Image download failed: {e}")
        post_discord_reply(f"⚠️ Image download failed: {e}", message_id)
        return False

    # Save raw binary before LLM call
    raw_bin_path = write_raw_binary("images", filename, resp.content)
    raw_bin_rel = vault_rel(raw_bin_path)
    image_b64 = base64.b64encode(resp.content).decode("utf-8")

    context_note = f"\nDrew's note: {drew_context}" if drew_context else ""
    try:
        raw = call_llm_with_image(
            get_system_prompt(),
            f"Content type: image attachment (Drew dropped this into #inbox)\nFilename: {filename}\nRaw file: [[{raw_bin_rel}]]{context_note}{get_existing_lists_context()}\n\nDescribe and classify this image for the wiki.",
            image_b64,
            mime_type,
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for image {filename}: {e}")
        write_retry_queue_entry("lm_failed", f"image:{filename}", raw_bin_rel)
        post_discord_reply("⚠️ Image ingest failed: LLM error — will retry next cycle", message_id)
        return False

    # Write sidecar .md with LLM description alongside the binary
    sidecar_body = f"![[{raw_bin_rel}]]\n\n{result.get('discord_reply', '').strip()}"
    write_raw_md("images", Path(filename).stem, sidecar_body,
                 extra_fm={"filename": filename, "raw_binary": raw_bin_rel})

    _apply_ingest_result(result, message_id, f"image: {filename}")
    return True


# ---------------------------------------------------------------------------
# PDF ingest
# ---------------------------------------------------------------------------
def run_ingest_pdf(att: dict, message_id: str, drew_context: str = "") -> bool:
    import pdfplumber

    filename = att.get("filename", "document.pdf")
    log.info(f"Downloading PDF: {filename}")

    try:
        resp = requests.get(att["url"], headers=_discord_headers(), timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"PDF download failed: {e}")
        post_discord_reply(f"⚠️ PDF download failed: {e}", message_id)
        return False

    # Save raw PDF binary before extraction
    raw_bin_path = write_raw_binary("pdfs", filename, resp.content)
    raw_bin_rel = vault_rel(raw_bin_path)

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = Path(tmp.name)
        try:
            with pdfplumber.open(tmp_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception as e:
        log.error(f"PDF extraction failed: {e}")
        post_discord_reply(f"⚠️ PDF text extraction failed: {e}", message_id)
        return False

    if not text:
        post_discord_reply(
            "⚠️ PDF appears to be image-only — text extraction returned nothing. Drop via Claude session for vision-based ingest.",
            message_id,
        )
        return True  # Not a retry-able failure

    # Save full extracted text to raw ingests (no truncation)
    raw_text_path = write_raw_md("pdfs", Path(filename).stem, text,
                                  extra_fm={"filename": filename, "pdf": raw_bin_rel})
    raw_text_rel = vault_rel(raw_text_path)

    try:
        raw = call_llm(
            get_system_prompt(),
            f"Content type: PDF attachment (Drew dropped this into #inbox)\nFilename: {filename}\nRaw file: [[{raw_text_rel}]]"
            + (f"\nDrew's note: {drew_context}" if drew_context else "")
            + f"{get_existing_lists_context()}\n\nExtracted text (capped at 50k):\n{text[:50000]}",
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for PDF {filename}: {e}")
        write_retry_queue_entry("lm_failed", f"pdf:{filename}", raw_text_rel)
        post_discord_reply("⚠️ PDF ingest failed: LLM error — will retry next cycle", message_id)
        return False

    _apply_ingest_result(result, message_id, f"pdf: {filename}")
    return True


# ---------------------------------------------------------------------------
# PDF drop folder — local path for PDFs too large for Discord (10MB limit)
# ---------------------------------------------------------------------------
def run_ingest_pdf_local(file_path: Path) -> bool:
    """Ingest a PDF from a local file path (drop folder). Same logic as run_ingest_pdf."""
    import pdfplumber

    filename = file_path.name
    log.info(f"Ingesting PDF from drop folder: {filename}")

    try:
        pdf_bytes = file_path.read_bytes()
    except Exception as e:
        log.error(f"Failed to read drop PDF {filename}: {e}")
        return False

    # Save raw binary to raw ingests
    raw_bin_path = write_raw_binary("pdfs", filename, pdf_bytes)
    raw_bin_rel = vault_rel(raw_bin_path)

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)
        try:
            with pdfplumber.open(tmp_path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception as e:
        log.error(f"PDF extraction failed for {filename}: {e}")
        post_discord_message(f"⚠️ PDF drop: text extraction failed for `{filename}`: {e}")
        return False

    if not text:
        post_discord_message(
            f"⚠️ PDF drop: `{filename}` appears to be image-only — text extraction returned nothing. "
            "Open via Claude session for vision-based ingest."
        )
        return True  # Not retryable — move to done

    raw_text_path = write_raw_md("pdfs", file_path.stem, text,
                                 extra_fm={"filename": filename, "pdf": raw_bin_rel, "source": "drop_folder"})
    raw_text_rel = vault_rel(raw_text_path)

    try:
        raw = call_llm(
            get_system_prompt(),
            f"Content type: PDF (dropped into local ingest folder by Drew)\nFilename: {filename}\nRaw file: [[{raw_text_rel}]]{get_existing_lists_context()}\n\nExtracted text (capped at 50k):\n{text[:50000]}",
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for drop PDF {filename}: {e}")
        write_retry_queue_entry("lm_failed", f"drop_pdf:{filename}", raw_text_rel)
        post_discord_message(f"⚠️ PDF drop: LLM error for `{filename}` — logged to retry queue")
        return False

    # Use post_discord_message (no reply thread) since there's no originating Discord message
    ingest_type = result.get("type", "unknown")
    if ingest_type not in ("youtube", "unsupported"):
        for op in result.get("files", []):
            rel_path = op.get("path", "")
            file_content = op.get("content", "")
            mode = op.get("mode", "create")
            if not rel_path or not file_content:
                continue
            if rel_path.startswith(_RAW_INGEST_PREFIX):
                continue
            path = VAULT_PATH / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                with path.open("a", encoding="utf-8") as f:
                    f.write(file_content.rstrip() + "\n")
            else:
                path.write_text(file_content, encoding="utf-8")
            log.info(f"  [{mode}] {rel_path}")

    post_discord_message(result.get("discord_reply", f"📄 PDF drop ingested: {filename}"))
    log.info(f"Drop PDF done: {filename}")
    return True


def check_pdf_drop_folder(state: dict):
    """Scan the PDF drop folder and process any new PDFs found."""
    PDF_DROP_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DROP_DONE.mkdir(parents=True, exist_ok=True)

    processed = set(state.get("processed_drop_files", []))
    new_files = [f for f in PDF_DROP_DIR.glob("*.pdf") if f.name not in processed]

    if not new_files:
        return

    log.info(f"Found {len(new_files)} new PDF(s) in drop folder")
    for pdf_path in new_files:
        success = run_ingest_pdf_local(pdf_path)
        if success:
            # Move to done/ so drop folder stays clean
            done_path = PDF_DROP_DONE / pdf_path.name
            try:
                pdf_path.rename(done_path)
            except Exception as e:
                log.warning(f"Could not move {pdf_path.name} to done/: {e}")
            processed.add(pdf_path.name)
            log.info(f"Drop PDF moved to done/: {pdf_path.name}")
        else:
            log.warning(f"Drop PDF failed (will retry next cycle): {pdf_path.name}")

    state["processed_drop_files"] = list(processed)[-500:]  # cap list size


# ---------------------------------------------------------------------------
# Message filter
# ---------------------------------------------------------------------------
def has_audio_attachment(msg: dict) -> bool:
    return any(
        Path(a.get("filename", "")).suffix.lower() in AUDIO_EXTENSIONS
        for a in msg.get("attachments", [])
    )


def has_pdf_attachment(msg: dict) -> bool:
    return any(
        Path(a.get("filename", "")).suffix.lower() in PDF_EXTENSIONS
        for a in msg.get("attachments", [])
    )


def has_image_attachment(msg: dict) -> bool:
    return any(
        Path(a.get("filename", "")).suffix.lower() in IMAGE_EXTENSIONS
        for a in msg.get("attachments", [])
    )


def is_text_drop(msg: dict) -> bool:
    """Plain text message from Drew with no URL and no attachments."""
    content = msg.get("content", "").strip()
    return bool(content) and not URL_RE.search(content) and not msg.get("attachments")


def should_process(msg: dict) -> bool:
    if msg.get("author", {}).get("bot"):
        return False
    if DREW_USER_ID and msg.get("author", {}).get("id") != DREW_USER_ID:
        return False
    return (
        bool(URL_RE.search(msg.get("content", "")))
        or has_audio_attachment(msg)
        or has_image_attachment(msg)
        or has_pdf_attachment(msg)
        or is_text_drop(msg)
    )


MAX_RETRIES = 5


def _process_message(msg: dict) -> bool:
    """Run all ingest handlers for a message. Returns True only if everything succeeded."""
    msg_id = msg["id"]
    ok = True
    full_content = msg.get("content", "")

    # Extract Drew's context: message text with URLs stripped (preserved for URL/image/PDF ingest)
    drew_context = URL_RE.sub("", full_content).strip()

    for url in URL_RE.findall(full_content):
        ok = run_ingest(url, msg_id, drew_context=drew_context) and ok
    for att in msg.get("attachments", []):
        suffix = Path(att.get("filename", "")).suffix.lower()
        if suffix in AUDIO_EXTENSIONS:
            ok = run_ingest_voice(att, msg_id) and ok
        elif suffix in IMAGE_EXTENSIONS:
            ok = run_ingest_image(att, msg_id, drew_context=drew_context) and ok
        elif suffix in PDF_EXTENSIONS:
            ok = run_ingest_pdf(att, msg_id, drew_context=drew_context) and ok
    if is_text_drop(msg):
        ok = run_ingest_text(full_content.strip(), msg_id) and ok
    return ok


def _mark_processed(state: dict, msg_id: str):
    state["processed_ids"].append(msg_id)
    state["processed_ids"] = state["processed_ids"][-500:]
    state["failed_ids"].pop(msg_id, None)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set in .env — exiting")
        sys.exit(1)
    if not LLM_API_KEY:
        log.error("LLM_API_KEY not set in .env — exiting")
        sys.exit(1)

    state = load_state()

    # First run: anchor to current latest message without processing history
    if state["last_message_id"] is None:
        log.info("First run — anchoring to current latest message (not processing history)")
        messages = discord_get(f"/channels/{INGEST_CHANNEL_ID}/messages?limit=1")
        if messages:
            state["last_message_id"] = messages[0]["id"]
            save_state(state)
        log.info(f"Anchored at message ID {state['last_message_id']} — watching for new drops")
    else:
        log.info(f"Resuming from message ID {state['last_message_id']}")

    log.info(f"Polling channel {INGEST_CHANNEL_ID} every {POLL_INTERVAL}s | {LLM_PROVIDER} / {LLM_MODEL}")

    while True:
        try:
            check_digest_notify()
            check_pdf_drop_folder(state)

            # Retry previously failed messages before processing new ones
            for msg_id, attempts in list(state["failed_ids"].items()):
                if attempts >= MAX_RETRIES:
                    log.warning(f"Message {msg_id} exceeded {MAX_RETRIES} retries — dropping")
                    _mark_processed(state, msg_id)
                    continue
                msg = discord_get_message(msg_id)
                if not msg or not should_process(msg):
                    log.warning(f"Could not re-fetch or no longer processable: {msg_id} — dropping")
                    _mark_processed(state, msg_id)
                    continue
                if _process_message(msg):
                    log.info(f"Retry succeeded for message {msg_id} (attempt {attempts + 1})")
                    _mark_processed(state, msg_id)
                else:
                    state["failed_ids"][msg_id] = attempts + 1
                    log.info(f"Retry {attempts + 1}/{MAX_RETRIES} failed for message {msg_id}")

            # Process new messages
            messages = fetch_new_messages(state["last_message_id"])
            for msg in messages:
                msg_id = msg["id"]

                # Always advance the cursor
                if not state["last_message_id"] or int(msg_id) > int(state["last_message_id"]):
                    state["last_message_id"] = msg_id

                if msg_id in state["processed_ids"] or msg_id in state["failed_ids"]:
                    continue

                if should_process(msg):
                    if _process_message(msg):
                        _mark_processed(state, msg_id)
                    else:
                        state["failed_ids"][msg_id] = 1
                        log.info(f"Message {msg_id} queued for retry (1/{MAX_RETRIES})")

            save_state(state)

        except Exception as e:
            log.error(f"Poll loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
