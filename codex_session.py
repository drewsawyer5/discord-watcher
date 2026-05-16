from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from codex_bridge_spike import (
    drain_chunks,
    start_reader,
    wait_for_ready,
    wait_for_ready_after,
    wait_for_text_after,
    write_raw_log,
)
from codex_process import CodexProcessConfig, CodexProcessController
from codex_terminal import extract_turn_text


ControllerFactory = Callable[[CodexProcessConfig], CodexProcessController]


@dataclass(frozen=True)
class CodexSessionConfig:
    """Configuration for one persistent Codex bridge session."""

    workspace: Path
    log_path: Path
    codex_bin: str = "codex"
    turn_timeout_seconds: int = 180


class CodexSession:
    """Persistent interactive Codex session backed by a Windows PTY."""

    def __init__(
        self,
        config: CodexSessionConfig,
        controller_factory: ControllerFactory = CodexProcessController,
    ) -> None:
        self.config = config
        self._controller_factory = controller_factory
        self._controller: CodexProcessController | None = None
        self._chunks: "queue.Queue[str]" = queue.Queue()
        self._transcript: list[str] = []
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start Codex and the PTY reader if needed."""
        with self._lock:
            if self._controller is not None:
                return
            controller = self._controller_factory(
                CodexProcessConfig(
                    codex_bin=self.config.codex_bin,
                    workspace=self.config.workspace,
                    startup_timeout_seconds=10,
                )
            )
            controller.start()
            self._stop_event.clear()
            start_reader(controller, self._chunks, self._stop_event)
            self._controller = controller

    def restart(self) -> None:
        """Stop and recreate the Codex process."""
        with self._lock:
            self._stop_locked()
            self._chunks = queue.Queue()
            self._transcript = []
            self._stop_event = threading.Event()
            self._controller = None
        self.start()

    def stop(self) -> None:
        """Stop the Codex process."""
        with self._lock:
            self._stop_locked()
            self._controller = None

    def ask(self, prompt: str) -> str:
        """Send one prompt to Codex and return a minimally cleaned final answer."""
        self.start()
        with self._lock:
            controller = self._require_controller()
            timeout = self.config.turn_timeout_seconds
            if not wait_for_ready(self._chunks, self._transcript, timeout):
                self._write_current_log(prompt)
                raise TimeoutError("Codex did not become ready before timeout")

            start_index = len("".join(self._transcript))
            controller.send_prompt(prompt)
            if not wait_for_text_after(self._chunks, self._transcript, prompt, start_index, timeout):
                self._write_current_log(prompt)
                raise TimeoutError("Codex did not echo the prompt before timeout")

            if not wait_for_ready_after(self._chunks, self._transcript, start_index, timeout):
                self._write_current_log(prompt)
                raise TimeoutError("Codex did not return to ready before timeout")

            drain_chunks(self._chunks, self._transcript)
            raw = "".join(self._transcript)
            write_raw_log(self.config.log_path, prompt, raw)
            return extract_turn_text(raw[start_index:])

    def _write_current_log(self, prompt: str) -> None:
        drain_chunks(self._chunks, self._transcript)
        write_raw_log(self.config.log_path, prompt, "".join(self._transcript))

    def _require_controller(self) -> CodexProcessController:
        if self._controller is None:
            raise RuntimeError("Codex session is not running")
        return self._controller

    def _stop_locked(self) -> None:
        self._stop_event.set()
        if self._controller is not None:
            self._controller.stop()
