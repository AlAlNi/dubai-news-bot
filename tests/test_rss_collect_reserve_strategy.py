import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import rss_collect  # noqa: E402


class ReserveStrategyTests(unittest.TestCase):
    def test_try_reserve_prefers_gnews_then_forces_low_priority(self) -> None:
        gnews_item = {"method": "gnews", "source_priority": 1}

        with patch("rss_collect.try_gnews_search_with_rotation", return_value=gnews_item), patch(
            "rss_collect.try_google_news_rss_reserve", return_value=None
        ) as google_mock:
            result = rss_collect.try_reserve_aggregators([], set(), set())

        self.assertEqual(result["method"], "gnews")
        self.assertEqual(result["source_priority"], 3)
        google_mock.assert_not_called()

    def test_try_reserve_falls_back_to_google_news_rss(self) -> None:
        google_item = {"method": "google_news_rss", "source_priority": 3}

        with patch("rss_collect.try_gnews_search_with_rotation", return_value=None), patch(
            "rss_collect.try_google_news_rss_reserve", return_value=google_item
        ) as google_mock:
            result = rss_collect.try_reserve_aggregators([], set(), set())

        self.assertEqual(result, google_item)
        google_mock.assert_called_once()

    def test_process_google_news_rss_entry_marks_reserve_method(self) -> None:
        entry = {
            "title": "Dubai Metro expands routes",
            "link": "https://example.com/news/dubai-metro",
            "summary": "RTA Dubai announced metro route expansion for commuters.",
            "source": {"title": "Example News"},
        }

        result = rss_collect.process_google_news_rss_entry(entry)

        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "google_news_rss")
        self.assertEqual(result["source_priority"], 3)


if __name__ == "__main__":
    unittest.main()
