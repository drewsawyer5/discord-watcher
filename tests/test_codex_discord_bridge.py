import unittest

from codex_discord_bridge import classify_message, split_discord_message


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


if __name__ == "__main__":
    unittest.main()
