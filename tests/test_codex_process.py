import unittest
from pathlib import Path
from unittest.mock import Mock

from codex_process import CodexProcessConfig, CodexProcessController


class CodexProcessControllerTests(unittest.TestCase):
    def test_start_uses_interactive_codex_with_lane1_flags(self):
        process = Mock()
        process.isalive.return_value = True
        process_factory = Mock(return_value=process)
        config = CodexProcessConfig(
            codex_bin="codex",
            workspace=Path(r"C:\Users\drews\Life Org"),
            startup_timeout_seconds=5,
        )

        controller = CodexProcessController(config, process_factory=process_factory)
        controller.start()

        process_factory.assert_called_once()
        args = process_factory.call_args.args[0]
        self.assertEqual(process_factory.call_args.kwargs["cwd"], str(config.workspace))
        self.assertEqual(args[0], "codex")
        self.assertIn("--cd", args)
        self.assertIn(r"C:\Users\drews\Life Org", args)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertIn("--search", args)
        self.assertIn("--no-alt-screen", args)

    def test_send_prompt_writes_prompt_then_enter_sequence_to_submit(self):
        process = Mock()
        process.isalive.return_value = True
        process_factory = Mock(return_value=process)
        config = CodexProcessConfig(
            codex_bin="codex",
            workspace=Path(r"C:\Users\drews\Life Org"),
            startup_timeout_seconds=5,
        )

        controller = CodexProcessController(config, process_factory=process_factory)
        controller.start()
        controller.send_prompt("hello codex")

        self.assertEqual(
            process.write.call_args_list,
            [
                unittest.mock.call("\x15"),
                unittest.mock.call("hello codex"),
                unittest.mock.call("\r"),
                unittest.mock.call("\r"),
            ],
        )

    def test_read_once_returns_process_output(self):
        process = Mock()
        process.isalive.return_value = True
        process.read.return_value = "codex output"
        process_factory = Mock(return_value=process)
        config = CodexProcessConfig(
            codex_bin="codex",
            workspace=Path(r"C:\Users\drews\Life Org"),
            startup_timeout_seconds=5,
        )

        controller = CodexProcessController(config, process_factory=process_factory)
        controller.start()

        self.assertEqual(controller.read_once(), "codex output")
        process.read.assert_called_once_with(4096)


if __name__ == "__main__":
    unittest.main()
