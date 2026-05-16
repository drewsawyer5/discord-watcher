import queue
import unittest

from codex_bridge_spike import wait_for_ready_after
from codex_terminal import PROMPT_MARKERS


class CodexBridgeSpikeTests(unittest.TestCase):
    def test_wait_for_ready_after_requires_working_before_ready(self):
        chunks: "queue.Queue[str]" = queue.Queue()
        chunks.put(f"{PROMPT_MARKERS[0]} Use /skills to list available skills gpt-5.5 default ~\\Life Org")
        transcript: list[str] = []

        self.assertFalse(
            wait_for_ready_after(
                chunks,
                transcript,
                start_index=0,
                timeout_seconds=0.1,
                settle_seconds=0,
            )
        )

    def test_wait_for_ready_after_accepts_settled_ready_after_working(self):
        chunks: "queue.Queue[str]" = queue.Queue()
        chunks.put("Working (0s esc to interrupt)")
        chunks.put(f"{PROMPT_MARKERS[0]} Use /skills to list available skills gpt-5.5 default ~\\Life Org")
        transcript: list[str] = []

        self.assertTrue(
            wait_for_ready_after(
                chunks,
                transcript,
                start_index=0,
                timeout_seconds=1,
                settle_seconds=0,
            )
        )


if __name__ == "__main__":
    unittest.main()
