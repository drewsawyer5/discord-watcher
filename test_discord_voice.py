import os
import types
import unittest
from unittest.mock import Mock, patch

import discord_voice


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {"text": "transcribed by tower"}
        self.status_code = status_code
        self.text = "response text"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._payload


class DiscordVoiceTests(unittest.TestCase):
    def test_transcript_guard_rejects_empty_short_and_punctuation_only(self):
        self.assertFalse(discord_voice.is_usable_transcript(""))
        self.assertFalse(discord_voice.is_usable_transcript("."))
        self.assertFalse(discord_voice.is_usable_transcript("...?!"))
        self.assertFalse(discord_voice.is_usable_transcript("too short"))
        self.assertTrue(discord_voice.is_usable_transcript("this is long enough"))

    def test_remote_endpoint_is_used_when_configured(self):
        discord_voice.reset_tower_state()
        attachment = {"filename": "voice.ogg", "url": "https://cdn.example/voice.ogg"}
        attachment_response = types.SimpleNamespace(content=b"OggS audio", raise_for_status=lambda: None)
        health_response = FakeResponse(payload={"status": "ok"})

        def get_side_effect(url, *args, **kwargs):
            if url.endswith("/health"):
                return health_response
            return attachment_response

        get = Mock(side_effect=get_side_effect)
        post = Mock(return_value=FakeResponse({"text": "tower transcript ok"}))

        with patch.dict(os.environ, {"WHISPER_ENDPOINT": "http://tower:9001"}, clear=False):
            transcript = discord_voice.transcribe_attachment_dict(
                attachment,
                headers={"Authorization": "Bot x"},
                requests_get=get,
                requests_post=post,
            )

        self.assertEqual(transcript, "tower transcript ok")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "http://tower:9001/transcribe")

    def test_select_first_audio_ignores_extra_audio_and_images(self):
        attachments = [
            types.SimpleNamespace(filename="first.ogg", content_type="audio/ogg"),
            types.SimpleNamespace(filename="photo.jpg", content_type="image/jpeg"),
            types.SimpleNamespace(filename="second.m4a", content_type="audio/mp4"),
        ]

        selected, ignored_audio_count = discord_voice.select_first_audio_attachment(attachments)

        self.assertIs(selected, attachments[0])
        self.assertEqual(ignored_audio_count, 1)

    def test_format_codex_voice_prompt_omits_channel_id_and_includes_typed_note(self):
        prompt = discord_voice.format_voice_prompt("fix the tests", "also commit it")

        self.assertEqual(prompt, "Voice transcript:\nfix the tests\n\nTyped note:\nalso commit it")
        self.assertNotIn("<#", prompt)


