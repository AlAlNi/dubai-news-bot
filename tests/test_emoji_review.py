"""Offline contract checks; semantic verdicts below are mocked, not live AI."""
import copy
import json
from pathlib import Path
import sys
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from emoji_review import inserted_emoji, valid_emoji_verdicts
from openai_emoji import apply_decorations
from source_verification import _validate_completion, is_verified_draft


class EmojiReviewTests(TestCase):
    def review(self, source, post, result):
        payload = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(result)}}]}
        return _validate_completion(payload, source, post, "offline", "openai")

    def verdicts(self, post):
        return [{**item, "supported": True, "reason": "Explicit paragraph subject"}
                for item in inserted_emoji(post)]

    def test_saved_failures_require_explicit_semantic_rejection(self):
        cases = json.loads((Path(__file__).parent / "fixtures/emoji_semantic_failures.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(source=case["source"]["title"]):
                old = case["old_review"]
                self.assertEqual(self.review(case["source"], case["post"], old)["status"], "rejected")
                result = {**old, "emoji_verdicts": self.verdicts(case["post"])}
                for item in result["emoji_verdicts"]:
                    if item["emoji"] == "🚇":
                        item.update(supported=False, reason="No metro in this paragraph or source")
                report = self.review(case["source"], case["post"], result)
                self.assertEqual(report["status"], "rejected")
                self.assertIn("emoji", report["reason"])

    def test_appropriate_symbols_preserve_exact_post_and_publish_hash(self):
        for symbol, subject in [("🚗", "electric vehicles"), ("🅿️", "parking"),
                                ("🚇", "metro"), ("✈️", "flights"), ("🎭", "theatre")]:
            with self.subTest(symbol=symbol):
                source = {"title": subject, "text": "Dubai announces " + subject + ".", "url": "https://example.com/news"}
                original = "<b>" + subject + "</b>\n\nDubai announces " + subject + ".\n\n<a href='https://example.com/news'>Source</a>"
                post = apply_decorations(original, {"decorations": [{"paragraph": 0, "emoji": symbol}]})
                result = {"supported": True, "reason": "Supported", "claims": [
                    {"claim": subject, "supported": True, "evidence": [1]}],
                    "emoji_verdicts": self.verdicts(post)}
                report = self.review(source, post, result)
                self.assertEqual(report["status"], "approved")
                self.assertEqual(post.replace(symbol + " ", "", 1), original)
                draft = {"source_snapshot": source, "summary_ru": post, "source_urls": [source["url"]],
                         "workflow_state": "approved_by_editor", "editorial_decision": "approved",
                         "source_verification": report}
                self.assertTrue(is_verified_draft(draft))
                modified = copy.deepcopy(draft)
                modified["summary_ru"] = original
                self.assertFalse(is_verified_draft(modified))
                modified = copy.deepcopy(draft)
                modified["source_verification"].pop("emoji_review_version")
                self.assertFalse(is_verified_draft(modified))

    def test_complete_exact_unique_verdicts_required(self):
        post = "<b>🏥 Hospital</b>\n\n🅿️ Parking."
        good = self.verdicts(post)
        self.assertTrue(valid_emoji_verdicts(post, good))
        invalid = [None, [], good[:1], good + [good[0]],
                   good + [{"paragraph": 2, "emoji": "🚗", "supported": True, "reason": "extra"}]]
        for field, value in [("paragraph", True), ("paragraph", 99), ("emoji", "🚇"),
                             ("supported", False), ("supported", "true"), ("reason", "")]:
            changed = copy.deepcopy(good)
            changed[0][field] = value
            invalid.append(changed)
        for value in invalid:
            with self.subTest(value=value):
                self.assertFalse(valid_emoji_verdicts(post, value))

    def test_zero_insertions_and_dedup_review_only_actual_prefixes(self):
        self.assertTrue(valid_emoji_verdicts("<b>Plain</b>\n\nBody.", []))
        self.assertFalse(valid_emoji_verdicts("Plain", None))
        post = apply_decorations("<b>Metro</b>\n\nMetro detail.", {"decorations": [
            {"paragraph": 0, "emoji": "🚇"}, {"paragraph": 1, "emoji": "🚇"}]})
        self.assertEqual(inserted_emoji(post), [{"paragraph": 0, "emoji": "🚇"}])
        self.assertTrue(valid_emoji_verdicts(post, self.verdicts(post)))
