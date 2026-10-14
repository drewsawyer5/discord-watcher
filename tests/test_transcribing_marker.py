"""#351: voice_watcher marks a memo as `<name>.transcribing` while it works on it."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import voice_watcher

# Keep test runs out of the live voice_watcher.log.
voice_watcher.root_log.removeHandler(voice_watcher.file_handler)


class TranscribingMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = Path(self.tmp.name)
        self.ogg = self.inbox / "memo.ogg"
        self.ogg.write_bytes(b"fake audio")
        self.marker = self.inbox / "memo.transcribing"
        patches = [
            patch.object(voice_watcher, "INBOX_DIR", self.inbox),
            patch.object(voice_watcher, "_record_soak", lambda *a, **k: None),
            patch.object(voice_watcher, "_notify_fallback", lambda *a, **k: None),
            patch.object(voice_watcher, "append_to_log", lambda *a, **k: None),
            patch.object(voice_watcher, "write_heartbeat", lambda: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _transcribe_seeing_marker(self, result):
        seen = {}

        def fake(path):
            seen["marker"] = self.marker.exists()
            return result

        with patch.object(voice_watcher.discord_voice, "transcribe_with_fallback", fake):
            voice_watcher.process_ogg(self.ogg)
        return seen.get("marker")

    def test_marker_exists_while_transcribing_and_is_removed_after(self):
        seen = self._transcribe_seeing_marker({"via": "local", "text": "hello there drew", "latency_ms": 5})
        self.assertTrue(seen)
        self.assertFalse(self.marker.exists())
        self.assertEqual((self.inbox / "memo.txt").read_text(encoding="utf-8"), "hello there drew")

    def test_marker_is_removed_when_transcription_fails(self):
        seen = self._transcribe_seeing_marker({"via": "failed", "error": "boom", "latency_ms": 5})
        self.assertTrue(seen)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.inbox / "memo.txt").exists())

    def test_marker_is_removed_when_transcription_raises(self):
        def boom(path):
            raise RuntimeError("crash")

        with patch.object(voice_watcher.discord_voice, "transcribe_with_fallback", boom), self.assertRaises(RuntimeError):
            voice_watcher.process_ogg(self.ogg)
        self.assertFalse(self.marker.exists())

    def test_stale_markers_are_cleaned_at_startup(self):
        self.marker.write_text("", encoding="utf-8")
        (self.inbox / "other.transcribing").write_text("", encoding="utf-8")
        voice_watcher.cleanup_stale_markers()
        self.assertEqual(list(self.inbox.glob("*.transcribing")), [])
        self.assertTrue(self.ogg.exists())


if __name__ == "__main__":
    unittest.main()
