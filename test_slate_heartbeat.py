"""slate_heartbeat tests (#172): registers then beats, re-registers on 404, never raises, off without a URL."""

import threading

import requests

import slate_heartbeat


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _drive(responses: list[object], ticks: int) -> list[str]:
    """Run the loop for `ticks` sleeps against a scripted server; return the paths it hit."""
    calls: list[str] = []
    stop = threading.Event()
    slept = []

    def post(url: str, **_: object) -> FakeResponse:
        calls.append(url.rsplit("/", 1)[1])
        outcome = responses.pop(0) if responses else 200
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)  # type: ignore[arg-type]

    def sleep(_: float) -> None:
        slept.append(1)
        if len(slept) >= ticks:
            stop.set()

    slate_heartbeat.run("discord-watcher", "http://slate/api", {}, sleep=sleep, stop=stop, post=post)
    return calls


def test_registers_once_then_beats() -> None:
    assert _drive([], ticks=3) == ["register", "heartbeat", "heartbeat", "heartbeat"]


def test_a_404_heartbeat_registers_again() -> None:
    assert _drive([200, 200, 404], ticks=3) == ["register", "heartbeat", "heartbeat", "register", "heartbeat"]


def test_errors_never_escape_and_it_keeps_trying() -> None:
    down = requests.ConnectionError("A6 asleep")
    calls = _drive([down, down, 200, 200], ticks=3)
    assert calls == ["register", "register", "register", "heartbeat"]


def test_off_without_a_url(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SLATE_API_URL", raising=False)
    assert slate_heartbeat.start("pa-bot") is None
