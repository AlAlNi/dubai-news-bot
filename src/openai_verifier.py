"""OpenAI verification with strict output, bounded requests and a persistent cache."""
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from http_client import request_with_retry
from api_diagnostics import openai_error
from openai_budget import (Budget, BudgetUnavailable, MODEL, INPUT_TOKEN_CEILING,
                           OUTPUT_TOKEN_CEILING, atomic_json)


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "supported": {"type": "boolean"}, "reason": {"type": "string"},
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"claim": {"type": "string"}, "supported": {"type": "boolean"},
                           "evidence": {"type": "string"}},
            "required": ["claim", "supported", "evidence"],
        }},
    },
    "required": ["supported", "reason", "claims"],
}


def cache_read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        return {key: entry for key, entry in data.items()
                if isinstance(entry, dict) and isinstance(entry.get("result"), dict)
                and datetime.fromisoformat(entry["saved_at"]) >= cutoff}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def verify_with_openai(source, summary, prompt, parse_response, timeout, storage_dir):
    base = {"version": 1, "status": "error", "provider": "openai", "model": MODEL, "api_calls": 0}
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return {**base, "reason": "Missing OPENAI_API_KEY"}
    payload = {
        "model": MODEL, "temperature": 0, "store": False,
        "max_completion_tokens": OUTPUT_TOKEN_CEILING,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "source_fidelity", "strict": True, "schema": SCHEMA,
        }},
        "messages": [
            {"role": "system", "content": prompt + " Keep reasons, claim paraphrases and evidence concise. "
             "Use the shortest sufficient exact quote. If output space is insufficient to review all claims, reject."},
            {"role": "user", "content": json.dumps({"source": source, "post": summary}, ensure_ascii=False)},
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    # UTF-8 bytes conservatively bound text tokens; reserve extra space for message/schema framing.
    # Do not silently truncate the source to save money: an oversized post waits instead.
    if len(serialized.encode("utf-8")) + 1024 > INPUT_TOKEN_CEILING:
        return {**base, "status": "deferred", "reason": "OpenAI request exceeds input cost ceiling"}
    cache_key = hashlib.sha256(("review-v2:" + serialized).encode("utf-8")).hexdigest()
    cache_path = Path(storage_dir) / "openai_verification_cache.json"
    cache = cache_read(cache_path)
    if cache_key in cache:
        result = cache[cache_key]["result"]
        # Revalidate the raw model response and exact source evidence, never trust a cached approval flag.
        return {**parse_response(result, source, summary, MODEL, "openai"), "cached": True, "api_calls": 0}
    budget = Budget(storage_dir)
    try:
        budget.reserve()
    except (BudgetUnavailable, OSError) as exc:
        reason = str(exc) if isinstance(exc, BudgetUnavailable) else "Cannot save budget reservation"
        return {**base, "status": "deferred", "reason": reason}
    base["api_calls"] = 1
    try:
        response = request_with_retry(
            "POST", "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"}, timeout=timeout, max_attempts=1,
            allow_redirects=False, json=payload,
        )
        if response.status_code != 200:
            return {**base, "reason": openai_error(response, key)}
        result = response.json()
        try:
            budget.record_usage(result.get("usage"))
        except (BudgetUnavailable, OSError, KeyError, TypeError):
            # The full reservation was already pushed; accounting failure cannot allow extra spend.
            print("OpenAI usage details could not be saved; full cost reservation retained")
        report = {**parse_response(result, source, summary, MODEL, "openai"), "api_calls": 1, "cached": False}
        if report["status"] in {"approved", "rejected"}:
            cache[cache_key] = {"saved_at": datetime.now(timezone.utc).isoformat(), "result": result}
            try:
                atomic_json(cache_path, dict(list(cache.items())[-500:]))
            except OSError:
                print("OpenAI cache unavailable; full cost reservation retained")
        return report
    except Exception as exc:
        return {**base, "reason": f"OpenAI verifier error: {type(exc).__name__}"}
