import os
import json
import textwrap
from pathlib import Path
from typing import List, Dict, Any

import feedparser
import requests
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# ========= НАСТРОЙКИ =========

FEEDS: List[Dict[str, Any]] = [
    # Блоги и обзоры про Дубай
    {
        "name": "Dubay Blog",
        "url": "https://dubayblog.com/feed/",
        "lang": "en",
    },
    {
        "name": "Dubai Chronicle",
        "url": "https://www.dubaichronicle.com/feed/",
        "lang": "en",
    },

    # Новости по ОАЭ / Дубаю
    {
        "name": "The Arabian Post",
        "url": "https://thearabianpost.com/feed/",
        "lang": "en",
    },
    {
        "name": "Arabian Business – UAE",
        "url": "https://www.arabianbusiness.com/gcc/uae/feed",
        "lang": "en",
    },

    # Арабоязычный локальный источник
    {
        "name": "Emarat Al Youm (Local)",
        "url": "https://www.emaratalyoum.com/1.533091?ot=ot.AjaxPageLayout",
        "lang": "ar",
    },

    # Emirates247 – общий RSS
    {
        "name": "Emirates247",
        "url": "https://www.emirates247.com/cmlink/rss-feed-1.4268?localLinksEnabled=false",
        "lang": "en",
    },

    # Gulf News – тематические разделы
    {
        "name": "Gulf News – Property",
        "url": "https://gulfnews.com/business/property/rss",
        "lang": "en",
    },
    {
        "name": "Gulf News – Tourism",
        "url": "https://gulfnews.com/business/tourism/rss",
        "lang": "en",
    },
    {
        "name": "Gulf News – Education",
        "url": "https://gulfnews.com/living-in-uae/education/rss",
        "lang": "en",
    },
    {
        "name": "Gulf News – Entertainment",
        "url": "https://gulfnews.com/entertainment/rss",
        "lang": "en",
    },
    {
        "name": "Gulf News – Sport",
        "url": "https://gulfnews.com/sport/rss",
        "lang": "en",
    },
]

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

DRAFTS_PATH = Path("data/drafts.json")
DRAFTS_PATH.parent.mkdir(parents=True, exist_ok=True)


# ========= УТИЛИТЫ =========

def load_drafts() -> list:
    if DRAFTS_PATH.exists():
        try:
            with DRAFTS_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_drafts(drafts: list) -> None:
    with DRAFTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(drafts, f, ensure_ascii=False, indent=2)


def normalize_title(title: str) -> str:
    """Простая нормализация заголовка для быстрого сравнения."""
    t = (title or "").strip().lower()
    # Можно добавить доп. очистку: удаление пунктуации, двойных пробелов и т.п.
    return " ".join(t.split())


