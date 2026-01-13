# Collect latest Dubai-related news from multiple RSS feeds

import feedparser

FEEDS = [
    {
        "name": "Emirates247",
        "url": "https://www.emirates247.com/cmlink/rss-feed-1.4268?localLinksEnabled=false",
        "lang": "en",
    },
    {
        "name": "The Arabian Post",
        "url": "https://thearabianpost.com/feed/",
        "lang": "en",
    },
    {
        "name": "Emarat Al Youm (Local)",
        "url": "https://www.emaratalyoum.com/1.533091?ot=ot.AjaxPageLayout",
        "lang": "ar",
    },
]


def prepare_for_deepseek(entry: dict) -> dict:
    title = entry.get("title", "").strip()
    link = entry.get("link", "").strip()
    published = entry.get("pubDate", "") or entry.get("published", entry.get("updated", "")).strip()
    summary = entry.get("summary", "").strip()

    raw_text = f"{title}\n\n{summary}\n\nSource: {link}"

    return {
        "title": title,
        "link": link,
        "published": published,
        "summary": summary,
        "raw_text": raw_text,
    }


def process_feed(feed_conf: dict, limit: int = 5):
    print("=" * 60)
    print(f"Источник: {feed_conf['name']} ({feed_conf['url']})")
    print("=" * 60)

    feed = feedparser.parse(feed_conf["url"])
    entries = feed.entries or []

    selected = []

    for entry in entries:
        title = entry.get("title", "") or ""
        summary = entry.get("summary", "") or ""

        # Простая фильтрация по Dubai только для англоязычных
        if feed_conf["lang"] == "en":
            text_for_filter = f"{title} {summary}".lower()
            if "dubai" not in text_for_filter:
                continue

        selected.append(entry)

    if not selected:
        print("Подходящих новостей не найдено.\n")
        return

    for entry in selected[:limit]:
        data = prepare_for_deepseek(entry)

        print("—" * 40)
        print("Заголовок:", data["title"])
        print("Ссылка:   ", data["link"])
        print("Дата:     ", data["published"])
        print("Текст для DeepSeek:")
        print(data["raw_text"])
        print()


def main():
    for feed_conf in FEEDS:
        process_feed(feed_conf)


if __name__ == "__main__":
    main()
