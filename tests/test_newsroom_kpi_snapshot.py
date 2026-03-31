import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from newsroom_kpi import compute_newsroom_kpi_snapshot


def _write(path, name, payload):
    (path / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_compute_newsroom_kpi_snapshot_daily_and_weekly(tmp_path):
    _write(
        tmp_path,
        "published_history.json",
        [
            {
                "source_name": "Dubai Police News",
                "published_at": "2026-03-31T08:30:00+00:00",
            },
            {
                "source_name": "Random Blog",
                "published_at": "2026-03-31T16:45:00+00:00",
            },
        ],
    )

    _write(
        tmp_path,
        "source_stats.json",
        [
            {
                "url": "https://bad-source.example/news/1",
                "timestamp": "2026-03-31T06:00:00+00:00",
                "status": "rejected",
                "reason": "duplicate content",
            },
            {
                "url": "https://bad-source.example/news/2",
                "timestamp": "2026-03-31T07:00:00+00:00",
                "status": "rejected",
                "reason": "не релевантно",
            },
            {
                "url": "https://bad-source.example/news/3",
                "timestamp": "2026-03-31T07:30:00+00:00",
                "status": "approved",
                "reason": "ok",
            },
            {
                "url": "https://good-source.example/news/1",
                "timestamp": "2026-03-31T07:40:00+00:00",
                "status": "approved",
                "reason": "ok",
            },
        ],
    )

    snapshot = compute_newsroom_kpi_snapshot(
        str(tmp_path),
        now_utc=datetime(2026, 3, 31, 20, 0, tzinfo=timezone.utc),
    )

    daily = snapshot["daily"]
    assert daily["published_per_day"]["value"] == 2
    assert daily["slot_fill_rate_morning"]["value"] == 100.0
    assert daily["slot_fill_rate_evening"]["value"] == 100.0
    assert daily["source_share_official"]["value"] == 50.0
    assert daily["reject_rate_after_editor"]["value"] == 50.0

    weak = snapshot["weekly_actions"]["disable_weak_sources"]
    assert weak
    assert weak[0]["source"] == "bad-source.example"
