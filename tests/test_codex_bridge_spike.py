import queue
import threading
import time
import unittest

from codex_bridge_spike import wait_for_ready_after
from codex_terminal import PROMPT_MARKERS


class CodexBridgeSpikeTests(unittest.TestCase):
    def test_wait_for_ready_after_requires_working_before_ready(self):
        chunks: "queue.Queue[str]" = queue.Queue()
        chunks.put(f"{PROMPT_MARKERS[0]} Use /skills to list available skills gpt-5.5 default ~\\Life Org")
        transcript: list[str] = []

        result = wait_for_ready_after(
            chunks,
            transcript,
            start_index=0,
            timeout_seconds=0.1,
            settle_seconds=0,
        )
        self.assertFalse(result.completed)
        self.assertEqual(result.reason, "timeout")

    def test_wait_for_ready_after_accepts_settled_ready_after_working(self):
        chunks: "queue.Queue[str]" = queue.Queue()
        chunks.put("Working (0s esc to interrupt)")
        chunks.put(f"{PROMPT_MARKERS[0]} Use /skills to list available skills gpt-5.5 default ~\\Life Org")
        transcript: list[str] = []

        result = wait_for_ready_after(
            chunks,
            transcript,
            start_index=0,
            timeout_seconds=1,
            settle_seconds=0,
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.reason, "ready_screen")

    def test_wait_for_ready_after_accepts_repainted_ready_screen(self):
        chunks: "queue.Queue[str]" = queue.Queue()
        chunks.put("Working (0s esc to interrupt)")
        transcript: list[str] = []
        ready = f"{PROMPT_MARKERS[0]} Summarize recent commits gpt-5.4 default ~\\Life Org"

        def repaint_ready_screen() -> None:
            deadline = time.monotonic() + 0.35
            while time.monotonic() < deadline:
                chunks.put(ready)
                time.sleep(0.03)

        thread = threading.Thread(target=repaint_ready_screen)
        thread.start()
        try:
            result = wait_for_ready_after(
                chunks,
                transcript,
                start_index=0,
                timeout_seconds=0.3,
                settle_seconds=0.1,
            )
            self.assertTrue(result.completed)
            self.assertEqual(result.reason, "ready_screen")
        finally:
            thread.join()

    def test_wait_for_ready_after_accepts_answer_smashed_into_ready_prompt(self):
        chunks: "queue.Queue[str]" = queue.Queue()
        chunks.put(
            "Working (0s esc to interrupt)\n"
            "â€¢ codex discord e2e okWogâ€ºImplement {feature}gpt-5.5 default Â· ~\\Life Org"
        )
        transcript: list[str] = []

        result = wait_for_ready_after(
            chunks,
            transcript,
            start_index=0,
            timeout_seconds=1,
            settle_seconds=0,
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.best_answer, "codex discord e2e ok")

    def test_wait_for_ready_after_accepts_answer_followed_by_model_status(self):
        chunks: "queue.Queue[str]" = queue.Queue()
        chunks.put(
            "Working (0s esc to interrupt)\n"
            "â€¢ codex discord e2e okWoggpt-5.5 default Â· ~\\Life Org"
        )
        transcript: list[str] = []

        result = wait_for_ready_after(
            chunks,
            transcript,
            start_index=0,
            timeout_seconds=1,
            settle_seconds=0,
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.reason, "answer_with_model_status")
        self.assertEqual(result.best_answer, "codex discord e2e ok")

    def test_wait_for_ready_after_checks_queued_chunks_at_deadline(self):
        chunks: "queue.Queue[str]" = queue.Queue()
        chunks.put(
            "Working (0s esc to interrupt)\n"
            "â€¢ codex discord e2e okWoggpt-5.5 default Â· ~\\Life Org"
        )
        transcript: list[str] = []

        result = wait_for_ready_after(
            chunks,
            transcript,
            start_index=0,
            timeout_seconds=0,
            settle_seconds=0,
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.best_answer, "codex discord e2e ok")


if __name__ == "__main__":
    unittest.main()
