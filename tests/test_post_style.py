import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from post_style import format_summary, headline_only, clean_editorial_text, incomplete_excerpt


class PostStyleTests(unittest.TestCase):
    def test_repeats_and_scaffolding_removed_but_attribution_preserved(self):
        text = ('<b>В Дубае открыли музей</b>\n\nВ Дубае открыли музей. '
                'По данным RTA, движение начнётся в ноябре.\n\n'
                'По данным RTA, движение начнётся в ноябре.\n\n'
                'Об этом сообщается в материале под заголовком «Museum opens in Dubai».\n\n'
                'В исходном тексте отмечается, что музей работает до 18:00.')
        result = clean_editorial_text(text)
        self.assertEqual(result.count('В Дубае открыли музей'), 1)
        self.assertEqual(result.count('По данным RTA'), 1)
        self.assertNotIn('Museum opens', result)
        self.assertNotIn('исходном', result)
        self.assertIn('музей работает до 18:00', result)

    def test_different_numbers_and_negation_are_not_deduplicated(self):
        text = '<b>Рейсы</b>\n\nРейс не отменён. Рейс отменён.\n\nБудет 2 рейса. Будет 3 рейса.'
        self.assertEqual(clean_editorial_text(text), text)

    def test_truncated_feeds_are_detected(self):
        for text in ['News ... [3058 chars]', 'News [&#8230;] The post appeared first', 'News…', 'News...']:
            self.assertTrue(incomplete_excerpt(text))
        self.assertFalse(incomplete_excerpt('Рейс запланирован на 3 ноября.'))

    def test_unavailable_full_text_blocks_incomplete_excerpt_before_ai(self):
        import rss_collect
        item = {'title': 'Dubai museum', 'description': 'Dubai museum opens ... [3058 chars]',
                'link': 'https://www.euronews.com/travel/dubai', 'method': 'gnews'}
        with patch('rss_collect.fetch_article', return_value=None), patch(
            'rss_collect.mark_news_as_rejected'
        ) as reject, patch('rss_collect.is_news_allowed_by_deepseek') as editor:
            self.assertIsNone(rss_collect.process_news_item(item))
        reject.assert_called_once()
        editor.assert_not_called()

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