def levenshtein_distance(a: str, b: str) -> int:
    """Классический Левенштейн, для коротких заголовков достаточно."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            cur = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[-1]


def is_same_title_fast(title1: str, title2: str, max_ratio: float = 0.2) -> bool:
    """
    Быстрый предфильтр по заголовку:
    - нормализуем строки;
    - считаем относительное расстояние Левенштейна;
    - если они почти совпадают (ratio <= max_ratio), считаем дубликатом без LLM.
    """
    n1 = normalize_title(title1)
    n2 = normalize_title(title2)
    if not n1 or not n2:
        return False

    if n1 == n2:
        return True

    dist = levenshtein_distance(n1, n2)
    max_len = max(len(n1), len(n2))
    if max_len == 0:
        return False

    ratio = dist / max_len
    return ratio <= max_ratio


# ========= ПОДГОТОВКА ДАННЫХ И ВЫЗОВ LLM =========

def prepare_for_deepseek(entry: dict) -> dict:
    title = entry.get("title", "").strip()
    link = entry.get("link", "").strip()
    published = (
        entry.get("pubDate", "")
        or entry.get("published", "")
        or entry.get("updated", "")
    ).strip()
    summary = entry.get("summary", "").strip()

    raw_text = f"{title}\n\n{summary}\n\nSource: {link}"

    return {
        "title": title,
        "link": link,
        "published": published,
        "summary": summary,
        "raw_text": raw_text,
    }


def summarize_with_deepseek(raw_text: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "[DEEPSEEK_API_KEY не задан, пропускаем вызов модели]"

    # Агрессивно ограничиваем длину текста для экономии токенов
    if len(raw_text) > 2000:
        raw_text = raw_text[:2000]

    prompt = textwrap.dedent(
        f"""
        Ты помогаешь вести телеграм‑канал о Дубае на русском языке.
        На входе — заголовок и краткое описание новости (может быть на английском или арабском).

        Задача:
        1) Перевести содержание на русский.
        2) Кратко пересказать новость (2–3 абзаца).
        3) Писать простым нейтральным языком.
        4) Не копировать формулировки из исходника, а пересказывать своими словами.
        5) Не добавлять ссылку на источник — она будет добавлена отдельно.

        Текст новости:

        ---
        {raw_text}
        ---
        """
    ).strip()

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Ты помощник‑журналист, делаешь краткие дайджесты новостей о Дубае на русском.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    try:
        resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip()
    except Exception as e:
        return f"[Ошибка DeepSeek: {e}]"


def are_same_news_llm(title1: str, summary1: str, title2: str, summary2: str) -> bool:
    """
    Сравнение двух новостей через DeepSeek.
    Возвращает True, если модель ответила строго 'yes', иначе False.
    Используется ТОЛЬКО после быстрого фильтра по заголовкам.
    """
    if not DEEPSEEK_API_KEY:
        return False

    # Обрезаем summary для экономии токенов
    summary1_short = (summary1 or "")[:500]
    summary2_short = (summary2 or "")[:500]

    prompt = textwrap.dedent(f"""
    Определи, описывают ли два текста одну и ту же новость (одно событие),
    даже если формулировки и язык различаются.

    Ответь одним словом:
    yes — если это одна и та же новость/событие;
    no — если это разные новости/события.

    Новость A:
    Заголовок: {title1}
    Описание: {summary1_short}

    Новость B:
    Заголовок: {title2}
    Описание: {summary2_short}
    """).strip()

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Отвечай строго одним словом: 'yes' или 'no'.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    try:
        resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip().lower()
        content = content.replace(".", "").strip()
        return content == "yes"
    except Exception:
        return False


# ========= ОБРАБОТКА RSS =========

def process_feed(feed_conf: dict, limit: int, with_deepseek: bool, drafts: list) -> list:
    print("=" * 80)
    print(f"Источник: {feed_conf['name']} ({feed_conf['url']})")
    print("=" * 80)

    feed = feedparser.parse(feed_conf["url"])
    entries = feed.entries or []

    selected = []
    for entry in entries:
        title = entry.get("title", "") or ""
        summary = entry.get("summary", "") or ""

        if feed_conf["lang"] == "en":
            text_for_filter = f"{title} {summary}".lower()
            if "dubai" not in text_for_filter:
                continue

        selected.append(entry)

    if not selected:
        print("Подходящих новостей не найдено.\n")
        return drafts

    for entry in selected[:limit]:
        data = prepare_for_deepseek(entry)

        print("—" * 80)
        print("Заголовок:", data["title"])
        print("Ссылка: ", data["link"])
        print("Дата: ", data["published"])
        print()

        summary_ru = ""
        if with_deepseek:
            print("Краткий дайджест на русском:")
            summary_ru = summarize_with_deepseek(data["raw_text"])
            print(summary_ru)
            print()

        # --- поиск похожего черновика с экономией токенов ---
        existing_draft = None
        for draft in drafts:
            draft_title = draft.get("title", "")
            draft_summary_ru = draft.get("summary_ru", "")

            # 1) быстрый предфильтр по заголовку
            if is_same_title_fast(draft_title, data["title"]):
                existing_draft = draft
                break

            # 2) если заголовки не очень похожи, опционально проверяем через LLM
            #    (это место можно дополнительно ограничить по дате/источнику)
            if are_same_news_llm(
                draft_title,
                draft_summary_ru,
                data["title"],
                summary_ru or data["summary"],
            ):
                existing_draft = draft
                break

        if existing_draft is not None:
            urls = existing_draft.get("source_urls", [])
            if data["link"] and data["link"] not in urls:
                urls.append(data["link"])
            existing_draft["source_urls"] = urls
        else:
            drafts.append(
                {
                    "source_name": feed_conf["name"],
                    "title": data["title"],
                    "summary_ru": summary_ru,
                    "published": data["published"],
                    "source_urls": [data["link"]] if data["link"] else [],
                }
            )

        print()

    return drafts


def main():
    drafts = load_drafts()
    for feed_conf in FEEDS:
        drafts = process_feed(feed_conf, limit=2, with_deepseek=True, drafts=drafts)
    save_drafts(drafts)
    print(f"Сохранено черновиков: {len(drafts)}")


if __name__ == "__main__":
    main()
