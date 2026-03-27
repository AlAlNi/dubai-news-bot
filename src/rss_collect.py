import os
import json
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone, timedelta
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
from http_client import request_with_retry

# ========= НАСТРОЙКИ =========

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
GNEWS_API_KEY_BACKUP = os.getenv("GNEWS_API_KEY_BACKUP")

# Определяем путь к хранилищу в зависимости от окружения
if os.getenv("GITHUB_ACTIONS") == "true":
    # В GitHub Actions используем папку в репозитории
    MOUNTED_BUCKET_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "dubai_news")
else:
    # Локальная разработка
    MOUNTED_BUCKET_PATH = "./storage/dubai_news"

# Создаем директорию, если её нет
os.makedirs(MOUNTED_BUCKET_PATH, exist_ok=True)

S3_OBJECT_KEY = os.getenv("S3_OBJECT_KEY", "drafts.json")
S3_RSS_LIST_KEY = os.getenv("S3_RSS_LIST_KEY", "rss_feeds.json")
S3_SOURCE_STATS_KEY = os.getenv("S3_SOURCE_STATS_KEY", "source_stats.json")
S3_PUBLISHED_HISTORY_KEY = os.getenv("S3_PUBLISHED_HISTORY_KEY", "published_history.json")
S3_RUN_REPORT_KEY = os.getenv("S3_RUN_REPORT_KEY", "run_report.json")

MAX_AGE_HOURS = 24
MAX_SOURCE_STATS_DAYS = 7
MAX_NEWS_AGE_DAYS = 2
MAX_DRAFTS = 50
CONTENT_UNIQUE_DAYS = 30  # Сколько дней хранить историю контента

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HTTP_TIMEOUT = 25
ENABLE_CHEAP_PREFILTER = os.getenv("ENABLE_CHEAP_PREFILTER", "1").lower() in ("1", "true", "yes", "on")
MAX_DEEPSEEK_CALLS_PER_RUN = max(0, int(os.getenv("MAX_DEEPSEEK_CALLS_PER_RUN", "2")))
PREFILTER_MIN_TEXT_LEN = max(40, int(os.getenv("PREFILTER_MIN_TEXT_LEN", "120")))
PREFILTER_MAX_NOISE_RATIO = float(os.getenv("PREFILTER_MAX_NOISE_RATIO", "0.35"))
PREFILTER_MAX_DOMAIN_REPEATS_PER_RUN = max(1, int(os.getenv("PREFILTER_MAX_DOMAIN_REPEATS_PER_RUN", "2")))

# ========= RSS ИСТОЧНИКИ =========

DUBAI_SPECIFIC_RSS_FEEDS = [
    # Основные новостные
    {"name": "Gulf News", "url": "https://gulfnews.com/rss", "lang": "en", "priority": 1},
    {"name": "Khaleej Times", "url": "https://www.khaleejtimes.com/rss", "lang": "en", "priority": 1},
    {"name": "The National", "url": "https://www.thenationalnews.com/rss", "lang": "en", "priority": 1},
    {"name": "Arabian Business", "url": "https://www.arabianbusiness.com/rss", "lang": "en", "priority": 1},
    {"name": "Gulf Business", "url": "https://www.gulfbusiness.com/feed", "lang": "en", "priority": 2},
    
    # Lifestyle и события
    {"name": "What's On Dubai", "url": "https://whatson.ae/feed", "lang": "en", "priority": 2},
    {"name": "Timeout Dubai", "url": "https://www.timeoutdubai.com/feed", "lang": "en", "priority": 2},
    {"name": "Emirates Woman", "url": "https://emirateswoman.com/feed", "lang": "en", "priority": 2},
    
    # Недвижимость и бизнес
    {"name": "PropertyNews.ae", "url": "https://propertynews.ae/feed", "lang": "en", "priority": 2},
    {"name": "Dubai Chronicle", "url": "https://dubaichronicle.com/feed/", "lang": "en", "priority": 2},
    {"name": "The Arabian Post", "url": "https://thearabianpost.com/feed", "lang": "en", "priority": 2},
    
    # Авиация и туризм
    {"name": "Flydubai", "url": "https://content.flydubai.com/feed", "lang": "en", "priority": 2},
    {"name": "Emirates Media Centre", "url": "https://www.emirates.com/media-centre/feed", "lang": "en", "priority": 2},
    {"name": "Dubai Travel Blog", "url": "https://www.dubaitravelblog.com/feed", "lang": "en", "priority": 3},
    
    # Дополнительные
    {"name": "Dubai Flea Market", "url": "https://dubai-fleamarket.com/feed", "lang": "en", "priority": 3},
    {"name": "BusinessLink UAE", "url": "https://businesslinkuae.com/feed", "lang": "en", "priority": 3},
    {"name": "SetHub", "url": "https://sethub.ae/feed", "lang": "en", "priority": 3},
    {"name": "DubayBlog", "url": "http://dubayblog.com/feed/", "lang": "en", "priority": 3},
    {"name": "Elegant Services", "url": "https://elegantservices.ae/blog/feed/", "lang": "en", "priority": 3},
]
GNEWS_SEARCH_QUERIES = [
    "Dubai",
    "UAE",
    "Abu Dhabi",
    "Dubai business",
    "Dubai tourism",
    "Dubai real estate",
    "United Arab Emirates",
]
GNEWS_MAX_SEARCH_CALLS_PER_RUN = max(1, int(os.getenv("GNEWS_MAX_SEARCH_CALLS_PER_RUN", "1")))

TIME_SLOTS = [
    {"name": "Первая половина суток", "range": "00:00-11:59 UTC", "hour_range": (0, 11)},
    {"name": "Вторая половина суток", "range": "12:00-23:59 UTC", "hour_range": (12, 23)},
]

# ========= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =========

def safe_strip(text: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, str):
        return text.strip()
    return str(text).strip()

