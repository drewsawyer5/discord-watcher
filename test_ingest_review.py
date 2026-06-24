import unittest

from ingest_review import check_extraction, summarize_review


class CheckExtractionTests(unittest.TestCase):
    def test_clean_long_article_has_no_flags(self):
        content = "word " * 500  # ~2500 chars of plausible article text
        self.assertEqual(check_extraction(content), [])

    def test_short_content_is_flagged(self):
        flags = check_extraction("too short")
        self.assertEqual(len(flags), 1)
        self.assertIn("short", flags[0].lower())

    def test_regex_fallback_is_flagged(self):
        content = "word " * 500
        flags = check_extraction(content, used_fallback=True)
        self.assertTrue(any("fallback" in f.lower() for f in flags))

    def test_truncation_is_flagged(self):
        content = "word " * 500
        flags = check_extraction(content, truncated=True)
        self.assertTrue(any("truncat" in f.lower() for f in flags))

    def test_paywall_marker_is_flagged(self):
        content = "word " * 500 + " Please enable JavaScript to continue."
        flags = check_extraction(content)
        self.assertTrue(any("paywall" in f.lower() or "block" in f.lower() for f in flags))

    def test_multiple_problems_produce_multiple_flags(self):
        flags = check_extraction("are you a robot", used_fallback=True)
        self.assertGreaterEqual(len(flags), 2)


class SummarizeReviewTests(unittest.TestCase):
    _CLEAN_LLM = {"extraction_complete": True, "output_consistent": True, "issues": ""}

    def test_no_problems_is_not_flagged(self):
        result = summarize_review([], self._CLEAN_LLM)
        self.assertFalse(result["flagged"])
        self.assertEqual(result["side"], "")
        self.assertEqual(result["reasons"], [])

    def test_python_extraction_flags_attribute_to_extraction(self):
        result = summarize_review(["content very short (90 chars)"], self._CLEAN_LLM)
        self.assertTrue(result["flagged"])
        self.assertEqual(result["side"], "extraction")
        self.assertIn("content very short (90 chars)", result["reasons"])

    def test_llm_incomplete_body_attributes_to_extraction(self):
        review = {"extraction_complete": False, "output_consistent": True, "issues": "ends mid-sentence"}
        result = summarize_review([], review)
        self.assertEqual(result["side"], "extraction")
        self.assertTrue(any("ends mid-sentence" in r for r in result["reasons"]))

    def test_llm_output_inconsistent_attributes_to_llm(self):
        review = {"extraction_complete": True, "output_consistent": False, "issues": "title says X, body is Y"}
        result = summarize_review([], review)
        self.assertEqual(result["side"], "llm")
        self.assertTrue(any("title says X" in r for r in result["reasons"]))

    def test_problems_on_both_sides_attribute_to_both(self):
        review = {"extraction_complete": True, "output_consistent": False, "issues": "mismatch"}
        result = summarize_review(["regex fallback used"], review)
        self.assertEqual(result["side"], "both")

    def test_missing_llm_review_defaults_to_no_llm_problem(self):
        result = summarize_review([], None)
        self.assertFalse(result["flagged"])


if __name__ == "__main__":
    unittest.main()
