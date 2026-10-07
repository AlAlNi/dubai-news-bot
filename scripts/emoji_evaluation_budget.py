"""Budget-only Git persistence from a clean dedicated main state checkout."""
import os
from pathlib import Path
import subprocess

from openai_budget import Budget, BudgetUnavailable

LEDGER = "storage/dubai_news/openai_budget.json"


class EvaluationBudget(Budget):
    def __init__(self, state_checkout):
        if os.getenv("GITHUB_ACTIONS") == "true" and (
                os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch"
                or os.getenv("GITHUB_REF") != "refs/heads/main"):
            raise BudgetUnavailable("Actions evaluation requires manual main context")
        if os.getenv("BOT_ENVIRONMENT") == "staging":
            raise BudgetUnavailable("Evaluation must use its dedicated main state checkout")
        self.checkout = Path(state_checkout).resolve()
        super().__init__(self.checkout / "storage/dubai_news")
        if self.git("status", "--porcelain").strip():
            raise BudgetUnavailable("Evaluation state checkout must be clean before reservation")
        if self.git("rev-parse", "HEAD").strip() != self.git("rev-parse", "origin/main").strip():
            raise BudgetUnavailable("Evaluation state must start at fetched origin/main")

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "safe.directory=" + self.checkout.as_posix(),
                                        "-C", str(self.checkout), *args], text=True,
                                       stderr=subprocess.PIPE, timeout=60)

    def persist_before_spend(self):
        try:
            paths = self.git("diff", "--name-only", "HEAD").splitlines()
            if paths != [LEDGER]:
                raise BudgetUnavailable("Only shared budget data may change")
            self.git("add", "--", LEDGER)
            self.git("commit", "--only", "-m", "Account isolated emoji evaluation [skip ci]", "--", LEDGER)
            if self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines() != [LEDGER]:
                raise BudgetUnavailable("Refusing a non-budget commit")
            # Fast-forward only: a concurrent main update cancels the request.
            self.git("push", "origin", "HEAD:main")
        except (OSError, subprocess.SubprocessError) as exc:
            raise BudgetUnavailable("Budget-only persistence failed; no automatic retry") from exc

    def record_usage(self, usage, search_calls=0):
        super().record_usage(usage, search_calls)
        self.persist_before_spend()
