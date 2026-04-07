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
# State — tracks last seen message ID so restarts don't re-process
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_message_id": None, "processed_ids": []}


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


# ---------------------------------------------------------------------------
# URL content fetch
# ---------------------------------------------------------------------------
def fetch_url_content(url: str) -> str:
    """Fetch URL and return stripped text (best-effort, capped at 8k chars)."""
    try:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DrewPA/1.0)"},
        )
        resp.raise_for_status()
        text = re.sub(r'<[^>]+>', ' ', resp.text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:8000]
    except Exception as e:
        log.warning(f"Fetch failed for {url}: {e}")
        return f"[Fetch failed: {e}]"


# ---------------------------------------------------------------------------
# System prompt (loaded once, cached)
# ---------------------------------------------------------------------------
_system_prompt: str | None = None


def get_system_prompt() -> str:
    global _system_prompt
    if _system_prompt is not None:
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
  "type": "article | paper | list_item | note | youtube | unsupported",
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
- Always include _log.md append (one row)
- Always include _index.md append (one row in the relevant table)
- For list items: append one line to the correct Lists/ page; set mode "append"
- For list pages that may not exist: set mode "create" with full page content including the Queue header, then the one item
- For youtube: set files=[] and discord_reply="Queued for YouTube ingestion (not yet built)."
- For unsupported (PDF, attachment): set files=[] and discord_reply="PDF ingestion not supported yet — drop via Claude session."
- discord_reply must be 4 lines or fewer
"""
    return _system_prompt


# ---------------------------------------------------------------------------
# Core ingest — shared write logic
# ---------------------------------------------------------------------------
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

        path = VAULT_PATH / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append":
            with path.open("a", encoding="utf-8") as f:
                f.write("\n" + file_content.rstrip() + "\n")
        else:
            path.write_text(file_content, encoding="utf-8")

        log.info(f"  [{mode}] {rel_path}")

    post_discord_reply(result.get("discord_reply", f"Ingested: {label}"), message_id)
    log.info(f"Done: {result.get('title', label)}")


def run_ingest(url: str, message_id: str) -> bool:
    log.info(f"Ingesting URL: {url}")
    content = fetch_url_content(url)
    try:
        raw = call_llm(get_system_prompt(), f"URL: {url}\n\nFetched content (truncated to 8k):\n{content}")
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for {url}: {e}")
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

    try:
        raw = call_llm(
            get_system_prompt(),
            f"Content type: voice transcript (Drew spoke this into #inbox)\n\nTranscript:\n{transcript}",
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for voice transcript: {e}")
        post_discord_reply(f"⚠️ Voice ingest failed: LLM error — will retry next cycle", message_id)
        return False

    _apply_ingest_result(result, message_id, f"voice: {att.get('filename', 'message')}")
    return True


# ---------------------------------------------------------------------------
# Text drop ingest
# ---------------------------------------------------------------------------
def run_ingest_text(content: str, message_id: str) -> bool:
    log.info(f"Ingesting text drop: {content[:80]}")
    try:
        raw = call_llm(
            get_system_prompt(),
            f"Content type: text drop (Drew typed this into #inbox)\n\nText:\n{content}",
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for text drop: {e}")
        post_discord_reply("⚠️ Text ingest failed: LLM error — will retry next cycle", message_id)
        return False
    _apply_ingest_result(result, message_id, f"text: {content[:40]}")
    return True


# ---------------------------------------------------------------------------
# Image ingest
# ---------------------------------------------------------------------------
def run_ingest_image(att: dict, message_id: str) -> bool:
    filename = att.get("filename", "image.jpg")
    suffix = Path(filename).suffix.lower()
    mime_type = IMAGE_MIME.get(suffix, "image/jpeg")

    log.info(f"Downloading image: {filename}")
    try:
        resp = requests.get(att["url"], headers=_discord_headers(), timeout=30)
        resp.raise_for_status()
        image_b64 = base64.b64encode(resp.content).decode("utf-8")
    except Exception as e:
        log.error(f"Image download failed: {e}")
        post_discord_reply(f"⚠️ Image download failed: {e}", message_id)
        return False

    try:
        raw = call_llm_with_image(
            get_system_prompt(),
            f"Content type: image attachment (Drew dropped this into #inbox)\nFilename: {filename}\n\nDescribe and classify this image for the wiki.",
            image_b64,
            mime_type,
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for image {filename}: {e}")
        post_discord_reply("⚠️ Image ingest failed: LLM error — will retry next cycle", message_id)
        return False

    _apply_ingest_result(result, message_id, f"image: {filename}")
    return True


# ---------------------------------------------------------------------------
# PDF ingest
# ---------------------------------------------------------------------------
def run_ingest_pdf(att: dict, message_id: str) -> bool:
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

    try:
        raw = call_llm(
            get_system_prompt(),
            f"Content type: PDF attachment (Drew dropped this into #inbox)\nFilename: {filename}\n\nExtracted text (capped at 12k):\n{text[:12000]}",
        )
        result = json.loads(raw)
    except Exception as e:
        log.error(f"LLM error for PDF {filename}: {e}")
        post_discord_reply("⚠️ PDF ingest failed: LLM error — will retry next cycle", message_id)
        return False

    _apply_ingest_result(result, message_id, f"pdf: {filename}")
    return True


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
            messages = fetch_new_messages(state["last_message_id"])
            for msg in messages:
                msg_id = msg["id"]

                # Always advance the cursor
                if not state["last_message_id"] or int(msg_id) > int(state["last_message_id"]):
                    state["last_message_id"] = msg_id

                if msg_id in state["processed_ids"]:
                    continue

                if should_process(msg):
                    ok = True
                    for url in URL_RE.findall(msg.get("content", "")):
                        ok = run_ingest(url, msg_id) and ok
                    for att in msg.get("attachments", []):
                        suffix = Path(att.get("filename", "")).suffix.lower()
                        if suffix in AUDIO_EXTENSIONS:
                            ok = run_ingest_voice(att, msg_id) and ok
                        elif suffix in IMAGE_EXTENSIONS:
                            ok = run_ingest_image(att, msg_id) and ok
                        elif suffix in PDF_EXTENSIONS:
                            ok = run_ingest_pdf(att, msg_id) and ok
                    if is_text_drop(msg):
                        ok = run_ingest_text(msg.get("content", "").strip(), msg_id) and ok
                    # Only mark processed if everything succeeded — failures re-queue on next poll
                    if ok:
                        state["processed_ids"].append(msg_id)
                        state["processed_ids"] = state["processed_ids"][-500:]
                    else:
                        log.info(f"Message {msg_id} will be retried next poll cycle")

            save_state(state)

        except Exception as e:
            log.error(f"Poll loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
