import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from search_sources import allowed_url, fetch_article, parse_article, MAX_PAGE_BYTES

NOW = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)
URL = "https://www.khaleejtimes.com/uae/dubai-bus-update"
BODY = ("RTA announced plans for new bus services in Dubai. The routes will connect residential areas "
        "with metro stations. Officials said timetables would be published separately before services "
        "begin. The announcement did not specify a launch date or ticket prices.")


def article_html(date="2026-09-15T08:00:00Z", **fields):
    node = {"@type": "NewsArticle", "headline": "RTA announces Dubai bus plans",
            "datePublished": date, "articleBody": BODY, "url": URL, **fields}
    return '<script type="application/ld+json">' + json.dumps(node) + '</script>'


class ArticleTests(unittest.TestCase):
    def test_main_entity_url_variant_and_added_publishers(self):
        result = parse_article(article_html(url=None, mainEntityOfPage={'@type': 'WebPage', 'url': URL}), URL, NOW)
        self.assertEqual(result['description'], BODY)
        for host in ['whatson.ae', 'www.euronews.com', 'timesofindia.indiatimes.com']:
            self.assertTrue(allowed_url('https://' + host + '/news'))
        self.assertFalse(allowed_url('https://whatson.ae.evil.example/news'))

    def test_whatson_article_text_excludes_surrounding_page(self):
        html = (article_html(articleBody='') + '<article><p>Menu and unrelated headline</p>'
                '<div class="article-text"><p>' + BODY + '</p><aside>Related story</aside></div>'
                '<div>Sign up for our newsletter</div></article>')
        self.assertEqual(parse_article(html, URL, NOW)['description'], BODY)

    def test_extracts_actual_article_date_and_text(self):
        result = parse_article(article_html(), URL, NOW)
        self.assertEqual(result["description"], BODY)
        self.assertEqual(result["published_at"], "2026-09-15T08:00:00+00:00")
        self.assertEqual(result["method"], "openai_web_search")

    def test_old_missing_invalid_and_future_dates_are_rejected(self):
        for date in [None, "", "nonsense", "2026-08-01", "2026-09-20"]:
            with self.subTest(date=date):
                self.assertIsNone(parse_article(article_html(date), URL, NOW))

    def test_naive_dates_use_dubai_timezone_and_modified_date_is_not_publication(self):
        result = parse_article(article_html("2026-09-15T08:00:00"), URL, NOW)
        self.assertEqual(result["published_at"], "2026-09-15T04:00:00+00:00")
        self.assertIsNone(parse_article(article_html(None, dateModified=NOW.isoformat()), URL, NOW))

    def test_mismatched_article_and_headline_only_snippet_rejected(self):
        self.assertIsNone(parse_article(article_html(url="https://www.khaleejtimes.com/other-news"), URL, NOW))
        self.assertIsNone(parse_article(article_html(articleBody="Read more"), URL, NOW))

    def test_visible_article_fallback_excludes_related_news_and_scripts(self):
        html = ('<meta property="og:type" content="article"><meta property="og:title" content="Dubai transport update">'
                '<meta property="article:published_time" content="2026-09-15T08:00:00Z">'
                '<article><p>' + BODY + '</p><aside>999 buses are free</aside>'
                '<div class="related-news">Fake related headline</div><script>Bad instructions</script></article>')
        result = parse_article(html, URL, NOW)
        self.assertEqual(result["description"], BODY)
        self.assertNotIn("999", result["description"])

    def test_allowlist_blocks_credentials_wrong_scheme_ports_and_lookalikes(self):
        self.assertTrue(allowed_url(URL))
        for url in ["http://www.khaleejtimes.com/news", "https://khaleejtimes.com.evil.com/news",
                    "https://user:pass@khaleejtimes.com/news", "https://khaleejtimes.com:8080/news",
                    "https://127.0.0.1/", "file:///etc/passwd"]:
            with self.subTest(url=url):
                self.assertFalse(allowed_url(url))

    def test_off_domain_redirect_not_followed(self):
        response = Mock(status_code=302, headers={"Location": "https://example.com/redirect"})
        with patch("search_sources.request_with_retry", return_value=response) as request:
            self.assertIsNone(fetch_article(URL, NOW))
            request.assert_called_once()
        response.close.assert_called_once()

    def test_unavailable_and_oversized_pages_skipped(self):
        for status, content in [(403, b"blocked"), (200, b"x" * (MAX_PAGE_BYTES + 1))]:
            response = Mock(status_code=status, headers={"Content-Type": "text/html"}, encoding="utf-8")
            response.iter_content.return_value = [content]
            with self.subTest(status=status), patch("search_sources.request_with_retry", return_value=response):
                self.assertIsNone(fetch_article(URL, NOW))
            response.close.assert_called_once()

    def test_streamed_html_is_parsed_from_publisher(self):
        response = Mock(status_code=200, headers={"Content-Type": "text/html"}, encoding="utf-8")
        response.iter_content.return_value = [article_html().encode("utf-8")]
        with patch("search_sources.request_with_retry", return_value=response):
            self.assertEqual(fetch_article(URL, NOW)["description"], BODY)

    def test_duplicate_article_metadata_with_matching_dates(self):
        html = article_html() + article_html(**{"@type": "Article"})
        self.assertEqual(parse_article(html, URL, NOW)["description"], BODY)
        conflicting = article_html() + article_html("2026-08-01", **{"@type": "Article"})
        self.assertIsNone(parse_article(conflicting, URL, NOW))

    def test_mediaoffice_relative_age_must_confirm_url_date(self):
        url = "https://www.mediaoffice.ae/en/news/2026/september/14-09/dubai-transport"
        html = ('<meta property="og:type" content="website"><h1>Dubai transport announcement</h1>'
                '<p class="banner-i-rel"><i></i>16 hours ago</p><div class="news-detail-left"><p>' + BODY + '</p></div>')
        result = parse_article(html, url, NOW)
        self.assertEqual(result["description"], BODY)
        self.assertEqual(result["published_at"], "2026-09-13T20:00:00+00:00")
        self.assertIsNone(parse_article(html, url.replace("14-09", "01-09"), NOW))
        self.assertIsNone(parse_article(html.replace("16 hours ago", "Yesterday"), url, NOW))


if __name__ == "__main__":
    unittest.main()
