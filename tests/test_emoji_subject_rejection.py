"""Offline regression coverage; no reconstruction of the historical EV response."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from openai_emoji import EmojiResponseError, subject_decorations, decorate_summary

TEXT = '<b>Cars &amp; parking</b>\n\nElectric vehicles.\n\n<a href="https://example.com?q=1&amp;x=2">Source</a>'


class SubjectRejectionTests(TestCase):
    def test_each_subject_condition_keeps_exact_original_and_cached_diagnostics(self):
        examples = [
            (None, "non_string"),
            ({"arbitrary": "not retained"}, "non_string"),
            ("", "empty_or_whitespace"),
            (" \t\n", "empty_or_whitespace"),
            ("x" * 80 + "PRIVATE_TAIL", "over_80_characters"),
            (" Cars", "surrounding_whitespace"),
            ("Cars ", "surrounding_whitespace"),
            ("cars", "not_exact_substring"),
            ("Cars &amp; parking", "not_exact_substring"),
            ("Electric vehicles", "not_exact_substring"),
        ]
        for subject, condition in examples:
            with self.subTest(subject=subject), tempfile.TemporaryDirectory() as storage:
                value = {"decorations": [{"paragraph": 0, "subject": subject, "emoji": None}]}
                expected = {"paragraph": 0, "subject_excerpt": subject[:80] if isinstance(subject, str) else None,
                            "condition": condition}
                with self.assertRaises(EmojiResponseError) as raised:
                    subject_decorations(TEXT, value)
                self.assertEqual(raised.exception.code, "unsupported_subject_excerpt")
                self.assertEqual(raised.exception.subject_rejection, expected)
                Path(storage, "openai_budget.json").write_text('{"version":1,"months":{}}')
                response = Mock(status_code=200)
                response.json.return_value = {
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                    "unrelated": "RAW_RESPONSE_NOT_RETAINED",
                }
                with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-key", "GITHUB_ACTIONS": "false",
                                            "OPENAI_MONTHLY_BUDGET_USD": "3", "OPENAI_MAX_CALLS_PER_DAY": "8"}, clear=True), patch(
                        "openai_emoji.request_with_retry", return_value=response) as request:
                    first = decorate_summary(TEXT, storage)
                    cached = decorate_summary(TEXT, storage)
                request.assert_called_once()
                for report in (first, cached):
                    self.assertEqual(report["post"], TEXT)
                    self.assertEqual(report["reason_code"], "unsupported_subject_excerpt")
                    self.assertEqual(report["diagnostics"]["subject_rejection"], expected)
                    self.assertEqual(report["decoration_count"], 0)
                self.assertEqual(first["api_calls"], 1)
                self.assertEqual(cached["api_calls"], 0)
                self.assertTrue(cached["cached"])
                cache = Path(storage, "openai_emoji_cache.json").read_text()
                for retained in (json.dumps(first), json.dumps(cached), cache):
                    for excluded in ("PRIVATE_TAIL", "RAW_RESPONSE_NOT_RETAINED", "not retained", "offline-key"):
                        self.assertNotIn(excluded, retained)

    def test_exact_visible_excerpt_still_passes_without_normalizing_subject(self):
        for paragraph, subject in [(0, "Cars & parking"), (1, "Electric vehicles")]:
            with self.subTest(paragraph=paragraph):
                self.assertEqual(subject_decorations(TEXT, {"decorations": [
                    {"paragraph": paragraph, "subject": subject, "emoji": None}]}), {"decorations": []})

    def test_diagnostic_points_to_first_failed_selection_not_first_paragraph(self):
        value = {"decorations": [
            {"paragraph": 0, "subject": "Cars", "emoji": None},
            {"paragraph": 1, "subject": "electric vehicles", "emoji": None},
        ]}
        with self.assertRaises(EmojiResponseError) as raised:
            subject_decorations(TEXT, value)
        self.assertEqual(raised.exception.subject_rejection, {
            "paragraph": 1, "subject_excerpt": "electric vehicles", "condition": "not_exact_substring"})
