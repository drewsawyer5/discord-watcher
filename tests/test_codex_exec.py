import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import Mock

from codex_exec import CodexExecConfig, CodexExecRunner, CodexExecSession


class CodexExecRunnerTests(unittest.TestCase):
    def test_ask_runs_codex_exec_and_returns_output_last_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_dir = Path(temp_dir) / "outputs"
            workspace.mkdir()

            def run_command(args, **kwargs):
                output_path = Path(args[args.index("-o") + 1])
                output_path.write_text("codex exec ok\n", encoding="utf-8")
                result = Mock()
                result.returncode = 0
                result.stderr = ""
                return result

            runner = CodexExecRunner(
                CodexExecConfig(workspace=workspace, output_dir=output_dir),
                run_command=run_command,
            )

            answer = runner.ask("Reply with exactly: codex exec ok.")

            self.assertEqual(answer, "codex exec ok")

    def test_ask_builds_lane1_codex_exec_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_dir = Path(temp_dir) / "outputs"
            workspace.mkdir()
            run_command = Mock()
            run_command.return_value.returncode = 0
            run_command.return_value.stderr = ""

            def write_output(args, **kwargs):
                output_path = Path(args[args.index("-o") + 1])
                output_path.write_text("done", encoding="utf-8")
                return run_command.return_value

            run_command.side_effect = write_output
            runner = CodexExecRunner(
                CodexExecConfig(workspace=workspace, output_dir=output_dir, timeout_seconds=77),
                run_command=run_command,
            )

            runner.ask("hello")

            args = run_command.call_args.args[0]
            self.assertIn(Path(args[0]).name.lower(), {"codex", "codex.cmd", "codex.exe"})
            self.assertEqual(args[1], "exec")
            self.assertIn("--cd", args)
            self.assertIn(str(workspace), args)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
            self.assertNotIn("--search", args)
            self.assertIn("--skip-git-repo-check", args)
            self.assertIn("-o", args)
            self.assertEqual(args[-1], "hello")
            self.assertEqual(run_command.call_args.kwargs["cwd"], str(workspace))
            self.assertEqual(run_command.call_args.kwargs["timeout"], 77)
            self.assertEqual(run_command.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(run_command.call_args.kwargs["encoding"], "utf-8")
            self.assertEqual(run_command.call_args.kwargs["errors"], "replace")

    def test_ask_raises_when_codex_exec_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            result = Mock()
            result.returncode = 2
            result.stderr = "bad auth"
            run_command = Mock(return_value=result)
            runner = CodexExecRunner(
                CodexExecConfig(workspace=workspace, output_dir=Path(temp_dir) / "outputs"),
                run_command=run_command,
            )

            with self.assertRaisesRegex(RuntimeError, "bad auth"):
                runner.ask("hello")


class CodexExecSessionTests(unittest.TestCase):
    def test_restart_is_noop_for_exec_session(self):
        runner = Mock()
        session = CodexExecSession(runner)

        session.restart()

        runner.ask.assert_not_called()

    def test_ask_delegates_to_runner(self):
        runner = Mock()
        runner.ask.return_value = "answer"
        session = CodexExecSession(runner)

        self.assertEqual(session.ask("prompt"), "answer")
        runner.ask.assert_called_once_with("prompt")


if __name__ == "__main__":
    unittest.main()
