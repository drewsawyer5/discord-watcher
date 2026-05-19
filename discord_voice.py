from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass


AUDIO_EXTENSIONS = {".ogg", ".mp3", ".mp4", ".wav", ".m4a", ".webm"}
DEFAULT_TRANSCRIPT_MIN_CHARS = 10


@dataclass(frozen=True)
class VoiceConfig:
    endpoint: str
    model: str
    timeout_seconds: int
    diarize: bool


def load_voice_config() -> VoiceConfig:
    return VoiceConfig(
        endpoint=os.getenv("WHISPER_ENDPOINT", "").strip().rstrip("/"),
        model=os.getenv("WHISPER_MODEL", "base").strip() or "base",
        timeout_seconds=int(os.getenv("WHISPER_TIMEOUT", "600")),
        diarize=os.getenv("WHISPER_DIARIZE", "").strip().lower() in {"1", "true", "yes"},
    )


def is_audio_attachment(attachment: Any) -> bool:
    content_type = str(getattr(attachment, "content_type", "") or "").lower()
    if content_type.startswith("audio/"):
        return True
    filename = str(getattr(attachment, "filename", "") or "").lower()
    return Path(filename).suffix in AUDIO_EXTENSIONS


def is_audio_attachment_dict(attachment: dict[str, Any]) -> bool:
    content_type = str(attachment.get("content_type", "") or "").lower()
    if content_type.startswith("audio/"):
        return True
    return Path(str(attachment.get("filename", "") or "").lower()).suffix in AUDIO_EXTENSIONS


def select_first_audio_attachment(attachments: list[Any]) -> tuple[Any | None, int]:
    audio = [attachment for attachment in attachments if is_audio_attachment(attachment)]
    if not audio:
        return None, 0
    return audio[0], max(0, len(audio) - 1)


def select_first_audio_attachment_dict(attachments: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, int]:
    audio = [attachment for attachment in attachments if is_audio_attachment_dict(attachment)]
    if not audio:
        return None, 0
    return audio[0], max(0, len(audio) - 1)


def is_usable_transcript(transcript: str, min_chars: int = DEFAULT_TRANSCRIPT_MIN_CHARS) -> bool:
    text = transcript.strip()
    if len(text) < min_chars:
        return False
    return len(re.findall(r"[A-Za-z0-9]", text)) >= min_chars


def format_voice_prompt(transcript: str, typed_note: str = "") -> str:
    prompt = f"Voice transcript:\n{transcript.strip()}"
    typed_note = typed_note.strip()
    if typed_note:
        prompt += f"\n\nTyped note:\n{typed_note}"
    return prompt


def transcribe_file(
    audio_path: Path,
    config: VoiceConfig | None = None,
    requests_post: Callable[..., Any] = requests.post,
) -> str:
    config = config or load_voice_config()
    if config.endpoint:
        return _transcribe_file_remote(audio_path, config, requests_post=requests_post)
    return _transcribe_file_local(audio_path, config)


def _transcribe_file_remote(
    audio_path: Path,
    config: VoiceConfig,
    requests_post: Callable[..., Any] = requests.post,
) -> str:
    post_data = {"diarize": "true"} if config.diarize else None
    with audio_path.open("rb") as handle:
        response = requests_post(
            f"{config.endpoint}/transcribe",
            files={"file": (audio_path.name, handle, _content_type_for_path(audio_path))},
            data=post_data,
            timeout=config.timeout_seconds,
        )
    response.raise_for_status()
    data = response.json()
    transcript = data.get("text")
    if transcript is None:
        segments = data.get("segments", [])
        transcript = " ".join(str(segment.get("text", "")).strip() for segment in segments).strip()
    if not isinstance(transcript, str):
        raise ValueError("Whisper response text is not a string")
    return transcript.strip()


def _transcribe_file_local(audio_path: Path, config: VoiceConfig) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(config.model, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), language="en", condition_on_previous_text=False)
    return " ".join(segment.text.strip() for segment in segments).strip()


def transcribe_attachment_dict(
    attachment: dict[str, Any],
    headers: dict[str, str] | None = None,
    config: VoiceConfig | None = None,
    requests_get: Callable[..., Any] = requests.get,
    requests_post: Callable[..., Any] = requests.post,
) -> str:
    response = requests_get(attachment["url"], headers=headers or {}, timeout=30)
    response.raise_for_status()
    filename = str(attachment.get("filename", "") or "voice.ogg")
    return _transcribe_bytes(filename, response.content, config=config, requests_post=requests_post)


async def transcribe_attachment(
    attachment: Any,
    config: VoiceConfig | None = None,
    requests_post: Callable[..., Any] = requests.post,
) -> str:
    filename = str(getattr(attachment, "filename", "") or "voice.ogg")
    audio_bytes = await attachment.read()
    return await asyncio.to_thread(_transcribe_bytes, filename, audio_bytes, config, requests_post)


def _transcribe_bytes(
    filename: str,
    audio_bytes: bytes,
    config: VoiceConfig | None = None,
    requests_post: Callable[..., Any] = requests.post,
) -> str:
    suffix = Path(filename).suffix or ".ogg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)
    try:
        return transcribe_file(tmp_path, config=config, requests_post=requests_post)
    finally:
        tmp_path.unlink(missing_ok=True)


def _content_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix in {".m4a", ".mp4"}:
        return "audio/mp4"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".webm":
        return "audio/webm"
    return "audio/ogg"
