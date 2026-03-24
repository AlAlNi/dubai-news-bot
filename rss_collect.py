import os
import json
import textwrap
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timedelta, timezone

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

    # News – остальные
    {
        "name": "Lovin Dubai",
        "url": "https://lovin.co/dubai/en/news/feed",
        "lang": "en",
    },
    {
        "name": "What's On",
        "url": "https://whatson.ae/feed",
        "lang": "en",
    },
    {
        "name": "Dubai Confidential",
        "url": "https://www.dubaiconfidential.ae/feed",
        "lang": "en",
    },
    {
        "name": "PropertyNews.ae",
        "url": "https://propertynews.ae/feed",
        "lang": "en",
    },
    {
        "name": "Emirates Woman",
        "url": "https://emirateswoman.com/feed",
        "lang": "en",
    },
    {
        "name": "Gulf Business",
        "url": "https://www.gulfbusiness.com/feed",
        "lang": "en",
    },
    {
        "name": "Dubai Travel Blog",
        "url": "https://dubaitravelblog.com/feed",
        "lang": "en",
    },
]

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

DRAFTS_PATH = Path("data/drafts.json")
DRAFTS_PATH.parent.mkdir(parents=True, exist_ok=True)

# RSS новости берём не старше 48 часов
MAX_NEWS_AGE_HOURS = 48
# LLM-сравнение делаем только с черновиками за последние 72 часа
MAX_DRAFT_AGE_HOURS_FOR_LLM = 72

# ========= ПРОСТОЙ ЛОГГЕР =========


def log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


# ========= УТИЛИТЫ =========


def load_drafts() -> list:
    if DRAFTS_PATH.exists():
        try:
            with DRAFTS_PATH.open("r", encoding="utf-8") as f:
                drafts = json.load(f)
                log("INFO", f"Загружено черновиков: {len(drafts)}")
                return drafts
        except Exception as e:
            log("ERROR", f"Не удалось прочитать drafts.json: {e}")
            return []
    log("INFO", "drafts.json не найден, начинаем с пустого списка.")
    return []


def save_drafts(drafts: list) -> None:
    try:
        with DRAFTS_PATH.open("w", encoding="utf-8") as f:
            json.dump(drafts, f, ensure_ascii=False, indent=2)
        log("INFO", f"Черновики сохранены, всего: {len(drafts)}")
    except Exception as e:
        log("ERROR", f"Ошибка при сохранении drafts.json: {e}")


def normalize_title(title: str) -> str:
    t = (title or "").strip().lower()
    return " ".join(t.split())


def levenshtein_distance(a: str, b: str) -> int:
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


def parse_entry_datetime(entry: dict) -> datetime | None:
    struct = (
        getattr(entry, "published_parsed", None)
        or getattr(entry, "updated_parsed", None)
        or getattr(entry, "created_parsed", None)
    )
    if not struct:
        return None
    try:
        dt = datetime(*struct[:6], tzinfo=timezone.utc)
        return dt
    except Exception as e:
        log("DEBUG", f"Не удалось разобрать дату entry: {e}")
        return None


def is_recent_enough(entry: dict, max_age_hours: int = MAX_NEWS_AGE_HOURS) -> bool:
    dt = parse_entry_datetime(entry)
    if dt is None:
        log("DEBUG", "Запись без корректной даты — пропускаем как неактуальную.")
        return False
    now_utc = datetime.now(timezone.utc)
    age = now_utc - dt
    is_ok = age <= timedelta(hours=max_age_hours)
    log(
        "DEBUG",
        f"Возраст новости: {age.total_seconds() / 3600:.1f} ч, "
        f"порог: {max_age_hours} ч, актуальна: {is_ok}",
    )
    return is_ok


def parse_published_str(date_str: str) -> datetime | None:
    if not date_str:
        return None
    fmts = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %z (%Z)",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def filter_recent_drafts_for_llm(drafts: list) -> list:
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=MAX_DRAFT_AGE_HOURS_FOR_LLM)
    result = []
    for d in drafts:
        dt = parse_published_str(d.get("published", ""))
        if dt is None:
            continue
        if dt >= cutoff:
            result.append(d)
    log(
        "INFO",
        f"Для LLM-сравнения отфильтровано черновиков: {len(result)} "
        f"(из {len(drafts)} за последние {MAX_DRAFT_AGE_HOURS_FOR_LLM} ч)",
    )
    return result


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
        log("WARNING", "DEEPSEEK_API_KEY не задан, пропускаем summarize_with_deepseek.")
        return "[DEEPSEEK_API_KEY не задан, пропускаем вызов модели]"
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
    log("INFO", "Вызов summarize_with_deepseek...")
    try:
        resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        log("INFO", "summarize_with_deepseek — успех.")
        return content.strip()
    except Exception as e:
        log("ERROR", f"Ошибка DeepSeek в summarize_with_deepseek: {e}")
        return f"[Ошибка DeepSeek: {e}]"


