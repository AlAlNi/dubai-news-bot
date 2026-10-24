"""Offline coverage of the bounded October production trial and automatic expiry."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from openai_budget import Budget, BudgetUnavailable, limits, RESERVATION_MICROUSD


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class OctoberBudgetProfileTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'GITHUB_ACTIONS': 'false', 'BOT_ENVIRONMENT': 'production',
            'OPENAI_MONTHLY_BUDGET_USD': '3', 'OPENAI_MAX_CALLS_PER_DAY': '8',
            'OPENAI_CALL_LIMIT_PROFILE': 'october-2026',
            'OPENAI_BYPASS_DAILY_LIMIT': 'false',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'openai_budget.json'
        self.path.write_text('{"version":1,"months":{}}')

    def budget(self, stamp='2026-10-24T12:00:00'):
        return Budget(self.temp.name, utc(stamp))

    def test_exact_start_and_expiry_boundaries(self):
        for stamp, expected in [
            ('2026-10-23T23:59:59.999999', 8),
            ('2026-10-24T00:00:00', 12),
            ('2026-10-31T23:59:59.999999', 12),
            ('2026-11-01T00:00:00', 8),
            ('2027-10-24T12:00:00', 8),
        ]:
            with self.subTest(stamp=stamp):
                self.assertEqual(limits(utc(stamp)), (3000000, expected))

    def test_non_utc_offset_uses_utc_window(self):
        dubai = timezone(timedelta(hours=4))
        self.assertEqual(limits(datetime(2026, 11, 1, 3, 59, 59, tzinfo=dubai))[1], 12)
        self.assertEqual(limits(datetime(2026, 11, 1, 4, 0, tzinfo=dubai))[1], 8)
        self.assertEqual(limits(datetime(2026, 10, 24, 3, 59, 59, tzinfo=dubai))[1], 8)

    def test_normal_profile_and_missing_profile_stay_eight(self):
        with patch.dict(os.environ, {'OPENAI_CALL_LIMIT_PROFILE': 'standard'}):
            self.assertEqual(limits(utc('2026-10-24T12:00:00'))[1], 8)
        with patch.dict(os.environ):
            del os.environ['OPENAI_CALL_LIMIT_PROFILE']
            self.assertEqual(limits(utc('2026-10-24T12:00:00'))[1], 8)

    def test_lower_limits_including_zero_are_preserved(self):
        for daily in range(8):
            with self.subTest(daily=daily), patch.dict(os.environ, {'OPENAI_MAX_CALLS_PER_DAY': str(daily)}):
                self.assertEqual(limits(utc('2026-10-24T12:00:00'))[1], daily)

    def test_unknown_profile_and_invalid_numeric_limits_fail_closed(self):
        for setting in [{'OPENAI_CALL_LIMIT_PROFILE': 'october-2027'},
                        {'OPENAI_MAX_CALLS_PER_DAY': '12'},
                        {'OPENAI_MONTHLY_BUDGET_USD': '3.01'},
                        {'OPENAI_MONTHLY_BUDGET_USD': 'NaN'}]:
            with self.subTest(setting=setting), patch.dict(os.environ, setting):
                with self.assertRaises(BudgetUnavailable):
                    self.budget().reserve()
                self.assertEqual(json.loads(self.path.read_text())['months'], {})
        with self.assertRaises(BudgetUnavailable):
            limits(datetime(2026, 10, 24))

    def test_six_pairs_are_allowed_and_thirteenth_call_is_blocked(self):
        for _ in range(6):
            self.budget().reserve('generation')
            self.budget().reserve('verification')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(BudgetUnavailable, 'Daily'):
            self.budget().reserve()
        self.assertEqual(self.path.read_bytes(), before)
        month = self.budget().read()['months']['2026-10']
        self.assertEqual(month['reserved_microusd'], 12 * RESERVATION_MICROUSD)
        self.assertEqual(month['days']['2026-10-24']['calls'], 12)

    def test_generation_needs_two_remaining_slots(self):
        for _ in range(11):
            self.budget().reserve()
        with self.assertRaisesRegex(BudgetUnavailable, 'Daily'):
            self.budget().reserve('generation')
        self.budget().reserve('verification')

    def test_expired_profile_really_blocks_ninth_call_and_preserves_history(self):
        for _ in range(12):
            self.budget('2026-10-31T23:59:59').reserve()
        october = self.budget().read()['months']['2026-10']
        for _ in range(8):
            self.budget('2026-11-01T00:00:00').reserve()
        with self.assertRaisesRegex(BudgetUnavailable, 'Daily'):
            self.budget('2026-11-01T00:00:00').reserve()
        self.assertEqual(self.budget().read()['months']['2026-10'], october)

    def test_monthly_hard_stop_still_wins_over_twelve_call_profile(self):
        # Valid existing v1 history, 280 calls reserved earlier in the month.
        days = {f'2026-10-{day:02d}': {
            'calls': 14, 'reserved_microusd': 14 * RESERVATION_MICROUSD,
            'input_tokens': 0, 'output_tokens': 0, 'estimated_microusd': 0,
        } for day in range(1, 21)}
        old = {'reserved_microusd': 280 * RESERVATION_MICROUSD, 'days': days}
        self.path.write_text(json.dumps({'version': 1, 'months': {'2026-10': old}}))
        for _ in range(4):
            self.budget().reserve()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(BudgetUnavailable, 'Monthly'):
            self.budget().reserve()
        self.assertEqual(self.path.read_bytes(), before)
        saved = self.budget().read()['months']['2026-10']
        self.assertEqual(saved['reserved_microusd'], 2999040)
        for day, original in days.items():
            self.assertEqual(saved['days'][day], original)

    def test_production_workflows_explicitly_select_profile_not_repository_override(self):
        for name in ['collect_news.yml', 'collect_priority_media.yml']:
            content = (ROOT / '.github' / 'workflows' / name).read_text()
            self.assertIn("OPENAI_MAX_CALLS_PER_DAY: '8'", content)
            self.assertIn("OPENAI_CALL_LIMIT_PROFILE: 'october-2026'", content)
            self.assertNotIn('vars.OPENAI_MAX_CALLS_PER_DAY', content)
            self.assertIn("OPENAI_MONTHLY_BUDGET_USD: ${{ vars.OPENAI_MONTHLY_BUDGET_USD || '3' }}", content)
            self.assertIn('SOURCE_VERIFIER: openai', content)


if __name__ == '__main__':
    unittest.main()
