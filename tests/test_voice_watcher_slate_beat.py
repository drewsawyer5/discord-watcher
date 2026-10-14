"""#347: voice_watcher beats `voice-watcher` into SLATE from its heartbeat loop."""

import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import voice_watcher

voice_watcher.root_log.removeHandler(voice_watcher.file_handler)


class Stop(Exception):
    pass


class FakeBeat:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sent = 0

    def beat(self):
        self.sent += 1
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


def run_loop(slate, ticks):
    """Run heartbeat_loop for `ticks` sleeps; returns how many status.json writes happened."""
    writes = []
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= ticks:
            raise Stop

    with patch.object(voice_watcher, "write_heartbeat", lambda: writes.append(1)):
        with contextlib.suppress(Stop):
            voice_watcher.heartbeat_loop(slate=slate, sleep=sleep)
    return len(writes), sleeps


class VoiceWatcherSlateBeatTests(unittest.TestCase):
    def test_slate_hears_every_minute_while_status_json_keeps_its_five_minutes(self):
        slate = FakeBeat(["ok"])
        writes, sleeps = run_loop(slate, ticks=10)
        self.assertEqual(slate.sent, 10)
        self.assertEqual(set(sleeps), {voice_watcher.SLATE_BEAT_INTERVAL})
        self.assertEqual(voice_watcher.SLATE_BEAT_INTERVAL, 60)
        self.assertEqual(writes, 2)  # ticks 0 and 5: every 300 s

    def test_a_retired_name_stops_the_slate_beat_but_not_the_status_file(self):
        slate = FakeBeat(["ok", "retired"])
        writes, _ = run_loop(slate, ticks=10)
        self.assertEqual(slate.sent, 2)
        self.assertEqual(writes, 2)

    def test_no_slate_configured_still_writes_status(self):
        writes, _ = run_loop(None, ticks=6)
        self.assertEqual(writes, 2)

    def test_a_beat_that_raises_does_not_kill_the_loop(self):
        class Boom:
            sent = 0

            def beat(self):
                self.sent += 1
                raise RuntimeError("unexpected")

        boom = Boom()
        run_loop(boom, ticks=3)
        self.assertEqual(boom.sent, 3)

    def test_the_slate_client_is_built_from_the_env_as_voice_watcher(self):
        env = {"SLATE_API_URL": "https://slate.example/api", "SLATE_JOBS_TOKEN": "t"}
        with patch.dict(voice_watcher.os.environ, env):
            beat = voice_watcher.make_slate_beat()
        self.assertEqual((beat.name, beat.interval_s), ("voice-watcher", 90))
        with patch.dict(voice_watcher.os.environ, {"SLATE_API_URL": ""}):
            self.assertIsNone(voice_watcher.make_slate_beat())


if __name__ == "__main__":
    unittest.main()
