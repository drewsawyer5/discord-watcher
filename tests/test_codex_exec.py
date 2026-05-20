import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import Mock

from codex_exec import CodexBridgeState, CodexExecConfig, CodexExecRunner, CodexExecSession, parse_session_id


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

    def test_ask_resumes_saved_session_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_dir = Path(temp_dir) / "outputs"
            state_path = Path(temp_dir) / "codex_bridge_state.json"
            workspace.mkdir()
            state_path.write_text(json.dumps({"session_id": "019e3733-ccd7-7791-babc-b67a9c4468e6"}), encoding="utf-8")

            def run_command(args, **kwargs):
                output_path = Path(args[args.index("-o") + 1])
                output_path.write_text("second answer\n", encoding="utf-8")
                result = Mock()
                result.returncode = 0
                result.stderr = ""
                result.stdout = "session id: 019e3733-ccd7-7791-babc-b67a9c4468e6\n"
                return result

            runner = CodexExecRunner(
                CodexExecConfig(workspace=workspace, output_dir=output_dir, state_path=state_path),
                run_command=run_command,
            )

            answer = runner.ask("what did I say before?")

            args = run_command.__self__.call_args.args[0] if hasattr(run_command, "__self__") else None
            self.assertEqual(answer, "second answer")

    def test_ask_builds_resume_command_when_state_has_session_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_dir = Path(temp_dir) / "outputs"
            state_path = Path(temp_dir) / "codex_bridge_state.json"
            workspace.mkdir()
            state_path.write_text(json.dumps({"session_id": "019e3733-ccd7-7791-babc-b67a9c4468e6"}), encoding="utf-8")
            run_command = Mock()

            def write_output(args, **kwargs):
                Path(args[args.index("-o") + 1]).write_text("done", encoding="utf-8")
                result = Mock()
                result.returncode = 0
                result.stderr = ""
                result.stdout = "session id: 019e3733-ccd7-7791-babc-b67a9c4468e6\n"
                return result

            run_command.side_effect = write_output
            runner = CodexExecRunner(
                CodexExecConfig(workspace=workspace, output_dir=output_dir, state_path=state_path),
                run_command=run_command,
            )

            runner.ask("hello again")

            args = run_command.call_args.args[0]
            self.assertEqual(args[1:4], ["exec", "resume", "019e3733-ccd7-7791-babc-b67a9c4468e6"])
            self.assertNotIn("--cd", args)
            self.assertEqual(args[-1], "-")
            self.assertEqual(run_command.call_args.kwargs["input"], "hello again")

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
            self.assertEqual(args[-1], "-")
            self.assertEqual(run_command.call_args.kwargs["cwd"], str(workspace))
            self.assertEqual(run_command.call_args.kwargs["timeout"], 77)
            self.assertEqual(run_command.call_args.kwargs["input"], "hello")
            self.assertEqual(run_command.call_args.kwargs["encoding"], "utf-8")
            self.assertEqual(run_command.call_args.kwargs["errors"], "replace")

    def test_ask_sends_multiline_prompt_through_stdin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_dir = Path(temp_dir) / "outputs"
            workspace.mkdir()
            run_command = Mock()

            def write_output(args, **kwargs):
                Path(args[args.index("-o") + 1]).write_text("saw transcript", encoding="utf-8")
                result = Mock()
                result.returncode = 0
                result.stderr = ""
                result.stdout = "session id: 019e3737-0000-7000-8000-abcdefabcdef\n"
                return result

            run_command.side_effect = write_output
            runner = CodexExecRunner(
                CodexExecConfig(workspace=workspace, output_dir=output_dir),
                run_command=run_command,
            )
            prompt = "Voice transcript:\nthis is the transcript\n\nTyped note:\nplease answer"

            answer = runner.ask(prompt)

            self.assertEqual(answer, "saw transcript")
            self.assertEqual(run_command.call_args.args[0][-1], "-")
            self.assertEqual(run_command.call_args.kwargs["input"], prompt)

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

    def test_ask_saves_new_session_id_from_stdout_when_state_is_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_dir = Path(temp_dir) / "outputs"
            state_path = Path(temp_dir) / "codex_bridge_state.json"
            workspace.mkdir()

            def run_command(args, **kwargs):
                Path(args[args.index("-o") + 1]).write_text("first answer", encoding="utf-8")
                result = Mock()
                result.returncode = 0
                result.stderr = ""
                result.stdout = "session id: 019e3734-0000-7000-8000-abcdefabcdef\n"
                return result

            runner = CodexExecRunner(
                CodexExecConfig(workspace=workspace, output_dir=output_dir, state_path=state_path),
                run_command=run_command,
            )

            self.assertEqual(runner.ask("first prompt"), "first answer")
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["session_id"], "019e3734-0000-7000-8000-abcdefabcdef")
            self.assertEqual(saved["last_answer_length"], 12)

    def test_reset_creates_fresh_session_and_saves_session_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_dir = Path(temp_dir) / "outputs"
            state_path = Path(temp_dir) / "codex_bridge_state.json"
            workspace.mkdir()
            state_path.write_text(json.dumps({"session_id": "old-session"}), encoding="utf-8")
            run_command = Mock()

            def write_output(args, **kwargs):
                Path(args[args.index("-o") + 1]).write_text("reset ready", encoding="utf-8")
                result = Mock()
                result.returncode = 0
                result.stderr = ""
                result.stdout = "session id: 019e3735-0000-7000-8000-abcdefabcdef\n"
                return result

            run_command.side_effect = write_output
            runner = CodexExecRunner(
                CodexExecConfig(workspace=workspace, output_dir=output_dir, state_path=state_path),
                run_command=run_command,
            )

            session_id = runner.reset("seed prompt")

            args = run_command.call_args.args[0]
            self.assertNotIn("resume", args)
            self.assertEqual(session_id, "019e3735-0000-7000-8000-abcdefabcdef")
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["session_id"], session_id)

    def test_ask_writes_jsonl_turn_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            output_dir = Path(temp_dir) / "outputs"
            state_path = Path(temp_dir) / "codex_bridge_state.json"
            turn_log_path = Path(temp_dir) / "codex_exec_turns.log"
            workspace.mkdir()

            def run_command(args, **kwargs):
                Path(args[args.index("-o") + 1]).write_text("logged answer", encoding="utf-8")
                result = Mock()
                result.returncode = 0
                result.stderr = ""
                result.stdout = "session id: 019e3736-0000-7000-8000-abcdefabcdef\n"
                return result

            runner = CodexExecRunner(
                CodexExecConfig(
                    workspace=workspace,
                    output_dir=output_dir,
                    state_path=state_path,
                    turn_log_path=turn_log_path,
                ),
                run_command=run_command,
            )

            runner.ask("log this")

            event = json.loads(turn_log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(event["event"], "turn_success")
            self.assertEqual(event["session_id"], "019e3736-0000-7000-8000-abcdefabcdef")
            self.assertEqual(event["prompt_length"], 8)
            self.assertEqual(event["answer_length"], 13)


class CodexBridgeStateTests(unittest.TestCase):
    def test_state_loads_missing_file_as_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = CodexBridgeState.load(Path(temp_dir) / "missing.json")

            self.assertIsNone(state.session_id)

    def test_parse_session_id_from_codex_stdout(self):
        stdout = "OpenAI Codex\nsession id: 019e3733-ccd7-7791-babc-b67a9c4468e6\n--------"

        self.assertEqual(parse_session_id(stdout), "019e3733-ccd7-7791-babc-b67a9c4468e6")


class CodexExecSessionTests(unittest.TestCase):
    def test_restart_resets_runner(self):
        runner = Mock()
        runner.reset.return_value = "new-session"
        session = CodexExecSession(runner)

        self.assertEqual(session.restart(), "new-session")

        runner.reset.assert_called_once()

    def test_ask_delegates_to_runner(self):
        runner = Mock()
        runner.ask.return_value = "answer"
        session = CodexExecSession(runner)

        self.assertEqual(session.ask("prompt"), "answer")
        runner.ask.assert_called_once_with("prompt")


if __name__ == "__main__":
    unittest.main()
