from __future__ import annotations

import json
import re
import subprocess
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CodexExecConfig:
    """Configuration for non-interactive Codex turns."""

    workspace: Path
    output_dir: Path
    state_path: Path | None = None
    turn_log_path: Path | None = None
    codex_bin: str = "codex"
    timeout_seconds: int = 180


@dataclass
class CodexBridgeState:
    """Local runtime state for the Discord-owned Codex session."""

    session_id: str | None = None
    mode: str = "exec-resume"
    session_source: str = ""
    session_file: str = ""
    last_output_file: str = ""
    last_success_at: str = ""
    last_error: str = ""
    last_elapsed_seconds: float | None = None
    last_answer_length: int | None = None

    @classmethod
    def load(cls, path: Path | None) -> "CodexBridgeState":
        if path is None or not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        allowed = {field: data.get(field) for field in cls.__dataclass_fields__ if field in data}
        return cls(**allowed)

    def save(self, path: Path | None) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def parse_session_id(stdout: str) -> str | None:
    """Extract a Codex session id from CLI stdout."""
    match = re.search(r"session id:\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", stdout, re.I)
    return match.group(1) if match else None


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
        state = CodexBridgeState.load(self.config.state_path)
        return self._run_turn(prompt, state=state, resume=True)

    def reset(self, seed_prompt: str | None = None) -> str:
        """Create a fresh bridge-owned Codex session and save its id."""
        prompt = seed_prompt or "You are Codex Lane 1 for Drew's Discord #codex channel. Reply briefly that the session is ready."
        state = CodexBridgeState(mode="exec-resume", session_source="codex reset")
        self._run_turn(prompt, state=state, resume=False, event_prefix="reset")
        if not state.session_id:
            raise RuntimeError("Codex reset did not return a session id")
        return state.session_id

    def _run_turn(
        self,
        prompt: str,
        state: CodexBridgeState,
        resume: bool,
        event_prefix: str = "turn",
    ) -> str:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.config.output_dir / f"codex-last-message-{int(time.time() * 1000)}.txt"
        args = self._build_args(prompt, output_path, session_id=state.session_id if resume else None)
        start = time.monotonic()
        result = self._run_command(
            args,
            cwd=str(self.config.workspace),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            input=prompt,
            text=True,
            timeout=self.config.timeout_seconds,
        )
        elapsed = time.monotonic() - start
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or f"codex exec exited with {result.returncode}"
            state.last_error = detail
            state.save(self.config.state_path)
            self._write_turn_log(
                {
                    "event": f"{event_prefix}_error",
                    "session_id": state.session_id,
                    "prompt_length": len(prompt),
                    "elapsed_seconds": round(elapsed, 3),
                    "error": detail,
                }
            )
            raise RuntimeError(detail)
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        session_id = parse_session_id(stdout)
        if session_id:
            state.session_id = session_id
        if output_path.exists():
            answer = output_path.read_text(encoding="utf-8").strip()
        else:
            answer = stdout.strip()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        state.last_output_file = str(output_path)
        state.last_success_at = now
        state.last_error = ""
        state.last_elapsed_seconds = round(elapsed, 3)
        state.last_answer_length = len(answer)
        state.save(self.config.state_path)
        self._write_turn_log(
            {
                "event": f"{event_prefix}_success",
                "session_id": state.session_id,
                "prompt_length": len(prompt),
                "answer_length": len(answer),
                "elapsed_seconds": round(elapsed, 3),
                "output_file": str(output_path),
            }
        )
        return answer

    def _build_args(self, prompt: str, output_path: Path, session_id: str | None = None) -> Sequence[str]:
        codex_bin = shutil.which(self.config.codex_bin) or self.config.codex_bin
        args = [
            codex_bin,
            "exec",
        ]
        if session_id:
            args.extend(["resume", session_id])
        else:
            args.extend(["--cd", str(self.config.workspace)])
        args.extend(
            [
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "-o",
                str(output_path),
                "-",
            ]
        )
        return args

    def _write_turn_log(self, event: dict[str, Any]) -> None:
        if self.config.turn_log_path is None:
            return
        self.config.turn_log_path.parent.mkdir(parents=True, exist_ok=True)
        event = {"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"), **event}
        with self.config.turn_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")


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
        return self._runner.reset()
