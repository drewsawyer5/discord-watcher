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
        attachment = {"filename": "voice.ogg", "url": "https://cdn.example/voice.ogg"}
        get_response = types.SimpleNamespace(content=b"OggS audio", raise_for_status=lambda: None)
        get = Mock(return_value=get_response)
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


if __name__ == "__main__":
    unittest.main()
