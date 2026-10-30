"""Permanent opt-in production profile; offline accounting regression checks."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from openai_budget import Budget, BudgetUnavailable, limits, RESERVATION_MICROUSD


class PermanentBudgetProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'openai_budget.json'
        self.path.write_text('{"version":1,"months":{}}')
        env = patch.dict(os.environ, {'GITHUB_ACTIONS': 'false',
            'OPENAI_MAX_CALLS_PER_DAY': '8', 'OPENAI_CALL_LIMIT_PROFILE': 'steady-12',
            'OPENAI_MONTHLY_BUDGET_USD': '3', 'OPENAI_BYPASS_DAILY_LIMIT': 'false'}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def budget(self, stamp='2026-11-01T00:00:00+00:00'):
        return Budget(self.temp.name, datetime.fromisoformat(stamp))

    def test_twelve_before_and_after_november_and_year_rollover(self):
        for stamp in ('2026-10-30T12:00:00+00:00', '2026-10-31T23:59:59+00:00',
                      '2026-11-01T00:00:00+00:00', '2027-01-01T00:00:00+00:00'):
            self.assertEqual(limits(datetime.fromisoformat(stamp)), (3000000, 12))

    def test_lower_limits_stay_lower_and_standard_stays_eight(self):
        for daily in range(8):
            with patch.dict(os.environ, {'OPENAI_MAX_CALLS_PER_DAY': str(daily)}):
                self.assertEqual(limits()[1], daily)
        with patch.dict(os.environ, {'OPENAI_CALL_LIMIT_PROFILE': 'standard'}):
            self.assertEqual(limits()[1], 8)

    def test_six_verified_pairs_then_thirteenth_blocked_no_ledger_reset(self):
        for _ in range(6):
            self.budget().reserve('generation')
            self.budget().reserve('verification')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(BudgetUnavailable, 'Daily'):
            self.budget().reserve()
        self.assertEqual(before, self.path.read_bytes())
        old = self.budget().read()['months']['2026-11']
        self.budget('2026-12-01T00:00:00+00:00').reserve()
        self.assertEqual(self.budget().read()['months']['2026-11'], old)

    def test_generation_cannot_consume_last_verification_slot(self):
        for _ in range(11):
            self.budget().reserve()
        with self.assertRaisesRegex(BudgetUnavailable, 'Daily'):
            self.budget().reserve('generation')
        self.budget().reserve('verification')

    def test_monthly_guard_and_historical_reservations_remain_strict(self):
        history = {'calls': 280, 'reserved_microusd': 280 * RESERVATION_MICROUSD,
                   'input_tokens': 0, 'output_tokens': 0, 'estimated_microusd': 0}
        self.path.write_text(json.dumps({'version': 1, 'months': {'2026-11': {
            'reserved_microusd': history['reserved_microusd'], 'days': {'2026-11-01': history}}}}))
        for _ in range(4):
            self.budget('2026-11-02T00:00:00+00:00').reserve()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(BudgetUnavailable, 'Monthly'):
            self.budget('2026-11-02T00:00:00+00:00').reserve()
        self.assertEqual(before, self.path.read_bytes())
        saved = self.budget().read()['months']['2026-11']
        self.assertEqual(saved['days']['2026-11-01'], history)
        self.assertEqual(saved['reserved_microusd'], 2999040)

    def test_invalid_budget_and_unknown_profile_fail_closed(self):
        for setting in ({'OPENAI_MONTHLY_BUDGET_USD': '3.01'},
                        {'OPENAI_CALL_LIMIT_PROFILE': 'steady-16'},
                        {'OPENAI_MAX_CALLS_PER_DAY': '16'}):
            with patch.dict(os.environ, setting), self.assertRaises(BudgetUnavailable):
                self.budget().reserve()
        self.assertEqual(json.loads(self.path.read_text())['months'], {})
