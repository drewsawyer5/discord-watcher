import unittest
from unittest.mock import Mock, patch

import slate_door


def _resp(status: int, body: dict | None = None, text: str = "") -> Mock:
    resp = Mock()
    resp.status_code = status
    resp.text = text
    resp.json = Mock(return_value=body if body is not None else {})
    return resp


class SlateDoorTests(unittest.TestCase):
    def test_unconfigured_lane_never_calls_out(self):
        with patch.dict("os.environ", {"SLATE_API_URL": ""}, clear=False), patch.object(slate_door.requests, "post") as post:
            self.assertIsNone(slate_door.capture_url("https://example.com"))
            self.assertIsNone(slate_door.capture_text("hello"))
            self.assertIsNone(slate_door.capture_file("a.pdf", b"%PDF"))
        post.assert_not_called()
        self.assertFalse(slate_door.configured())

    def test_url_and_text_post_json_with_service_token_and_origin(self):
        env = {"SLATE_API_URL": "http://a6:8080/api/", "SLATE_JOBS_TOKEN": "tok"}
        with patch.dict("os.environ", env, clear=False), patch.object(
            slate_door.requests, "post", return_value=_resp(201, {"id": 7, "kind": "url", "name": "Home NAS", "duplicate_of": None})
        ) as post:
            source = slate_door.capture_url("https://example.com/nas", name="Home NAS")
            self.assertEqual(source["id"], 7)
            args, kwargs = post.call_args
            self.assertEqual(args[0], "http://a6:8080/api/knowledge/sources")
            self.assertEqual(kwargs["json"], {"url": "https://example.com/nas", "origin": "ingest-lane", "name": "Home NAS"})
            self.assertEqual(kwargs["headers"], {"Authorization": "Bearer tok"})

            slate_door.capture_text("a thought", name="a thought")
            self.assertEqual(post.call_args.kwargs["json"], {"text": "a thought", "origin": "ingest-lane", "name": "a thought"})

    def test_file_posts_multipart_with_detected_mime(self):
        env = {"SLATE_API_URL": "http://a6:8080/api", "SLATE_JOBS_TOKEN": ""}  # loopback needs no token
        with patch.dict("os.environ", env, clear=False), patch.object(
            slate_door.requests, "post", return_value=_resp(200, {"id": 3, "kind": "pdf", "name": "paper.pdf", "duplicate_of": 3})
        ) as post:
            source = slate_door.capture_file("paper.pdf", b"%PDF-1.7")
            self.assertEqual(source["duplicate_of"], 3)
            kwargs = post.call_args.kwargs
            self.assertEqual(kwargs["files"]["file"], ("paper.pdf", b"%PDF-1.7", "application/pdf"))
            self.assertEqual(kwargs["data"], {"origin": "ingest-lane"})
            self.assertEqual(kwargs["headers"], {})

    def test_refusals_and_outages_are_logged_not_raised(self):
        with patch.dict("os.environ", {"SLATE_API_URL": "http://a6:8080/api"}, clear=False):
            with patch.object(slate_door.requests, "post", return_value=_resp(413, text="too large")):
                self.assertIsNone(slate_door.capture_file("big.pdf", b"x"))
            with patch.object(slate_door.requests, "post", side_effect=slate_door.requests.ConnectionError("refused")):
                self.assertIsNone(slate_door.capture_url("https://example.com"))

    def test_describe_and_reply_suffix(self):
        self.assertEqual(slate_door.describe({"id": 7, "duplicate_of": None}), "SLATE source #7")
        self.assertEqual(slate_door.describe({"id": 9, "duplicate_of": 2}), "SLATE source #2 (already captured)")
        self.assertEqual(slate_door.describe(None), "")
        self.assertEqual(slate_door.reply_suffix({"id": 7, "duplicate_of": None}), "\n**SLATE:** SLATE source #7")
        self.assertEqual(slate_door.reply_suffix(None), "")


if __name__ == "__main__":
    unittest.main()
