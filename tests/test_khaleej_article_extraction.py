"""Synthetic fixture mirrors public Khaleej Times markup observed 2026-10-24."""
import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from search_sources import parse_article

URL = 'https://www.khaleejtimes.com/uae/dubai-bus-update'
NOW = datetime(2026, 10, 24, 3, tzinfo=timezone.utc)
BODY = ('Dubai officials announced new bus services connecting residential areas with metro stations. '
        'Timetables will be published before the routes begin operating. The announcement did not '
        'specify a launch date or ticket prices. Further details will follow separately.')


def page(body=BODY, date='2026-10-23T09:00:00+04:00', reference=URL):
    node = {'@type': 'NewsArticle', 'mainEntityOfPage': reference,
            'headline': 'Dubai officials announce new bus services', 'datePublished': date}
    # No articleBody in publisher metadata. Text is server-rendered, not hidden data.
    return ('<script type="application/ld+json">' + json.dumps(node) + '</script>'
            '<div class="article-top"><h1>Page headline</h1>'
            '<div class="col-12 details article-center-wrap-nf asif check">'
            '<div class="inner innermixmatch"><p><p class="dropcap">' + body + '</p></p>'
            '<div class="rendered_board_widget article_mid_netcore"><h3>Recommended For You</h3>'
            '<p>Unrelated headline must never become article evidence.</p></div>'
            '<div class="recommend-wrap-mobile-lb">Other recommendation</div>'
            '<p><strong><a class="whatsapp-style">Follow our channel</a></strong></p>'
            '<div class="follow-wrap"><p>Written by Author and author biography</p></div>'
            '<div class="advertisement">Buy now</div><div class="related">Related news</div>'
            '</div></div><p>Outside article text</p></div>')


class KhaleejExtractionTests(unittest.TestCase):
    def test_visible_body_without_jsonld_articlebody(self):
        article = parse_article(page(), URL, NOW)
        self.assertIsNotNone(article)
        self.assertEqual(article['description'], BODY)
        self.assertEqual(article['published_at'], '2026-10-23T05:00:00+00:00')

    def test_short_teaser_not_padded_by_recommendations_or_author(self):
        diagnostics = []
        self.assertIsNone(parse_article(page(body='Short teaser.'), URL, NOW, diagnostics))
        self.assertEqual(diagnostics[-1]['reason'], 'article_text_too_short')

    def test_existing_date_and_identity_gates_still_apply(self):
        for html, reason in [(page(date='2026-10-20T09:00:00+04:00'), 'outside_48h_window'),
                             (page(reference=URL + '-different'), 'article_url_mismatch')]:
            diagnostics = []
            self.assertIsNone(parse_article(html, URL, NOW, diagnostics))
            self.assertEqual(diagnostics[-1]['reason'], reason)

    def test_unrelated_container_does_not_supply_body(self):
        html = page().replace('article-center-wrap-nf', 'unrelated-layout')
        self.assertIsNone(parse_article(html, URL, NOW))


if __name__ == '__main__':
    unittest.main()
