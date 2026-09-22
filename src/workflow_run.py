"""Translate handler results into meaningful GitHub Actions outcomes."""
import importlib
import json
import os
import sys


def report_result(result):
    body = json.loads(result["body"])
    print(json.dumps(body, ensure_ascii=False, indent=2))
    failed = (result.get("statusCode", 500) >= 400 or bool(body.get("error"))
              or bool(body.get("technical_errors")))
    outcome = "ERROR" if failed else "OK" if body.get("new_draft") or body.get("published") else "NO POST"
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"## Bot result: {outcome}\n\n")
            for name in ("new_draft", "published", "reason", "technical_errors", "discovery_status",
                         "openai_search_calls", "openai_search_cache_hits", "replacement_search",
                         "publisher_fallback", "publisher_articles_checked",
                         "discovered_urls", "verification_paused"):
                if name in body:
                    stream.write(f"- {name}: {body[name]}\n")
    if outcome == "NO POST":
        print("::warning::No post produced. Check the handler result and source verification logs.")
    if failed:
        print("::error::Bot reported a technical failure. See the API diagnostics above.")
    return int(failed)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"rss_collect", "auto_notify"}:
        raise SystemExit("Usage: workflow_run.py rss_collect|auto_notify")
    raise SystemExit(report_result(importlib.import_module(sys.argv[1]).handler({}, None)))
