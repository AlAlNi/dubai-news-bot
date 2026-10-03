import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from openai_emoji import apply_decorations, decorate_summary
from openai_budget import Budget, BudgetUnavailable, MODEL
from post_style import plain_news_summary

TEXT = '<b>Emirates открывает рейс в Хельсинки</b>\n\nНачало 1 октября 2026 года.\n\nSkyCargo перевозит до 16 тонн груза.'
VALUE = {'decorations': [{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': 1, 'emoji': '📅'}]}

class EmojiTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        Path(self.temp.name, 'openai_budget.json').write_text('{"version":1,"months":{}}', encoding='utf-8')
        env = patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-test-key', 'GITHUB_ACTIONS': 'false', 'OPENAI_MONTHLY_BUDGET_USD': '3', 'OPENAI_MAX_CALLS_PER_DAY': '8'})
        env.start()
        self.addCleanup(env.stop)

    def response(self, value=VALUE):
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
        return response

    def test_only_prefix_insertions_exact_words_and_layout(self):
        result = apply_decorations(TEXT, VALUE)
        self.assertEqual(result.replace('✈️ ', '', 1).replace('📅 ', '', 1), TEXT)
        self.assertTrue(result.startswith('<b>✈️ Emirates'))

    def test_plain_writer_has_only_bold_heading(self):
        raw = '<b>✈️ Title</b>\n\n📅 Start <b>1 October</b>.\n\n<i>16 tonnes</i>.'
        expected = '<b>Title</b>\n\nStart 1 October.\n\n16 tonnes.'
        self.assertEqual(plain_news_summary(raw), expected)
        self.assertEqual(plain_news_summary(expected), expected)
        self.assertEqual(plain_news_summary(TEXT), TEXT)

    def test_malicious_or_invalid_placements_rejected(self):
        values = [{'decorations': [], 'post': 'changed'}, {'decorations': [{'paragraph': True, 'emoji': '✈️'}]}, {'decorations': [{'paragraph': 99, 'emoji': '✈️'}]}, {'decorations': [{'paragraph': 0, 'emoji': 'evil'}]}, {'decorations': [{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': 1, 'emoji': '✈️'}]}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                apply_decorations(TEXT, value)

    def test_quotes_links_code_and_italics_protected(self):
        for protected in ('<blockquote>First\n\nSecond</blockquote>', '<a href="https://example.com">A &amp; B</a>', '<code>123</code>', '<i>123</i>'):
            with self.subTest(protected=protected), self.assertRaises(ValueError):
                apply_decorations('<b>Title</b>\n\n' + protected, {'decorations': [{'paragraph': 1, 'emoji': '📅'}]})

    def test_success_cache_avoids_second_call(self):
        with patch('openai_emoji.request_with_retry', return_value=self.response()) as request:
            first = decorate_summary(TEXT, self.temp.name)
            second = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(first['status'], 'decorated')
        self.assertEqual(first['post'], second['post'])
        self.assertTrue(second['cached'])
        self.assertEqual(second['api_calls'], 0)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs['json']['model'], MODEL)
        self.assertEqual(request.call_args.kwargs['max_attempts'], 1)

    def test_network_failure_keeps_original_and_does_not_retry(self):
        with patch('openai_emoji.request_with_retry', side_effect=TimeoutError('offline')) as request:
            first = decorate_summary(TEXT, self.temp.name)
            second = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(first['post'], TEXT)
        self.assertEqual(first['status'], 'fallback')
        self.assertTrue(second['cached'])
        request.assert_called_once()

    def test_invalid_response_falls_back_exactly(self):
        with patch('openai_emoji.request_with_retry', return_value=self.response({'decorations': [], 'text': 'rewrite'})):
            result = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(result['post'], TEXT)
        self.assertEqual(result['status'], 'fallback')

    def test_response_close_failure_also_restores_original(self):
        response = self.response()
        response.close.side_effect = OSError('close failed')
        with patch('openai_emoji.request_with_retry', return_value=response):
            result = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(result['post'], TEXT)
        self.assertEqual(result['status'], 'fallback')

    def test_crash_pending_cache_prevents_retry(self):
        with patch('openai_emoji.request_with_retry', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            decorate_summary(TEXT, self.temp.name)
        with patch('openai_emoji.request_with_retry') as request:
            result = decorate_summary(TEXT, self.temp.name)
        request.assert_not_called()
        self.assertTrue(result['cached'])
        self.assertEqual(result['post'], TEXT)

    def test_html_escapes_and_link_bytes_unchanged(self):
        text = '<b>A &amp; B</b>\n\nAmount &lt; 20.\n\n<a href="https://example.com?q=1&amp;x=2">Source</a>'
        decorated = apply_decorations(text, {'decorations': [{'paragraph': 0, 'emoji': '💰'}]})
        self.assertEqual(decorated.replace('💰 ', '', 1), text)

    def test_length_overflow_rejected(self):
        with self.assertRaises(ValueError):
            apply_decorations('<b>' + 'x' * 900 + '</b>', {'decorations': [{'paragraph': 0, 'emoji': '✈️'}]})

    def test_budget_keeps_last_call_for_verifier(self):
        budget = Budget(self.temp.name)
        for _ in range(7):
            budget.reserve()
        with patch('openai_emoji.request_with_retry') as request:
            result = decorate_summary(TEXT, self.temp.name)
        request.assert_not_called()
        self.assertEqual(result['post'], TEXT)
        budget.reserve()
        with self.assertRaises(BudgetUnavailable):
            budget.reserve()

    def test_corrupt_budget_and_cache_persistence_failure_never_calls_api(self):
        for value in ('broken', '{"version":1,"months":{}}'):
            Path(self.temp.name, 'openai_budget.json').write_text(value)
            with patch('openai_emoji.persist_cache', side_effect=OSError('disk')), patch('openai_emoji.request_with_retry') as request:
                result = decorate_summary(TEXT, self.temp.name)
            request.assert_not_called()
            self.assertEqual(result['post'], TEXT)

    def test_monthly_headroom_preserved(self):
        with patch.dict(os.environ, {'OPENAI_MONTHLY_BUDGET_USD': '0.02'}), patch('openai_emoji.request_with_retry') as request:
            self.assertEqual(decorate_summary(TEXT, self.temp.name)['post'], TEXT)
            Budget(self.temp.name).reserve()
        request.assert_not_called()

    def test_oversize_input_skipped(self):
        with patch('openai_emoji.request_with_retry') as request:
            result = decorate_summary('<b>Title</b>\n\n' + 'x' * 10000, self.temp.name)
        request.assert_not_called()
        self.assertEqual(result['api_calls'], 0)
