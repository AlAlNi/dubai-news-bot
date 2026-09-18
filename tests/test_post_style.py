import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from post_style import format_summary, headline_only


class PostStyleTests(unittest.TestCase):
    def test_markdown_heading_becomes_telegram_html(self):
        self.assertEqual(format_summary('**Новые рейсы**\nАвиакомпания объявила рейсы.'),
                         '<b>Новые рейсы</b>\n\nАвиакомпания объявила рейсы.')

    def test_valid_post_and_facts_are_preserved(self):
        text = '<b>Новые рейсы</b>\n\nС 3 ноября.\n\n• Один рейс в день\n• Два со следующего года'
        self.assertEqual(format_summary(text), text)

    def test_plain_heading_and_html_escaping(self):
        self.assertEqual(format_summary('Рейсы\nA & B'), '<b>Рейсы</b>\n\nA &amp; B')

    def test_unsupported_attributes_and_tags_are_removed(self):
        self.assertEqual(format_summary('<b class="x">Рейсы</b><p>A &amp; B</p>'),
                         '<b>Рейсы</b>\n\nA &amp; B')

    def test_headline_only_cannot_be_mistaken_for_body(self):
        self.assertTrue(headline_only(format_summary('**Только заголовок**')))
        self.assertFalse(headline_only(format_summary('<b>Заголовок</b>\nФакты.')))

    def test_collector_rejects_heading_only_and_enriches_rss(self):
        import rss_collect
        heading = '<b>В Дубае объявили о новых автобусных маршрутах</b>'
        self.assertTrue(rss_collect.is_fallback_summary(rss_collect.ensure_summary_quality(heading, '', '')))
        item = {"title": "Dubai buses", "description": "Short excerpt", "method": "rss",
                "link": "https://www.khaleejtimes.com/uae/dubai-buses"}
        article = {"description": "RTA plans new bus routes in Dubai. " * 20,
                   "source_retrieved_at": "2026-09-18T10:00:00Z", "published_at": "2026-09-18T09:00:00Z"}
        with patch('rss_collect.fetch_article', return_value=article), patch(
            'rss_collect.ENABLE_CHEAP_PREFILTER', False
        ), patch('rss_collect.is_news_allowed_by_deepseek', return_value=(False, 'skip', False)) as editor, patch(
            'rss_collect.mark_news_as_rejected'
        ):
            rss_collect.process_news_item(item)
        self.assertEqual(editor.call_args.args[1], article['description'].strip())
        self.assertEqual(item['description'], 'Short excerpt')
