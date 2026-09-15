import json
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from openai_budget import Budget, BudgetUnavailable, manual_daily_limit_bypass


class ManualBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / "openai_budget.json"
        self.path.write_text('{"version":1,"months":{}}', encoding="utf-8")
        self.env = patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main", "OPENAI_BYPASS_DAILY_LIMIT": "true",
            "OPENAI_MONTHLY_BUDGET_USD": "3", "OPENAI_MAX_CALLS_PER_DAY": "0",
            "OPENAI_MAX_SEARCHES_PER_DAY": "0",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def budget(self):
        return Budget(self.temp.name, datetime(2026, 9, 15, tzinfo=timezone.utc))

    def test_manual_bypass_counts_and_persists_all_spend(self):
        with patch.object(Budget, "persist_before_spend") as persist:
            self.budget().reserve("search")
            self.budget().reserve()
        self.assertEqual(persist.call_count, 2)
        day = self.budget().read()["months"]["2026-09"]["days"]["2026-09-15"]
        self.assertEqual(day["search_calls"], 1)
        self.assertEqual(day["calls"], 1)
        self.assertEqual(day["reserved_microusd"], 35560)

    def test_monthly_limit_and_search_headroom_still_apply(self):
        with patch.dict(os.environ, {"OPENAI_MONTHLY_BUDGET_USD": "0.03"}), patch.object(Budget, "persist_before_spend"):
            with self.assertRaisesRegex(BudgetUnavailable, "Monthly"):
                self.budget().reserve("search")
            self.budget().reserve()
            self.budget().reserve()
            with self.assertRaisesRegex(BudgetUnavailable, "Monthly"):
                self.budget().reserve()

    def test_schedule_other_branches_and_unchecked_do_not_bypass(self):
        for override in [
            {"GITHUB_EVENT_NAME": "schedule"}, {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_REF": "refs/heads/test"}, {"GITHUB_ACTIONS": "false"},
            {"OPENAI_BYPASS_DAILY_LIMIT": "false"}, {"OPENAI_BYPASS_DAILY_LIMIT": ""},
        ]:
            with self.subTest(override=override), patch.dict(os.environ, override):
                self.assertFalse(manual_daily_limit_bypass())
                with self.assertRaisesRegex(BudgetUnavailable, "Daily"):
                    self.budget().reserve()

    def test_manual_spend_consumes_later_scheduled_allowance(self):
        with patch.object(Budget, "persist_before_spend"):
            self.budget().reserve("search")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule", "OPENAI_MAX_CALLS_PER_DAY": "8",
                                    "OPENAI_MAX_SEARCHES_PER_DAY": "1"}):
            with self.assertRaisesRegex(BudgetUnavailable, "Daily OpenAI search"):
                self.budget().reserve("search")

    def test_failed_persistence_still_blocks_manual_request(self):
        with patch.object(Budget, "persist_before_spend", side_effect=BudgetUnavailable("push failed")):
            with self.assertRaisesRegex(BudgetUnavailable, "push failed"):
                self.budget().reserve("search")
        self.assertEqual(self.budget().read()["months"]["2026-09"]["reserved_microusd"], 25000)