def safe_get(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def canonicalize_url(url: str) -> str:
    """Канонизирует URL для дедупликации."""
    raw_url = safe_strip(url)
    if not raw_url:
        return ""

    try:
        parsed = urlparse(raw_url)
    except Exception:
        return raw_url

    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/")

    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = (key or "").lower()
        if normalized_key.startswith("utm_") or normalized_key == "fbclid":
            continue
        filtered_query.append((key, value))

    query = urlencode(filtered_query, doseq=True)
    return urlunparse((parsed.scheme, host, path, parsed.params, query, parsed.fragment))

def extract_domain(url: str) -> str:
    raw_url = safe_strip(url)
    if not raw_url:
        return ""
    try:
        return (urlparse(raw_url).netloc or "").lower()
    except Exception:
        return ""

def normalize_text_for_compare(text: str) -> str:
    normalized = re.sub(r"\s+", " ", safe_strip(text))
    return normalized.lower()

def generate_content_hashes(title: str, description: str, url: str) -> tuple[str, str]:
    """Возвращает strict и fuzzy хеши."""
    canonical_url = canonicalize_url(url)
    strict_content = f"{safe_strip(title)}|{safe_strip(description)[:500]}|{canonical_url}"
    fuzzy_content = (
        f"{normalize_text_for_compare(title)}|"
        f"{normalize_text_for_compare(description)[:500]}|"
        f"{canonical_url}"
    )
    strict_hash = hashlib.md5(strict_content.encode()).hexdigest()
    fuzzy_hash = hashlib.md5(fuzzy_content.encode()).hexdigest()
    return strict_hash, fuzzy_hash

def generate_content_hash(title: str, description: str, url: str) -> str:
    """Совместимость: возвращает strict-hash."""
    strict_hash, _ = generate_content_hashes(title, description, url)
    return strict_hash

def cheap_prefilter_news_item(news_item: Dict[str, Any], domain_window: Dict[str, int]) -> tuple[bool, str]:
    """
    Быстрый и дешевый prefilter до вызова DeepSeek:
    - слишком короткий/шумный текст;
    - явные PR/реклама паттерны;
    - повторы домена в рамках текущего запуска.
    """
    title = safe_strip(news_item.get("title"))
    description = safe_strip(news_item.get("description"))
    link = safe_strip(news_item.get("link"))
    text = f"{title}. {description}".strip()
    text_lower = text.lower()

    if len(text) < PREFILTER_MIN_TEXT_LEN:
        return False, f"Cheap prefilter: слишком короткий текст ({len(text)} символов)"

    non_word_chars = len(re.findall(r"[^\w\s]", text, flags=re.UNICODE))
    total_chars = max(1, len(text))
    noise_ratio = non_word_chars / total_chars
    if noise_ratio > PREFILTER_MAX_NOISE_RATIO:
        return False, f"Cheap prefilter: шумный текст (ratio={noise_ratio:.2f})"

    ad_patterns = (
        "press release",
        "sponsored",
        "advertisement",
        "promo code",
        "book now",
        "buy now",
        "limited time offer",
        "partner content",
        "pr newswire",
        "business wire",
        "globenewswire",
    )
    if any(pattern in text_lower for pattern in ad_patterns):
        return False, "Cheap prefilter: распознан рекламный/PR паттерн"

    domain = extract_domain(link)
    if domain:
        domain_count = domain_window.get(domain, 0)
        if domain_count >= PREFILTER_MAX_DOMAIN_REPEATS_PER_RUN:
            return False, f"Cheap prefilter: слишком частый домен в окне ({domain})"
        domain_window[domain] = domain_count + 1

    return True, "Cheap prefilter: ok"

# ========= РАБОТА С ФАЙЛАМИ =========

def get_file_path(key: str) -> str:
    return os.path.join(MOUNTED_BUCKET_PATH, key)

def ensure_directory_exists(file_path: str) -> None:
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

def list_files_in_mounted_bucket() -> List[str]:
    try:
        if os.path.exists(MOUNTED_BUCKET_PATH):
            files = os.listdir(MOUNTED_BUCKET_PATH)
            print(f"📁 Файлы в смонтированном бакете ({MOUNTED_BUCKET_PATH}):")
            for file in files:
                file_path = os.path.join(MOUNTED_BUCKET_PATH, file)
                if os.path.isfile(file_path):
                    size = os.path.getsize(file_path)
                    print(f"   - {file} ({size} bytes)")
            return files
        else:
            print(f"⚠️ Путь {MOUNTED_BUCKET_PATH} не существует")
            return []
    except Exception as e:
        print(f"⚠️ Ошибка при получении списка файлов: {e}")
        return []

def read_json_from_mounted_bucket(key: str, default_value: Any = None) -> Any:
    file_path = get_file_path(key)
    
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"✅ Прочитан файл: {file_path}")
            return data
        else:
            print(f"[INFO] Файл {file_path} не найден")
            return default_value
    except Exception as e:
        print(f"[INFO] Ошибка чтения {file_path}: {e}")
        return default_value

def write_json_to_mounted_bucket(key: str, data: Any) -> bool:
    file_path = get_file_path(key)
    
    try:
        ensure_directory_exists(file_path)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Успешно записано в: {file_path}")
        return True
    except Exception as e:
        print(f"[INFO] Ошибка записи {file_path}: {e}")
        return False

# ========= ИСТОРИЯ ПУБЛИКАЦИЙ =========

def load_published_history() -> List[Dict[str, Any]]:
    """Загружает историю опубликованных постов"""
    history = read_json_from_mounted_bucket(S3_PUBLISHED_HISTORY_KEY, [])
    print(f"Загружено {len(history)} записей истории публикаций")
    return history

def save_published_history(history: List[Dict[str, Any]]) -> bool:
    """Сохраняет историю опубликованных постов с очисткой старых"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=CONTENT_UNIQUE_DAYS)
    
    cleaned_history = []
    for item in history:
        ts_str = item.get("published_at")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts >= cutoff:
                    cleaned_history.append(item)
            except Exception:
                continue
    
    success = write_json_to_mounted_bucket(S3_PUBLISHED_HISTORY_KEY, cleaned_history)
    if success:
        print(f"История публикаций сохранена, записей: {len(cleaned_history)}")
    return success

def is_content_already_published(content_hash: str, history: List[Dict[str, Any]]) -> bool:
    """Проверяет, был ли уже опубликован контент с таким хешем"""
    for item in history:
        if item.get("content_hash") == content_hash:
            return True
    return False

def add_to_published_history(draft: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Добавляет опубликованный пост в историю"""
    history = load_published_history()
    
    title = draft.get("title", "").strip()
    summary = draft.get("summary_ru", "").strip()
    source_urls = draft.get("source_urls", []) or []
    main_url = (source_urls[0] or "").strip() if source_urls else ""
    
    strict_hash, fuzzy_hash = generate_content_hashes(title, summary, main_url)
    
    history.append({
        "content_hash": strict_hash,
        "strict_hash": strict_hash,
        "fuzzy_hash": fuzzy_hash,
        "title": title,
        "source_url": canonicalize_url(main_url),
        "published_at": datetime.now(timezone.utc).isoformat()
    })
    
    return history

