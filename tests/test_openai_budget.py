import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from openai_budget import Budget, BudgetUnavailable, RESERVATION_MICROUSD, limits


class OpenAIBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "OPENAI_MONTHLY_BUDGET_USD": "3",
                                          "OPENAI_MAX_CALLS_PER_DAY": "8"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = pathlib.Path(self.temp.name) / "openai_budget.json"
        self.path.write_text('{"version": 1, "months": {}}', encoding="utf-8")

    def budget(self, day=15, month=9):
        return Budget(self.temp.name, datetime(2026, month, day, tzinfo=timezone.utc))

    def test_daily_limit_survives_new_instances(self):
        for _ in range(8):
            self.budget().reserve()
        with self.assertRaisesRegex(BudgetUnavailable, "Daily"):
            self.budget().reserve()
        self.budget(day=16).reserve()
        state = json.loads(self.path.read_text())
        self.assertEqual(state["months"]["2026-09"]["reserved_microusd"], 9 * RESERVATION_MICROUSD)

    def test_monthly_limit_and_month_rollover(self):
        with patch.dict(os.environ, {"OPENAI_MONTHLY_BUDGET_USD": "0.02"}):
            self.budget().reserve()
            with self.assertRaisesRegex(BudgetUnavailable, "Monthly"):
                self.budget(day=16).reserve()
            self.budget(day=1, month=10).reserve()
        self.assertEqual(len(json.loads(self.path.read_text())["months"]), 2)

    def test_usage_does_not_refund_crash_safe_reservation(self):
        budget = self.budget()
        budget.reserve()
        budget.record_usage({"prompt_tokens": 1000, "completion_tokens": 500})
        month = json.loads(self.path.read_text())["months"]["2026-09"]
        self.assertEqual(month["reserved_microusd"], RESERVATION_MICROUSD)
        self.assertEqual(month["days"]["2026-09-15"]["estimated_microusd"], 1200)

    def test_no_reset_when_missing_corrupt_or_wrong_schema(self):
        for text in [None, "broken", "[]", '{"version":1,"months":{"2026-09":{}}}']:
            with self.subTest(text=text):
                if text is None:
                    self.path.unlink()
                else:
                    self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(BudgetUnavailable):
                    self.budget().reserve()

    def test_concurrent_reservation_blocked_by_lock(self):
        self.path.with_suffix(".lock").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(BudgetUnavailable, "locked"):
            self.budget().reserve()
        self.assertEqual(json.loads(self.path.read_text())["months"], {})

    def test_cannot_raise_authorized_limits_or_use_invalid_config(self):
        for monthly, daily in [("3.01", "8"), ("3", "9"), ("NaN", "8"), ("-1", "8"), ("3", "bad")]:
            with self.subTest(monthly=monthly, daily=daily), patch.dict(
                os.environ, {"OPENAI_MONTHLY_BUDGET_USD": monthly, "OPENAI_MAX_CALLS_PER_DAY": daily}
            ), self.assertRaises(BudgetUnavailable):
                limits()
        with patch.dict(os.environ, {"OPENAI_MAX_CALLS_PER_DAY": "0"}), self.assertRaises(BudgetUnavailable):
            self.budget().reserve()

    def test_git_reservation_is_durable_before_return(self):
        budget = self.budget()
        # Point only the persistence check at a path under this checkout; all git operations are mocked.
        repo = pathlib.Path(__file__).resolve().parents[1]
        budget.path = repo / "storage" / "dubai_news" / "openai_budget.json"
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main"}), patch(
            "openai_budget.subprocess.run"
        ) as git:
            budget.persist_before_spend()
            commands = [call.args[0] for call in git.call_args_list]
            self.assertEqual(commands[-1][-3:], ["push", "origin", "HEAD:main"])
            self.assertIn("--only", commands[1])
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main"}), patch(
            "openai_budget.subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")
        ), self.assertRaisesRegex(BudgetUnavailable, "could not be pushed"):
            budget.persist_before_spend()

    def test_branch_spending_is_rejected(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/feature"}), patch(
            "openai_budget.subprocess.run"
        ) as git, self.assertRaisesRegex(BudgetUnavailable, "only on main"):
            self.budget().persist_before_spend()
        git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
