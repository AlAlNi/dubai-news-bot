import copy
import json
import os
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import source_verification as verification
import rss_collect
import next_draft


class SourceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.source = verification.source_snapshot(
            "Dubai bus plans", "RTA plans to add 10 buses in Dubai in December.", "https://example.com/news"
        )
        self.summary = "<b>Автобусы в Дубае</b>\nRTA планирует добавить 10 автобусов в декабре."
        self.review = {"supported": True, "reason": "Supported by source", "claims": [
            {"claim": "RTA планирует добавить 10 автобусов в Дубае в декабре.",
             "supported": True, "evidence": self.source["text"]}
        ]}

    def response(self, review=None, finish_reason="stop", status=200):
        response = Mock(status_code=status)
        response.json.return_value = {"choices": [{"finish_reason": finish_reason, "message": {
            "content": json.dumps(self.review if review is None else review)
        }}]}
        return response

    def check(self, response):
        if response.status_code != 200:
            return {"status": "error"}
        return verification._validate_completion(response.json(), self.source, self.summary,
                                                   "gpt-4.1-mini-2025-04-14", "openai")

    def test_supported_translation_and_exact_quotes_approved(self):
        self.assertEqual(self.check(self.response())["status"], "approved")

    def test_invented_evidence_rejected_even_if_model_approves(self):
        self.review["claims"][0]["evidence"] = "RTA has already added 100 buses."
        self.assertEqual(self.check(self.response())["status"], "rejected")

    def test_changed_number_date_location_and_certainty_rejected(self):
        for claim in ["100 автобусов", "в январе", "в Абу-Даби", "уже добавила автобусы"]:
            with self.subTest(claim=claim):
                self.review["supported"] = False
                self.review["claims"][0].update(claim=claim, supported=False)
                self.assertEqual(self.check(self.response())["status"], "rejected")

    def test_malformed_empty_and_truncated_verdicts_fail_closed(self):
        for review in [{}, [], {"supported": "true", "reason": "OK", "claims": []},
                       {"supported": True, "reason": "OK", "claims": []}]:
            with self.subTest(review=review):
                self.assertNotEqual(self.check(self.response(review))["status"], "approved")
        self.assertEqual(self.check(self.response(finish_reason="length"))["status"], "error")
        self.assertEqual(self.check(self.response(status=429))["status"], "error")

    def test_missing_key_and_headline_only_blocked(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "SOURCE_VERIFIER": "openai"}), patch("source_verification.request_with_retry") as request:
            self.assertEqual(verification.verify_summary(self.source, self.summary)["status"], "error")
            self.source["text"] = self.source["title"]
            self.assertEqual(verification.verify_summary(self.source, self.summary)["status"], "rejected")
            request.assert_not_called()

    def test_modified_and_legacy_drafts_cannot_publish(self):
        draft = {"source_snapshot": self.source, "summary_ru": self.summary,
                 "source_urls": [self.source["url"]], "workflow_state": "approved_by_editor",
                 "editorial_decision": "approved", "source_verification": self.check(self.response())}
        self.assertTrue(verification.is_verified_draft(draft))
        for field, value in [("summary_ru", "Изменённый пост"), ("source_urls", ["https://other.com"]),
                             ("source_verification", {}), ("editorial_decision", "rejected")]:
            modified = copy.deepcopy(draft)
            modified[field] = value
            self.assertFalse(verification.is_verified_draft(modified))
        modified = copy.deepcopy(draft)
        modified["source_snapshot"]["text"] = "Changed source"
        self.assertFalse(verification.is_verified_draft(modified))

    def test_selector_skips_unverified_draft_before_image_or_source_checks(self):
        with patch("next_draft.os.path.exists", return_value=True), patch(
            "next_draft.load_drafts", return_value=[{"summary_ru": self.summary}]
        ), patch("next_draft.load_source_stats", return_value=[]), patch(
            "next_draft.load_published_history", return_value=[]
        ), patch("next_draft.save_source_stats"), patch("next_draft.is_valid_image_url") as image:
            self.assertEqual(next_draft.get_next_post_payload_with_image(), (None, None, None))
            image.assert_not_called()

    def test_short_factual_russian_post_is_not_padded(self):
        self.assertEqual(rss_collect.ensure_summary_quality(self.summary, "", ""),
                         self.summary.replace("</b>\n", "</b>\n\n"))

    def test_emoji_step_runs_before_final_verifier(self):
        item = {"title": self.source["title"], "description": self.source["text"],
                "link": self.source["url"], "source": "RTA", "source_priority": 1}
        decorated = "<b>Decorated headline</b>\n\nUnchanged body."
        events = []
        def decorate(text, storage):
            events.append("emoji")
            return {"post": decorated, "api_calls": 1, "cached": False,
                    "status": "decorated", "reason_code": "", "decoration_count": 1,
                    "diagnostics": {"http_status": 200}}
        def verify(source, text, *args, **kwargs):
            events.append("verify")
            self.assertEqual(text, decorated)
            return {"status": "approved", "reason": "test", "api_calls": 1, "provider": "openai"}
        with patch("rss_collect.ENABLE_CHEAP_PREFILTER", False), patch(
            "rss_collect.prepare_post", return_value={"status": "prepared", "reason": "test", "post": self.summary}
        ), patch("rss_collect.fetch_article", return_value=None), patch(
            "rss_collect.fetch_image_for_news", return_value=None
        ), patch("rss_collect.decorate_summary", side_effect=decorate), patch(
            "rss_collect.verify_summary", side_effect=verify
        ):
            metrics = {}
            result = rss_collect.process_news_item(item, writer_context={"calls_made": 0, "max_calls": -1}, run_metrics=metrics)
        self.assertEqual(events, ["emoji", "verify"])
        self.assertEqual(result["summary_ru"], decorated)
        self.assertEqual(metrics["openai_calls"], 2)
        self.assertEqual(result["emoji_decoration"]["status"], "decorated")
        self.assertEqual(result["emoji_decoration"]["decoration_count"], 1)
        self.assertEqual(result["emoji_decoration"]["diagnostics"]["http_status"], 200)
        self.assertNotEqual(result["emoji_decoration"]["input_sha256"],
                            result["emoji_decoration"]["output_sha256"])

    def test_legacy_verifier_cannot_make_paid_requests(self):
        with patch.dict(os.environ, {"SOURCE_VERIFIER": "deepseek"}), patch("source_verification.request_with_retry") as request:
            self.assertEqual(verification.verify_summary(self.source, self.summary, "old-key")["status"], "error")
            request.assert_not_called()

    def test_collector_rejects_post_after_generation_and_counts_verifier_error(self):
        item = {"title": self.source["title"], "description": self.source["text"],
                "link": self.source["url"], "source": "RTA", "source_priority": 1}
        for status in ["rejected", "error", "approved"]:
            with self.subTest(status=status), patch("rss_collect.ENABLE_CHEAP_PREFILTER", False), patch(
                "rss_collect.prepare_post", return_value={"status": "prepared", "reason": "OK", "post": self.summary}
            ), patch("rss_collect.fetch_image_for_news", return_value=None), patch("rss_collect.verify_summary", return_value={"status": status, "reason": "test"}) as review, patch(
                "rss_collect.mark_news_as_rejected"
            ), patch("rss_collect.mark_news_as_technical_error"):
                metrics = {}
                calls = {"calls_made": 0, "max_calls": -1}
                result = rss_collect.process_news_item(item, writer_context=calls, run_metrics=metrics)
                review.assert_called_once()
                self.assertEqual(calls["calls_made"], 1)
                if status == "approved":
                    self.assertEqual(result["source_snapshot"], self.source)
                else:
                    self.assertIsNone(result)
                self.assertEqual(metrics.get("technical_errors", 0), int(status == "error"))

    def test_openai_deferral_pauses_collection_without_rejecting_source(self):
        item = {"title": self.source["title"], "description": self.source["text"],
                "link": self.source["url"], "source": "RTA", "source_priority": 1}
        with patch("rss_collect.verifier_provider", return_value="openai"), patch(
            "rss_collect.ENABLE_CHEAP_PREFILTER", False
        ), patch("rss_collect.prepare_post", return_value={"status": "prepared", "reason": "OK", "post": self.summary}), patch(
            "rss_collect.fetch_image_for_news", return_value=None
        ), patch(
            "rss_collect.verify_summary", return_value={"status": "deferred", "reason": "Daily budget",
                                                       "provider": "openai", "api_calls": 0}
        ), patch("rss_collect.mark_news_as_rejected") as rejected:
            metrics, calls = {}, {"calls_made": 0, "max_calls": -1}
            self.assertIsNone(rss_collect.process_news_item(item, writer_context=calls, run_metrics=metrics))
            self.assertEqual(calls["calls_made"], 1)
            self.assertTrue(metrics["verification_paused"])
            self.assertEqual(metrics["openai_calls"], 0)
            rejected.assert_not_called()


if __name__ == "__main__":
    unittest.main()