# ========= РАБОТА С ЧЕРНОВИКАМИ =========

def filter_old_drafts(drafts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not drafts:
        return []

    now = datetime.now(timezone.utc)
    filtered: List[Dict[str, Any]] = []

    for draft in drafts:
        if draft.get("blocked"):
            continue

        try:
            published_str = draft.get("published", "")
            if not published_str:
                filtered.append(draft)
                continue

            published_date = datetime.fromisoformat(
                published_str.replace("Z", "+00:00")
            )
            age_hours = (now - published_date).total_seconds() / 3600
            if age_hours <= MAX_AGE_HOURS:
                filtered.append(draft)
            else:
                print(f"🗑️ Удаляем старую новость ({age_hours:.1f} ч)")
        except Exception:
            filtered.append(draft)

    return filtered

def load_existing_drafts() -> List[Dict[str, Any]]:
    drafts = read_json_from_mounted_bucket(S3_OBJECT_KEY, [])
    
    if drafts:
        approved_drafts = [draft for draft in drafts if not draft.get("blocked")]
        print(f"📊 Загружено {len(approved_drafts)} одобренных новостей")
        return filter_old_drafts(approved_drafts)
    
    print("📊 Нет сохраненных черновиков")
    return []

def save_drafts(drafts: List[Dict[str, Any]]) -> bool:
    approved_drafts = [draft for draft in drafts if not draft.get("blocked")]
    filtered_drafts = filter_old_drafts(approved_drafts)
    
    if len(filtered_drafts) > MAX_DRAFTS:
        filtered_drafts = filtered_drafts[:MAX_DRAFTS]

    success = write_json_to_mounted_bucket(S3_OBJECT_KEY, filtered_drafts)
    if success:
        print(f"✅ Сохранено {len(filtered_drafts)} одобренных новостей")
    return success

# ========= РАБОТА СО СТАТИСТИКОЙ ИСТОЧНИКОВ =========

def load_source_stats() -> List[Dict[str, Any]]:
    data = read_json_from_mounted_bucket(S3_SOURCE_STATS_KEY, [])
    if not data:
        return []

    now = datetime.now(timezone.utc)
    fresh_stats: List[Dict[str, Any]] = []

    for item in data:
        ts_str = item.get("timestamp")
        if not ts_str:
            fresh_stats.append(item)
            continue

        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_days = (now - ts).total_seconds() / 86400
            if age_days <= MAX_SOURCE_STATS_DAYS:
                fresh_stats.append(item)
            else:
                print(f"🗑️ Удаляем старую запись source_stats ({age_days:.1f} дн)")
        except Exception:
            fresh_stats.append(item)

    if len(fresh_stats) != len(data):
        save_source_stats(fresh_stats)

    return fresh_stats

def save_source_stats(stats: List[Dict[str, Any]]) -> bool:
    return write_json_to_mounted_bucket(S3_SOURCE_STATS_KEY, stats)

def append_run_report(report: Dict[str, Any], max_entries: int = 200) -> bool:
    existing = read_json_from_mounted_bucket(S3_RUN_REPORT_KEY, [])
    if not isinstance(existing, list):
        existing = []

    existing.append(report)
    if len(existing) > max_entries:
        existing = existing[-max_entries:]

    return write_json_to_mounted_bucket(S3_RUN_REPORT_KEY, existing)

def add_url_to_source_stats(
    url: str,
    status: str = "processed",
    content_hash: str = "",
    reason: str = "",
    fuzzy_hash: str = "",
) -> None:
    """
    Добавляет URL в source_stats с явным указанием статуса.
    status может быть: "published", "rejected", "processed", "approved", "error"
    """
    canonical_url = canonicalize_url(url)
    if not canonical_url:
        return

    stats = load_source_stats()
    
    # Проверяем, есть ли уже такой URL или хеш
    for item in stats:
        item_url = canonicalize_url(item.get("url", ""))
        item_strict = item.get("strict_hash") or item.get("content_hash")
        item_fuzzy = item.get("fuzzy_hash")
        if (
            item_url == canonical_url
            or (content_hash and item_strict == content_hash)
            or (fuzzy_hash and item_fuzzy == fuzzy_hash)
        ):
            # Обновляем существующую запись
            item["timestamp"] = datetime.now(timezone.utc).isoformat()
            item["status"] = status
            if reason:
                item["reason"] = reason
            if content_hash:
                item["content_hash"] = content_hash
                item["strict_hash"] = content_hash
            if fuzzy_hash:
                item["fuzzy_hash"] = fuzzy_hash
            save_source_stats(stats)
            return

    # Создаём новую запись
    new_entry = {
        "url": canonical_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,  # Явно указываем статус
    }
    
    if content_hash:
        new_entry["content_hash"] = content_hash
        new_entry["strict_hash"] = content_hash
    if fuzzy_hash:
        new_entry["fuzzy_hash"] = fuzzy_hash
    if reason:
        new_entry["reason"] = reason
    
    stats.append(new_entry)
    save_source_stats(stats)
    
    status_emoji = "✅" if status in ["published", "approved"] else "🚫" if status == "rejected" else "📝"
    print(f"{status_emoji} Записано в source_stats: {status}")

# ========= ПРОВЕРКИ =========

def is_about_dubai(text: str) -> bool:
    if not text:
        return False
    
    text_lower = text.lower()
    
    dubai_keywords = [
        'dubai', 'uae', 'united arab emirates', 'emirati',
        'abu dhabi', 'sharjah', 'ajman', 'ras al khaimah',
        'fujairah', 'umm al quwain', 'gcc', 'gulf',
        'burj', 'palm jumeirah', 'dubai mall', 'dubai metro',
        'emaar', 'nakheel', 'dp world', 'emirates airline',
        'dubai ruler', 'sheikh mohammed', 'maktoum',
        'dubai tourism', 'dubai real estate', 'dubai property',
        'dxb', 'dwc', 'expo dubai', 'dubai international'
    ]
    
    for keyword in dubai_keywords:
        if keyword in text_lower:
            return True
    
    return False

def check_dubai_relevance(title: str, description: str) -> tuple[bool, str]:
    text = f"{title} {description}"
    
    if not is_about_dubai(text):
        return False, "Не содержит упоминаний Дубай/ОАЭ"
    
    return True, "Содержит тематику Дубай/ОАЭ"

def build_seen_links(existing_drafts: List[Dict[str, Any]]) -> tuple[set, set]:
    """Возвращает множество URL и множество хешей контента"""
    seen_urls = set()
    seen_hashes = set()

    for draft in existing_drafts:
        for url in draft.get("source_urls", []):
            if url:
                seen_urls.add(canonicalize_url(url))
        
        # Добавляем хеш контента
        title = draft.get("title", "")
        summary = draft.get("summary_ru", "")
        source_urls = draft.get("source_urls", []) or []
        main_url = (source_urls[0] or "").strip() if source_urls else ""
        strict_hash, fuzzy_hash = generate_content_hashes(title, summary, main_url)
        seen_hashes.add(strict_hash)
        seen_hashes.add(fuzzy_hash)

    # Загружаем статистику источников
    stats = load_source_stats()
    for item in stats:
        url = item.get("url")
        if url:
            seen_urls.add(canonicalize_url(url))
        strict_hash = item.get("strict_hash") or item.get("content_hash")
        fuzzy_hash = item.get("fuzzy_hash")
        if strict_hash:
            seen_hashes.add(strict_hash)
        if fuzzy_hash:
            seen_hashes.add(fuzzy_hash)
    
    # Добавляем историю публикаций
    history = load_published_history()
    for item in history:
        strict_hash = item.get("strict_hash") or item.get("content_hash")
        fuzzy_hash = item.get("fuzzy_hash")
        if strict_hash:
            seen_hashes.add(strict_hash)
        if fuzzy_hash:
            seen_hashes.add(fuzzy_hash)

    print(f"🔁 Всего обработанных URL: {len(seen_urls)}")
    print(f"🔁 Всего уникальных хешей контента: {len(seen_hashes)}")
    return seen_urls, seen_hashes

def is_news_already_processed(link: str, strict_hash: str, fuzzy_hash: str, seen_urls: set, seen_hashes: set) -> bool:
    """Проверяет, была ли новость уже обработана по URL или хешу контента"""
    canonical_link = canonicalize_url(link)
    if canonical_link in seen_urls:
        print(f"📌 URL уже обработан: {canonical_link[:60]}...")
        return True
    
    if strict_hash and strict_hash in seen_hashes:
        print(f"📌 Контент уже обработан (strict: {strict_hash[:8]}...)")
        return True

    if fuzzy_hash and fuzzy_hash in seen_hashes:
        print(f"📌 Контент уже обработан (fuzzy: {fuzzy_hash[:8]}...)")
        return True
    
    return False

def clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def is_too_old(published_iso: str, max_days: int = MAX_NEWS_AGE_DAYS) -> bool:
    if not published_iso:
        return False
    try:
        dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
    except Exception:
        return False

    now = datetime.now(timezone.utc)
    age_days = (now - dt).total_seconds() / 86400
    if age_days > max_days:
        print(f"⏰ Новость слишком старая ({age_days:.1f} дн)")
        return True
    return False

def is_rss_entry_too_old(entry, max_days: int = MAX_NEWS_AGE_DAYS) -> bool:
    date_str = (
        safe_get(entry, "published") or
        safe_get(entry, "updated") or
        safe_get(entry, "pubDate") or ""
    )
    if not date_str:
        return False

    try:
        parsed = getattr(entry, "published_parsed", None) or getattr(
            entry, "updated_parsed", None
        )
        if parsed:
            dt = datetime(*parsed[:6], tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        return False

    now = datetime.now(timezone.utc)
    age_days = (now - dt).total_seconds() / 86400
    if age_days > max_days:
        print(f"⏰ Старая RSS-новость ({age_days:.1f} дн)")
        return True
    return False

# ========= GNEWS.IO =========

def fetch_full_article_content(url: str) -> Optional[str]:
    try:
        headers = {"User-Agent": USER_AGENT}
        resp = request_with_retry("GET", url, headers=headers, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            text = clean_html(resp.text)
            return text[:3000]
        else:
            print(f"⚠️ Не удалось получить полный текст, статус {resp.status_code}")
    except Exception as e:
        print(f"⚠️ Не удалось получить полный текст статьи: {e}")
    return None

def build_gnews_query_plan() -> List[str]:
    """
    Возвращает детерминированный план запросов к GNews:
    - 1-й запрос всегда максимально релевантный ("Dubai");
    - последующие — стабильная ротация по дню для разнообразия.
    """
    if not GNEWS_SEARCH_QUERIES:
        return []

    now = datetime.now(timezone.utc)
    day_seed = now.toordinal()

    primary_query = GNEWS_SEARCH_QUERIES[0]
    secondary_queries = GNEWS_SEARCH_QUERIES[1:]
    if not secondary_queries:
        return [primary_query]

    rotation_offset = day_seed % len(secondary_queries)
    rotated_secondary = secondary_queries[rotation_offset:] + secondary_queries[:rotation_offset]
    return [primary_query] + rotated_secondary

def fetch_news_from_gnews(api_key: str, query: str) -> Optional[List[Dict[str, Any]]]:
    if not api_key:
        return None

    params = {
        "q": query,
        "lang": "en",
        "country": "ae",
        "max": 20,
        "apikey": api_key,
        "from": (datetime.now(timezone.utc) - timedelta(days=MAX_NEWS_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    
    try:
        print(f"🔍 GNews.io поиск: '{query}' в ОАЭ")
        print(f"🔑 Ключ: {api_key[:8]}...")
        
        resp = request_with_retry("GET", 
            "https://gnews.io/api/v4/search",
            params=params,
            timeout=HTTP_TIMEOUT,
        )
        
        if resp.status_code == 200:
            data = resp.json()
            articles = data.get("articles", [])
            total = data.get("totalArticles", 0)
            
            print(f"✅ Найдено статей: {len(articles)} (всего: {total})")
            
            if articles:
                for i, article in enumerate(articles[:3]):
                    title = article.get("title", "Без заголовка")[:60]
                    source = article.get("source", {}).get("name", "Неизвестно")
                    published = article.get("publishedAt", "")[:10]
                    print(f" {i+1}. [{published}] {source}: {title}...")
            
            return articles
        else:
            print(f"⚠️ GNews.io ошибка HTTP: {resp.status_code}")
            
    except Exception as e:
        print(f"⚠️ Ошибка запроса к GNews.io: {e}")
    
    return None

def process_gnews_article(article: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not article:
        return None
    
    title = safe_strip(article.get("title"))
    link = safe_strip(article.get("url"))
    
    if not title or not link:
        return None
    
    source = safe_strip(article.get("source", {}).get("name", "Unknown"))
    published_at = safe_strip(article.get("publishedAt", ""))

    print(f"✅ Найдена новость: {title[:80]}...")
    print(f"📍 Источник: {source}")
    print(f"📅 Дата: {published_at[:10]}")

    content = safe_strip(article.get("content"))
    description = safe_strip(article.get("description"))
    
    if content:
        full_content = content
    elif description:
        full_content = description
    else:
        full_content = fetch_full_article_content(link)
        if not full_content:
            full_content = title
    
    print(f"📖 Текст: {len(full_content)} символов")

    relevant, reason = check_dubai_relevance(title, full_content)
    if not relevant:
        print(f"🚫 Не про Дубай: {reason}")
        return None

    strict_hash, fuzzy_hash = generate_content_hashes(title, full_content[:500], link)

    return {
        "title": title,
        "link": link,
        "canonical_url": canonicalize_url(link),
        "source": source,
        "description": full_content,
        "lang": "en",
        "content_hash": strict_hash,
        "strict_hash": strict_hash,
        "fuzzy_hash": fuzzy_hash,
        "method": "gnews",
        "raw_article": article,
        "published_at": published_at,
    }

def get_time_slot_gnews_key() -> str:
    current_hour = datetime.now(timezone.utc).hour
    time_half = 0 if current_hour < 12 else 1
    
    available_keys = []
    if GNEWS_API_KEY:
        available_keys.append(GNEWS_API_KEY)
    if GNEWS_API_KEY_BACKUP:
        available_keys.append(GNEWS_API_KEY_BACKUP)
    
    print(f"\n📋 РАСПРЕДЕЛЕНИЕ КЛЮЧЕЙ GNEWS:")
    print(f"🕐 Текущее время UTC: {current_hour}:00")
    print(f"📊 Половина суток: {time_half + 1}/2 ({TIME_SLOTS[time_half]['range']})")
    
    if len(available_keys) >= 2:
        if time_half < len(available_keys):
            selected_key = available_keys[time_half]
            print(f"🔑 Выбран строго назначенный ключ #{time_half + 1}")
            return selected_key
        else:
            selected_key = available_keys[-1]
            return selected_key
    elif len(available_keys) == 1:
        return available_keys[0]
    else:
        return ""

def try_gnews_search_with_rotation(
    existing_drafts: List[Dict[str, Any]], 
    seen_urls: set,
    seen_hashes: set
) -> Optional[Dict[str, Any]]:
    del existing_drafts

    api_key = get_time_slot_gnews_key()
    if not api_key:
        print("⚠️ Нет доступных ключей GNews.io")
        return None

    query_plan = build_gnews_query_plan()
    max_calls = min(GNEWS_MAX_SEARCH_CALLS_PER_RUN, len(query_plan))

    print(f"📉 GNews cost-control: максимум {max_calls} API вызов(ов) за запуск")

    for query in query_plan[:max_calls]:
        articles = fetch_news_from_gnews(api_key, query)
        if not articles:
            continue

        articles = sorted(
            articles,
            key=lambda x: safe_strip(x.get("publishedAt", "")),
            reverse=True
        )

        for article in articles[:15]:
            link = safe_strip(article.get("url", ""))
            if not link:
                continue

            published_at = safe_strip(article.get("publishedAt", ""))
            if is_too_old(published_at):
                continue

            title = safe_strip(article.get("title", ""))
            if not is_about_dubai(title):
                continue

            processed = process_gnews_article(article)
            if not processed:
                continue

            # Проверяем на дубликаты
            if is_news_already_processed(
                link,
                processed.get("strict_hash", processed.get("content_hash", "")),
                processed.get("fuzzy_hash", ""),
                seen_urls,
                seen_hashes,
            ):
                continue

            print(f"🎉 Найдена новая новость через GNews.io")
            return processed
    
    return None

# ========= RSS =========

def extract_image_from_rss_entry(entry) -> Optional[str]:
    media_content = getattr(entry, "media_content", None)
    if media_content and isinstance(media_content, list):
        for mc in media_content:
            url = mc.get("url")
            if url and url.startswith("http"):
                return url

    enclosures = getattr(entry, "enclosures", None)
    if enclosures and isinstance(enclosures, list):
        for enc in enclosures:
            url = enc.get("href") or enc.get("url")
            if url and url.startswith("http"):
                return url

    image = getattr(entry, "image", None)
    if isinstance(image, dict):
        url = image.get("href") or image.get("url")
        if url and url.startswith("http"):
            return url

    return None

def extract_full_content_from_rss(entry) -> str:
    content_fields = [
        safe_get(entry.get("content", [{}])[0], "value", ""),
        safe_get(entry, "summary", ""),
        safe_get(entry, "description", ""),
        safe_get(entry, "title", ""),
    ]

    for content in content_fields:
        if content and len(content) > 20:
            clean_content = clean_html(content)
            return clean_content

    return safe_strip(safe_get(entry, "title", ""))

def try_rss_feeds(
    existing_drafts: List[Dict[str, Any]], 
    seen_urls: set,
    seen_hashes: set
) -> Optional[Dict[str, Any]]:
    print("📡 Поиск через UAE RSS ленты...")
    
    rss_feeds = DUBAI_SPECIFIC_RSS_FEEDS
    rss_feeds = sorted(rss_feeds, key=lambda x: x.get("priority", 99))
    
    print(f"📰 Доступно RSS лент: {len(rss_feeds)}")
    
    for feed_conf in rss_feeds:
        print(f"\n📡 Пробуем: {feed_conf['name']}")
        
        try:
            resp = request_with_retry("GET", 
                feed_conf["url"],
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                print(f" ⚠️ Статус: {resp.status_code}")
                continue

            feed = feedparser.parse(resp.content)
            if not feed.entries:
                print(" ⚠️ Нет новостей")
                continue

            print(f" ✅ Новостей: {len(feed.entries)}")
            
            for entry in feed.entries[:10]:
                title = safe_strip(safe_get(entry, "title"))
                link = safe_strip(safe_get(entry, "link"))
                
                if not title or not link:
                    continue
                
                if is_rss_entry_too_old(entry):
                    continue
                
                content = extract_full_content_from_rss(entry)
                relevant, reason = check_dubai_relevance(title, content)
                if not relevant:
                    continue
                
                strict_hash, fuzzy_hash = generate_content_hashes(title, content[:500], link)
                
                if is_news_already_processed(link, strict_hash, fuzzy_hash, seen_urls, seen_hashes):
                    continue
                
                print(f" ✅ Найдена новая RSS-новость: {title[:60]}...")
                
                return {
                    "title": title,
                    "link": link,
                    "canonical_url": canonicalize_url(link),
                    "source": feed_conf["name"],
                    "description": content,
                    "lang": feed_conf.get("lang", "en"),
                    "content_hash": strict_hash,
                    "strict_hash": strict_hash,
                    "fuzzy_hash": fuzzy_hash,
                    "method": "rss",
                    "rss_entry": entry,
                }

        except Exception as e:
            print(f" ⚠️ Ошибка: {e}")
    
    return None

# ========= DEEPSEEK =========

def is_news_allowed_by_deepseek(title: str, description: str, content_hash: str, existing_titles: List[str] = None) -> tuple[bool, str, bool]:
    """
    Проверяет новость через DeepSeek с учетом проверки на дубликаты
    """
    if not DEEPSEEK_API_KEY:
        return True, "DEEPSEEK_API_KEY не задан, фильтр пропущен", False

    try:
        safe_description = (description or "")[:2000]
        
        existing_context = ""
        if existing_titles and len(existing_titles) > 0:
            recent_titles = existing_titles[:10]
            existing_context = "\n\nКонтекст (последние опубликованные новости для проверки на дубликаты):\n"
            for i, existing_title in enumerate(recent_titles, 1):
                if existing_title:
                    existing_context += f"{i}. {existing_title}\n"
        
        prompt = f"""Ты строгий редактор новостного телеграм-канала о Дубае.
Твоя задача — решить, можно ли публиковать новость по семи правилам:

1) Связь с регионом. Есть ли явная связь с Дубаем/ОАЭ/GCC? Если нет — ОТКЛОНИТЬ.

2) Политика и конфликты. Является ли основная тема большой политикой, войной, протестами? Если да — ОТКЛОНИТЬ.

3) Рекламный характер. Выглядит ли текст как реклама/пресс-релиз без общественно значимой новости? Если да — ОТКЛОНИТЬ.

4) Уважение к ценностям UAE. Содержит ли текст темы, которые могут быть восприняты как неуважение к ОАЭ? Если да — ОТКЛОНИТЬ.

5) Проверь естественность и достоверность текста — ОТКЛОНИТЬ материалы с признаками ИИ‑генерации, кликбейта.

6) Интерес для русскоговорящего жителя Дубая. Будет ли новость интересна или полезна? Если нет — ОТКЛОНИТЬ.

7) ДУБЛИКАТЫ И ПОВТОРЫ (ВАЖНО!). Проверь, не является ли эта новость точной копией или очень похожей на уже опубликованные новости.

{existing_context}

Новость для проверки:
Заголовок: {title}
Текст новости:
{safe_description}

Хеш контента для проверки дубликатов: {content_hash}

Сначала кратко ОЦЕНИ НОВОСТЬ в 1-2 предложениях на русском и укажи, почему она подходит или не подходит.

В конце на НОВОЙ строке дай окончательное решение В ОДНОМ СЛОВЕ (заглавными буквами):
- ПУБЛИКОВАТЬ
- ОТКЛОНИТЬ
"""
        response = request_with_retry("POST", 
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты строгий редактор новостного канала о Дубае, который тщательно проверяет новости на дубликаты.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 300,
                "temperature": 0.0,
            },
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            print(f"⚠️ DeepSeek editor HTTP {response.status_code}")
            return False, f"DeepSeek editor HTTP ошибка {response.status_code}", True

        result = response.json()
        content = result["choices"][0]["message"]["content"].strip()
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        decision = lines[-1].upper() if lines else ""
        explanation = "\n".join(lines[:-1]) if len(lines) > 1 else content

        print("🧐 DeepSeek editor:")
        print(explanation)
        print(f"Решение: {decision}")

        if "ОТКЛОНИТЬ" in decision:
            return False, explanation or "Новость отклонена редактором", False

        return True, explanation or "Новость одобрена редактором", False

    except Exception as e:
        print(f"⚠️ DeepSeek editor error: {e}")
        return False, f"Технический сбой: {str(e)}", True

def process_with_deepseek_simple(title: str, description: str) -> str:
    if not DEEPSEEK_API_KEY:
        return f"**{title}**\n\nНовость о Дубае. Подробности по ссылке."

    try:
        title = safe_strip(title)
        description = safe_strip(description)
        
        if not title:
            title = "Новость без заголовка"
        if not description:
            description = title
            
        safe_description = description[:2000]
        
        prompt = f"""Напиши короткую новость для телеграм-канала на русском языке.
Заголовок: {title}
Текст новости:
{safe_description}

ПРАВИЛА:
1. Перевести содержание на русский.
2. Сохрани ВСЕ факты, цифры, локации, имена, даты
3. Переведи заголовок и оберни в <b>...</b> в начало добавь уместные эмодзи
4. Перескажи текст своими словами, но БЕЗ изменений фактов
5. Убедитесь, что текст звучит естественно, разнообразен по длине предложений и включает естественные переходы.
6. Применяй смайлики и эмодзи, а также <blockquote>цитата</blockquote> но исключительно в подходящих случаях
7. Если есть список элементов - сохрани его полностью 
8. Не добавлять ссылку на источник — она будет добавлена отдельно
9. Объем: 300-450 символов
10. Добавь 2-3 хэштега
"""
        response = request_with_retry("POST", 
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты пишешь короткие новости для телеграм-канала.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 400,
                "temperature": 0.7,
            },
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()
        else:
            print(f"⚠️ DeepSeek ошибка: {response.status_code}")
            return f"**{title}**\n\nНовость о Дубае. Подробности по ссылке."
    except Exception as e:
        print(f"⚠️ DeepSeek ошибка: {e}")
        return f"**{title}**\n\nНовость о Дубае. Подробности по ссылке."

def can_call_deepseek(deepseek_context: Dict[str, int]) -> bool:
    return deepseek_context.get("calls_made", 0) < deepseek_context.get("max_calls", MAX_DEEPSEEK_CALLS_PER_RUN)

def consume_deepseek_call(deepseek_context: Dict[str, int], reason: str) -> bool:
    if not can_call_deepseek(deepseek_context):
        print(f"⛔ DeepSeek budget exhausted, skip: {reason}")
        return False
    deepseek_context["calls_made"] = deepseek_context.get("calls_made", 0) + 1
    print(
        f"💸 DeepSeek call {deepseek_context['calls_made']}/{deepseek_context.get('max_calls', MAX_DEEPSEEK_CALLS_PER_RUN)}: {reason}"
    )
    return True

# ========= ПОЛУЧЕНИЕ КАРТИНКИ =========

def fetch_image_from_html(url: str) -> Optional[str]:
    try:
        headers = {"User-Agent": USER_AGENT}
        resp = request_with_retry("GET", url, headers=headers, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return None

        html = resp.text
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if m:
            return m.group(1)
    except Exception as e:
        print(f"⚠️ Ошибка получения og:image: {e}")
    return None

def fetch_image_for_news(news_item: Dict[str, Any]) -> Optional[str]:
    if not news_item:
        return None
        
    method = news_item.get("method", "")

    if method == "gnews":
        raw = news_item.get("raw_article") or {}
        img = raw.get("image")
        if img and isinstance(img, str) and img.startswith("http"):
            return img

    if method == "rss":
        entry = news_item.get("rss_entry")
        if entry is not None:
            img = extract_image_from_rss_entry(entry)
            if img:
                return img

    link = news_item.get("link")
    if link:
        return fetch_image_from_html(link)
    
    return None

# ========= ОБРАБОТКА НОВОСТИ =========

def mark_news_as_rejected(news_item: Dict[str, Any], reason: str) -> None:
    """Записывает URL и хеш в source_stats при отклонении с явным статусом 'rejected'"""
    link = news_item.get("link", "")
    strict_hash = news_item.get("strict_hash", news_item.get("content_hash", ""))
    fuzzy_hash = news_item.get("fuzzy_hash", "")
    
    if link and not reason.startswith("Технический сбой"):
        add_url_to_source_stats(
            link,
            status="rejected",
            content_hash=strict_hash,
            reason=reason,
            fuzzy_hash=fuzzy_hash,
        )
        print(f"📝 Записана в source_stats как отклоненная: {reason[:50]}...")

def mark_news_as_technical_error(news_item: Dict[str, Any], error: str) -> None:
    """Записывает URL в source_stats при технической ошибке"""
    link = news_item.get("link", "")
    strict_hash = news_item.get("strict_hash", news_item.get("content_hash", ""))
    fuzzy_hash = news_item.get("fuzzy_hash", "")
    
    if link:
        add_url_to_source_stats(
            link,
            status="error",
            content_hash=strict_hash,
            reason=error,
            fuzzy_hash=fuzzy_hash,
        )

def process_news_item(
    news_item: Dict[str, Any],
    existing_titles: List[str] = None,
    seen_hashes: set = None,
    deepseek_context: Dict[str, int] = None,
    domain_window: Dict[str, int] = None,
) -> Optional[Dict[str, Any]]:
    if not news_item:
        return None
    
    title = safe_strip(news_item.get("title"))
    description = safe_strip(news_item.get("description"))
    strict_hash = news_item.get("strict_hash", news_item.get("content_hash", ""))
    fuzzy_hash = news_item.get("fuzzy_hash", "")
    
    if not title:
        return None
    
    if not description:
        description = title

    if ENABLE_CHEAP_PREFILTER:
        prefilter_window = domain_window if domain_window is not None else {}
        allowed_by_prefilter, prefilter_reason = cheap_prefilter_news_item(news_item, prefilter_window)
        if not allowed_by_prefilter:
            print(f"🚫 {prefilter_reason}: {title[:80]}...")
            mark_news_as_rejected(news_item, prefilter_reason)
            return None
    
    # Проверяем на дубликаты по хешу еще раз
    if seen_hashes and ((strict_hash and strict_hash in seen_hashes) or (fuzzy_hash and fuzzy_hash in seen_hashes)):
        print(f"🚫 Дубликат по хешу контента: {title[:80]}...")
        mark_news_as_rejected(news_item, "Дубликат по хешу контента")
        return None
    
    if deepseek_context is None:
        deepseek_context = {"calls_made": 0, "max_calls": MAX_DEEPSEEK_CALLS_PER_RUN}

    if not consume_deepseek_call(deepseek_context, "editor validation"):
        mark_news_as_rejected(news_item, "DeepSeek budget exhausted before editor validation")
        return None

    allowed, reason, is_technical_error = is_news_allowed_by_deepseek(title, description, strict_hash or fuzzy_hash, existing_titles)
    
    if not allowed:
        print(f"🚫 Новость отклонена DeepSeek: {title[:80]}...")
        print(f"📝 Причина: {reason[:100]}...")
        
        if is_technical_error:
            mark_news_as_technical_error(news_item, reason)
        else:
            mark_news_as_rejected(news_item, reason)
        
        return None

    print(f"✅ Новость одобрена редактором: {title[:80]}...")

    current_time = datetime.now(timezone.utc).isoformat()
    if consume_deepseek_call(deepseek_context, "summary generation"):
        summary_ru = process_with_deepseek_simple(title, description)
    else:
        summary_ru = f"**{title}**\n\nНовость о Дубае. Подробности по ссылке."

    image_url = fetch_image_for_news(news_item)

    draft: Dict[str, Any] = {
        "source_name": news_item.get("source", "Unknown"),
        "title": title,
        "summary_ru": summary_ru,
        "published": current_time,
        "source_urls": [news_item.get("link", "")],
        "lang": news_item.get("lang", "en"),
        "content_hash": strict_hash,
        "strict_hash": strict_hash,
        "fuzzy_hash": fuzzy_hash,
        "method": news_item.get("method", "unknown"),
    }
    
    if image_url:
        draft["image_url"] = image_url

    return draft

# ========= ОСНОВНОЙ HANDLER =========

def handler(event, context):
    start_time = datetime.now(timezone.utc)
    attempt = 0
    fetched_count = 0
    gnews_fetches = 0
    rss_fetches = 0
    technical_errors = 0
    rejected_in_session = 0
    deepseek_context = {"calls_made": 0, "max_calls": MAX_DEEPSEEK_CALLS_PER_RUN}

    try:
        print("=" * 60)
        print("=== DUBAI NEWS COLLECTOR START ===")
        print("=" * 60)
        print(start_time.isoformat())
        
        if os.path.exists(MOUNTED_BUCKET_PATH):
            print(f"✅ Смонтированный бакет доступен")
        else:
            print(f"❌ Смонтированный бакет НЕ найден")
        
        current_hour = datetime.now(timezone.utc).hour
        time_half = 0 if current_hour < 12 else 1
        
        print(f"\n📊 ВРЕМЯ: {current_hour}:00 UTC, половина {time_half + 1}/2")
        
        existing_drafts = load_existing_drafts()
        existing_titles = [draft.get("title", "") for draft in existing_drafts]
        seen_urls, seen_hashes = build_seen_links(existing_drafts)
        
        print(f"📊 Существующих новостей: {len(existing_drafts)}")
        print(f"📝 Заголовков для проверки: {len(existing_titles)}")
        
        approved_draft = None
        max_attempts = 3
        domain_window: Dict[str, int] = {}

        print(
            f"⚙️ DeepSeek cost control: feature_flag={ENABLE_CHEAP_PREFILTER}, "
            f"max_calls_per_run={MAX_DEEPSEEK_CALLS_PER_RUN}, domain_repeat_limit={PREFILTER_MAX_DOMAIN_REPEATS_PER_RUN}"
        )
        
        for attempt in range(1, max_attempts + 1):
            print(f"\n{'='*50}")
            print(f"🔄 ПОПЫТКА #{attempt}/{max_attempts}")
            print('='*50)
            
            news_item = None
            
            if attempt % 2 == 1:
                print(f"🎯 Стратегия: GNews.io")
                gnews_fetches += 1
                news_item = try_gnews_search_with_rotation(existing_drafts, seen_urls, seen_hashes)
            else:
                print("🎯 Стратегия: RSS")
                rss_fetches += 1
                news_item = try_rss_feeds(existing_drafts, seen_urls, seen_hashes)
            
            if not news_item:
                print(f"⚠️ Не найдено новостей (попытка #{attempt})")
                continue

            fetched_count += 1
            
            processed = process_news_item(
                news_item,
                existing_titles,
                seen_hashes,
                deepseek_context=deepseek_context,
                domain_window=domain_window,
            )
            
            if not processed:
                rejected_in_session += 1
                print(f"🚫 Новость отклонена (всего: {rejected_in_session})")
                
                link = news_item.get("link", "")
                strict_hash = news_item.get("strict_hash", news_item.get("content_hash", ""))
                fuzzy_hash = news_item.get("fuzzy_hash", "")
                if link:
                    seen_urls.add(canonicalize_url(link))
                if strict_hash:
                    seen_hashes.add(strict_hash)
                if fuzzy_hash:
                    seen_hashes.add(fuzzy_hash)
            else:
                approved_draft = processed
                print(f"🎉 НАЙДЕНА ПОДХОДЯЩАЯ НОВОСТЬ!")
                break
        
        if approved_draft:
            # Добавляем в source_stats как опубликованную (status="published")
            add_url_to_source_stats(
                approved_draft["source_urls"][0], 
                status="published",  # Явно указываем статус "published"
                content_hash=approved_draft.get("content_hash", ""),
                reason="approved",
                fuzzy_hash=approved_draft.get("fuzzy_hash", ""),
            )
            
            # Сохраняем в drafts
            existing = load_existing_drafts()
            updated = [approved_draft] + existing
            
            if len(updated) > MAX_DRAFTS:
                updated = updated[:MAX_DRAFTS]
            
            save_drafts(updated)
            
            result = {
                "success": True,
                "total_drafts": len(updated),
                "new_draft": True,
                "source": approved_draft.get("source_name", ""),
                "title": approved_draft.get("title", "")[:50],
                "method": approved_draft.get("method", ""),
                "attempts_made": attempt,
                "rejected_in_session": rejected_in_session,
                "technical_errors": technical_errors,
                "has_image": "image_url" in approved_draft,
                "deepseek_calls_made": deepseek_context["calls_made"],
                "deepseek_calls_max": deepseek_context["max_calls"],
                "cheap_prefilter_enabled": ENABLE_CHEAP_PREFILTER,
            }
            
        else:
            result = {
                "success": False,
                "total_drafts": len(existing_drafts),
                "new_draft": False,
                "message": f"Не найдено новостей после {max_attempts} попыток",
                "attempts_made": max_attempts,
                "rejected_in_session": rejected_in_session,
                "technical_errors": technical_errors,
                "deepseek_calls_made": deepseek_context["calls_made"],
                "deepseek_calls_max": deepseek_context["max_calls"],
                "cheap_prefilter_enabled": ENABLE_CHEAP_PREFILTER,
            }
        
        print("\n" + "=" * 60)
        print("=== DUBAI NEWS COLLECTOR END ===")
        print("=" * 60)

        end_time = datetime.now(timezone.utc)
        run_report = {
            "script": "rss_collect",
            "started_at": start_time.isoformat(),
            "finished_at": end_time.isoformat(),
            "duration_seconds": round((end_time - start_time).total_seconds(), 3),
            "success": bool(result.get("success")),
            "counters": {
                "attempts_made": result.get("attempts_made", 0),
                "fetched": fetched_count,
                "rejected": rejected_in_session,
                "approved": 1 if approved_draft else 0,
                "deepseek_calls": deepseek_context["calls_made"],
                "deepseek_calls_max": deepseek_context["max_calls"],
                "gnews_fetches": gnews_fetches,
                "rss_fetches": rss_fetches,
                "telegram_failures": 0,
                "technical_errors": technical_errors,
            },
        }
        append_run_report(run_report)
        
        return {
            "statusCode": 200,
            "body": json.dumps(result, ensure_ascii=False, indent=2),
            "headers": {"Content-Type": "application/json"},
        }

    except Exception as e:
        print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

        end_time = datetime.now(timezone.utc)
        run_report = {
            "script": "rss_collect",
            "started_at": start_time.isoformat(),
            "finished_at": end_time.isoformat(),
            "duration_seconds": round((end_time - start_time).total_seconds(), 3),
            "success": False,
            "error": str(e),
            "counters": {
                "attempts_made": attempt,
                "fetched": fetched_count,
                "rejected": rejected_in_session,
                "approved": 0,
                "deepseek_calls": deepseek_context["calls_made"],
                "deepseek_calls_max": deepseek_context["max_calls"],
                "gnews_fetches": gnews_fetches,
                "rss_fetches": rss_fetches,
                "telegram_failures": 0,
                "technical_errors": technical_errors,
            },
        }
        append_run_report(run_report)
        
        return {
            "statusCode": 500,
            "body": json.dumps({"success": False, "error": str(e)}),
            "headers": {"Content-Type": "application/json"},
        }

if __name__ == "__main__":
    print("🚀 Запуск Dubai News Collector локально...")
    MOUNTED_BUCKET_PATH = "./storage/dubai_news"
    os.makedirs(MOUNTED_BUCKET_PATH, exist_ok=True)
    
    result = handler({}, None)
    if isinstance(result, dict) and "body" in result:
        try:
            body = json.loads(result["body"])
            print(f"\n📋 Результат:")
            print(json.dumps(body, indent=2, ensure_ascii=False))
        except:
            print(f"\n📋 Результат: {result}")
