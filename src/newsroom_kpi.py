import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

OFFICIAL_SOURCE_MARKERS = (
    "dubai media office",
    "government of dubai",
    "dubai police",
    "dubai health authority",
    "dha",
    "rta",
    "dubai airports",
    "emirates",
    "flydubai",
    "dewa",
    "khda",
    "gdrfa",
)

WEEKLY_SOURCE_TEST_CANDIDATES = [
    "Dubai Municipality News",
    "Expo City Dubai News",
    "DET Dubai (Department of Economy and Tourism)",
    "MOHRE UAE News",
    "Dubai Courts News",
    "DFM Market News",
]


def _safe_parse_iso(dt_value: str) -> datetime | None:
    raw = (dt_value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _load_json_list(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _is_official_source(source_name: str) -> bool:
    normalized = (source_name or "").strip().lower()
    return any(marker in normalized for marker in OFFICIAL_SOURCE_MARKERS)


def compute_newsroom_kpi_snapshot(storage_path: str, now_utc: datetime | None = None) -> Dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    week_start = day_start - timedelta(days=6)

    storage = Path(storage_path)
    published_history = _load_json_list(storage / "published_history.json")
    source_stats = _load_json_list(storage / "source_stats.json")

    published_today: List[Dict[str, Any]] = []
    morning_hits = 0
    evening_hits = 0

    for item in published_history:
        published_at = _safe_parse_iso(str(item.get("published_at", "")))
        if not published_at or not (day_start <= published_at < day_end):
            continue
        published_today.append(item)
        if 8 <= published_at.hour <= 11:
            morning_hits += 1
        if 16 <= published_at.hour <= 20:
            evening_hits += 1

    total_published = len(published_today)
    official_published = sum(
        1
        for item in published_today
        if _is_official_source(str(item.get("source_name") or item.get("source") or ""))
    )
    source_share_official = round((official_published / total_published) * 100, 2) if total_published else 0.0

    editor_approved = 0
    editor_rejected = 0
    weekly_source_counters: Dict[str, Dict[str, int]] = {}

    for item in source_stats:
        created_at = _safe_parse_iso(str(item.get("timestamp", "")))
        if not created_at:
            continue

        status = (item.get("status") or "").strip().lower()
        reason = (item.get("reason") or "").strip().lower()
        source_url = (item.get("url") or "").strip()

        if day_start <= created_at < day_end:
            if status == "approved":
                editor_approved += 1
            elif status == "rejected":
                editor_rejected += 1

        if week_start <= created_at < day_end:
            source_key = source_url.split("/")[2] if "://" in source_url else source_url or "unknown"
            stats = weekly_source_counters.setdefault(source_key, {"approved": 0, "rejected": 0, "duplicates": 0})
            if status == "approved":
                stats["approved"] += 1
            elif status == "rejected":
                stats["rejected"] += 1
            if "дублик" in reason or "duplicat" in reason:
                stats["duplicates"] += 1

    reviewed_total = editor_approved + editor_rejected
    reject_rate_after_editor = round((editor_rejected / reviewed_total) * 100, 2) if reviewed_total else 0.0

    weak_sources = []
    for source_name, counters in weekly_source_counters.items():
        total = counters["approved"] + counters["rejected"]
        if total < 3:
            continue
        reject_rate = (counters["rejected"] / total) * 100
        duplicate_rate = (counters["duplicates"] / total) * 100
        if reject_rate >= 60 or duplicate_rate >= 40:
            weak_sources.append(
                {
                    "source": source_name,
                    "events": total,
                    "reject_rate": round(reject_rate, 2),
                    "duplicate_rate": round(duplicate_rate, 2),
                }
            )

    weak_sources.sort(key=lambda x: (x["reject_rate"], x["duplicate_rate"]), reverse=True)

    active_source_domains = set()
    for item in source_stats:
        url = (item.get("url") or "").strip()
        if "://" in url:
            active_source_domains.add(url.split("/")[2].lower())

    test_candidates = [
        name for name in WEEKLY_SOURCE_TEST_CANDIDATES if not any(part.lower() in " ".join(active_source_domains) for part in name.split()[:1])
    ][:3]

    return {
        "date_utc": day_start.date().isoformat(),
        "daily": {
            "published_per_day": {"value": total_published, "target_min": 2, "ok": total_published >= 2},
            "slot_fill_rate_morning": {"value": 100.0 if morning_hits > 0 else 0.0, "target_min": 95.0, "ok": morning_hits > 0},
            "slot_fill_rate_evening": {"value": 100.0 if evening_hits > 0 else 0.0, "target_min": 95.0, "ok": evening_hits > 0},
            "source_share_official": {"value": source_share_official, "target_min": 35.0, "ok": source_share_official >= 35.0},
            "reject_rate_after_editor": {
                "value": reject_rate_after_editor,
                "alert_if_gt": 60.0,
                "ok": reject_rate_after_editor <= 60.0,
            },
        },
        "weekly_actions": {
            "disable_weak_sources": weak_sources[:10],
            "add_sources_ab_test_7d": test_candidates,
        },
    }
