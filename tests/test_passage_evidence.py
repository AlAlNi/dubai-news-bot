import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unittest import TestCase
from unittest.mock import patch
import source_verification as review
import rss_collect


class PassageEvidenceTests(TestCase):
    def setUp(self):
        self.source = {
            "title": "Dubai transport plans",
            "text": "Dubai plans a new facility. Work has not started. Capacity will be 170,000 a year.",
            "url": "https://example.com/story",
        }

    def check(self, evidence, supported=True):
        result = {"emoji_verdicts": [], "supported": supported, "reason": "review result", "claims": [
            {"claim": "A facility is planned with capacity of 170,000 a year.",
             "supported": supported, "evidence": evidence}]}
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
        return review._validate_completion(payload, self.source, "post", "model", "openai")

    def test_separate_source_passages_support_compound_claim_without_copying(self):
        result = self.check([1, 3])
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["evidence_passages"][1]["text"], "Dubai plans a new facility.")
        self.assertEqual(result["evidence_passages"][3]["text"], "Capacity will be 170,000 a year.")

    def test_bad_references_never_approved(self):
        for evidence in [[], [99], [-1], [True], ["1"], [1, 99], None]:
            with self.subTest(evidence=evidence):
                self.assertNotEqual(self.check(evidence)["status"], "approved")

    def test_semantic_rejection_still_blocks_valid_references(self):
        self.assertEqual(self.check([1, 2], supported=False)["status"], "rejected")

    def test_legacy_ellipsis_cannot_be_accepted_as_quote(self):
        self.assertEqual(self.check(
            "Dubai plans a new facility... Capacity will be 170,000 a year.")["status"], "rejected")

    def test_only_legacy_evidence_failures_are_retried(self):
        stats = [
            {"url": "https://example.com/format", "status": "rejected", "strict_hash": "format-hash",
             "reason": "Пересказ не подтверждён источником: Missing or invalid source evidence"},
            {"url": "https://example.com/facts", "status": "rejected", "strict_hash": "facts-hash",
             "reason": "Wrong location"},
            {"url": "https://example.com/new-format", "status": "rejected",
             "reason": "Пересказ не подтверждён источником: Invalid source passage references"},
            {"url": "https://example.com/published", "status": "published",
             "reason": "Пересказ не подтверждён источником: Missing or invalid source evidence"},
        ]
        with patch("rss_collect.load_source_stats", return_value=stats), patch(
                "rss_collect.load_published_history", return_value=[]):
            urls, hashes = rss_collect.build_seen_links([])
        self.assertNotIn("https://example.com/format", urls)
        self.assertNotIn("format-hash", hashes)
        for suffix in ["facts", "new-format", "published"]:
            self.assertIn("https://example.com/" + suffix, urls)
        stats[0].update(reason="Wrong location")
        with patch("rss_collect.load_source_stats", return_value=stats), patch(
                "rss_collect.load_published_history", return_value=[]):
            urls, _ = rss_collect.build_seen_links([])
        self.assertIn("https://example.com/format", urls)
