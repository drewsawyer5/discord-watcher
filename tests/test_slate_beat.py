"""#347: Comms Services beat SLATE (register on 404, stop for good on 410, never raise on network trouble)."""

import sys
import tempfile
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import slate_beat

BASE = "https://slate.example/api"


class FakeSlate:
    """Answers POSTs like SLATE's Services routes, from a scripted list of status codes per path suffix."""

    def __init__(self, heartbeat=(200,), register=(200,), error=None):
        self.answers = {"heartbeat": list(heartbeat), "register": list(register)}
        self.error = error
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json, headers))
        if self.error is not None:
            raise self.error
        kind = url.rsplit("/", 1)[-1]
        codes = self.answers[kind]
        code = codes.pop(0) if len(codes) > 1 else codes[0]
        resp = requests.Response()
        resp.status_code = code
        resp.url = url
        return resp


def make(fake, **kw):
    return slate_beat.SlateBeat("voice-watcher", BASE, "tok", interval_s=90, post=fake.post, hostname="A6", pid=42, **kw)


class SlateBeatTests(unittest.TestCase):
    def test_a_beat_carries_the_interval_details_token_and_a_user_agent(self):
        fake = FakeSlate()
        beat = make(fake, details=lambda: {"files_transcribed": 3})
        self.assertEqual(beat.beat(), "ok")
        kind, body, headers = fake.calls[0]
        self.assertEqual(kind, "heartbeat")
        self.assertEqual(body, {"details": {"interval_s": 90, "kind": "voice-watcher", "files_transcribed": 3}})
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertIn("User-Agent", headers)
        self.assertNotIn("python-requests", headers["User-Agent"])

    def test_an_unknown_name_registers_then_beats(self):
        fake = FakeSlate(heartbeat=(404, 200))
        self.assertEqual(make(fake).beat(), "registered")
        self.assertEqual([c[0] for c in fake.calls], ["heartbeat", "register", "heartbeat"])
        self.assertEqual(fake.calls[1][1], {"hostname": "A6", "pid": 42})

    def test_a_retired_name_stops_beating_and_never_registers(self):
        fake = FakeSlate(heartbeat=(410,))
        beat = make(fake)
        self.assertEqual(beat.beat(), "retired")
        self.assertEqual(beat.beat(), "retired")
        self.assertEqual([c[0] for c in fake.calls], ["heartbeat"])

    def test_a_tombstone_found_at_register_also_stops(self):
        fake = FakeSlate(heartbeat=(404,), register=(410,))
        beat = make(fake)
        self.assertEqual(beat.beat(), "retired")
        self.assertEqual(beat.beat(), "retired")
        self.assertEqual(len(fake.calls), 2)

    def test_network_trouble_and_server_errors_are_reported_not_raised(self):
        self.assertEqual(make(FakeSlate(error=requests.ConnectionError("down"))).beat(), "error")
        self.assertEqual(make(FakeSlate(heartbeat=(502,))).beat(), "error")

    def test_details_that_blow_up_do_not_stop_the_beat(self):
        def broken():
            raise RuntimeError("tower state unreadable")

        fake = FakeSlate()
        self.assertEqual(make(fake, details=broken).beat(), "ok")
        self.assertEqual(fake.calls[0][1], {"details": {"interval_s": 90, "kind": "voice-watcher"}})

    def test_run_beats_every_interval_and_ends_on_retirement(self):
        fake = FakeSlate(heartbeat=(200, 502, 200, 410))
        slept = []
        make(fake).run(every_s=60, sleep=slept.append)
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(slept, [60, 60, 60])

    def test_from_env_is_off_without_a_url(self):
        self.assertIsNone(slate_beat.from_env("voice-watcher", interval_s=90, env={}))
        beat = slate_beat.from_env("voice-watcher", interval_s=90, env={"SLATE_API_URL": BASE + "/", "SLATE_JOBS_TOKEN": "t"})
        self.assertEqual(beat.base, BASE)


class OneShotTests(unittest.TestCase):
    """The A6 supervisor runs `python slate_beat.py break-glass ...` once per 5-min run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def once(self, fake):
        beat = slate_beat.SlateBeat("break-glass", BASE, "tok", interval_s=420, post=fake.post, hostname="A6", pid=7)
        return slate_beat.beat_once(beat, state_dir=self.state)

    def test_a_good_beat_exits_zero(self):
        self.assertEqual(self.once(FakeSlate()), 0)

    def test_a_retirement_is_remembered_so_later_runs_send_nothing(self):
        self.assertEqual(self.once(FakeSlate(heartbeat=(410,))), slate_beat.EXIT_RETIRED)
        later = FakeSlate()
        self.assertEqual(self.once(later), slate_beat.EXIT_RETIRED)
        self.assertEqual(later.calls, [])

    def test_a_failed_beat_exits_nonzero_without_raising(self):
        self.assertEqual(self.once(FakeSlate(error=requests.Timeout("slow"))), slate_beat.EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
