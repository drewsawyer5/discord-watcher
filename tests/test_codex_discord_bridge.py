import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from codex_discord_bridge import DiscordBridgeConfig, build_codex_session, classify_message, split_discord_message
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
