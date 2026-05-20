from __future__ import annotations

import asyncio
import argparse
import logging
import logging.handlers
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests
from dotenv import load_dotenv

from codex_exec import CodexBridgeState, CodexExecConfig, CodexExecRunner, CodexExecSession
from codex_session import CodexSession, CodexSessionConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "discord-watcher"))
import discord_voice


if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_DIR = Path(__file__).parent
ENV_PATH = REPO_DIR.parent / ".env"
DEFAULT_WORKSPACE = Path(r"C:\Users\drews\Life Org")
DEFAULT_CODEX_VOICE_DIR = DEFAULT_WORKSPACE / "Obsidian" / ".audiofiles" / "codex_bridge"
DEFAULT_CODEX_CHANNEL_ID = 1475166363201962077
MAX_DISCORD_MESSAGE_LENGTH = 1900

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscordBridgeConfig:
    token: str
    codex_channel_id: int
    drew_user_id: int | None
    workspace: Path
    turn_timeout_seconds: int
    log_path: Path
    output_dir: Path
    state_path: Path
    turn_log_path: Path
    session_mode: str
    voice_dir: Path
    voice_ready_delay_seconds: float


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    handler = logging.handlers.RotatingFileHandler(
        REPO_DIR / "codex_discord_bridge.log",
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    logging.getLogger().addHandler(handler)


def load_config() -> DiscordBridgeConfig:
    load_dotenv(ENV_PATH)
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    drew_user_id = _optional_int(os.getenv("DREW_USER_ID", ""))
    return DiscordBridgeConfig(
        token=token,
        codex_channel_id=int(os.getenv("CODEX_CHANNEL_ID", str(DEFAULT_CODEX_CHANNEL_ID))),
        drew_user_id=drew_user_id,
        workspace=Path(os.getenv("CODEX_WORKSPACE", str(DEFAULT_WORKSPACE))),
        turn_timeout_seconds=int(os.getenv("CODEX_TURN_TIMEOUT_SECONDS", "180")),
        log_path=Path(os.getenv("CODEX_RAW_LOG_PATH", str(REPO_DIR / "codex_discord_bridge_raw.log"))),
        output_dir=Path(os.getenv("CODEX_OUTPUT_DIR", str(REPO_DIR / "codex_exec_outputs"))),
        state_path=Path(os.getenv("CODEX_STATE_PATH", str(REPO_DIR / "codex_bridge_state.json"))),
        turn_log_path=Path(os.getenv("CODEX_TURN_LOG_PATH", str(REPO_DIR / "codex_exec_turns.log"))),
        session_mode=os.getenv("CODEX_SESSION_MODE", "exec").strip().lower(),
        voice_dir=Path(os.getenv("CODEX_VOICE_DIR", str(DEFAULT_CODEX_VOICE_DIR))),
        voice_ready_delay_seconds=float(os.getenv("CODEX_VOICE_READY_DELAY_SECONDS", "1.0")),
    )


def classify_message(content: str) -> str:
    """Classify a Discord message as prompt, restart, or status."""
    text = content.strip().lower()
    compact = " ".join(text.split())
    if compact in {"!codex-restart", "/codex-restart", "restart codex", "restart-session"}:
        return "restart"
    if "restart" in compact and "codex" in compact:
        return "restart"
    if "clean" in compact and "codex" in compact and "session" in compact:
        return "restart"
    if compact in {"!codex-status", "/codex-status", "codex status", "status"}:
        return "status"
    if "codex" in compact and any(phrase in compact for phrase in ("are you up", "are you running", "status")):
        return "status"
    return "prompt"


def split_discord_message(content: str, limit: int = MAX_DISCORD_MESSAGE_LENGTH) -> list[str]:
    """Split a response into Discord-safe message chunks."""
    if len(content) <= limit:
        return [content]
    chunks: list[str] = []
    remaining = content
    while remaining:
        chunk = remaining[:limit]
        split_at = chunk.rfind("\n")
        if split_at > 200:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        remaining = remaining[len(chunk):].lstrip("\n")
    return chunks


def _optional_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


def build_prompt_from_message(message: object, transcribe: Callable[[object], str]) -> tuple[str | None, str]:
    """Build a Codex prompt from text or the first audio attachment."""
    content = str(getattr(message, "content", "") or "").strip()
    attachments = list(getattr(message, "attachments", []) or [])
    audio_attachment, ignored_audio_count = discord_voice.select_first_audio_attachment(attachments)
    if audio_attachment is None:
        return content, ""

    transcript = transcribe(audio_attachment).strip()
    if not discord_voice.is_usable_transcript(transcript):
        return None, "Couldn't transcribe that - try again?"

    warnings = []
    if ignored_audio_count:
        warnings.append(f"Ignored {ignored_audio_count} extra audio attachment{'s' if ignored_audio_count != 1 else ''}.")
    if len(attachments) > ignored_audio_count + 1:
        warnings.append("Ignored non-audio attachment(s).")
    return discord_voice.format_voice_prompt(transcript, content), " ".join(warnings)


def build_prompt_from_message_payload(
    payload: dict,
    transcribe: Callable[[dict], str],
) -> tuple[str | None, str]:
    content = str(payload.get("content", "") or "").strip()
    attachments = list(payload.get("attachments", []) or [])
    audio_attachment, ignored_audio_count = discord_voice.select_first_audio_attachment_dict(attachments)
    if audio_attachment is None:
        return content, ""

    transcript = transcribe(audio_attachment).strip()
    if not discord_voice.is_usable_transcript(transcript):
        return None, "Couldn't transcribe that - try again?"

    warnings = []
    if ignored_audio_count:
        warnings.append(f"Ignored {ignored_audio_count} extra audio attachment{'s' if ignored_audio_count != 1 else ''}.")
    if len(attachments) > ignored_audio_count + 1:
        warnings.append("Ignored non-audio attachment(s).")
    return discord_voice.format_voice_prompt(transcript, content), " ".join(warnings)


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return value.strip("-") or "voice-message"


def download_attachment_bytes(attachment: dict) -> bytes:
    response = requests.get(attachment["url"], timeout=30)
    response.raise_for_status()
    return response.content


def wait_for_stable_file(path: Path, checks: int = 2, interval_seconds: float = 0.25) -> None:
    """Wait until a downloaded audio file has stopped changing before transcription."""
    last_size = -1
    stable_checks = 0
    while stable_checks < checks:
        current_size = path.stat().st_size
        if current_size == last_size:
            stable_checks += 1
        else:
            stable_checks = 0
            last_size = current_size
        time.sleep(interval_seconds)


def transcribe_payload_audio_to_sidecar(
    payload: dict,
    voice_dir: Path,
    download: Callable[[dict], bytes] = download_attachment_bytes,
    transcribe_file: Callable[[Path], str] = discord_voice.transcribe_file,
    wait_for_file: Callable[[Path], None] = wait_for_stable_file,
) -> tuple[str | None, str]:
    content = str(payload.get("content", "") or "").strip()
    attachments = list(payload.get("attachments", []) or [])
    audio_attachment, ignored_audio_count = discord_voice.select_first_audio_attachment_dict(attachments)
    if audio_attachment is None:
        return content, ""

    message_id = _safe_filename(str(payload.get("id", "") or "message"))
    attachment_id = _safe_filename(str(audio_attachment.get("id", "") or "attachment"))
    filename = _safe_filename(str(audio_attachment.get("filename", "") or "voice-message.ogg"))
    if not Path(filename).suffix:
        filename += ".ogg"

    voice_dir.mkdir(parents=True, exist_ok=True)
    audio_path = voice_dir / f"{message_id}-{attachment_id}-{filename}"
    txt_path = audio_path.with_suffix(".txt")

    if txt_path.exists():
        transcript = txt_path.read_text(encoding="utf-8").strip()
    else:
        audio_bytes = download(audio_attachment)
        audio_path.write_bytes(audio_bytes)
        wait_for_file(audio_path)
        transcript = transcribe_file(audio_path).strip()
        txt_path.write_text(transcript, encoding="utf-8")

    if not discord_voice.is_usable_transcript(transcript):
        return None, "Couldn't transcribe that - try again?"

    warnings = []
    if ignored_audio_count:
        warnings.append(f"Ignored {ignored_audio_count} extra audio attachment{'s' if ignored_audio_count != 1 else ''}.")
    if len(attachments) > ignored_audio_count + 1:
        warnings.append("Ignored non-audio attachment(s).")
    return discord_voice.format_voice_prompt(transcript, content), " ".join(warnings)


def fetch_message_payload(config: DiscordBridgeConfig, message_id: int | str) -> dict:
    response = requests.get(
        f"https://discord.com/api/v10/channels/{config.codex_channel_id}/messages/{message_id}",
        headers={"Authorization": f"Bot {config.token}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


async def build_prompt_from_message_async(message: object) -> tuple[str | None, str]:
    content = str(getattr(message, "content", "") or "").strip()
    attachments = list(getattr(message, "attachments", []) or [])
    audio_attachment, ignored_audio_count = discord_voice.select_first_audio_attachment(attachments)
    if audio_attachment is None:
        return content, ""

    transcript = (await discord_voice.transcribe_attachment(audio_attachment)).strip()
    if not discord_voice.is_usable_transcript(transcript):
        return None, "Couldn't transcribe that - try again?"

    warnings = []
    if ignored_audio_count:
        warnings.append(f"Ignored {ignored_audio_count} extra audio attachment{'s' if ignored_audio_count != 1 else ''}.")
    if len(attachments) > ignored_audio_count + 1:
        warnings.append("Ignored non-audio attachment(s).")
    return discord_voice.format_voice_prompt(transcript, content), " ".join(warnings)


async def build_prompt_for_bridge(config: DiscordBridgeConfig, message: object) -> tuple[str | None, str]:
    attachments = list(getattr(message, "attachments", []) or [])
    audio_attachment, _ = discord_voice.select_first_audio_attachment(attachments)
    content = str(getattr(message, "content", "") or "").strip()
    message_id = getattr(message, "id", "")
    log.info(
        "message_input id=%s content_chars=%s attachments=%s audio_detected=%s",
        message_id,
        len(content),
        len(attachments),
        bool(audio_attachment),
    )
    if audio_attachment is not None or content.lower() == "voice transcript:" or int(getattr(message, "flags", 0) or 0) & 8192:
        reason = "gateway_audio" if audio_attachment is not None else "voice_without_gateway_audio"
        log.info("message_fetch_fallback id=%s reason=%s", message_id, reason)
        if config.voice_ready_delay_seconds > 0:
            await asyncio.sleep(config.voice_ready_delay_seconds)
        payload = await asyncio.to_thread(fetch_message_payload, config, message_id)
        prompt, warning = await asyncio.to_thread(
            transcribe_payload_audio_to_sidecar,
            payload,
            config.voice_dir,
        )
        log.info(
            "message_fetch_fallback_result id=%s rest_attachments=%s prompt_chars=%s warning=%s voice_dir=%s",
            message_id,
            len(payload.get("attachments", []) or []),
            len(prompt or ""),
            warning,
            config.voice_dir,
        )
        return prompt, warning

    return content, ""


def build_codex_session(config: DiscordBridgeConfig) -> object:
    """Build the bridge session implementation selected by configuration."""
    if config.session_mode == "pty":
        return CodexSession(
            CodexSessionConfig(
                workspace=config.workspace,
                log_path=config.log_path,
                turn_timeout_seconds=config.turn_timeout_seconds,
            )
        )
    if config.session_mode != "exec":
        raise ValueError(f"Unsupported CODEX_SESSION_MODE: {config.session_mode}")
    return CodexExecSession(
        CodexExecRunner(
            CodexExecConfig(
                workspace=config.workspace,
                output_dir=config.output_dir,
                state_path=config.state_path,
                turn_log_path=config.turn_log_path,
                timeout_seconds=config.turn_timeout_seconds,
            )
        )
    )


class CodexDiscordBridge:
    """Discord gateway wrapper for one persistent Codex session."""

    def __init__(
        self,
        config: DiscordBridgeConfig,
        session: object,
        state_loader: Callable[[Path], CodexBridgeState] = CodexBridgeState.load,
    ) -> None:
        self.config = config
        self.session = session
        self._state_loader = state_loader
        self.queue: "asyncio.Queue[tuple[object, str]]" = asyncio.Queue()

    async def enqueue_prompt(self, message: object, prompt: str) -> None:
        await self.queue.put((message, prompt))
        position = self.queue.qsize()
        if position > 1:
            await message.reply(f"Codex is working. Queued at position {position}.")

    async def worker(self) -> None:
        while True:
            message, prompt = await self.queue.get()
            try:
                await message.reply("Codex working...")
                answer = await asyncio.to_thread(self.session.ask, prompt)
                if not answer:
                    answer = "(Codex returned no extractable text; raw log was saved.)"
                for chunk in split_discord_message(answer):
                    await message.channel.send(chunk)
            except Exception as exc:
                log.exception("Codex prompt failed")
                await message.channel.send(f"Codex bridge error: {exc}")
            finally:
                self.queue.task_done()

    async def restart(self, message: object) -> None:
        await message.reply("Restarting Codex session...")
        session_id = await asyncio.to_thread(self.session.restart)
        if session_id:
            await message.channel.send(f"Codex session reset: {session_id}")
        else:
            await message.channel.send("Codex session restarted.")

    async def status(self, message: object) -> None:
        state = self._state_loader(self.config.state_path)
        await message.reply(
            "Codex bridge online. "
            f"Mode: {self.config.session_mode}. "
            f"Queue depth: {self.queue.qsize()}. "
            f"Session: {state.session_id or 'none'}. "
            f"Last success: {state.last_success_at or 'none'}. "
            f"Last output: {state.last_output_file or 'none'}."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Lane 1 Codex Discord bridge.")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and imports without connecting.")
    args = parser.parse_args()

    setup_logging()
    config = load_config()
    if not config.token:
        raise SystemExit("DISCORD_BOT_TOKEN is not set")

    try:
        import discord
    except ImportError as exc:
        raise SystemExit("discord.py is not installed. Run: pip install -r requirements.txt") from exc

    if args.dry_run:
        print(
            "Codex Discord bridge config OK "
            f"(channel={config.codex_channel_id}, workspace={config.workspace}, mode={config.session_mode})"
        )
        return

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    session = build_codex_session(config)
    bridge = CodexDiscordBridge(config, session)
    worker_task: asyncio.Task[None] | None = None

    @client.event
    async def on_ready() -> None:
        nonlocal worker_task
        log.info("Logged in as %s", client.user)
        if worker_task is None or worker_task.done():
            worker_task = client.loop.create_task(bridge.worker())
        channel = client.get_channel(config.codex_channel_id)
        if channel is not None:
            await channel.send("Codex bridge online.")

    @client.event
    async def on_message(message: object) -> None:
        if message.author.bot:
            return
        if message.channel.id != config.codex_channel_id:
            return
        if config.drew_user_id is not None and message.author.id != config.drew_user_id:
            return

        action = classify_message(message.content)
        if action == "restart":
            await bridge.restart(message)
        elif action == "status":
            await bridge.status(message)
        else:
            try:
                prompt, warning = await build_prompt_for_bridge(config, message)
            except Exception as exc:
                log.exception("Codex voice/text prompt preparation failed")
                await message.reply(f"Codex bridge could not prepare that message: {exc}")
                return
            if warning:
                await message.reply(warning)
            if prompt:
                await bridge.enqueue_prompt(message, prompt)

    client.run(config.token)


if __name__ == "__main__":
    main()
