"""Emoji-only evaluation, never a publication approval.

Default: offline mocked corpus validation. Future separately authorized live use:
--live --case cycling --state-checkout PATH, after separate authorization.
It calls the existing budgeted emoji stage once; no writer, verifier or sender.
The result requires human semantic evaluation and cannot authorize publication.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from openai_emoji import apply_decorations, subject_decorations
from emoji_review import inserted_emoji


def evaluate_mock(case):
    selected = subject_decorations(case["text"], case["mock_selection"])
    post = apply_decorations(case["text"], selected)
    return {"case": case["id"], "evaluation": "offline_mock_contract_only",
            "semantic_accuracy_proven": False, "api_calls": 0,
            "subject_selections": case["mock_selection"]["decorations"],
            "insertions": inserted_emoji(post), "post": post}


def live_context_allowed():
    return ((os.getenv("GITHUB_ACTIONS") != "true" or (
                os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
                and os.getenv("GITHUB_REF") == "refs/heads/main"))
            and os.getenv("BOT_ENVIRONMENT") != "staging"
            and os.getenv("OPENAI_BYPASS_DAILY_LIMIT", "false").lower() != "true"
            and bool(os.getenv("OPENAI_API_KEY", "").strip()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--state-checkout", type=Path)
    parser.add_argument("--case", choices=["parking", "electric_vehicle", "cycling", "education"])
    args = parser.parse_args()
    cases = json.loads((ROOT / "tests/fixtures/emoji_subject_corpus.json").read_text(encoding="utf-8"))
    cases = [case for case in cases if not args.case or case["id"] == args.case]
    if args.live:
        if not args.case or not args.state_checkout or not live_context_allowed():
            parser.error("Live evaluation requires one case, main state checkout and local OPENAI_API_KEY; obtain separate approval first")
        if os.getenv("OPENAI_BYPASS_DAILY_LIMIT", "false").lower() == "true":
            parser.error("Evaluation does not allow budget bypass")
        from openai_emoji import evaluate_summary
        from emoji_evaluation_budget import EvaluationBudget
        budget = EvaluationBudget(args.state_checkout)
        storage = args.state_checkout.resolve().parent / "emoji-evaluation-cache"
        storage.mkdir(parents=True, exist_ok=True)
        report = evaluate_summary(cases[0]["text"], storage, lambda _: budget)
        output = [{"case": args.case, "evaluation": "live_emoji_only_requires_human_review",
                   "publication_approved": False, "result": report}]
    else:
        output = [evaluate_mock(case) for case in cases]
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
