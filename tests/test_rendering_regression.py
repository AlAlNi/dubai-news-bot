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

    def test_airline_route_does_not_add_bus_emoji(self):
        raw = ("<b>Emirates запустила первый рейс из Дубая в Хельсинки</b>\n\n"
               "Самолёт вмещает 298 пассажиров. Новый маршрут обеспечивает "
               "стыковки через Дубай к более чем 140 направлениям.")
        result = contextual_emoji(raw)
        self.assertTrue(result.startswith("<b>✈️ "))
        self.assertNotIn("🚌", result)
        self.assertTrue(result.endswith(raw.split("\n\n")[1]))
        self.assertEqual(contextual_emoji(result), result)

    def test_cultural_trip_does_not_add_metro_emoji(self):
        raw = ("<b>Dubai Culture завершает визит в Австрию</b>\n\n"
               "В рамках поездки обсуждались проекты культурного сектора "
               "и управление музеями и библиотеками.")
        result = contextual_emoji(raw)
        self.assertNotIn("🚇", result)
        self.assertIn("🎭", result)
        self.assertTrue(result.endswith(raw.split("\n\n")[1]))
        self.assertEqual(contextual_emoji(result), result)

    def test_real_transport_terms_keep_their_emoji(self):
        for word in ("метро", "поезд", "поезда", "поезду", "поездом", "поезде",
                     "поезды", "поездов", "поездам", "поездами", "поездах",
                     "железнодорожный маршрут"):
            with self.subTest(word=word):
                self.assertTrue(contextual_emoji(f"<b>Новый {word}</b>").startswith("<b>🚇 "))
        for word in ("автобус", "автобусы", "автобусный маршрут"):
            with self.subTest(word=word):
                self.assertTrue(contextual_emoji(f"<b>Новый {word}</b>").startswith("<b>🚌 "))

    def test_ambiguous_route_and_trip_remain_undecorated(self):
        for word in ("маршрут", "поездка", "поездки", "поездкой"):
            with self.subTest(word=word):
                raw = f"<b>Новая {word}</b>\n\nПодробности позже."
                self.assertEqual(contextual_emoji(raw), raw)
