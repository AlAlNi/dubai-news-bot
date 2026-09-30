import re
import sys
from pathlib import Path
from unittest import TestCase
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from post_style import format_summary, contextual_emoji, clean_editorial_text


class RenderingRegressionTests(TestCase):
    def test_emoji_outside_bold_heading_never_bolds_body_on_repeated_formatting(self):
        raw = "🚇 <b>Коммерческий банк Дубая присоединился к CBMT</b>\n\nПервый абзац новости.\n\nВторой абзац новости."
        expected = "<b>🚇 Коммерческий банк Дубая присоединился к CBMT</b>\n\nПервый абзац новости.\n\nВторой абзац новости."
        for _ in range(4):
            raw = format_summary(clean_editorial_text(format_summary(raw)))
            self.assertEqual(raw, expected)

    def test_legacy_nested_wrapper_is_removed_without_changing_words(self):
        raw = "<b>🚇 <b>Заголовок новости</b>\n\nПервый абзац.\n\nВторой абзац.</b>"
        self.assertEqual(format_summary(raw), "<b>🚇 Заголовок новости</b>\n\nПервый абзац.\n\nВторой абзац.")

    def test_whole_paragraph_bold_removed_short_fact_kept(self):
        raw = "<b>Новость</b>\n\n<b>Банк объявил о новом проекте для клиентов в нескольких странах мира.</b>\n\nНачало <b>1 октября</b>."
        result = format_summary(raw)
        self.assertNotIn("<b>Банк", result)
        self.assertIn("<b>1 октября</b>", result)

    def test_finance_uses_body_emoji_and_replaces_metro_heading(self):
        raw = "🚇 <b>Коммерческий банк Дубая присоединился к CBMT</b>\n\nБанк исследует трансграничные расчёты.\n\nCBD будет работать в тестовой среде Sandbox."
        result = contextual_emoji(raw)
        self.assertTrue(result.startswith("<b>🏦 "))
        self.assertIn("\n\n💱 Банк", result)
        self.assertIn("\n\n🧪 CBD", result)
        self.assertNotIn("🚇", result)
        self.assertEqual(contextual_emoji(result), result)

    def test_quote_text_is_never_decorated_even_with_blank_lines(self):
        quote = "<blockquote>«Банк тестирует систему.\n\nПлатежи будут исследованы» — автор.</blockquote>"
        result = contextual_emoji("<b>Банк Дубая</b>\n\n" + quote)
        self.assertIn(quote, result)

    def test_unknown_topic_not_given_invented_emoji(self):
        raw = "<b>Новая инициатива</b>\n\nПодробности станут известны позже."
        self.assertEqual(contextual_emoji(raw), raw)
