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
from openai_budget import Budget, BudgetUnavailable, RESERVATION_MICROUSD, SEARCH_RESERVATION_MICROUSD, ASTRA_RESERVATION_MICROUSD
from openai_news_search import discovered_urls, search_news
import rss_collect

NOW = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)
URL = "https://www.khaleejtimes.com/uae/dubai-bus-update"


def search_response(urls=None):
    urls = [URL] if urls is None else urls
    return {"status": "completed", "model": "gpt-4.1-mini-2025-04-14", "usage": {"input_tokens": 500, "output_tokens": 100}, "output": [
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
        self.assertNotIn("filters", kwargs["json"]["tools"][0])
        self.assertNotIn("return_token_budget", kwargs["json"]["tools"][0])
        self.assertEqual(kwargs["json"]["model"], "gpt-4.1-mini-2025-04-14")
        self.assertEqual(kwargs["json"]["temperature"], 0)
        self.assertNotIn("reasoning", kwargs["json"])
        self.assertIn("mediaoffice.ae", kwargs["json"]["instructions"])
        self.assertNotIn("test-key", self.ledger.read_text())

    def test_identical_dubai_day_uses_cache_and_filters_seen_links(self):
        with patch("openai_news_search.request_with_retry", return_value=self.response) as request, patch(
            "openai_news_search.fetch_article", return_value=self.article
        ) as fetch:
            first = search_news(self.temp.name, now=NOW)
            second = search_news(self.temp.name, seen_urls={URL}, now=NOW + timedelta(hours=7))
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
        self.assertEqual(request.call_count, 2)  # Initial and one replacement; both cached.

    def test_replacement_receives_rejections_and_stops_after_success(self):
        replacement_url = "https://www.mediaoffice.ae/en/news/new-dubai-story"
        replacement_response = Mock(status_code=200)
        replacement_response.json.return_value = search_response([replacement_url])
        article = {**self.article, "link": replacement_url}
        def extract(url, now, diagnostics=None):
            if url == URL:
                diagnostics.append({"url": url, "reason": "outside_48h_window"})
                return None
            return article
        with patch("openai_news_search.request_with_retry", side_effect=[self.response, replacement_response]) as request, patch(
            "openai_news_search.fetch_article", side_effect=extract
        ):
            first = search_news(self.temp.name, now=NOW)
            second = search_news(self.temp.name, now=NOW + timedelta(hours=1))
        self.assertEqual(first["items"], [article])
        self.assertEqual(first["api_calls"], 2)
        self.assertEqual(second["items"], [article])
        self.assertEqual(second["api_calls"], 0)
        self.assertEqual(request.call_count, 2)
        feedback = request.call_args_list[1].kwargs["json"]["input"]
        self.assertIn("outside_48h_window", feedback)
        self.assertIn(URL, feedback)

    def test_one_search_setting_prevents_paid_replacement(self):
        self.response.json.return_value = search_response([])
        with patch.dict(os.environ, {"OPENAI_MAX_SEARCHES_PER_DAY": "1"}), patch(
            "openai_news_search.request_with_retry", return_value=self.response
        ) as request:
            result = search_news(self.temp.name, now=NOW)
        self.assertEqual(result["status"], "deferred")
        self.assertIn("Daily", result["reason"])
        request.assert_called_once()

    def test_editorial_rejection_can_use_remaining_replacement(self):
        with patch("openai_news_search.request_with_retry", return_value=self.response) as request, patch(
            "openai_news_search.fetch_article", return_value=self.article
        ):
            search_news(self.temp.name, now=NOW)
            result = search_news(self.temp.name, seen_urls={URL}, now=NOW,
                                editorial_rejections=[{"url": URL, "reason": "editorial_or_quality_rejection"}])
        self.assertTrue(result["replacement_search"])
        self.assertEqual(request.call_count, 2)
        self.assertIn("editorial_or_quality_rejection", request.call_args.kwargs["json"]["input"])

    def test_monthly_headroom_still_blocks_replacement(self):
        self.response.json.return_value = search_response([])
        with patch.dict(os.environ, {"OPENAI_MONTHLY_BUDGET_USD": "0.04"}), patch(
            "openai_news_search.request_with_retry", return_value=self.response
        ) as request:
            result = search_news(self.temp.name, now=NOW)
        self.assertEqual(result["status"], "deferred")
        self.assertIn("Monthly", result["reason"])
        request.assert_called_once()

    def test_failed_replacement_is_not_repeated_hourly(self):
        self.response.json.return_value = search_response([])
        with patch("openai_news_search.request_with_retry", side_effect=[self.response, TimeoutError]) as request:
            first = search_news(self.temp.name, now=NOW)
            second = search_news(self.temp.name, now=NOW + timedelta(hours=1))
        self.assertEqual(first["status"], "error")
        self.assertEqual(second["status"], "error")
        self.assertEqual(second["api_calls"], 0)
        self.assertEqual(request.call_count, 2)

    def test_new_dubai_day_searches_again_and_revalidates_articles(self):
        with patch("openai_news_search.request_with_retry", return_value=self.response) as request, patch(
            "openai_news_search.fetch_article", return_value=self.article
        ) as fetch:
            search_news(self.temp.name, now=NOW)
            result = search_news(self.temp.name, now=NOW + timedelta(days=1))
        self.assertFalse(result["cached"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(fetch.call_count, 2)

    def test_searches_and_verification_share_existing_ledger_without_reset(self):
        Budget(self.temp.name, NOW).reserve()
        Budget(self.temp.name, NOW).reserve(kind="search")  # Legacy mini discovery.
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
                               ({"OPENAI_API_KEY": ""}, "error")]:
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

    def test_blocked_publisher_listing_and_single_domain_do_not_fill_batch(self):
        urls = ["https://gulfbusiness.com/en/news/story", "https://www.thenationalnews.com/tags/transport/",
                "https://rta.ae/report.pdf", URL, URL + "-2", URL + "-3",
                "https://mediaoffice.ae/en/news/dubai-story", "https://gulfnews.com/uae/dubai-story"]
        self.assertEqual(discovered_urls(search_response(urls)), [URL, URL + "-2", urls[-2], urls[-1]])

    def test_mini_can_search_with_existing_astra_spend_without_reset(self):
        prior = Budget(self.temp.name, NOW - timedelta(days=1))
        reservation = prior.reserve("astra_search")
        prior.settle_astra(reservation, {"model": "gpt-6-astra", "status": "completed",
                           "usage": {"input_tokens": 80000, "output_tokens": 1000}})
        before = json.loads(self.ledger.read_text())["months"]["2026-09"]["reserved_microusd"]
        self.assertGreater(before, 1000000)  # Less than $2 remains: Astra cannot reserve again.
        with patch("openai_news_search.request_with_retry", return_value=self.response), patch(
            "openai_news_search.fetch_article", return_value=self.article
        ):
            self.assertEqual(search_news(self.temp.name, now=NOW)["items"], [self.article])
        after = Budget(self.temp.name, NOW).read()["months"]["2026-09"]
        self.assertEqual(after["reserved_microusd"], before + SEARCH_RESERVATION_MICROUSD)
        self.assertIn(reservation, after["days"]["2026-09-14"]["astra_requests"])


class DiscoveryIntegrationTests(unittest.TestCase):
    def run_collector(self, rss_item=None, ready_drafts=None, reject_first=False):
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
            if reject_first:
                stack.enter_context(patch("rss_collect.process_news_item", side_effect=[None, draft]))
                replacement = {**article, "title": "Dubai announces new metro service", "link": URL + "-replacement"}
                search.side_effect = [
                    {"status": "ok", "items": [article], "api_calls": 1, "cached": False},
                    {"status": "ok", "items": [replacement], "api_calls": 1, "cached": True,
                     "replacement_search": True},
                ]
            rss = stack.enter_context(patch("rss_collect.try_rss_feeds", side_effect=AssertionError("RSS must not run")))
            reserve = stack.enter_context(patch("rss_collect.try_reserve_aggregators", side_effect=AssertionError("GNews must not run")))
            result = json.loads(rss_collect.handler({}, None)["body"])
            rss.assert_not_called()
            reserve.assert_not_called()
            return result, search.call_count

    def test_mini_is_primary_and_result_uses_normal_processing(self):
        result, count = self.run_collector()
        self.assertTrue(result["new_draft"])
        self.assertEqual(count, 1)
        self.assertEqual(result["openai_search_calls"], 1)

    def test_even_available_rss_does_not_replace_mini(self):
        result, count = self.run_collector(rss_item={"title": "RSS news"})
        self.assertTrue(result["new_draft"])
        self.assertEqual(count, 1)

    def test_two_ready_drafts_do_not_trigger_paid_search(self):
        result, count = self.run_collector(ready_drafts=[{}, {}])
        self.assertFalse(result["new_draft"])
        self.assertEqual(count, 0)

    def test_editorial_rejection_gets_one_more_batch_and_combined_cost_counts(self):
        result, count = self.run_collector(reject_first=True)
        self.assertTrue(result["new_draft"])
        self.assertEqual(count, 2)
        self.assertEqual(result["openai_search_calls"], 2)


if __name__ == "__main__":
    unittest.main()
