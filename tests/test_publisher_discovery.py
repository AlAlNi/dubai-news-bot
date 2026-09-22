import pathlib
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from publisher_discovery import SectionLinks, section_urls, latest_articles, SECTIONS
from openai_news_search import publisher_fallback

NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
URL = 'https://gulfnews.com/uae/dubai/new-service-1.12345'


class PublisherTests(unittest.TestCase):
    def test_links_are_same_publisher_article_paths_and_deduplicated(self):
        parser = SectionLinks(SECTIONS[0])
        parser.feed(''.join(f'<a href="{u}">news</a>' for u in [
            URL, URL + '?tracking=1', '/uae/dubai', 'https://evil.example/dubai-1.22',
            'https://gulfnews.com.evil.example/dubai-1.22', '/uae/abu-dhabi-1.55',
            'http://gulfnews.com/uae/dubai/service-1.555', '/uae/dubai/other-1.12346']))
        self.assertEqual(parser.urls, [URL, 'https://gulfnews.com/uae/dubai/other-1.12346'])

    def test_section_fetch_has_size_limit_and_closes_on_failure(self):
        response = Mock(status_code=200, headers={'Content-Type': 'text/html'})
        response.iter_content.return_value = [b'x' * 3_000_001]
        diagnostics = []
        with patch('publisher_discovery.request_with_retry', return_value=response) as request:
            self.assertEqual(section_urls(SECTIONS[0], diagnostics), [])
        self.assertEqual(diagnostics[0]['reason'], 'section_too_large')
        self.assertFalse(request.call_args.kwargs['allow_redirects'])
        response.close.assert_called_once()

    def test_old_or_unreadable_articles_stay_rejected_and_fetches_are_bounded(self):
        urls = [URL + str(i) for i in range(20)]
        with patch('publisher_discovery.section_urls', return_value=urls), patch(
            'publisher_discovery.fetch_article', return_value=None
        ) as fetch:
            result = latest_articles([urls[0]], NOW)
        self.assertEqual(result['items'], [])
        self.assertEqual(fetch.call_count, 8)
        self.assertNotIn(urls[0], [c.args[0] for c in fetch.call_args_list])
        self.assertTrue(all(c.args[1] == NOW for c in fetch.call_args_list))

    def test_fallback_preserves_search_cost_and_requires_full_article(self):
        article = {'link': URL, 'title': 'New Dubai service', 'description': 'Full original text'}
        with patch('publisher_discovery.section_urls', return_value=[URL]), patch(
            'publisher_discovery.fetch_article', return_value=article
        ):
            result = publisher_fallback({'status': 'ok', 'items': [], 'api_calls': 2,
                                         'cached': True}, [], NOW)
        self.assertEqual(result['items'][0]['method'], 'publisher_latest')
        self.assertEqual(result['api_calls'], 2)
        self.assertTrue(result['publisher_fallback'])
        self.assertEqual(result['publisher_articles_checked'], 1)

    def test_api_errors_and_budget_pause_do_not_trigger_fallback(self):
        with patch('openai_news_search.latest_articles') as fetch:
            for status in ('disabled', 'error', 'deferred'):
                publisher_fallback({'status': status, 'items': []}, [], NOW)
            publisher_fallback({'status': 'ok', 'items': [{'link': URL}]}, [], NOW)
        fetch.assert_not_called()
