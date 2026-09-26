"""The #inbox lane is a thin client of SLATE's door (#161): every drop also posts a Source, and its id rides the reply."""

import unittest
from unittest.mock import patch

import process_ingest


class ProcessIngestSlateDoorTests(unittest.TestCase):
    def test_url_drop_goes_through_the_door_and_the_reply_names_the_source(self):
        with (
            patch.object(process_ingest.slate_door, "capture_url", return_value={"id": 7, "duplicate_of": None}) as capture,
            patch.object(process_ingest, "fetch_url_content", return_value=("article body " * 50, False)),
            patch.object(process_ingest, "write_raw_md", return_value=process_ingest.VAULT_PATH / "raw.md"),
            patch.object(process_ingest, "vault_rel", return_value="raw.md"),
            patch.object(process_ingest, "get_system_prompt", return_value="sys"),
            patch.object(process_ingest, "get_existing_lists_context", return_value=""),
            patch.object(process_ingest, "call_llm", return_value='{"type":"article","title":"T","discord_reply":"**Ingested:** T","files":[]}'),
            patch.object(process_ingest, "post_discord_reply") as reply,
            patch.object(process_ingest, "_record_ingest_event"),
        ):
            ok = process_ingest.run_ingest("https://example.com/nas", "123")

        self.assertTrue(ok)
        capture.assert_called_once_with("https://example.com/nas")
        reply.assert_called_once_with("**Ingested:** T\n**SLATE:** SLATE source #7", "123")

    def test_text_drop_goes_through_the_door_and_a_closed_door_never_blocks_the_wiki(self):
        with (
            patch.object(process_ingest.slate_door, "capture_text", return_value=None) as capture,
            patch.object(process_ingest, "write_raw_md", return_value=process_ingest.VAULT_PATH / "raw.md"),
            patch.object(process_ingest, "vault_rel", return_value="raw.md"),
            patch.object(process_ingest, "get_system_prompt", return_value="sys"),
            patch.object(process_ingest, "get_existing_lists_context", return_value=""),
            patch.object(process_ingest, "call_llm", return_value='{"type":"note","title":"n","discord_reply":"noted","files":[]}'),
            patch.object(process_ingest, "post_discord_reply") as reply,
            patch.object(process_ingest, "_record_ingest_event"),
        ):
            ok = process_ingest.run_ingest_text("remember the plate seal remover is LA-101", "124")

        self.assertTrue(ok)
        capture.assert_called_once_with("remember the plate seal remover is LA-101")
        reply.assert_called_once_with("noted", "124")

    def test_voice_transcript_is_captured_as_text(self):
        with (
            patch.object(process_ingest.discord_voice, "transcribe_attachment_dict", return_value="remember to update the vault"),
            patch.object(process_ingest.slate_door, "capture_text", return_value={"id": 9, "duplicate_of": None}) as capture,
            patch.object(process_ingest, "write_raw_md", return_value=process_ingest.VAULT_PATH / "raw.md"),
            patch.object(process_ingest, "vault_rel", return_value="raw.md"),
            patch.object(process_ingest, "call_llm", return_value='{"discord_reply":"ok","files":[]}'),
            patch.object(process_ingest, "_apply_ingest_result") as apply_result,
        ):
            ok = process_ingest.run_ingest_voice({"filename": "voice.ogg", "url": "https://cdn.example/voice.ogg"}, "125")

        self.assertTrue(ok)
        capture.assert_called_once_with("remember to update the vault", name="voice: voice")
        self.assertEqual(apply_result.call_args.kwargs["slate_source"], {"id": 9, "duplicate_of": None})


class EveryDropReachesTheDoorTests(unittest.TestCase):
    """Review 2026-09-26: the commit said *every* drop — YouTube links and the two early-return paths included."""

    def test_youtube_drop_goes_through_the_door_and_the_reply_names_the_source(self):
        import json
        import subprocess
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock

        meta = Mock(returncode=0, stdout=json.dumps({"title": "Prime editing explained", "channel": "Ch", "upload_date": "20260901", "duration": 61, "id": "vid1"}))
        caps = Mock(returncode=0, stdout="")
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(subprocess, "run", side_effect=[meta, caps]),
            patch.object(process_ingest, "VAULT_PATH", Path(tmp)),
            patch.object(process_ingest.slate_door, "capture_url", return_value={"id": 11, "duplicate_of": None}) as capture,
            patch.object(process_ingest, "write_raw_md", return_value=Path(tmp) / "raw.md"),
            patch.object(process_ingest, "vault_rel", return_value="raw.md"),
            patch.object(process_ingest, "post_discord_reply") as reply,
        ):
            ok = process_ingest.run_ingest("https://www.youtube.com/watch?v=vid1", "130")

        self.assertTrue(ok)
        capture.assert_called_once_with("https://www.youtube.com/watch?v=vid1", name="Prime editing explained")
        self.assertIn("SLATE source #11", reply.call_args.args[0])

    def test_a_blocked_fetch_still_reports_the_source(self):
        with (
            patch.object(process_ingest.slate_door, "capture_url", return_value={"id": 7, "duplicate_of": None}),
            patch.object(process_ingest, "fetch_url_content", return_value=("[Fetch failed: 403]", False)),
            patch.object(process_ingest, "write_raw_md", return_value=process_ingest.VAULT_PATH / "raw.md"),
            patch.object(process_ingest, "vault_rel", return_value="raw.md"),
            patch.object(process_ingest, "write_retry_queue_entry"),
            patch.object(process_ingest, "post_discord_reply") as reply,
        ):
            ok = process_ingest.run_ingest("https://example.com/paywalled", "131")

        self.assertTrue(ok)
        self.assertIn("SLATE source #7", reply.call_args.args[0])

    def test_an_image_only_pdf_still_reports_the_source(self):
        from unittest.mock import Mock

        with (
            patch.object(process_ingest.requests, "get", return_value=Mock(content=b"%PDF-1.7 scan", raise_for_status=Mock())),
            patch.object(process_ingest, "write_raw_binary", return_value=process_ingest.VAULT_PATH / "scan.pdf"),
            patch.object(process_ingest, "vault_rel", return_value="scan.pdf"),
            patch.object(process_ingest.slate_door, "capture_file", return_value={"id": 5, "duplicate_of": None}),
            patch.object(process_ingest, "_extract_pdf_text", return_value=""),
            patch.object(process_ingest, "post_discord_reply") as reply,
        ):
            ok = process_ingest.run_ingest_pdf({"filename": "scan.pdf", "url": "https://cdn.example/scan.pdf"}, "132")

        self.assertTrue(ok)
        self.assertIn("SLATE source #5", reply.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
