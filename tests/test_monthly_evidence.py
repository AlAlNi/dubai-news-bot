import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone, date
from unittest.mock import Mock, patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import monthly_changes as monthly
from monthly_discovery import allowed_primary
from search_sources import parse_article, fetch_article

NOW = datetime(2026, 10, 31, 13, tzinfo=timezone.utc)
URL = 'https://rta.ae/news/notice'


def page(body, published='2026-06-15T00:00:00Z'):
    return '<script type="application/ld+json">' + json.dumps({
        '@type': 'NewsArticle', 'url': URL, 'headline': 'Confirmed transport changes',
        'datePublished': published, 'articleBody': body}) + '</script>'


class EvidenceTests(unittest.TestCase):
    def test_old_announcement_monthly_only_and_date_not_inferred(self):
        raw = page('A confirmed transport change takes effect on 1 November 2026. ' * 5)
        self.assertIsNone(parse_article(raw, URL, NOW))
        article = parse_article(raw, URL, NOW, max_age_hours=24*366, require_full=True)
        self.assertEqual(article['published_at'], '2026-06-15T00:00:00+00:00')
        self.assertEqual(article['description'], ('A confirmed transport change takes effect on 1 November 2026. ' * 5).strip())
        self.assertIsNone(parse_article(page('A full notice. ' * 30, '2027-01-01'), URL, NOW,
                                        max_age_hours=24*366, require_full=True))

    def test_full_review_never_truncates(self):
        self.assertIsNone(parse_article(page('Material exception. ' * 800), URL, NOW,
                                        max_age_hours=24*366, require_full=True))

    def test_redirect_outside_primary_domains_is_not_fetched(self):
        response = Mock(status_code=302, headers={'Location': 'https://example.com/news'})
        with patch('search_sources.public_destination', return_value=True), \
             patch('search_sources.request_with_retry', return_value=response) as request:
            self.assertIsNone(fetch_article(URL, NOW, url_policy=allowed_primary,
                                            max_age_hours=24*366, require_full=True))
        self.assertEqual(request.call_count, 1)
        response.close.assert_called_once()

    def test_monthly_policy_explicitly_checks_every_claim_and_effective_date(self):
        with patch.object(monthly, 'verify_with_openai', return_value={'status': 'rejected'}) as verifier:
            monthly.review({'text': 'Notice'}, 'Post', pathlib.Path('/tmp'), date(2026, 11, 1))
        prompt = verifier.call_args.args[2]
        for phrase in ('2026-11-01', 'EVERY factual assertion', 'publication date', 'tentative proposal',
                       'year', 'exceptions', 'Never mix notices'):
            self.assertIn(phrase, prompt)
