import unittest

from codex_terminal import extract_turn_text, is_ready_screen, strip_terminal_controls


class CodexTerminalTests(unittest.TestCase):
    def test_strip_terminal_controls_removes_ansi_and_osc_sequences(self):
        raw = "\x1b]0;Life Org\x07\x1b[1m›\x1b[22m answer\x1b[?25h\r\n"

        cleaned = strip_terminal_controls(raw)

        self.assertEqual(cleaned, "› answer\n")

    def test_is_ready_screen_detects_codex_input_prompt(self):
        raw = (
            "\x1b[12;1H\x1b[1m›\x1b[12;3H\x1b[22m"
            "\x1b[2mUse /skills to list available skills"
            "\x1b[14;3Hgpt-5.5 default · ~\\Life Org"
        )

        self.assertTrue(is_ready_screen(raw))

    def test_is_ready_screen_accepts_mojibake_prompt_after_boot_line(self):
        raw = (
            "Booting MCP server: codex_apps (0s â€¢ esc to interrupt)"
            "â€º Use /skills to list available skills"
            "gpt-5.5 default Â· ~\\Life Org"
        )

        self.assertTrue(is_ready_screen(raw))

    def test_is_ready_screen_accepts_existing_draft_with_model_status(self):
        raw = "â€º Summarize recent commits gpt-5.5 default Â· ~\\Life Org"

        self.assertTrue(is_ready_screen(raw))

    def test_is_ready_screen_rejects_working_state(self):
        raw = "• Working (12s • esc to interrupt)\n"

        self.assertFalse(is_ready_screen(raw))

    def test_extract_turn_text_keeps_prompt_response_slice_readable(self):
        raw = (
            "\x1b[12;1H› Reply with exactly: codex pty submit ok\r\n"
            "\x1b[15;1H• Working (2s • esc to interrupt)\r\n"
            "\x1b[14;24r\r\n"
            "\x1b[2m• \x1b[22mcodex pty submit ok\r\n"
            "\x1b[18;1H›\x1b[18;3H\x1b[2mImplement {feature}"
        )

        text = extract_turn_text(raw)

        self.assertIn("codex pty submit ok", text)
        self.assertNotIn("esc to interrupt", text)
        self.assertNotIn("Implement {feature}", text)

    def test_extract_turn_text_trims_rate_limit_dialog_after_answer(self):
        raw = (
            "\u203a Reply with exactly: codex bridge spike script ok\n"
            "Working (0s \u2022 esc to interrupt)\n"
            "\u26a0 Heads up, you have less than 5% of your 5h limit left.\n"
            "\u2022 codex bridge spike script ok"
            "Approaching rate limits"
            "Switch to gpt-5.4-mini for lower credit usage?"
        )

        text = extract_turn_text(raw)

        self.assertEqual(text, "codex bridge spike script ok")


if __name__ == "__main__":
    unittest.main()
