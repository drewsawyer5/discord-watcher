from __future__ import annotations

import subprocess
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CodexExecConfig:
    """Configuration for non-interactive Codex turns."""

    workspace: Path
    output_dir: Path
    codex_bin: str = "codex"
    timeout_seconds: int = 180


class CodexExecRunner:
    """Runs one Codex turn through `codex exec` and returns the final answer."""

    def __init__(
        self,
        config: CodexExecConfig,
        run_command: RunCommand = subprocess.run,
    ) -> None:
        self.config = config
        self._run_command = run_command

    def ask(self, prompt: str) -> str:
        """Run one non-interactive Codex prompt."""
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.config.output_dir / f"codex-last-message-{int(time.time() * 1000)}.txt"
        args = self._build_args(prompt, output_path)
        result = self._run_command(
            args,
            cwd=str(self.config.workspace),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=self.config.timeout_seconds,
        )
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or f"codex exec exited with {result.returncode}"
            raise RuntimeError(detail)
        if output_path.exists():
            return output_path.read_text(encoding="utf-8").strip()
        return (result.stdout or "").strip()

    def _build_args(self, prompt: str, output_path: Path) -> Sequence[str]:
        codex_bin = shutil.which(self.config.codex_bin) or self.config.codex_bin
        return [
            codex_bin,
            "exec",
            "--cd",
            str(self.config.workspace),
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-o",
            str(output_path),
            prompt,
        ]


class CodexExecSession:
    """Session-shaped wrapper for the Discord bridge.

    The bridge still calls `ask()` and `restart()`, but the first stable path is
    process-per-turn `codex exec`; live TUI/status can be layered in separately.
    """

    def __init__(self, runner: CodexExecRunner) -> None:
        self._runner = runner

    def ask(self, prompt: str) -> str:
        return self._runner.ask(prompt)

    def restart(self) -> None:
        return None
