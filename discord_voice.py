from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import threading
import time
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

HEALTH_PROBE_CONNECT_TIMEOUT = 1.0
HEALTH_PROBE_READ_TIMEOUT = 2.0
TOWER_STATE_RECHECK_SECONDS = 30.0

_LOCAL_INITIAL_PROMPT = (
    "Casual conversation between Drew and Sydney. "
    "Topics: Obsidian, Discord, Claude, Resy, Tasker, AutoInput, Boston, Somerville, "
    "Eevee, TCG Pocket, browser-harness, reservations, wine bar."
)

_log = logging.getLogger(__name__)
_tower_state_lock = threading.Lock()
_tower_state: dict[str, Any] = {"healthy": True, "checked_at": 0.0, "last_error": None}
_local_model_lock = threading.Lock()
_local_model: Any = None
_local_model_size: str | None = None


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
    requests_get: Callable[..., Any] = requests.get,
) -> str:
    """Transcribe an audio file.

    When ``config.endpoint`` is set this uses the Tower-first fallback chain
    (probe → remote → local). Otherwise it runs the local faster-whisper path.
    Returns just the transcript text for backwards compatibility; callers that
    need the via/error metadata should use :func:`transcribe_with_fallback`.
    """
    config = config or load_voice_config()
    if not config.endpoint:
        return _transcribe_file_local(audio_path, config)
    result = transcribe_with_fallback(
        audio_path,
        config=config,
        requests_post=requests_post,
        requests_get=requests_get,
    )
    if result["via"] == "failed":
        error = result.get("error") or "transcription failed (both Tower and local)"
        raise RuntimeError(error)
    return result["text"]


