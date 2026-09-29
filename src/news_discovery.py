"""Use supplied source URLs without buying a web search in digest mode."""
import json
from pathlib import Path
from datetime import datetime, timezone

from search_sources import allowed_url, fetch_article, url_identity

CONFIG = Path(__file__).resolve().parents[1] / "config" / "news_discovery.json"
MAX_URLS = 20


def search_news(storage_dir, seen_urls=(), now=None, editorial_rejections=(), config=CONFIG):
    # Missing config preserves the existing production discovery route.
    mode, urls = "openai", []
    report = {"status": "error", "items": [], "api_calls": 0, "cached": False,
              "mode": "digest", "discovered_urls": 0, "source_rejections": []}
    try:
        if config.exists():
            if config.stat().st_size > 64000:
                raise ValueError()
            data = json.loads(config.read_text(encoding="utf-8"))
            mode = data["mode"]
            urls = data.get("urls", [])
            if mode not in {"openai", "digest"}:
                raise ValueError()
            if mode == "digest" and (not isinstance(urls, list) or len(urls) > MAX_URLS
                    or any(not isinstance(url, str) or len(url) > 3000 for url in urls)):
                raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        return {**report, "reason": "Invalid discovery config; paid fallback disabled"}
    if mode == "openai":
        from openai_news_search import search_news as paid_search
        return paid_search(storage_dir, seen_urls, now, editorial_rejections)
    now = now or datetime.now(timezone.utc)
    seen = {url_identity(url) for url in seen_urls}
    seen.update(url_identity(row["url"]) for row in editorial_rejections)
    report["discovered_urls"] = len(urls)
    for url in urls:
        if not allowed_url(url):
            report["source_rejections"].append({"url": url, "reason": "disallowed_url"})
            continue
        identity = url_identity(url)
        if identity in seen:
            report["source_rejections"].append({"url": url, "reason": "already_processed"})
            continue
        seen.add(identity)
        article = fetch_article(url, now, diagnostics=report["source_rejections"])
        if article:
            actual = url_identity(article["link"])
            if actual != identity and actual in seen:
                report["source_rejections"].append({"url": url, "reason": "already_processed"})
                continue
            seen.add(actual)
            article["method"] = "digest_import"
            report["items"].append(article)
    for rejected in report["source_rejections"]:
        print("Digest article skipped: " + rejected["reason"] + " — " + rejected["url"])
    return {**report, "status": "ok",
            "reason": ("Loaded fresh source articles from supplied digest" if report["items"]
                       else "No readable fresh unprocessed articles in supplied digest; no paid search")}
