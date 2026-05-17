from __future__ import annotations

import asyncio
import argparse
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from codex_exec import CodexBridgeState, CodexExecConfig, CodexExecRunner, CodexExecSession
from codex_session import CodexSession, CodexSessionConfig


if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_DIR = Path(__file__).parent
ENV_PATH = REPO_DIR.parent / ".env"
DEFAULT_WORKSPACE = Path(r"C:\Users\drews\Life Org")
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
            await bridge.enqueue_prompt(message, message.content)

    client.run(config.token)


if __name__ == "__main__":
    main()
