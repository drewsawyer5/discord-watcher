"""#351: the no-rewhisper PreToolUse hook (~/.claude/hooks/no-rewhisper.py) tells Claude
whether to wait for voice_watcher's transcript or transcribe itself.

The hook lives outside this repo; these tests run it as Claude Code does (JSON on stdin,
exit 2 + stderr to block), pointing it at a temp inbox and status file through env vars.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HOOK = Path.home() / ".claude" / "hooks" / "no-rewhisper.py"


@unittest.skipUnless(HOOK.exists(), "no-rewhisper hook not installed on this box")
class NoRewhisperHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inbox = Path(self.tmp.name) / "inbox"
        self.inbox.mkdir()
        self.status = Path(self.tmp.name) / "status.json"
        self.set_last_seen(minutes_ago=2)

    def set_last_seen(self, minutes_ago):
        seen = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        self.status.write_text(json.dumps({"last_seen": seen}), encoding="utf-8")

    def add_ogg(self, name="memo", mb=0.1):
        (self.inbox / f"{name}.ogg").write_bytes(b"\0" * int(mb * 1024 * 1024))

    def run_hook(self, command, tool="Bash", stdin=None):
        env = dict(os.environ, NO_REWHISPER_INBOX=str(self.inbox), NO_REWHISPER_STATUS=str(self.status))
        payload = stdin if stdin is not None else json.dumps({"tool_name": tool, "tool_input": {"command": command}})
        return subprocess.run(
            [sys.executable, "-I", str(HOOK)], input=payload, capture_output=True, text=True, env=env, timeout=10, check=False
        )

    def whisper(self, name="memo"):
        return f"whisper {self.inbox / (name + '.ogg')} --model small"

    # --- unchanged behaviour -------------------------------------------------

    def test_ordinary_commands_pass(self):
        self.assertEqual(self.run_hook("ls -la").returncode, 0)

    def test_whisper_ok_passes(self):
        self.assertEqual(self.run_hook(self.whisper() + "  # whisper-ok").returncode, 0)

    def test_bad_stdin_fails_open(self):
        self.assertEqual(self.run_hook("", stdin="not json").returncode, 0)

    # --- the decision ---------------------------------------------------------

    def test_marker_present_means_wait_and_poll(self):
        self.add_ogg()
        (self.inbox / "memo.transcribing").write_text("", encoding="utf-8")
        r = self.run_hook(self.whisper())
        self.assertEqual(r.returncode, 2)
        self.assertIn("transcribing it now", r.stderr)
        self.assertIn("memo.txt", r.stderr)
        self.assertNotIn("whisper-ok` added", r.stderr)

    def test_transcript_already_there_means_read_it(self):
        self.add_ogg()
        (self.inbox / "memo.txt").write_text("hi", encoding="utf-8")
        r = self.run_hook(self.whisper())
        self.assertEqual(r.returncode, 2)
        self.assertIn("already transcribed", r.stderr)
        self.assertIn("memo.txt", r.stderr)

    def test_no_marker_no_txt_and_stale_status_means_down(self):
        self.add_ogg()
        self.set_last_seen(minutes_ago=20)
        r = self.run_hook(self.whisper())
        self.assertEqual(r.returncode, 2)
        self.assertIn("voice_watcher is down", r.stderr)
        self.assertIn("whisper-ok", r.stderr)

    def test_missing_status_file_means_down(self):
        self.add_ogg()
        self.status.unlink()
        r = self.run_hook(self.whisper())
        self.assertIn("voice_watcher is down", r.stderr)

    def test_marker_left_by_a_stopped_watcher_means_down(self):
        self.add_ogg()
        (self.inbox / "memo.transcribing").write_text("", encoding="utf-8")
        self.set_last_seen(minutes_ago=20)
        r = self.run_hook(self.whisper())
        self.assertIn("voice_watcher is down", r.stderr)

    def test_fresh_status_no_marker_waits_scaled_to_size(self):
        self.add_ogg(mb=1.5)  # ~5-minute memo: 90 s/MB -> 135 s
        r = self.run_hook(self.whisper())
        self.assertEqual(r.returncode, 2)
        self.assertIn("135 s", r.stderr)
        self.assertIn("whisper-ok", r.stderr)

    def test_small_memo_waits_at_least_30_s(self):
        self.add_ogg(mb=0.05)
        r = self.run_hook(self.whisper())
        self.assertIn("30 s", r.stderr)

    def test_unknown_file_with_any_marker_in_inbox_means_wait(self):
        self.add_ogg("other")
        (self.inbox / "other.transcribing").write_text("", encoding="utf-8")
        r = self.run_hook('python -c "from faster_whisper import WhisperModel"')
        self.assertEqual(r.returncode, 2)
        self.assertIn("transcribing it now", r.stderr)
        self.assertIn("other.txt", r.stderr)

    def test_unknown_file_no_marker_stale_status_means_down(self):
        self.set_last_seen(minutes_ago=20)
        r = self.run_hook('python -c "from faster_whisper import WhisperModel"', tool="PowerShell")
        self.assertIn("voice_watcher is down", r.stderr)


if __name__ == "__main__":
    unittest.main()