def transcribe_with_fallback(
    audio_path: Path,
    config: VoiceConfig | None = None,
    requests_post: Callable[..., Any] = requests.post,
    requests_get: Callable[..., Any] = requests.get,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Tower-first, local-fallback transcription.

    Returns ``{"text", "via", "latency_ms", "error", "used_endpoint"}`` where
    ``via`` is ``"tower"``, ``"local"``, or ``"failed"``. ``error`` is non-empty
    only when both Tower and local failed (or when local was used and the
    Tower attempt produced a recoverable error worth surfacing).
    """
    config = config or load_voice_config()
    start = now()

    if config.endpoint and _tower_is_healthy(config.endpoint, requests_get=requests_get, now=now):
        try:
            text = _transcribe_file_remote(audio_path, config, requests_post=requests_post)
            return {
                "text": text,
                "via": "tower",
                "latency_ms": int((now() - start) * 1000),
                "error": None,
                "used_endpoint": config.endpoint,
            }
        except Exception as exc:
            _mark_tower_unhealthy(str(exc), now=now)
            tower_error = f"tower transcription failed: {exc}"
            _log.warning(tower_error)
    else:
        tower_error = _tower_state.get("last_error") if config.endpoint else "no endpoint configured"

    try:
        text = _transcribe_file_local(audio_path, config)
        return {
            "text": text,
            "via": "local",
            "latency_ms": int((now() - start) * 1000),
            "error": tower_error if config.endpoint else None,
            "used_endpoint": None,
        }
    except Exception as exc:
        local_error = f"local transcription failed: {exc}"
        _log.error(local_error)
        combined = tower_error + " | " + local_error if config.endpoint and tower_error else local_error
        return {
            "text": "",
            "via": "failed",
            "latency_ms": int((now() - start) * 1000),
            "error": combined,
            "used_endpoint": None,
        }


def probe_tower(
    endpoint: str,
    requests_get: Callable[..., Any] = requests.get,
    connect_timeout: float = HEALTH_PROBE_CONNECT_TIMEOUT,
    read_timeout: float = HEALTH_PROBE_READ_TIMEOUT,
) -> tuple[bool, str | None]:
    """Check Tower WhisperX health. Returns (healthy, error_message)."""
    if not endpoint:
        return False, "no endpoint configured"
    try:
        resp = requests_get(
            f"{endpoint}/health",
            timeout=(connect_timeout, read_timeout),
        )
        if resp.status_code == 200:
            return True, None
        return False, f"health probe returned HTTP {resp.status_code}"
    except Exception as exc:
        return False, f"health probe failed: {exc}"


def _tower_is_healthy(
    endpoint: str,
    requests_get: Callable[..., Any] = requests.get,
    now: Callable[[], float] = time.monotonic,
) -> bool:
    with _tower_state_lock:
        age = now() - _tower_state["checked_at"]
        cached = _tower_state["checked_at"] > 0 and age < TOWER_STATE_RECHECK_SECONDS
        if cached:
            return bool(_tower_state["healthy"])

    healthy, error = probe_tower(endpoint, requests_get=requests_get)
    with _tower_state_lock:
        _tower_state["healthy"] = healthy
        _tower_state["checked_at"] = now()
        _tower_state["last_error"] = error
    return healthy


def _mark_tower_unhealthy(error: str, now: Callable[[], float] = time.monotonic) -> None:
    with _tower_state_lock:
        _tower_state["healthy"] = False
        _tower_state["checked_at"] = now()
        _tower_state["last_error"] = error


def reset_tower_state() -> None:
    """Test hook: clear the cached Tower health so the next call re-probes."""
    with _tower_state_lock:
        _tower_state["healthy"] = True
        _tower_state["checked_at"] = 0.0
        _tower_state["last_error"] = None


def get_tower_state() -> dict[str, Any]:
    """Return a copy of the cached Tower state for status / debugging."""
    with _tower_state_lock:
        return dict(_tower_state)


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


def _get_local_model(model_size: str) -> Any:
    """Lazy-load and cache one faster-whisper model per process."""
    global _local_model, _local_model_size
    with _local_model_lock:
        if _local_model is None or _local_model_size != model_size:
            from faster_whisper import WhisperModel

            _log.info(f"Loading local faster-whisper model: {model_size} (cpu/int8)")
            _local_model = WhisperModel(model_size, device="cpu", compute_type="int8")
            _local_model_size = model_size
        return _local_model


def warm_local_model(model_size: str | None = None) -> None:
    """Pre-load the local faster-whisper model so first-fallback latency is low."""
    size = model_size or load_voice_config().model
    _get_local_model(size)


def _transcribe_file_local(audio_path: Path, config: VoiceConfig) -> str:
    model = _get_local_model(config.model)
    segments, _ = model.transcribe(
        str(audio_path),
        language="en",
        condition_on_previous_text=False,
        initial_prompt=_LOCAL_INITIAL_PROMPT,
        vad_filter=True,
        no_speech_threshold=0.4,
        compression_ratio_threshold=2.1,
        log_prob_threshold=-0.8,
        beam_size=5,
    )
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
    return _transcribe_bytes(
        filename,
        response.content,
        config=config,
        requests_post=requests_post,
        requests_get=requests_get,
    )


async def transcribe_attachment(
    attachment: Any,
    config: VoiceConfig | None = None,
    requests_post: Callable[..., Any] = requests.post,
    requests_get: Callable[..., Any] = requests.get,
) -> str:
    filename = str(getattr(attachment, "filename", "") or "voice.ogg")
    audio_bytes = await attachment.read()
    return await asyncio.to_thread(
        _transcribe_bytes, filename, audio_bytes, config, requests_post, requests_get
    )


def _transcribe_bytes(
    filename: str,
    audio_bytes: bytes,
    config: VoiceConfig | None = None,
    requests_post: Callable[..., Any] = requests.post,
    requests_get: Callable[..., Any] = requests.get,
) -> str:
    suffix = Path(filename).suffix or ".ogg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)
    try:
        return transcribe_file(
            tmp_path,
            config=config,
            requests_post=requests_post,
            requests_get=requests_get,
        )
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
