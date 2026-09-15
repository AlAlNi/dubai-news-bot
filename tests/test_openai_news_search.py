import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from openai_budget import Budget, BudgetUnavailable, RESERVATION_MICROUSD, SEARCH_RESERVATION_MICROUSD
from openai_news_search import discovered_urls, search_news
import rss_collect

NOW = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)
URL = "https://www.khaleejtimes.com/uae/dubai-bus-update"


def search_response(urls=None):
    urls = [URL] if urls is None else urls
    return {"status": "completed", "usage": {"input_tokens": 500, "output_tokens": 100}, "output": [
        {"type": "web_search_call", "status": "completed", "action": {
            "type": "search", "sources": [{"type": "url", "url": url} for url in urls]}},
        {"type": "message", "content": [{"type": "output_text", "text": "INVENTED NEWS: 999 free buses",
            "annotations": [{"type": "url_citation", "url": url} for url in urls]}]},
    ]}


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "GITHUB_ACTIONS": "false",
            "OPENAI_NEWS_SEARCH_ENABLED": "true", "OPENAI_MAX_SEARCHES_PER_DAY": "2",
            "OPENAI_MONTHLY_BUDGET_USD": "3", "OPENAI_MAX_CALLS_PER_DAY": "8"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ledger = pathlib.Path(self.temp.name) / "openai_budget.json"
        self.ledger.write_text('{"version":1,"months":{}}', encoding="utf-8")
        self.response = Mock(status_code=200)
        self.response.json.return_value = search_response()
        self.article = {"title": "RTA plans new buses", "description": "Real article text",
            "link": URL, "source": "Khaleej Times", "published_at": NOW.isoformat(),
            "source_retrieved_at": NOW.isoformat(), "method": "openai_web_search"}

    def test_only_one_tool_call_with_limits_and_actual_article_text(self):
        with patch("openai_news_search.request_with_retry", return_value=self.response) as request, patch(
            "openai_news_search.fetch_article", return_value=self.article
        ):
            result = search_news(self.temp.name, now=NOW)
        self.assertEqual(result["items"][0]["description"], "Real article text")
        self.assertNotIn("999", str(result))
        self.assertEqual(result["api_calls"], 1)
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["max_attempts"], 1)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["json"]["max_tool_calls"], 1)
        self.assertEqual(kwargs["json"]["max_output_tokens"], 1000)
        self.assertEqual(kwargs["json"]["tools"][0]["search_context_size"], "low")
        self.assertIn("mediaoffice.ae", kwargs["json"]["tools"][0]["filters"]["allowed_domains"])
        self.assertNotIn("test-key", self.ledger.read_text())

    def test_identical_half_day_uses_cache_and_filters_seen_links(self):
        with patch("openai_news_search.request_with_retry", return_value=self.response) as request, patch(
            "openai_news_search.fetch_article", return_value=self.article
        ) as fetch:
            first = search_news(self.temp.name, now=NOW)
            second = search_news(self.temp.name, seen_urls={URL}, now=NOW + timedelta(hours=1))
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["items"], [])
        request.assert_called_once()
        fetch.assert_called_once()

    def test_empty_results_are_cached(self):
        self.response.json.return_value = search_response([])
        with patch("openai_news_search.request_with_retry", return_value=self.response) as request:
            self.assertEqual(search_news(self.temp.name, now=NOW)["items"], [])
            self.assertTrue(search_news(self.temp.name, now=NOW)["cached"])
        request.assert_called_once()

    def test_searches_and_verification_share_existing_ledger_without_reset(self):
        Budget(self.temp.name, NOW).reserve()  # Previous verifier-only schema.
        with patch("openai_news_search.request_with_retry", return_value=self.response), patch(
            "openai_news_search.fetch_article", return_value=None
        ):
            search_news(self.temp.name, now=NOW)
            search_news(self.temp.name, now=NOW + timedelta(hours=4))
        month = json.loads(self.ledger.read_text())["months"]["2026-09"]
        self.assertEqual(month["reserved_microusd"], RESERVATION_MICROUSD + 2 * SEARCH_RESERVATION_MICROUSD)
        self.assertEqual(month["days"]["2026-09-15"]["calls"], 1)
        self.assertEqual(month["days"]["2026-09-15"]["search_calls"], 2)
        with self.assertRaisesRegex(BudgetUnavailable, "search limit"):
            Budget(self.temp.name, NOW).reserve(kind="search")
        Budget(self.temp.name, NOW).reserve()  # Search cap does not disable verification.

    def test_insufficient_combined_budget_leaves_room_for_verification(self):
        with patch.dict(os.environ, {"OPENAI_MONTHLY_BUDGET_USD": "0.03"}), patch(
            "openai_news_search.request_with_retry"
        ) as request:
            self.assertEqual(search_news(self.temp.name, now=NOW)["status"], "deferred")
            request.assert_not_called()
            Budget(self.temp.name, NOW).reserve()

    def test_timeout_keeps_reservation_and_no_retry(self):
        with patch("openai_news_search.request_with_retry", side_effect=TimeoutError) as request:
            self.assertEqual(search_news(self.temp.name, now=NOW)["status"], "error")
            request.assert_called_once()
        day = json.loads(self.ledger.read_text())["months"]["2026-09"]["days"]["2026-09-15"]
        self.assertEqual(day["search_calls"], 1)
        self.assertEqual(day["reserved_microusd"], SEARCH_RESERVATION_MICROUSD)

    def test_invalid_search_limits_disabled_search_and_absent_key(self):
        for config, status in [({"OPENAI_MAX_SEARCHES_PER_DAY": "3"}, "deferred"),
                               ({"OPENAI_MAX_SEARCHES_PER_DAY": "0"}, "deferred"),
                               ({"OPENAI_NEWS_SEARCH_ENABLED": "false"}, "disabled"),
                               ({"OPENAI_API_KEY": ""}, "disabled")]:
            with self.subTest(config=config), patch.dict(os.environ, config), patch(
                "openai_news_search.request_with_retry"
            ) as request:
                self.assertEqual(search_news(self.temp.name, now=NOW)["status"], status)
                request.assert_not_called()

    def test_incomplete_or_unexecuted_search_cannot_supply_news(self):
        for result in [{"status": "incomplete"}, {"status": "completed", "output": []}]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                discovered_urls(result)
        result = search_response()
        result["output"].insert(0, result["output"][0])
        with self.assertRaises(ValueError):
            discovered_urls(result)

    def test_only_allowlisted_citations_and_sources_used(self):
        self.assertEqual(discovered_urls(search_response([URL, "https://example.com/fake",
            "https://khaleejtimes.com.evil.example/fake", "http://127.0.0.1/"])), [URL])


