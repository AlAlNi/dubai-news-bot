import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import source_verification as verification
from openai_budget import Budget, BudgetUnavailable, OUTPUT_TOKEN_CEILING


class OpenAIVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "SOURCE_VERIFIER": "auto",
                                          "OPENAI_API_KEY": "test-openai-key", "OPENAI_MAX_CALLS_PER_DAY": "8",
                                          "OPENAI_MONTHLY_BUDGET_USD": "3"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = pathlib.Path(self.temp.name) / "openai_budget.json"
        self.path.write_text('{"version":1,"months":{}}', encoding="utf-8")
        self.source = verification.source_snapshot("Dubai buses", "RTA plans to add 10 buses in Dubai.",
                                                   "https://example.com/news")
        self.summary = "<b>Дубай</b> RTA планирует добавить 10 автобусов."
        self.review = {"supported": True, "reason": "Supported", "claims": [{
            "claim": self.summary, "supported": True, "evidence": self.source["text"],
        }]}
        self.response = Mock(status_code=200)
        self.response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(self.review), "refusal": None,
        }}], "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}

    def verify(self):
        return verification.verify_summary(self.source, self.summary, "deepseek-key", storage_dir=self.temp.name)

    def calls(self):
        return sum(day["calls"] for month in json.loads(self.path.read_text())["months"].values()
                   for day in month["days"].values())

    def test_routes_to_openai_with_caps_and_schema_without_deepseek_call(self):
        with patch("openai_verifier.request_with_retry", return_value=self.response) as request, patch(
            "source_verification.request_with_retry"
        ) as deepseek:
            report = self.verify()
        self.assertEqual(report["status"], "approved")
        self.assertEqual(report["provider"], "openai")
        self.assertEqual(self.calls(), 1)
        self.assertEqual(request.call_args.args[1], "https://api.openai.com/v1/chat/completions")
        args = request.call_args.kwargs
        self.assertEqual(args["max_attempts"], 1)
        self.assertFalse(args["allow_redirects"])
        self.assertFalse(args["json"]["store"])
        self.assertEqual(args["json"]["max_completion_tokens"], OUTPUT_TOKEN_CEILING)
        self.assertTrue(args["json"]["response_format"]["json_schema"]["strict"])
        deepseek.assert_not_called()
        self.assertNotIn("test-openai-key", self.path.read_text())

    def test_identical_post_uses_persistent_cache_without_spending(self):
        with patch("openai_verifier.request_with_retry", return_value=self.response) as request:
            first, second = self.verify(), self.verify()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["api_calls"], 0)
        request.assert_called_once()
        self.assertEqual(self.calls(), 1)

    def test_changed_source_or_post_invalidates_cache(self):
        with patch("openai_verifier.request_with_retry", return_value=self.response) as request:
            self.verify()
            self.summary += " Дополнительный текст."
            self.verify()
            self.source["url"] += "?updated=1"
            self.verify()
        self.assertEqual(request.call_count, 3)

    def test_cache_cannot_supply_approval_without_real_evidence(self):
        with patch("openai_verifier.request_with_retry", return_value=self.response):
            self.verify()
        cache_path = pathlib.Path(self.temp.name) / "openai_verification_cache.json"
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.review["claims"][0]["evidence"] = "Completely invented evidence"
        next(iter(cache.values()))["result"]["choices"][0]["message"]["content"] = json.dumps(self.review)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
        with patch("openai_verifier.request_with_retry") as request:
            self.assertEqual(self.verify()["status"], "rejected")
            request.assert_not_called()

    def test_budget_failure_blocks_request_and_no_fallback(self):
        with patch.object(Budget, "persist_before_spend", side_effect=BudgetUnavailable("push failed")), patch(
            "openai_verifier.request_with_retry"
        ) as request, patch("source_verification.request_with_retry") as deepseek:
            self.assertEqual(self.verify()["status"], "deferred")
            request.assert_not_called()
            deepseek.assert_not_called()
        self.assertEqual(self.calls(), 1)  # Reservation remains even when push fails.

    def test_timeout_keeps_reservation_and_does_not_retry_or_fallback(self):
        with patch("openai_verifier.request_with_retry", side_effect=TimeoutError) as request, patch(
            "source_verification.request_with_retry"
        ) as deepseek:
            self.assertEqual(self.verify()["status"], "error")
            request.assert_called_once()
            deepseek.assert_not_called()
        self.assertEqual(self.calls(), 1)

    def test_daily_limit_blocks_uncached_request(self):
        with patch.dict(os.environ, {"OPENAI_MAX_CALLS_PER_DAY": "0"}), patch(
            "openai_verifier.request_with_retry"
        ) as request:
            self.assertEqual(self.verify()["status"], "deferred")
            request.assert_not_called()
        self.assertEqual(self.calls(), 0)

    def test_oversized_unicode_input_is_not_truncated_or_sent(self):
        self.source["text"] = "Д" * 12000
        with patch("openai_verifier.request_with_retry") as request:
            self.assertEqual(self.verify()["status"], "deferred")
            request.assert_not_called()
        self.assertEqual(self.calls(), 0)

    def test_malformed_truncated_refused_and_http_fail_closed(self):
        with patch("openai_verifier.request_with_retry", return_value=self.response):
            self.response.status_code = 429
            self.assertEqual(self.verify()["status"], "error")
            self.response.status_code = 200
            choice = self.response.json.return_value["choices"][0]
            choice["finish_reason"] = "length"
            self.assertEqual(self.verify()["status"], "error")
            choice["finish_reason"] = "stop"
            choice["message"]["refusal"] = "I cannot help"
            self.assertEqual(self.verify()["status"], "error")
            choice["message"]["refusal"] = None
            choice["message"]["content"] = "not JSON"
            self.assertEqual(self.verify()["status"], "error")
        self.assertEqual(self.calls(), 4)

    def test_missing_key_in_explicit_openai_mode_does_not_fall_back(self):
        with patch.dict(os.environ, {"SOURCE_VERIFIER": "openai", "OPENAI_API_KEY": ""}), patch(
            "source_verification.request_with_retry"
        ) as deepseek:
            self.assertEqual(self.verify()["reason"], "Missing OPENAI_API_KEY")
            deepseek.assert_not_called()

    def test_publisher_requires_selected_provider(self):
        with patch("openai_verifier.request_with_retry", return_value=self.response):
            report = self.verify()
        draft = {"source_snapshot": self.source, "source_verification": report,
                 "summary_ru": self.summary, "source_urls": [self.source["url"]],
                 "workflow_state": "approved_by_editor", "editorial_decision": "approved"}
        self.assertTrue(verification.is_verified_draft(draft))
        draft["source_verification"]["provider"] = "deepseek"
        self.assertFalse(verification.is_verified_draft(draft))


if __name__ == "__main__":
    unittest.main()
