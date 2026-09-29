import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from news_discovery import search_news

URL = "https://gulfnews.com/uae/test-article"
NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


class DigestImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.json"

    def save(self, data):
        self.config.write_text(json.dumps(data))

    def run_digest(self, **kwargs):
        return search_news(self.tmp.name, now=NOW, config=self.config, **kwargs)

    def test_digest_uses_publisher_not_digest_prose_and_never_paid_search(self):
        self.save({"mode": "digest", "urls": [URL], "summary": "Invented digest text"})
        article = {"title": "Publisher title", "description": "Publisher text", "link": URL,
                   "published_at": NOW.isoformat(), "source_retrieved_at": NOW.isoformat()}
        with patch("news_discovery.fetch_article", return_value=article) as fetch, patch("openai_news_search.search_news") as paid, patch.dict(os.environ, {"OPENAI_REFRESH_SEARCH": "true"}):
            report = self.run_digest()
            self.assertEqual(report["api_calls"], 0)
            self.assertEqual(report["items"][0]["description"], "Publisher text")
            self.assertEqual(report["items"][0]["method"], "digest_import")
            self.assertEqual(fetch.call_args.args[1], NOW)
            paid.assert_not_called()

    def test_empty_or_failed_extraction_has_no_paid_fallback(self):
        for urls in [[], [URL]]:
            self.save({"mode": "digest", "urls": urls})
            with patch("news_discovery.fetch_article", return_value=None), patch("openai_news_search.search_news") as paid:
                result = self.run_digest()
                self.assertEqual(result["items"], [])
                self.assertEqual(result["status"], "ok")
                paid.assert_not_called()

    def test_duplicates_and_unapproved_hosts_not_fetched(self):
        self.save({"mode": "digest", "urls": [URL, URL + "?utm_source=chatgpt", "https://t.me/channel", "https://www.reuters.com/world/test"]})
        with patch("news_discovery.fetch_article") as fetch:
            result = self.run_digest(seen_urls=[URL])
            fetch.assert_not_called()
            self.assertEqual(len(result["source_rejections"]), 4)

    def test_invalid_config_cannot_buy_search(self):
        for value in [{"mode": "typo"}, {"mode": "digest", "urls": [4]},
                      {"mode": "digest", "urls": [URL] * 21}]:
            self.save(value)
            with patch("openai_news_search.search_news") as paid:
                self.assertEqual(self.run_digest()["status"], "error")
                paid.assert_not_called()

    def test_absent_config_preserves_explicit_legacy_route(self):
        with patch("openai_news_search.search_news", return_value={"status": "ok"}) as paid:
            self.assertEqual(self.run_digest(), {"status": "ok"})
            paid.assert_called_once()

    def test_editorially_rejected_urls_not_reprocessed(self):
        self.save({"mode": "digest", "urls": [URL]})
        with patch("news_discovery.fetch_article") as fetch:
            self.run_digest(editorial_rejections=[{"url": URL, "reason": "rejected"}])
            fetch.assert_not_called()