class ReserveIntegrationTests(unittest.TestCase):
    def run_collector(self, rss_item=None, ready_drafts=None):
        article = {"title": "RTA bus routes in Dubai", "description": "Actual dated source article.", "link": URL}
        draft = {"title": article["title"], "source_urls": [URL], "method": "openai_web_search"}
        with ExitStack() as stack:
            for name, value in {
                "load_existing_drafts": ready_drafts or [], "build_seen_links": (set(), set()),
                "get_active_sla_shortage": None, "try_rss_feeds": rss_item, "try_reserve_aggregators": None,
                "process_news_item": draft, "add_url_to_source_stats": None, "save_drafts": None,
                "append_run_report": None, "compute_newsroom_kpi_snapshot": {},
                "verifier_provider": "openai", "is_verified_draft": True,
            }.items():
                stack.enter_context(patch("rss_collect." + name, return_value=value))
            search = stack.enter_context(patch("rss_collect.search_news", return_value={
                "status": "ok", "items": [article], "api_calls": 1, "cached": False}))
            result = json.loads(rss_collect.handler({}, None)["body"])
            return result, search.call_count

    def test_search_is_last_resort_and_result_uses_normal_processing(self):
        result, count = self.run_collector()
        self.assertTrue(result["new_draft"])
        self.assertEqual(count, 1)
        self.assertEqual(result["openai_search_calls"], 1)

    def test_rss_success_does_not_trigger_search(self):
        result, count = self.run_collector(rss_item={"title": "RSS news"})
        self.assertTrue(result["new_draft"])
        self.assertEqual(count, 0)

    def test_two_ready_drafts_do_not_trigger_paid_search(self):
        result, count = self.run_collector(ready_drafts=[{}, {}])
        self.assertFalse(result["new_draft"])
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
