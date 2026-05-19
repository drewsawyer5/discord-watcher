import unittest
from unittest.mock import Mock, patch

import process_ingest


class ProcessIngestVoiceTests(unittest.TestCase):
    def test_run_ingest_voice_uses_shared_transcriber(self):
        attachment = {"filename": "voice.ogg", "url": "https://cdn.example/voice.ogg"}

        with (
            patch.object(process_ingest.discord_voice, "transcribe_attachment_dict", return_value="remember to update the vault") as transcribe,
            patch.object(process_ingest, "write_raw_md", return_value=process_ingest.VAULT_PATH / "raw.md"),
            patch.object(process_ingest, "vault_rel", return_value="raw.md"),
            patch.object(process_ingest, "call_llm", return_value='{"discord_reply":"ok","files":[]}'),
            patch.object(process_ingest, "_apply_ingest_result") as apply_result,
        ):
            ok = process_ingest.run_ingest_voice(attachment, "123")

        self.assertTrue(ok)
        transcribe.assert_called_once()
        apply_result.assert_called_once()

    def test_run_ingest_voice_rejects_garbled_transcript(self):
        attachment = {"filename": "noise.ogg", "url": "https://cdn.example/noise.ogg"}

        with (
            patch.object(process_ingest.discord_voice, "transcribe_attachment_dict", return_value="."),
            patch.object(process_ingest, "post_discord_reply") as reply,
            patch.object(process_ingest, "call_llm") as call_llm,
        ):
            ok = process_ingest.run_ingest_voice(attachment, "123")

        self.assertTrue(ok)
        reply.assert_called_once_with("Couldn't transcribe that - try again?", "123")
        call_llm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
