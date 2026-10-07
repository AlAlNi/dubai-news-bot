"""Offline budget-only persistence contract; no external mutation."""
from pathlib import Path
import subprocess
import sys
from unittest import TestCase
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from emoji_evaluation_budget import EvaluationBudget, LEDGER
from openai_budget import BudgetUnavailable


class EvaluationPersistenceTests(TestCase):
    def budget(self):
        budget = EvaluationBudget.__new__(EvaluationBudget)
        budget.git = Mock()
        return budget

    def test_only_budget_commit_is_pushed(self):
        budget = self.budget()
        budget.git.side_effect = [LEDGER + "\n", "", "", LEDGER + "\n", ""]
        budget.persist_before_spend()
        calls = [call.args for call in budget.git.call_args_list]
        self.assertEqual(calls[1], ("add", "--", LEDGER))
        self.assertEqual(calls[2][-2:], ("--", LEDGER))
        self.assertIn("--only", calls[2])
        self.assertEqual(calls[-1], ("push", "origin", "HEAD:main"))
        self.assertNotIn("--force", calls[-1])

    def test_source_change_or_concurrent_push_failure_stops_persistence(self):
        for paths in ["src/openai_emoji.py\n", LEDGER + "\nsrc/openai_emoji.py\n"]:
            budget = self.budget()
            budget.git.return_value = paths
            with self.assertRaises(BudgetUnavailable):
                budget.persist_before_spend()
            budget.git.assert_called_once()
        budget = self.budget()
        budget.git.side_effect = [LEDGER + "\n", "", "", LEDGER + "\n",
                                 subprocess.CalledProcessError(1, "git push")]
        with self.assertRaises(BudgetUnavailable):
            budget.persist_before_spend()
        self.assertEqual(budget.git.call_count, 5)  # No retry or force push.
