import os
import pathlib
import sys
import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from monthly_discovery import allowed_primary, discover, discovery_payload
from openai_budget import BudgetUnavailable, MODEL

NOW = datetime(2026, 10, 31, 1, tzinfo=timezone.utc)
TARGET = date(2026, 11, 1)
OFFICIAL = "https://www.rta.ae/news/new-rule"


def search_response(urls):
    return {"status": "completed", "usage": {"input_tokens": 500, "output_tokens": 100},
            "output": [{"type": "web_search_call", "status": "completed", "action": {
                "type": "search", "sources": [{"url": url} for url in urls]}},
                {"type": "message", "content": [{"type": "output_text", "text": "Not evidence"}]}]}


class MonthlyDiscoveryTests(unittest.TestCase):
    def test_primary_domain_boundaries(self):
        for url in [OFFICIAL, "https://dewa.gov.ae/a", "https://u.ae/a",
                    "https://mediaoffice.ae/a", "https://www.wam.ae/a"]:
            self.assertTrue(allowed_primary(url), url)
        for url in ["http://rta.ae/a", "https://rta.ae.evil.com/a", "https://evilgov.ae/a",
                    "https://evil.gov.ae.evil.com/a", "https://rta.ae@evil.com/a",
                    "https://user@rta.ae/a", "https://rta.ae:444/a", "https://127.0.0.1/a",
                    "https://khaleejtimes.com/a", "https://rta.ae\\@evil.com/a", None]:
            self.assertFalse(allowed_primary(url), url)

    def test_exact_date_year_and_no_filler_prompt(self):
        payload = discovery_payload(TARGET, NOW)
        self.assertEqual(payload["model"], MODEL)
        self.assertIn("2026-11-01", payload["input"])
        self.assertIn("not the announcement/publication date", payload["instructions"])
        self.assertIn("no results", payload["instructions"])
        self.assertEqual(payload["max_tool_calls"], 1)
        for target, now in [(date(2026, 11, 2), NOW), (TARGET, NOW.replace(tzinfo=None)),
                            (NOW, NOW), ("2026-11-01", NOW)]:
            with self.assertRaises(ValueError):
                discovery_payload(target, now)

    def test_reservation_precedes_single_request_and_primary_filter(self):
        budget = Mock()
        response = Mock(status_code=200)
        response.json.return_value = search_response([OFFICIAL, "https://example.com/news"])
        events = []
        budget.reserve.side_effect = lambda **kwargs: events.append("reserve")
        def request(*args, **kwargs):
            events.append("request")
            self.assertEqual(kwargs["max_attempts"], 1)
            self.assertFalse(kwargs["allow_redirects"])
            return response
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
                "monthly_discovery.Budget", return_value=budget) as cls, patch(
                "monthly_discovery.request_with_retry", side_effect=request) as network:
            self.assertEqual(discover("storage", TARGET, NOW), [OFFICIAL])
        cls.assert_called_once_with("storage", NOW)
        self.assertEqual(events, ["reserve", "request"])
        budget.reserve.assert_called_once_with(kind="search")
        budget.record_usage.assert_called_once_with(
            {"prompt_tokens": 500, "completion_tokens": 100}, search_calls=1)
        network.assert_called_once()
        response.close.assert_called_once()

    def test_budget_failure_prevents_network(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), patch(
                "monthly_discovery.Budget") as cls, patch("monthly_discovery.request_with_retry") as network:
            cls.return_value.reserve.side_effect = BudgetUnavailable("Monthly OpenAI budget reached")
            with self.assertRaises(BudgetUnavailable):
                discover("storage", TARGET, NOW)
            network.assert_not_called()

    def test_errors_are_sanitized_and_never_retried(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}, clear=True), patch(
                "monthly_discovery.Budget"), patch("monthly_discovery.request_with_retry") as network:
            network.side_effect = RuntimeError("test-secret raw error")
            with self.assertRaisesRegex(RuntimeError, "^monthly_search_failed$"):
                discover("storage", TARGET, NOW)
            network.assert_called_once()

    def test_incomplete_or_multiple_search_calls_fail_closed(self):
        for result in [{"status": "incomplete"}, {"status": "completed", "output": []},
                       {"status": "completed", "output": [
                           {"type": "web_search_call", "status": "completed", "action": {"type": "search"}}
                       ] * 2}]:
            with self.subTest(result=result), patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch(
                    "monthly_discovery.Budget"), patch("monthly_discovery.request_with_retry") as request:
                response = request.return_value
                response.status_code = 200
                response.json.return_value = result
                with self.assertRaisesRegex(RuntimeError, "^monthly_search_failed$"):
                    discover("storage", TARGET, NOW)
                response.close.assert_called_once()

    def test_no_results_is_empty_not_placeholder(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch(
                "monthly_discovery.Budget"), patch("monthly_discovery.request_with_retry") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = search_response([])
            self.assertEqual(discover("storage", TARGET, NOW), [])

    def test_missing_key_disabled_search_and_persist_failure_do_not_spend(self):
        for env in [{}, {"OPENAI_API_KEY": "test", "OPENAI_NEWS_SEARCH_ENABLED": "false"}]:
            with patch.dict(os.environ, env, clear=True), patch("monthly_discovery.Budget") as budget, patch(
                    "monthly_discovery.request_with_retry") as network:
                with self.assertRaises(RuntimeError):
                    discover("storage", TARGET, NOW)
                budget.assert_not_called()
                network.assert_not_called()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True), patch(
                "monthly_discovery.Budget") as budget, patch("monthly_discovery.request_with_retry") as network:
            budget.return_value.reserve.side_effect = OSError("secret path")
            with self.assertRaisesRegex(RuntimeError, "^monthly_search_reservation_failed$"):
                discover("storage", TARGET, NOW)
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
