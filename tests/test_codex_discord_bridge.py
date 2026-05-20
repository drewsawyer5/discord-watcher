import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from codex_discord_bridge import (
    DiscordBridgeConfig,
    build_codex_session,
    build_prompt_for_bridge,
    build_prompt_from_message,
    build_prompt_from_message_payload,
    classify_message,
    split_discord_message,
    transcribe_payload_audio_to_sidecar,
)
from codex_exec import CodexBridgeState, CodexExecSession
from codex_session import CodexSession


class CodexDiscordBridgeTests(unittest.TestCase):
    def test_classify_message_accepts_explicit_restart_command(self):
        self.assertEqual(classify_message("!codex-restart"), "restart")

    def test_classify_message_accepts_natural_restart(self):
        self.assertEqual(classify_message("Can you restart the Codex session?"), "restart")

    def test_classify_message_accepts_status(self):
        self.assertEqual(classify_message("is codex running status?"), "status")

    def test_classify_message_defaults_to_prompt(self):
        self.assertEqual(classify_message("Edit the homepage note"), "prompt")

    def test_split_discord_message_preserves_short_message(self):
        self.assertEqual(split_discord_message("hello", limit=10), ["hello"])

    def test_split_discord_message_chunks_long_message(self):
        chunks = split_discord_message("abc\ndef\nghi", limit=7)

        self.assertEqual(chunks, ["abc\ndef", "ghi"])
        self.assertTrue(all(len(chunk) <= 7 for chunk in chunks))
        self.assertEqual("\n".join(chunks), "abc\ndef\nghi")

    def test_build_prompt_from_message_uses_first_audio_transcript_and_typed_note(self):
        message = Mock()
        message.content = "also commit"
        first_audio = Mock(filename="first.ogg", content_type="audio/ogg")
        second_audio = Mock(filename="second.m4a", content_type="audio/mp4")
        image = Mock(filename="photo.jpg", content_type="image/jpeg")
        message.attachments = [first_audio, image, second_audio]

        prompt, warning = build_prompt_from_message(message, transcribe=lambda attachment: "please fix the test suite")

        self.assertEqual(
            prompt,
            "Voice transcript:\nplease fix the test suite\n\nTyped note:\nalso commit",
        )
        self.assertIn("Ignored 1 extra audio attachment", warning)
        self.assertIn("Ignored non-audio attachment", warning)

    def test_build_prompt_from_message_rejects_garbled_voice_without_codex_prompt(self):
        message = Mock()
        message.content = ""
        message.attachments = [Mock(filename="noise.ogg", content_type="audio/ogg")]

        prompt, warning = build_prompt_from_message(message, transcribe=lambda attachment: "...")

        self.assertIsNone(prompt)
        self.assertEqual(warning, "Couldn't transcribe that - try again?")

    def test_build_prompt_from_message_payload_uses_rest_attachment_when_event_misses_it(self):
        payload = {
            "content": "",
            "attachments": [
                {
                    "filename": "voice-message.ogg",
                    "content_type": "audio/ogg",
                    "url": "https://cdn.example/voice-message.ogg",
                }
            ],
        }

        prompt, warning = build_prompt_from_message_payload(
            payload,
            transcribe=lambda attachment: "please use the rest attachment",
        )

        self.assertEqual(prompt, "Voice transcript:\nplease use the rest attachment")
        self.assertEqual(warning, "")

    def test_transcribe_payload_audio_to_sidecar_writes_ogg_and_txt_before_prompt(self):
        import tempfile

        payload = {
            "id": "msg-123",
            "content": "typed note",
            "attachments": [
                {
                    "id": "att-456",
                    "filename": "voice-message.ogg",
                    "content_type": "audio/ogg",
                    "url": "https://cdn.example/voice-message.ogg",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            prompt, warning = transcribe_payload_audio_to_sidecar(
                payload,
                Path(tmp),
                download=lambda attachment: b"OggS test audio",
                transcribe_file=lambda path: "sidecar transcript works",
            )
            ogg = Path(tmp) / "msg-123-att-456-voice-message.ogg"
            txt = Path(tmp) / "msg-123-att-456-voice-message.txt"

            self.assertTrue(ogg.exists())
            self.assertTrue(txt.exists())
            self.assertEqual(txt.read_text(encoding="utf-8"), "sidecar transcript works")
            self.assertEqual(prompt, "Voice transcript:\nsidecar transcript works\n\nTyped note:\ntyped note")
            self.assertEqual(warning, "")

    def test_build_prompt_for_bridge_fetches_rest_payload_for_gateway_audio(self):
        import asyncio

        config = DiscordBridgeConfig(
            token="token",
            codex_channel_id=123,
            drew_user_id=None,
            workspace=Path(r"C:\workspace"),
            turn_timeout_seconds=180,
            log_path=Path("raw.log"),
            output_dir=Path("outputs"),
            state_path=Path("state.json"),
            turn_log_path=Path("turns.log"),
            session_mode="exec",
            voice_dir=Path("voice"),
            voice_ready_delay_seconds=0,
        )
        message = Mock()
        message.id = 456
        message.content = ""
        message.flags = 8192
        message.attachments = [Mock(filename="voice-message.ogg", content_type="audio/ogg")]
        payload = {
            "content": "",
            "attachments": [{"filename": "voice-message.ogg", "content_type": "audio/ogg", "url": "url"}],
        }

        async def run():
            import codex_discord_bridge
            from unittest.mock import patch

            with (
                patch.object(codex_discord_bridge, "fetch_message_payload", return_value=payload),
                patch.object(
                    codex_discord_bridge,
                    "transcribe_payload_audio_to_sidecar",
                    return_value=("Voice transcript:\nrest transcript works", ""),
                ),
            ):
                return await build_prompt_for_bridge(config, message)

        prompt, warning = asyncio.run(run())

        self.assertEqual(prompt, "Voice transcript:\nrest transcript works")
        self.assertEqual(warning, "")

    def test_build_codex_session_defaults_to_exec_session(self):
        config = DiscordBridgeConfig(
            token="token",
            codex_channel_id=123,
            drew_user_id=None,
            workspace=Path(r"C:\workspace"),
            turn_timeout_seconds=180,
            log_path=Path("raw.log"),
            output_dir=Path("outputs"),
            state_path=Path("state.json"),
            turn_log_path=Path("turns.log"),
            session_mode="exec",
            voice_dir=Path("voice"),
            voice_ready_delay_seconds=0,
        )

        self.assertIsInstance(build_codex_session(config), CodexExecSession)

    def test_build_codex_session_can_create_pty_session(self):
        config = DiscordBridgeConfig(
            token="token",
            codex_channel_id=123,
            drew_user_id=None,
            workspace=Path(r"C:\workspace"),
            turn_timeout_seconds=180,
            log_path=Path("raw.log"),
            output_dir=Path("outputs"),
            state_path=Path("state.json"),
            turn_log_path=Path("turns.log"),
            session_mode="pty",
            voice_dir=Path("voice"),
            voice_ready_delay_seconds=0,
        )

        self.assertIsInstance(build_codex_session(config), CodexSession)

    async def _call_status(self, state: CodexBridgeState):
        from codex_discord_bridge import CodexDiscordBridge

        config = DiscordBridgeConfig(
            token="token",
            codex_channel_id=123,
            drew_user_id=None,
            workspace=Path(r"C:\workspace"),
            turn_timeout_seconds=180,
            log_path=Path("raw.log"),
            output_dir=Path("outputs"),
            state_path=Path("state.json"),
            turn_log_path=Path("turns.log"),
            session_mode="exec",
            voice_dir=Path("voice"),
            voice_ready_delay_seconds=0,
        )
        session = Mock()
        bridge = CodexDiscordBridge(config, session, state_loader=lambda path: state)
        message = Mock()
        message.reply = AsyncMock()

        await bridge.status(message)

        return message.reply.call_args.args[0]

    def test_status_includes_session_id_and_last_output(self):
        import asyncio

        state = CodexBridgeState(
            session_id="019e3733-ccd7-7791-babc-b67a9c4468e6",
            last_output_file="out.txt",
            last_success_at="2026-05-17T14:30:10-04:00",
        )

        text = asyncio.run(self._call_status(state))

        self.assertIn("019e3733-ccd7-7791-babc-b67a9c4468e6", text)
        self.assertIn("out.txt", text)

    def test_restart_reports_new_session_id(self):
        import asyncio
        from codex_discord_bridge import CodexDiscordBridge

        config = DiscordBridgeConfig(
            token="token",
            codex_channel_id=123,
            drew_user_id=None,
            workspace=Path(r"C:\workspace"),
            turn_timeout_seconds=180,
            log_path=Path("raw.log"),
            output_dir=Path("outputs"),
            state_path=Path("state.json"),
            turn_log_path=Path("turns.log"),
            session_mode="exec",
            voice_dir=Path("voice"),
            voice_ready_delay_seconds=0,
        )
        session = Mock()
        session.restart.return_value = "019e3735-0000-7000-8000-abcdefabcdef"
        bridge = CodexDiscordBridge(config, session)
        message = Mock()
        message.reply = AsyncMock()
        message.channel.send = AsyncMock()

        asyncio.run(bridge.restart(message))

        message.channel.send.assert_called_once()
        self.assertIn("019e3735-0000-7000-8000-abcdefabcdef", message.channel.send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
