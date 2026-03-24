import json
from pathlib import Path
from datetime import datetime

DRAFTS_PATH = Path("data/drafts.json")


def load_drafts() -> list:
    if not DRAFTS_PATH.exists():
        return []
    try:
        with DRAFTS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def parse_date(date_str: str) -> datetime:
    """
    Пытаемся разобрать дату, если не получилось — возвращаем очень старую,
    чтобы не упасть.
    """
    if not date_str:
        return datetime(1970, 1, 1)
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",      # Wed, 07 Jan 2026 06:48:08 +0000
        "%a, %d %b %Y %H:%M:%S %z (%Z)", # на всякий случай
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(date_str, fmt)
        except Exception:
            continue
    return datetime(1970, 1, 1)


def pick_next_draft(drafts: list) -> dict | None:
    """
    Простейшая логика выбора:
    - сортируем по дате публикации (сначала самые свежие);
    - берём первый по списку.
    При желании сюда можно добавить статус/фильтры.
    """
    if not drafts:
        return None

    sorted_drafts = sorted(
        drafts,
        key=lambda d: parse_date(d.get("published", "")),
        reverse=True,
    )
    return sorted_drafts[0]


def format_draft_for_console(draft: dict) -> str:
    title = draft.get("title", "").strip()
    summary_ru = draft.get("summary_ru", "").strip()
    source_name = draft.get("source_name", "").strip()
    published = draft.get("published", "").strip()
    source_urls = draft.get("source_urls", []) or []

    lines = []
    lines.append(f"Источник: {source_name}")
    lines.append(f"Дата:     {published}")
    lines.append("")
    lines.append(f"Заголовок:")
    lines.append(title)
    lines.append("")
    lines.append("Текст дайджеста:")
    lines.append(summary_ru)
    lines.append("")
    if source_urls:
        lines.append("Источники:")
        for url in source_urls:
            lines.append(f"- {url}")

    return "\n".join(lines)


def main():
    drafts = load_drafts()
    if not drafts:
        print("Черновики не найдены (data/drafts.json пуст или отсутствует).")
        return

    draft = pick_next_draft(drafts)
    if not draft:
        print("Подходящих черновиков нет.")
        return

    print("=" * 80)
    print("СЛЕДУЮЩИЙ ЧЕРНОВИК")
    print("=" * 80)
    print(format_draft_for_console(draft))
    print("=" * 80)


if __name__ == "__main__":
    main()
