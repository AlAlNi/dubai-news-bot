"""Manual evaluation workflow never loads publication credentials or stages."""
from pathlib import Path
from unittest import TestCase


class EvaluationWorkflowTests(TestCase):
    def test_manual_only_one_case_shared_lock_and_existing_secret(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/emoji_evaluation.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertIn("group: dubai-news-storage", workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertEqual(workflow.count('--live --case "$EVALUATION_CASE"'), 1)
        self.assertIn("type: choice", workflow)
        self.assertIn("required: true", workflow)
        self.assertIn("options: [cycling, parking, electric_vehicle, education]", workflow)
        self.assertIn("EVALUATION_CASE: ${{ inputs.case }}", workflow)
        self.assertNotIn("--case ${{", workflow)
        self.assertNotIn("TELEGRAM", workflow)
        self.assertNotIn("run_staging", workflow)
        self.assertNotIn("collect_and_publish", workflow)
        self.assertIn("secrets.OPENAI_API_KEY", workflow)
        self.assertIn("OPENAI_BYPASS_DAILY_LIMIT: 'false'", workflow)
        self.assertIn("OPENAI_MAX_CALLS_PER_DAY: '8'", workflow)
        self.assertIn("OPENAI_MONTHLY_BUDGET_USD: '3'", workflow)
