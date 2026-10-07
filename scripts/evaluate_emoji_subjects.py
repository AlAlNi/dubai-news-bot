"""Emoji-only evaluation, never a publication approval.

Default: offline mocked corpus validation. Future separately authorized live use:
--live --case cycling, only inside a manual main staging Actions checkout.
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
    return all(os.getenv(key) == expected for key, expected in {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main", "BOT_ENVIRONMENT": "staging",
    }.items()) and os.getenv("OPENAI_BYPASS_DAILY_LIMIT", "false").lower() != "true"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--case", choices=["parking", "electric_vehicle", "cycling", "education"])
    args = parser.parse_args()
    cases = json.loads((ROOT / "tests/fixtures/emoji_subject_corpus.json").read_text(encoding="utf-8"))
    cases = [case for case in cases if not args.case or case["id"] == args.case]
    if args.live:
        if not args.case or not live_context_allowed():
            parser.error("Live evaluation requires one case and manual main staging context; obtain separate approval first")
        # Existing stage retains one verification slot, shared ledger, durable
        # reservation and no automatic retry. Evaluation never invokes verification.
        from openai_emoji import decorate_summary
        storage = ROOT / "storage/dubai_news_staging_emoji_eval"
        storage.mkdir(parents=True, exist_ok=True)
        report = decorate_summary(cases[0]["text"], storage)
        output = [{"case": args.case, "evaluation": "live_emoji_only_requires_human_review",
                   "publication_approved": False, "result": report}]
    else:
        output = [evaluate_mock(case) for case in cases]
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
