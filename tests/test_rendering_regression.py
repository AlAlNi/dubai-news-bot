import re
import sys
from pathlib import Path
from unittest import TestCase
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from post_style import format_summary, plain_news_summary, clean_editorial_text

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