class TranscribeWithFallbackTests(unittest.TestCase):
    """Tower-first, local-fallback path. Tests run with no real network or model."""

    def setUp(self):
        discord_voice.reset_tower_state()

    def _config(self, endpoint="http://tower:9001"):
        return discord_voice.VoiceConfig(endpoint=endpoint, model="base", timeout_seconds=30, diarize=False)

    def _fake_audio(self) -> "Path":
        import tempfile
        from pathlib import Path
        tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        tmp.write(b"OggS-fake")
        tmp.close()
        return Path(tmp.name)

    def test_tower_healthy_uses_tower(self):
        get = Mock(return_value=FakeResponse(payload={"status": "ok"}))
        post = Mock(return_value=FakeResponse({"text": "tower transcript ok"}))
        audio = self._fake_audio()

        result = discord_voice.transcribe_with_fallback(
            audio,
            config=self._config(),
            requests_get=get,
            requests_post=post,
        )

        self.assertEqual(result["via"], "tower")
        self.assertEqual(result["text"], "tower transcript ok")
        self.assertIsNone(result["error"])
        get.assert_called_once()
        post.assert_called_once()

    def test_probe_failure_falls_back_to_local(self):
        get = Mock(side_effect=RuntimeError("connect timeout"))
        post = Mock(return_value=FakeResponse({"text": "should not be called"}))
        audio = self._fake_audio()

        with patch.object(discord_voice, "_transcribe_file_local", return_value="local backup ran") as local_mock:
            result = discord_voice.transcribe_with_fallback(
                audio,
                config=self._config(),
                requests_get=get,
                requests_post=post,
            )

        self.assertEqual(result["via"], "local")
        self.assertEqual(result["text"], "local backup ran")
        self.assertIn("health probe failed", result["error"])
        local_mock.assert_called_once()
        post.assert_not_called()

    def test_remote_failure_after_healthy_probe_falls_back_to_local(self):
        get = Mock(return_value=FakeResponse(payload={"status": "ok"}))
        post = Mock(side_effect=RuntimeError("tower exploded mid-transcribe"))
        audio = self._fake_audio()

        with patch.object(discord_voice, "_transcribe_file_local", return_value="local saved us") as local_mock:
            result = discord_voice.transcribe_with_fallback(
                audio,
                config=self._config(),
                requests_get=get,
                requests_post=post,
            )

        self.assertEqual(result["via"], "local")
        self.assertEqual(result["text"], "local saved us")
        self.assertIn("tower transcription failed", result["error"])
        local_mock.assert_called_once()

    def test_both_failed_returns_failed_via(self):
        get = Mock(side_effect=RuntimeError("network down"))
        post = Mock()
        audio = self._fake_audio()

        with patch.object(discord_voice, "_transcribe_file_local", side_effect=RuntimeError("local broke")):
            result = discord_voice.transcribe_with_fallback(
                audio,
                config=self._config(),
                requests_get=get,
                requests_post=post,
            )

        self.assertEqual(result["via"], "failed")
        self.assertEqual(result["text"], "")
        self.assertIn("local transcription failed", result["error"])

    def test_cached_unhealthy_state_skips_probe(self):
        # Pre-mark Tower as unhealthy 5s ago — should skip probe entirely.
        discord_voice._mark_tower_unhealthy("prior failure", now=lambda: 100.0)

        get = Mock()  # must NOT be called
        post = Mock()
        audio = self._fake_audio()

        with patch.object(discord_voice, "_transcribe_file_local", return_value="local hit") as local_mock:
            result = discord_voice.transcribe_with_fallback(
                audio,
                config=self._config(),
                requests_get=get,
                requests_post=post,
                now=lambda: 105.0,  # only 5s later, well within recheck window
            )

        self.assertEqual(result["via"], "local")
        get.assert_not_called()
        post.assert_not_called()
        local_mock.assert_called_once()

    def test_cached_healthy_state_skips_probe(self):
        # Pre-mark Tower as healthy 5s ago — should skip probe and go straight to remote.
        with discord_voice._tower_state_lock:
            discord_voice._tower_state["healthy"] = True
            discord_voice._tower_state["checked_at"] = 100.0
            discord_voice._tower_state["last_error"] = None

        get = Mock()  # must NOT be called
        post = Mock(return_value=FakeResponse({"text": "tower fast path"}))
        audio = self._fake_audio()

        result = discord_voice.transcribe_with_fallback(
            audio,
            config=self._config(),
            requests_get=get,
            requests_post=post,
            now=lambda: 105.0,
        )

        self.assertEqual(result["via"], "tower")
        get.assert_not_called()
        post.assert_called_once()

    def test_no_endpoint_goes_straight_to_local(self):
        post = Mock()
        get = Mock()
        audio = self._fake_audio()

        with patch.object(discord_voice, "_transcribe_file_local", return_value="local only path") as local_mock:
            result = discord_voice.transcribe_with_fallback(
                audio,
                config=self._config(endpoint=""),
                requests_get=get,
                requests_post=post,
            )

        self.assertEqual(result["via"], "local")
        self.assertIsNone(result["error"])
        get.assert_not_called()
        post.assert_not_called()
        local_mock.assert_called_once()


class ProbeTowerTests(unittest.TestCase):
    def test_probe_returns_true_on_200(self):
        get = Mock(return_value=FakeResponse(payload={"status": "ok"}))
        healthy, error = discord_voice.probe_tower("http://tower:9001", requests_get=get)
        self.assertTrue(healthy)
        self.assertIsNone(error)
        get.assert_called_once_with("http://tower:9001/health", timeout=(1.0, 2.0))

    def test_probe_returns_false_on_non_200(self):
        get = Mock(return_value=FakeResponse(payload={}, status_code=503))
        healthy, error = discord_voice.probe_tower("http://tower:9001", requests_get=get)
        self.assertFalse(healthy)
        self.assertIn("503", error or "")

    def test_probe_returns_false_on_exception(self):
        get = Mock(side_effect=RuntimeError("network unreachable"))
        healthy, error = discord_voice.probe_tower("http://tower:9001", requests_get=get)
        self.assertFalse(healthy)
        self.assertIn("network unreachable", error or "")

    def test_probe_empty_endpoint_returns_false(self):
        healthy, error = discord_voice.probe_tower("")
        self.assertFalse(healthy)
        self.assertIn("no endpoint", error or "")


if __name__ == "__main__":
    unittest.main()
