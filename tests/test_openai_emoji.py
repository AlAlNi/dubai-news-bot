import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from openai_emoji import apply_decorations, decorate_summary, EmojiResponseError
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

    def response(self, value=VALUE, text=TEXT):
        import copy, re
        from html import unescape
        value = copy.deepcopy(value)
        for item in value.get("decorations", []):
            if isinstance(item, dict):
                index = item.get("paragraph")
                item["subject"] = unescape(re.sub(r"<[^>]*>", "", text.split("\n\n")[index]))[:30] if type(index) is int and 0 <= index < len(text.split("\n\n")) else "subject"
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
        values = [{'decorations': [], 'post': 'changed'}, {'decorations': [{'paragraph': True, 'emoji': '✈️'}]}, {'decorations': [{'paragraph': 99, 'emoji': '✈️'}]}, {'decorations': [{'paragraph': 0, 'emoji': 'evil'}]}]
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

    def test_subject_abstention_counts_no_insertions_and_is_cached(self):
        value = {'decorations': [{'paragraph': 0, 'subject': 'Emirates', 'emoji': None}]}
        with patch('openai_emoji.request_with_retry', return_value=self.response(value)) as request:
            first = decorate_summary(TEXT, self.temp.name)
            second = decorate_summary(TEXT, self.temp.name)
        request.assert_called_once()
        self.assertEqual(first['post'], TEXT)
        self.assertEqual(first['status'], 'decorated')
        self.assertEqual(first['decoration_count'], 0)
        self.assertEqual(first['omitted_duplicate_emoji'], 0)
        self.assertEqual(first['subject_selections'][0]['emoji'], None)
        self.assertEqual(second['post'], TEXT)
        self.assertEqual(second['api_calls'], 0)

    def test_api_requires_subject_even_when_legacy_placement_is_valid(self):
        response = self.response()
        response.json.return_value['choices'][0]['message']['content'] = json.dumps(VALUE)
        with patch('openai_emoji.request_with_retry', return_value=response):
            result = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(result['post'], TEXT)
        self.assertEqual(result['reason_code'], 'invalid_decoration_fields')

    def test_network_failure_keeps_original_and_does_not_retry(self):
        with patch('openai_emoji.request_with_retry', side_effect=TimeoutError('offline')) as request:
            first = decorate_summary(TEXT, self.temp.name)
            second = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(first['post'], TEXT)
        self.assertEqual(first['status'], 'fallback')
        self.assertEqual(first['reason_code'], 'request_error')
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
        self.assertEqual(result['reason_code'], 'response_close_error')

    def test_crash_pending_cache_prevents_retry(self):
        with patch('openai_emoji.request_with_retry', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            decorate_summary(TEXT, self.temp.name)
        with patch('openai_emoji.request_with_retry') as request:
            result = decorate_summary(TEXT, self.temp.name)
        request.assert_not_called()
        self.assertTrue(result['cached'])
        self.assertEqual(result['post'], TEXT)
        self.assertEqual(result['reason_code'], 'previous_attempt_incomplete')

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

    def test_specific_rejection_codes_without_weakening_validation(self):
        examples = [
            ({'decorations': [], 'post': 'injection'}, 'unexpected_response_fields'),
            ({'decorations': 'invalid'}, 'invalid_decoration_count'),
            ({'decorations': [{'paragraph': 0, 'emoji': '✈️', 'text': 'injection'}]}, 'invalid_decoration_fields'),
            ({'decorations': [{'paragraph': True, 'emoji': '✈️'}]}, 'invalid_paragraph_id'),
            ({'decorations': [{'paragraph': 99, 'emoji': '✈️'}]}, 'invalid_paragraph_id'),
            ({'decorations': [{'paragraph': 0, 'emoji': 'not-allowed'}]}, 'unsupported_emoji'),
            ({'decorations': [{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': 0, 'emoji': '📅'}]}, 'duplicate_paragraph'),
        ]
        for value, code in examples:
            with self.subTest(code=code), self.assertRaises(EmojiResponseError) as raised:
                apply_decorations(TEXT, value)
            self.assertEqual(raised.exception.code, code)

    def test_rejected_decisions_retained_and_cache_preserves_reason(self):
        duplicate = {'decorations': [{'paragraph': 0, 'emoji': '🏦'}, {'paragraph': 0, 'emoji': '🏦'}]}
        with patch('openai_emoji.request_with_retry', return_value=self.response(duplicate)) as request:
            first = decorate_summary(TEXT, self.temp.name)
            second = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(first['post'], TEXT)
        self.assertEqual(first['reason_code'], 'duplicate_paragraph')
        self.assertEqual(second['reason_code'], 'duplicate_paragraph')
        self.assertEqual(first['diagnostics']['http_status'], 200)
        self.assertEqual(first['diagnostics']['finish_reason'], 'stop')
        self.assertEqual(first['diagnostics']['decisions'], duplicate['decorations'])
        self.assertTrue(second['cached'])
        request.assert_called_once()

    def test_completion_failures_have_distinct_safe_codes(self):
        examples = [
            ({}, 'invalid_completion_choices'),
            ({'choices': [{}]}, 'incomplete_completion'),
            ({'choices': [{'finish_reason': 'length'}]}, 'incomplete_completion'),
            ({'choices': [{'finish_reason': 'stop', 'message': None}]}, 'invalid_completion_message'),
            ({'choices': [{'finish_reason': 'stop', 'message': {'refusal': 'fake-test-key'}}]}, 'refused_completion'),
            ({'choices': [{'finish_reason': 'stop', 'message': {'content': None}}]}, 'invalid_completion_content'),
            ({'choices': [{'finish_reason': 'stop', 'message': {'content': 'fake-test-key'}}]}, 'decoration_json_error'),
            ([], 'invalid_provider_response'),
        ]
        for body, code in examples:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as storage:
                Path(storage, 'openai_budget.json').write_text('{"version":1,"months":{}}')
                response = self.response()
                response.json.return_value = body
                with patch('openai_emoji.request_with_retry', return_value=response):
                    result = decorate_summary(TEXT, storage)
                self.assertEqual(result['post'], TEXT)
                self.assertEqual(result['reason_code'], code)
                self.assertNotIn('fake-test-key', json.dumps(result))
                self.assertNotIn('fake-test-key', Path(storage, 'openai_emoji_cache.json').read_text())

    def test_http_error_reason_is_safe(self):
        response = self.response()
        response.status_code = 429
        with patch('openai_emoji.request_with_retry', return_value=response):
            result = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(result['reason_code'], 'http_error')
        self.assertEqual(result['diagnostics']['http_status'], 429)
        response.json.assert_not_called()

    def test_no_raw_model_text_in_invalid_decision_diagnostics(self):
        value = {'decorations': [{'paragraph': 'fake-test-key', 'emoji': 'fake-test-key', 'post': 'fake-test-key'}]}
        with patch('openai_emoji.request_with_retry', return_value=self.response(value)):
            result = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(result['reason_code'], 'invalid_decoration_fields')
        self.assertEqual(result['diagnostics']['decisions'], [{'paragraph': None, 'emoji': None}])
        self.assertNotIn('fake-test-key', json.dumps(result))
        self.assertNotIn('fake-test-key', Path(self.temp.name, 'openai_emoji_cache.json').read_text())

    def test_legacy_fallback_stays_unknown_and_is_not_retried(self):
        with patch('openai_emoji.request_with_retry', side_effect=TimeoutError):
            decorate_summary(TEXT, self.temp.name)
        path = Path(self.temp.name, 'openai_emoji_cache.json')
        cache = json.loads(path.read_text())
        for entry in cache.values():
            entry['result'] = {'status': 'fallback'}
        path.write_text(json.dumps(cache))
        with patch('openai_emoji.request_with_retry') as request:
            result = decorate_summary(TEXT, self.temp.name)
        request.assert_not_called()
        self.assertEqual(result['reason_code'], 'legacy_cached_fallback_unknown')

    def test_full_success_retains_count_and_safe_choices(self):
        with patch('openai_emoji.request_with_retry', return_value=self.response()):
            result = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(result['decoration_count'], 2)
        self.assertEqual(result['diagnostics']['decisions'], VALUE['decorations'])
        self.assertEqual(result['diagnostics']['phase'], 'complete')

    def test_saved_library_response_keeps_first_calendar_and_culture(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'emoji_library_response.json').read_text(encoding='utf-8'))
        before, response = fixture['writer_text'], fixture['response']
        after = apply_decorations(before, response)
        paragraphs = before.split('\n\n')
        self.assertEqual(after, '<b>📅 ' + paragraphs[0][3:] + '\n\n' + paragraphs[1] + '\n\n🎭 ' + paragraphs[2])
        self.assertEqual(after.replace('📅 ', '', 1).replace('🎭 ', '', 1), before)
        with patch('openai_emoji.request_with_retry', return_value=self.response(response, before)) as request:
            first = decorate_summary(before, self.temp.name)
            second = decorate_summary(before, self.temp.name)
        self.assertEqual(first['status'], 'decorated')
        self.assertEqual(first['post'], after)
        self.assertEqual(first['decoration_count'], 2)
        self.assertEqual(first['omitted_duplicate_emoji'], 1)
        self.assertEqual(first['diagnostics']['decisions'], response['decorations'])
        self.assertEqual(second['post'], after)
        self.assertEqual(second['decoration_count'], 2)
        self.assertEqual(second['omitted_duplicate_emoji'], 1)
        self.assertTrue(second['cached'])
        self.assertEqual(second['api_calls'], 0)
        request.assert_called_once()

    def test_first_occurrence_means_response_order(self):
        choices = {'decorations': [{'paragraph': 2, 'emoji': '📅'}, {'paragraph': 0, 'emoji': '📅'}]}
        parts = TEXT.split('\n\n')
        self.assertEqual(apply_decorations(TEXT, choices), '\n\n'.join([parts[0], parts[1], '📅 ' + parts[2]]))

    def test_all_repeats_keep_just_one_insertion(self):
        choices = {'decorations': [{'paragraph': i, 'emoji': '✈️'} for i in range(3)]}
        self.assertEqual(apply_decorations(TEXT, choices), '<b>✈️ ' + TEXT[3:])

    def test_duplicate_symbols_never_hide_invalid_placements(self):
        invalid = [
            ([{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': 99, 'emoji': '✈️'}], 'invalid_paragraph_id'),
            ([{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': True, 'emoji': '✈️'}], 'invalid_paragraph_id'),
            ([{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': 0, 'emoji': '✈️'}], 'duplicate_paragraph'),
            ([{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': 1, 'emoji': '✈️'}, {'paragraph': 1, 'emoji': '📅'}], 'duplicate_paragraph'),
            ([{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': 1, 'emoji': '✈️', 'post': 'injection'}], 'invalid_decoration_fields'),
            ([{'paragraph': 0, 'emoji': '✈️'}] * 4, 'invalid_decoration_count'),
        ]
        for choices, code in invalid:
            with self.subTest(code=code), self.assertRaises(EmojiResponseError) as raised:
                apply_decorations(TEXT, {'decorations': choices})
            self.assertEqual(raised.exception.code, code)
        with self.assertRaises(EmojiResponseError) as raised:
            apply_decorations('<b>Title</b>\n\n<blockquote>Quote</blockquote>',
                              {'decorations': [{'paragraph': 0, 'emoji': '✈️'}, {'paragraph': 1, 'emoji': '✈️'}]})
        self.assertEqual(raised.exception.code, 'protected_paragraph')

    def test_skipped_duplicates_do_not_change_links_escapes_or_newlines(self):
        before = '<b>A &amp; B</b>\n\nStart &lt; 20.\n\n<a href="https://example.com?q=1&amp;x=2">Source</a>'
        choices = {'decorations': [{'paragraph': 0, 'emoji': '📅'}, {'paragraph': 1, 'emoji': '📅'}]}
        after = apply_decorations(before, choices)
        self.assertEqual(after.replace('📅 ', '', 1), before)

    def test_empty_decisions_remain_valid(self):
        self.assertEqual(apply_decorations(TEXT, {'decorations': []}), TEXT)
        with patch('openai_emoji.request_with_retry', return_value=self.response({'decorations': []})) as request:
            result = decorate_summary(TEXT, self.temp.name)
        self.assertEqual(result['post'], TEXT)
        self.assertEqual(result['status'], 'decorated')
        self.assertEqual(result['decoration_count'], 0)
        # Contract coverage only: this does not test live model judgment.
        payload = request.call_args.kwargs['json']
        self.assertEqual([m['role'] for m in payload['messages']], ['system', 'user'])
        policy = payload['messages'][0]['content']
        self.assertIn('explicit subject or detail in that paragraph', policy)
        self.assertIn('Parking, cars, roads, vehicle validation and Salik do not imply metro', policy)
        self.assertIn('Omit uncertain insertions', policy)
        self.assertIn('There is no target emoji count', policy)
        self.assertEqual(json.loads(payload['messages'][1]['content'])['paragraphs'][0]['text'], TEXT.split('\n\n')[0])
        schema = payload['response_format']['json_schema']['schema']
        self.assertEqual(schema['required'], ['decorations'])
        self.assertFalse(schema['additionalProperties'])
        self.assertNotIn('post', schema['properties'])
