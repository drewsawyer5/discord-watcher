from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from winpty import PtyProcess


ProcessFactory = Callable[..., Any]


@dataclass(frozen=True)
class CodexProcessConfig:
    """Configuration for the Lane 1 Codex child process."""

    codex_bin: str
    workspace: Path
    startup_timeout_seconds: int = 10


class CodexProcessController:
    """Owns one interactive Codex process for the Discord bridge."""

    def __init__(
        self,
        config: CodexProcessConfig,
        process_factory: ProcessFactory = PtyProcess.spawn,
    ) -> None:
        self.config = config
        self._process_factory = process_factory
        self._process: Any | None = None

    def start(self) -> None:
        """Start Codex in interactive mode with Lane 1 bridge flags.

        Raises:
            RuntimeError: If the Codex process exits during startup.
        """
        if self._process is not None and self._process.isalive():
            return

        args = [
            self.config.codex_bin,
            "--cd",
            str(self.config.workspace),
            "--dangerously-bypass-approvals-and-sandbox",
            "--search",
            "--no-alt-screen",
        ]
        self._process = self._process_factory(args, cwd=str(self.config.workspace))
        self._raise_if_exited_during_startup()

    def send_prompt(self, prompt: str) -> None:
        """Send one prompt line to the running Codex process.

        Args:
            prompt: User prompt from Discord.

        Raises:
            RuntimeError: If Codex is not running or stdin is unavailable.
        """
        process = self._require_process()
        process.write(f"{prompt}\r\r")

    def read_once(self, size: int = 4096) -> str:
        """Read one chunk of output from the running Codex process.

        Args:
            size: Maximum characters to read.

        Returns:
            A chunk of terminal output.

        Raises:
            RuntimeError: If Codex is not running.
        """
        process = self._require_process()
        return process.read(size)

    def stop(self) -> None:
        """Terminate the Codex process if it is running."""
        if self._process is None or not self._process.isalive():
            return
        self._process.terminate()

    def _require_process(self) -> Any:
        if self._process is None or not self._process.isalive():
            raise RuntimeError("Codex process is not running")
        return self._process

    def _raise_if_exited_during_startup(self) -> None:
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            process = self._process
            if process is None:
                raise RuntimeError("Codex process was not started")
            if not process.isalive():
                raise RuntimeError("Codex exited during startup")
            return