def are_same_news_llm(title1: str, summary1: str, title2: str, summary2: str) -> bool:
    if not DEEPSEEK_API_KEY:
        log("DEBUG", "Нет DEEPSEEK_API_KEY — are_same_news_llm всегда False.")
        return False
    summary1_short = (summary1 or "")[:500]
    summary2_short = (summary2 or "")[:500]
    prompt = textwrap.dedent(
        f"""
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
                "content": "Отвечай строго одним словом: 'yes' или 'no'.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }
    log("INFO", "Вызов are_same_news_llm для сравнения двух новостей...")
    try:
        resp = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip().lower()
        content = content.replace(".", "").strip()
        result = content == "yes"
        log("INFO", f"are_same_news_llm ответ: {content}, результат: {result}")
        return result
    except Exception as e:
        log("ERROR", f"Ошибка DeepSeek в are_same_news_llm: {e}")
        return False


# ========= ОБРАБОТКА RSS =========


def process_feed(feed_conf: dict, limit: int, with_deepseek: bool, drafts: list) -> list:
    log("INFO", f"Начало обработки источника: {feed_conf['name']} ({feed_conf['url']})")
    feed = feedparser.parse(feed_conf["url"])
    entries = feed.entries or []
    log("INFO", f"Загружено записей из RSS: {len(entries)}")

    selected = []
    for idx, entry in enumerate(entries):
        log("DEBUG", f"[{feed_conf['name']}] Проверка записи #{idx + 1}/{len(entries)}")
        if not is_recent_enough(entry):
            log("DEBUG", "Новость старше порога или без даты — пропускаем.")
            continue
        title = entry.get("title", "") or ""
        summary = entry.get("summary", "") or ""
        if feed_conf["lang"] == "en":
            text_for_filter = f"{title} {summary}".lower()
            if "dubai" not in text_for_filter:
                log("DEBUG", "В тексте нет 'dubai' — пропускаем.")
                continue
        selected.append(entry)

    log("INFO", f"После фильтрации осталось записей: {len(selected)}")
    if not selected:
        log("INFO", "Подходящих новостей не найдено.\n")
        return drafts

    recent_drafts_for_llm = filter_recent_drafts_for_llm(drafts)

    for i, entry in enumerate(selected[:limit]):
        log("INFO", f"Обработка выбранной новости {i + 1}/{min(len(selected), limit)}")
        data = prepare_for_deepseek(entry)
        log("INFO", f"Новость: {data['title']}")
        log("INFO", f"Ссылка: {data['link']}")
        log("INFO", f"Дата:   {data['published']}")

        summary_ru = ""
        if with_deepseek:
            summary_ru = summarize_with_deepseek(data["raw_text"])

        existing_draft = None
        total_drafts = len(drafts)
        log("INFO", f"Дедупликация: всего черновиков {total_drafts}")

        # быстрый заголовочный фильтр по всем черновикам
        for j, draft in enumerate(drafts):
            if j % 20 == 0:
                log("DEBUG", f"Проверка быстрых дублей: черновик {j + 1}/{total_drafts}")
            draft_title = draft.get("title", "")
            if is_same_title_fast(draft_title, data["title"]):
                log("INFO", "Найден дубликат по быстрому заголовочному фильтру.")
                existing_draft = draft
                break

        # LLM только по свежим черновикам, если быстрый фильтр не нашёл дубликат
        if existing_draft is None and recent_drafts_for_llm:
            log(
                "INFO",
                f"Быстрый фильтр не нашёл дубликат, "
                f"проверяем через LLM только {len(recent_drafts_for_llm)} свежих черновиков.",
            )
            for j, draft in enumerate(recent_drafts_for_llm):
                draft_title = draft.get("title", "")
                draft_summary_ru = draft.get("summary_ru", "")
                if are_same_news_llm(
                    draft_title,
                    draft_summary_ru,
                    data["title"],
                    summary_ru or data["summary"],
                ):
                    log("INFO", "Найден дубликат через are_same_news_llm.")
                    existing_draft = draft
                    break

        if existing_draft is not None:
            urls = existing_draft.get("source_urls", [])
            if data["link"] and data["link"] not in urls:
                urls.append(data["link"])
            existing_draft["source_urls"] = urls
            log("INFO", f"Обновлён существующий черновик, источников: {len(urls)}")
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
            log("INFO", "Создан новый черновик.")

        log("INFO", "Обработка новости завершена.\n")

    return drafts


def main():
    log("INFO", "Запуск rss_collect.py")
    drafts = load_drafts()
    for idx, feed_conf in enumerate(FEEDS):
        log("INFO", f"=== Источник {idx + 1}/{len(FEEDS)}: {feed_conf['name']} ===")
        drafts = process_feed(feed_conf, limit=2, with_deepseek=True, drafts=drafts)
    save_drafts(drafts)
    log("INFO", "Работа скрипта завершена.")


if __name__ == "__main__":
    main()
