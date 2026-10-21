"""#387: scripts load only the keys they use from the shared .env, never the whole file."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import env_keys

SAMPLE = """# shared file
OPENAI_API_KEY=sk-should-never-load
export DISCORD_BOT_TOKEN="bot token with spaces"
GENERAL_CHANNEL_ID=123 # inline comment
WHISPER_MODEL='small'
WHISPER_ENDPOINT=
SLATE_API_URL=https://slate.example/api
SLATE_API_URL=https://later.example/api
URL_WITH_REF=${HOME_BASE}/x
"""


class EnvKeys(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / ".env"
        self.path.write_text(SAMPLE, encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def test_loads_only_named_keys(self):
        env = {}
        got = env_keys.load("DISCORD_BOT_TOKEN", "GENERAL_CHANNEL_ID", path=self.path, environ=env)
        self.assertEqual(got, ["DISCORD_BOT_TOKEN", "GENERAL_CHANNEL_ID"])
        self.assertEqual(env, {"DISCORD_BOT_TOKEN": "bot token with spaces", "GENERAL_CHANNEL_ID": "123"})
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_quotes_empty_values_and_last_line_wins(self):
        env = {}
        env_keys.load("WHISPER_MODEL", "WHISPER_ENDPOINT", "SLATE_API_URL", path=self.path, environ=env)
        self.assertEqual(env, {"WHISPER_MODEL": "small", "WHISPER_ENDPOINT": "", "SLATE_API_URL":
                               "https://later.example/api"})

    def test_environment_wins(self):
        env = {"GENERAL_CHANNEL_ID": "999"}
        self.assertEqual(env_keys.load("GENERAL_CHANNEL_ID", path=self.path, environ=env), [])
        self.assertEqual(env["GENERAL_CHANNEL_ID"], "999")

    def test_missing_key_and_missing_file(self):
        env = {}
        self.assertEqual(env_keys.load("NOT_THERE", path=self.path, environ=env), [])
        self.assertEqual(env_keys.load("SLATE_API_URL", path=Path(self.dir.name) / "nope", environ=env), [])
        self.assertEqual(env, {})

    def test_read_returns_only_named(self):
        self.assertEqual(set(env_keys.read(["SLATE_API_URL", "NOPE"], self.path)), {"SLATE_API_URL"})


class NoWholeFileLoads(unittest.TestCase):
    def test_no_script_calls_load_dotenv(self):
        offenders = [p.name for p in REPO.glob("*.py")
                     if re.search(r"\bload_dotenv\s*\(|\bdotenv_values\s*\(", p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_unit_file_has_no_environment_file(self):
        self.assertNotIn("EnvironmentFile=", (REPO / "voice-watcher.service").read_text(encoding="utf-8"))

    def test_scripts_import_with_only_their_keys(self):
        """Import each script against a sample shared file in a clean process: none of them picks up OPENAI_API_KEY."""
        with tempfile.TemporaryDirectory() as d:
            env_file = Path(d) / ".env"
            env_file.write_text(SAMPLE, encoding="utf-8")
            code = (
                "import os, sys; sys.path.insert(0, %r)\n"
                "import discord_client, discord_voice, slate_beat, tower_uptime_monitor, restart, voice_watcher\n"
                "print('OPENAI' if 'OPENAI_API_KEY' in os.environ else 'clean',"
                " os.environ.get('DISCORD_BOT_TOKEN'), os.environ.get('WHISPER_MODEL'),"
                " os.environ.get('SLATE_API_URL'))\n" % str(REPO)
            )
            sample_keys = set(re.findall(r"^(?:export )?(\w+)=", SAMPLE, re.M))
            env = {k: v for k, v in os.environ.items() if k not in sample_keys}
            env["SHARED_ENV_FILE"] = str(env_file)
            env["INBOX_LOG_DIR"] = d
            env["DISCORD_INBOX"] = d
            done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120,
                                  cwd=d)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertEqual(done.stdout.strip().splitlines()[-1],
                             "clean bot token with spaces small https://later.example/api")


if __name__ == "__main__":
    unittest.main()
