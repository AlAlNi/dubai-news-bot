"""Bounded API diagnostics without credentials or raw response dumps."""
import re


def openai_error(response, key):
    prefix = f"OpenAI HTTP {response.status_code}"
    try:
        error = response.json().get("error", {})
        if not isinstance(error, dict):
            return prefix
        parts = []
        for field in ("type", "code", "param", "message"):
            value = error.get(field)
            if not isinstance(value, str):
                continue
            if key:
                value = value.replace(key, "[redacted]")
            value = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", value)
            value = " ".join(value.split())[:500]
            parts.append(f"{field}={value}")
        return prefix + (": " + "; ".join(parts) if parts else "")
    except (ValueError, AttributeError, TypeError):
        return prefix
