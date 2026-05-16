import importlib
import json
import os
import shutil
import sys
import threading
import time
import types
from pathlib import Path


os.environ["WHISPER_ENDPOINT"] = "http://synthetic-whisperx"
SCRATCH_ROOT = Path(__file__).parent / ".synthetic_test_tmp"
if SCRATCH_ROOT.exists():
    shutil.rmtree(SCRATCH_ROOT)
SCRATCH_ROOT.mkdir()
INBOX_ROOT = SCRATCH_ROOT / "inbox"
LOG_ROOT = SCRATCH_ROOT / "inbox_logs"
INBOX_ROOT.mkdir()
LOG_ROOT.mkdir()
os.environ["DISCORD_INBOX"] = str(INBOX_ROOT)
os.environ["INBOX_LOG_DIR"] = str(LOG_ROOT)

fake_faster_whisper = types.ModuleType("faster_whisper")


class FakeWhisperModel:
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, *args, **kwargs):
        return [], None


fake_faster_whisper.WhisperModel = FakeWhisperModel
sys.modules["faster_whisper"] = fake_faster_whisper

import voice_watcher


voice_watcher = importlib.reload(voice_watcher)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"text": "synthetic transcript"}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise voice_watcher.requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._payload


def reset_state(tmpdir: Path):
    voice_watcher._in_progress.clear()
    voice_watcher.files_transcribed = 0
    voice_watcher.LOG_BASE = tmpdir / "logs"
    voice_watcher.STATUS_FILE = tmpdir / "status.json"
    voice_watcher.SOAK_LOG = tmpdir / "whisperx_soak.jsonl"


def test_exactly_one_post_per_file():
    tmpdir = SCRATCH_ROOT / "success"
    tmpdir.mkdir()
    reset_state(tmpdir)
    ogg = tmpdir / "sample.ogg"
    ogg.write_bytes(b"OggS synthetic")
    post_calls = []

    def fake_post(*args, **kwargs):
        post_calls.append((args, kwargs))
        time.sleep(0.05)
        return FakeResponse()

    voice_watcher.requests.post = fake_post
    path_a = ogg
    path_b = tmpdir / "." / "sample.ogg"
    threads = [
        threading.Thread(target=voice_watcher.process_ogg, args=(path_a,)),
        threading.Thread(target=voice_watcher.process_ogg, args=(path_b,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(post_calls) == 1, f"expected exactly one POST, got {len(post_calls)}"


def test_endpoint_failure_writes_soak_error_row():
    tmpdir = SCRATCH_ROOT / "failure"
    tmpdir.mkdir()
    reset_state(tmpdir)
    ogg = tmpdir / "failure.ogg"
    ogg.write_bytes(b"OggS synthetic")

    def fake_post(*args, **kwargs):
        return FakeResponse(status_code=500, text="tower exploded")

    voice_watcher.requests.post = fake_post
    try:
        voice_watcher.process_ogg(ogg)
    except Exception:
        pass
    else:
        raise AssertionError("expected endpoint failure to be raised")

    rows = [
        json.loads(line)
        for line in voice_watcher.SOAK_LOG.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1, f"expected exactly one soak row, got {len(rows)}"
    assert rows[0]["outcome"] == "HTTP_ERROR_5XX", rows[0]
    assert rows[0].get("error"), rows[0]


if __name__ == "__main__":
    try:
        assertions = []
        for test in (test_exactly_one_post_per_file, test_endpoint_failure_writes_soak_error_row):
            print(f"RUN {test.__name__}", flush=True)
            try:
                test()
            except AssertionError as exc:
                print(f"FAIL {test.__name__}: {exc}", flush=True)
                raise
            else:
                msg = f"PASS {test.__name__}"
                assertions.append(msg)
                print(msg, flush=True)
        print("ASSERTIONS: " + "; ".join(assertions), flush=True)
    finally:
        shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)
