"""Manual staging entry point. Executed from main with develop source files."""
import importlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
STORAGE = ROOT / "storage" / "dubai_news_staging"
sys.path.insert(0, str(ROOT / "src"))


def validate_context():
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("GITHUB_REF") != "refs/heads/main"
            or os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.getenv("BOT_ENVIRONMENT") != "staging"):
        raise RuntimeError("Staging requires manual launch from main")


def validate_channel():
    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    target = os.getenv("TELEGRAM_CHANNEL_ID", "")
    production = os.getenv("PRODUCTION_CHANNEL_ID", "")
    if not token or not re.fullmatch(r"-100\d+", target) or not production:
        raise RuntimeError("Configure TEST_TELEGRAM_BOT_TOKEN, TEST_TELEGRAM_CHANNEL_ID and production channel")
    def get_chat(chat):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/getChat",
                json={"chat_id": chat}, timeout=20, allow_redirects=False)
            try:
                data = response.json()
                if response.status_code != 200 or data.get("ok") is not True:
                    raise ValueError()
                return data["result"]
            finally:
                response.close()
        except Exception:
            raise RuntimeError("Cannot validate Telegram destinations; check bot access and channel settings") from None
    test_chat = get_chat(target)
    production_chat = get_chat(production)
    if (test_chat.get("type") != "channel" or test_chat.get("username")
            or str(test_chat.get("id")) != target
            or test_chat["id"] == production_chat["id"]):
        raise RuntimeError("Test destination must be a different private channel")


def scrub_state():
    """Do not commit private Telegram destination identifiers to the public repo."""
    def scrub(value):
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()
                    if k not in {"telegram_channel_id", "chat_id", "channel_username"}}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value
    for path in STORAGE.glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(scrub(value), ensure_ascii=False, indent=2), encoding="utf-8")


def run(mode):
    validate_context()
    validate_channel()  # Validate before spending, collecting or sending.
    if mode == "check_connection":
        print("Test channel validated; no paid APIs or publication invoked.")
        return 0
    STORAGE.mkdir(parents=True, exist_ok=True)
    from workflow_run import report_result
    if mode == "retro":
        module = importlib.import_module("retro_publish")
        result = module.run(storage=STORAGE, launch=ROOT / "config" / "retro_first_post.json")
        print(json.dumps(result, ensure_ascii=False))
        return int(result["status"] not in {"published", "already_attempted", "no_verified_photo"})
    if mode not in {"collect", "publish", "collect_and_publish"}:
        raise ValueError("Unknown staging operation")
    # These modules otherwise default to production storage.
    for name in ("rss_collect", "next_draft", "auto_notify"):
        module = importlib.import_module(name)
        module.MOUNTED_BUCKET_PATH = str(STORAGE)
    if mode in {"collect", "collect_and_publish"}:
        result = importlib.import_module("rss_collect").handler({}, None)
        outcome = report_result(result)
        if outcome or mode == "collect":
            return outcome
    return report_result(importlib.import_module("auto_notify").handler({}, None))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: run_staging.py check_connection|collect|publish|collect_and_publish|retro|scrub")
    if sys.argv[1] == "scrub":
        scrub_state()
    else:
        try:
            raise SystemExit(run(sys.argv[1]))
        finally:
            scrub_state()
