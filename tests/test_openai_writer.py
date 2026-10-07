import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from openai_writer import prepare_post
from openai_budget import Budget, BudgetUnavailable, RESERVATION_MICROUSD


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        (self.path / "openai_budget.json").write_text('{"version":1,"months":{}}')
        env = patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "BOT_ENVIRONMENT": "production",
            "OPENAI_API_KEY": "fake", "OPENAI_MONTHLY_BUDGET_USD": "3",
            "OPENAI_MAX_CALLS_PER_DAY": "8", "OPENAI_BYPASS_DAILY_LIMIT": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.source = {"title": "Dubai buses", "text": "RTA plans to add 10 buses in Dubai.", "url": "https://example.com/article"}
        self.response = Mock(status_code=200)
        self.response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"publish": True, "reason": "Useful news", "post": "<b>Дубай</b>\n\nRTA планирует добавить 10 автобусов."})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100}}

    def test_one_request_reserved_before_network_and_cached(self):
        def network(*args, **kwargs):
            ledger = Budget(self.path).read()
            self.assertEqual(sum(m["reserved_microusd"] for m in ledger["months"].values()), RESERVATION_MICROUSD)
            cache = json.loads((self.path / "openai_writer_cache.json").read_text())
            self.assertEqual(next(iter(cache.values()))["result"]["status"], "deferred")
            self.assertEqual(kwargs["max_attempts"], 1)
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(args[1], "https://api.openai.com/v1/chat/completions")
            self.assertEqual(kwargs["json"]["max_completion_tokens"], 1000)
            return self.response
        with patch("openai_writer.request_with_retry", side_effect=network) as req:
            self.assertEqual(prepare_post(self.source, self.path)["status"], "prepared")
            cached = prepare_post(self.source, self.path)
            self.assertTrue(cached["cached"])
            self.assertEqual(cached["api_calls"], 0)
            req.assert_called_once()

    def test_failed_reservation_sends_nothing(self):
        with patch("openai_writer.persist_cache", side_effect=OSError), patch("openai_writer.request_with_retry") as req:
            self.assertEqual(prepare_post(self.source, self.path)["status"], "deferred")
            req.assert_not_called()

    def test_historical_year_and_publication_metadata_stay_separate(self):
        import hashlib
        import openai_writer
        fixture = json.loads((Path(__file__).parent / "fixtures/writer_historical_year.json").read_text(encoding="utf-8"))
        with patch("openai_writer.request_with_retry", return_value=self.response) as request:
            prepare_post(fixture["source"], self.path)
        payload = request.call_args.kwargs["json"]
        source = json.loads(payload["messages"][1]["content"])["source"]
        self.assertEqual(source, fixture["source"])
        self.assertIn("October 5, 2024", source["text"])
        self.assertTrue(source["published_at"].startswith("2026-10-05"))
        instruction = payload["messages"][0]["content"]
        self.assertIn("freshness only, not an event date", instruction)
        self.assertIn("existing programme as a new launch", instruction)
        old = json.loads(json.dumps(payload))
        old["messages"][0]["content"] = instruction.replace(openai_writer.NEWS_PROMPT.split("Напиши", 1)[0], "", 1)
        key = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertNotEqual(key(payload), key(old))

    def test_saved_wrong_year_rejected_corrected_year_approved_offline(self):
        from source_verification import _validate_completion
        fixture = json.loads((Path(__file__).parent / "fixtures/writer_historical_year.json").read_text(encoding="utf-8"))
        review = fixture["bad_review"]
        def validate(post):
            raw = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(review)}}]}
            return _validate_completion(raw, fixture["source"], post, "offline", "openai")
        self.assertEqual(validate(fixture["bad_post"])["status"], "rejected")
        # Mocked reviewer: verifies the contract, not real model performance.
        post = fixture["bad_post"].replace("2026 года", "2024 года").replace("стартовала", "действует")
        review.update(supported=True, reason="Historical date preserved", emoji_verdicts=[])
        for claim in review["claims"]:
            claim.update(supported=True, claim=claim["claim"].replace("2026 года", "2024 года"))
        self.assertEqual(validate(post)["status"], "approved")

    def test_timeout_is_not_retried_on_next_run(self):
        with patch("openai_writer.request_with_retry", side_effect=TimeoutError) as req:
            self.assertEqual(prepare_post(self.source, self.path)["status"], "error")
            self.assertTrue(prepare_post(self.source, self.path)["cached"])
            req.assert_called_once()

    def test_generation_leaves_budget_and_daily_slot_for_verifier(self):
        with patch.dict(os.environ, {"OPENAI_MONTHLY_BUDGET_USD": "0.02"}), patch("openai_writer.request_with_retry") as req:
            self.assertEqual(prepare_post(self.source, self.path)["status"], "deferred")
            req.assert_not_called()
        budget = Budget(self.path)
        for _ in range(7):
            budget.reserve()
        with self.assertRaises(BudgetUnavailable):
            budget.reserve("generation")
        budget.reserve()  # Last slot can still verify an existing post.

    def test_invalid_and_truncated_results_cannot_be_prepared(self):
        for finish, content in [("length", {}), ("stop", {"publish": "true", "reason": "", "post": "x"}),
                                ("stop", {"publish": True, "reason": "", "post": ""})]:
            with self.subTest(finish=finish, content=content):
                self.source["title"] = str(content) + finish
                self.response.json.return_value["choices"][0] = {"finish_reason": finish,
                    "message": {"content": json.dumps(content)}}
                with patch("openai_writer.request_with_retry", return_value=self.response):
                    self.assertEqual(prepare_post(self.source, self.path)["status"], "error")

    def test_no_deepseek_endpoints_remain(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "src").glob("*.py"):
            self.assertNotIn("api.deepseek.com", path.read_text(encoding="utf-8"))

    def test_collector_stops_before_third_generation(self):
        import rss_collect
        item = {"title": self.source["title"], "description": self.source["text"],
                "link": self.source["url"], "source": "RTA", "source_priority": 1}
        with patch("rss_collect.ENABLE_CHEAP_PREFILTER", False), patch("rss_collect.prepare_post") as writer:
            metrics = {}
            self.assertIsNone(rss_collect.process_news_item(item, writer_context={"calls_made": 2}, run_metrics=metrics))
            writer.assert_not_called()
            self.assertTrue(metrics["verification_paused"])

    def test_generation_deferral_neither_rejects_source_nor_verifies(self):
        import rss_collect
        item = {"title": self.source["title"], "description": self.source["text"],
                "link": self.source["url"], "source": "RTA", "source_priority": 1}
        with patch("rss_collect.ENABLE_CHEAP_PREFILTER", False), patch("rss_collect.prepare_post", return_value={
            "status": "deferred", "reason": "Monthly limit", "api_calls": 0}), patch("rss_collect.verify_summary") as review, patch("rss_collect.mark_news_as_rejected") as reject:
            metrics = {}
            self.assertIsNone(rss_collect.process_news_item(item, run_metrics=metrics))
            self.assertTrue(metrics["verification_paused"])
            review.assert_not_called()
            reject.assert_not_called()
