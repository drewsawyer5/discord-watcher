import unittest
from pathlib import Path

from codex_discord_bridge import DiscordBridgeConfig, build_codex_session, classify_message, split_discord_message
from codex_exec import CodexExecSession
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
            session_mode="pty",
        )

        self.assertIsInstance(build_codex_session(config), CodexSession)


if __name__ == "__main__":
    unittest.main()
