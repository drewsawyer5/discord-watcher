from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from codex_process import CodexProcessConfig, CodexProcessController
from codex_terminal import extract_turn_text, is_ready_screen


DEFAULT_WORKSPACE = Path(r"C:\Users\drews\Life Org")
DEFAULT_LOG_PATH = Path(__file__).parent / "codex_bridge_spike.log"


def wait_for_ready(chunks: "queue.Queue[str]", transcript: list[str], timeout_seconds: int) -> bool:
    """Wait until Codex appears ready for input.

    Args:
        chunks: Queue populated by the PTY reader thread.
        transcript: Accumulated raw transcript chunks.
        timeout_seconds: Maximum seconds to wait.

    Returns:
        True if ready state is detected before timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        drain_chunks(chunks, transcript)
        tail = "".join(transcript)[-6000:]
        if is_ready_screen(tail):
            return True
        time.sleep(0.25)
    return False


def wait_for_text_after(
    chunks: "queue.Queue[str]",
    transcript: list[str],
    text: str,
    start_index: int,
    timeout_seconds: int,
) -> bool:
    """Wait until specific text appears after a transcript offset.

    Args:
        chunks: Queue populated by the PTY reader thread.
        transcript: Accumulated raw transcript chunks.
        text: Text to wait for.
        start_index: Raw transcript offset to search from.
        timeout_seconds: Maximum seconds to wait.

    Returns:
        True if text appears after start_index before timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        drain_chunks(chunks, transcript)
        if text in "".join(transcript)[start_index:]:
            return True
        time.sleep(0.25)
    return False


def wait_for_ready_after(
    chunks: "queue.Queue[str]",
    transcript: list[str],
    start_index: int,
    timeout_seconds: int,
    settle_seconds: float = 2.0,
) -> bool:
    """Wait until Codex appears ready in output after a transcript offset.

    Args:
        chunks: Queue populated by the PTY reader thread.
        transcript: Accumulated raw transcript chunks.
        start_index: Raw transcript offset to inspect from.
        timeout_seconds: Maximum seconds to wait.

    Returns:
        True if a working state is followed by a settled ready state before timeout.
    """
    deadline = time.monotonic() + timeout_seconds
    saw_working = False
    ready_since: float | None = None
    ready_length = 0
    while time.monotonic() < deadline:
        before_length = len("".join(transcript))
        drain_chunks(chunks, transcript)
        raw_after_prompt = "".join(transcript)[start_index:]
        current_length = start_index + len(raw_after_prompt)
        if "Working" in raw_after_prompt or "esc to interrupt" in raw_after_prompt:
            saw_working = True
        if saw_working and is_ready_screen(raw_after_prompt):
            if ready_since is None or current_length != ready_length:
                ready_since = time.monotonic()
                ready_length = current_length
            elif time.monotonic() - ready_since >= settle_seconds:
                return True
        else:
            ready_since = None
        if current_length != before_length:
            ready_length = current_length
        time.sleep(0.25)
    return False


def drain_chunks(chunks: "queue.Queue[str]", transcript: list[str]) -> None:
    """Move all queued PTY chunks into the transcript.

    Args:
        chunks: Queue populated by the PTY reader thread.
        transcript: Accumulated raw transcript chunks.
    """
    while True:
        try:
            transcript.append(chunks.get_nowait())
        except queue.Empty:
            return


def start_reader(
    controller: CodexProcessController,
    chunks: "queue.Queue[str]",
    stop_event: threading.Event,
) -> threading.Thread:
    """Start a background thread that reads Codex PTY output.

    Args:
        controller: Running Codex process controller.
        chunks: Queue receiving raw PTY chunks.
        stop_event: Signal to stop reading.

    Returns:
        Started daemon thread.
    """
    def reader() -> None:
        while not stop_event.is_set():
            try:
                chunk = controller.read_once(4096)
            except EOFError:
                break
            except Exception as exc:
                chunks.put(f"\n[reader error: {exc!r}]\n")
                break
            if chunk:
                chunks.put(chunk)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return thread


def write_raw_log(path: Path, prompt: str, raw: str) -> None:
    """Append a raw spike transcript to disk.

    Args:
        path: Log file path.
        prompt: Prompt sent to Codex.
        raw: Raw PTY transcript.
    """
    timestamp = datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n===== {timestamp} =====\n")
        handle.write(f"PROMPT: {prompt}\n")
        handle.write(raw)
        handle.write("\n===== END =====\n")


def print_text(text: str) -> None:
    """Print extracted text without failing on a narrow Windows console encoding.

    Args:
        text: Text to print.
    """
    print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))


def run_spike(prompt: str, workspace: Path, log_path: Path, timeout_seconds: int) -> int:
    """Run one local Codex bridge process-control spike.

    Args:
        prompt: Prompt to send to Codex.
        workspace: Codex working directory.
        log_path: Raw transcript log path.
        timeout_seconds: Timeout for ready/turn waits.

    Returns:
        Process exit code.
    """
    config = CodexProcessConfig(
        codex_bin="codex",
        workspace=workspace,
        startup_timeout_seconds=10,
    )
    controller = CodexProcessController(config)
    chunks: "queue.Queue[str]" = queue.Queue()
    transcript: list[str] = []
    stop_event = threading.Event()

    try:
        controller.start()
        start_reader(controller, chunks, stop_event)
        if not wait_for_ready(chunks, transcript, timeout_seconds):
            drain_chunks(chunks, transcript)
            write_raw_log(log_path, prompt, "".join(transcript))
            print("Codex did not become ready before timeout.", file=sys.stderr)
            return 1

        start_index = len("".join(transcript))
        controller.send_prompt(prompt)
        if not wait_for_text_after(chunks, transcript, prompt, start_index, timeout_seconds):
            drain_chunks(chunks, transcript)
            raw = "".join(transcript)
            write_raw_log(log_path, prompt, raw)
            print("Codex did not echo the prompt before timeout.", file=sys.stderr)
            print_text(extract_turn_text(raw[start_index:]))
            return 1

        if not wait_for_ready_after(chunks, transcript, start_index, timeout_seconds):
            drain_chunks(chunks, transcript)
            raw = "".join(transcript)
            write_raw_log(log_path, prompt, raw)
            print("Codex did not return to ready before timeout.", file=sys.stderr)
            print_text(extract_turn_text(raw[start_index:]))
            return 1

        drain_chunks(chunks, transcript)
        raw = "".join(transcript)
        write_raw_log(log_path, prompt, raw)
        print_text(extract_turn_text(raw[start_index:]))
        return 0
    finally:
        stop_event.set()
        controller.stop()


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Run a local Codex PTY bridge spike.")
    parser.add_argument("prompt", help="Prompt to send to Codex.")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE), help="Codex working directory.")
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH), help="Raw transcript log path.")
    parser.add_argument("--timeout", type=int, default=120, help="Seconds to wait for ready states.")
    return parser.parse_args()


def main() -> None:
    """Run the command line spike."""
    args = parse_args()
    raise SystemExit(
        run_spike(
            prompt=args.prompt,
            workspace=Path(args.workspace),
            log_path=Path(args.log_path),
            timeout_seconds=args.timeout,
        )
    )


if __name__ == "__main__":
    main()
