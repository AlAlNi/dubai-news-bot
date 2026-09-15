import os
import json
from pathlib import Path
from typing import Tuple, Optional, List, Any, Set
from datetime import datetime, timezone, timedelta
import hashlib
import re
import requests
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from http_client import request_with_retry
from source_verification import is_verified_draft

# ================== НАСТРОЙКИ ==================

# Определяем путь к хранилищу в зависимости от окружения
if os.getenv("GITHUB_ACTIONS") == "true":
    # В GitHub Actions используем папку в репозитории
    MOUNTED_BUCKET_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "dubai_news")
else:
    # Локальная разработка
    MOUNTED_BUCKET_PATH = "./storage/dubai_news"

# Создаем директорию, если её нет
os.makedirs(MOUNTED_BUCKET_PATH, exist_ok=True)

DRAFTS_FILENAME = "drafts.json"
SOURCE_STATS_FILENAME = "source_stats.json"
PUBLISHED_HISTORY_FILENAME = "published_history.json"

DRAFTS_CACHE_PATH = Path("/tmp/drafts.json")

def env_int(name: str, default: int, min_value: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"⚠️ Некорректное значение {name}={raw!r}, используем default={default}")
        return default
    if value < min_value:
        print(f"⚠️ {name}={value} меньше минимума {min_value}, используем {min_value}")
        return min_value
    return value

SOURCE_WINDOW_HOURS = env_int("SOURCE_WINDOW_HOURS", 48)
DRAFT_MAX_AGE_DAYS = env_int("DRAFT_MAX_AGE_DAYS", 7)
CONTENT_UNIQUE_DAYS = env_int("CONTENT_UNIQUE_DAYS", 30)  # Сколько дней хранить историю опубликованного контента

BLOCKED_PUBLICATION_IMAGE_URLS = {
    "https://lh3.googleusercontent.com/J6_coFbogxhRI9iM864NL_liGXvsQp2AupsKei7z0cNNfDvGUmWUy20nuUhkREQyrpY4bEeIBuc=s0-w300",
}

def canonicalize_url(url: str) -> str:
    raw_url = (url or "").strip()
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
    return urlunparse((parsed.scheme, host, path, parsed.params, query, ""))

def normalize_text_for_compare(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()

def generate_content_hashes(title: str, summary: str, url: str) -> tuple[str, str]:
    canonical_url = canonicalize_url(url)
    strict_source = f"{(title or '').strip()}|{(summary or '').strip()}|{canonical_url}"
    fuzzy_source = (
        f"{normalize_text_for_compare(title)}|"
        f"{normalize_text_for_compare(summary)}|"
        f"{canonical_url}"
    )
    return (
        hashlib.md5(strict_source.encode()).hexdigest(),
        hashlib.md5(fuzzy_source.encode()).hexdigest(),
    )

# ================== ПРОВЕРКА ИЗОБРАЖЕНИЙ ==================

def is_valid_image_url(url: str) -> bool:
    """
    Проверяет, ведет ли URL напрямую к изображению.
    Возвращает False, если URL невалидный или ведет на веб-страницу.
    """
    if not url or not isinstance(url, str):
        return False
    
    url = url.strip()
    if not url:
        return False

    if url in BLOCKED_PUBLICATION_IMAGE_URLS:
        print("⚠️ URL изображения в списке блокировки, публикуем пост без картинки")
        return False
    
    # Проверка расширения файла (быстрая предварительная проверка)
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.jfif')
    url_lower = url.lower()
    
    # Если URL заканчивается на известное расширение изображения
    if any(url_lower.endswith(ext) for ext in image_extensions):
        print(f"✓ URL имеет корректное расширение изображения")
        return True
    
    # Если расширение не указано, делаем HEAD-запрос для проверки Content-Type
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; TelegramBot/1.0)'
        }
        response = request_with_retry(
            "HEAD",
            url,
            allow_redirects=True,
            timeout=5,
            headers=headers,
        )
        content_type = response.headers.get('content-type', '').lower()
        
        # Проверяем, что это изображение
        if content_type.startswith('image/'):
            print(f"✓ URL ведет на изображение (Content-Type: {content_type})")
            return True
        else:
            print(f"✗ URL ведет на {content_type}, а не на изображение")
            return False
            
    except requests.exceptions.Timeout:
        print(f"⚠️ Таймаут при проверке URL изображения: {url}")
        # В случае таймаута лучше пропустить изображение, чем задерживать публикацию
        return False
    except Exception as e:
        print(f"⚠️ Ошибка при проверке URL изображения {url}: {e}")
        return False

# ================== РАБОТА С ФАЙЛАМИ ==================

def get_file_path(filename: str) -> str:
    return os.path.join(MOUNTED_BUCKET_PATH, filename)

def ensure_directory_exists(file_path: str) -> None:
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

def read_json_from_mounted_bucket(filename: str, default_value: Any = None) -> Any:
    file_path = get_file_path(filename)
    
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

def write_json_to_mounted_bucket(filename: str, data: Any) -> bool:
    file_path = get_file_path(filename)
    
    try:
        ensure_directory_exists(file_path)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Успешно записано в: {file_path}")
        return True
    except Exception as e:
        print(f"[INFO] Ошибка записи {file_path}: {e}")
        return False

# ================== ИСТОРИЯ ПУБЛИКАЦИЙ ==================

def load_published_history() -> List[dict]:
    """Загружает историю опубликованных постов"""
    history = read_json_from_mounted_bucket(PUBLISHED_HISTORY_FILENAME, [])
    print(f"Загружено {len(history)} записей истории публикаций")
    return history

def save_published_history(history: List[dict]) -> bool:
    """Сохраняет историю опубликованных постов"""
    # Очищаем старые записи
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
    
    success = write_json_to_mounted_bucket(PUBLISHED_HISTORY_FILENAME, cleaned_history)
    if success:
        print(f"История публикаций сохранена, записей: {len(cleaned_history)}")
    return success

def is_draft_already_published(draft: dict, history: List[dict]) -> bool:
    """
    Проверяет, не был ли этот пост уже опубликован.
    Сравнивает по заголовку, summary_ru и URL источника.
    """
    title = draft.get("title", "").strip()
    summary = draft.get("summary_ru", "").strip()
    source_urls = draft.get("source_urls", []) or []
    main_url = (source_urls[0] or "").strip() if source_urls else ""
    
    # Создаем хеш контента для более точного сравнения
    strict_hash, fuzzy_hash = generate_content_hashes(title, summary, main_url)
    canonical_main_url = canonicalize_url(main_url)
    
    for published in history:
        # Проверяем по хешу
        published_strict = published.get("strict_hash") or published.get("content_hash")
        published_fuzzy = published.get("fuzzy_hash")
        if published_strict == strict_hash or (published_fuzzy and published_fuzzy == fuzzy_hash):
            return True
        
        # Проверяем по URL источника (как дополнительная защита)
        if canonical_main_url and canonicalize_url(published.get("source_url", "")) == canonical_main_url:
            # Если URL уже использовался, проверяем время
            pub_time = datetime.fromisoformat(published["published_at"].replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            # Если прошло меньше SOURCE_WINDOW_HOURS, считаем что пост уже был
            if now - pub_time < timedelta(hours=SOURCE_WINDOW_HOURS):
                return True
    
    return False

def add_to_published_history(draft: dict) -> List[dict]:
    """
    Добавляет опубликованный пост в историю.
    Сохраняет ВСЮ информацию о посте.
    """
    history = load_published_history()
    
    # Создаем копию драфта для сохранения в историю
    published_entry = draft.copy() if draft else {}
    source_url = (draft.get("source_urls") or [""])[0]
    strict_hash, fuzzy_hash = generate_content_hashes(
        draft.get("title", ""),
        draft.get("summary_ru", ""),
        source_url,
    )
    
    # Добавляем служебные поля
    published_entry.update({
        "published_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": strict_hash,
        "strict_hash": strict_hash,
        "fuzzy_hash": fuzzy_hash,
        "source_url": canonicalize_url(source_url),
    })
    
    # Убеждаемся, что сохраняются все ключевые поля
    # Если каких-то полей нет, добавляем их с пустыми значениями для консистентности
    required_fields = [
        "title", "summary", "summary_ru", "summary_en", 
        "image_url", "source_urls", "source_names", 
        "published", "language", "top_image", "keywords"
    ]
    
    for field in required_fields:
        if field not in published_entry:
            if field in ["source_urls", "source_names", "keywords"]:
                published_entry[field] = []
            elif field in ["image_url", "top_image"]:
                published_entry[field] = ""
            else:
                published_entry[field] = ""
    
    history.append(published_entry)
    print(f"✅ Добавлен пост в историю: {published_entry.get('title', 'Без названия')}")
    print(f"   Сохранены поля: {', '.join(published_entry.keys())}")
    
    return history

def get_published_post_by_hash(content_hash: str) -> Optional[dict]:
    """Получает опубликованный пост по его хешу"""
    history = load_published_history()
    for post in history:
        if post.get("content_hash") == content_hash:
            return post
    return None

def get_published_posts_by_date(date_from: datetime, date_to: datetime = None) -> List[dict]:
    """Получает опубликованные посты за период"""
    if not date_to:
        date_to = datetime.now(timezone.utc)
    
    history = load_published_history()
    result = []
    
    for post in history:
        pub_time_str = post.get("published_at")
        if pub_time_str:
            try:
                pub_time = datetime.fromisoformat(pub_time_str.replace("Z", "+00:00"))
                if date_from <= pub_time <= date_to:
                    result.append(post)
            except Exception:
                continue
    
    return result

# ================== ЧЕРНОВИКИ ==================

def cleanup_old_drafts(drafts: list) -> list:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DRAFT_MAX_AGE_DAYS)
    cleaned: List[dict] = []

    for d in drafts:
        ts_str = d.get("published")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts >= cutoff:
            cleaned.append(d)

    if len(cleaned) < len(drafts):
        print(
            f"cleanup_old_drafts: было {len(drafts)} драфтов, "
            f"оставили {len(cleaned)} (не старше {DRAFT_MAX_AGE_DAYS} дней)"
        )
    return cleaned

def load_drafts() -> list:
    drafts = read_json_from_mounted_bucket(DRAFTS_FILENAME, [])
    
    if drafts:
        drafts = cleanup_old_drafts(drafts)
        
        # Сохраняем копию в /tmp
        try:
            DRAFTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with DRAFTS_CACHE_PATH.open("w", encoding="utf-8") as f:
                json.dump(drafts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Не удалось сохранить кеш в /tmp: {e}")
        
        return drafts
    
    print("📊 Нет сохраненных черновиков")
    return []

def save_drafts(drafts: list) -> bool:
    drafts = cleanup_old_drafts(drafts)
    
    success = write_json_to_mounted_bucket(DRAFTS_FILENAME, drafts)
    
    if success:
        print(f"✅ Сохранено {len(drafts)} черновиков")
        
        # Обновляем кеш в /tmp
        try:
            DRAFTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with DRAFTS_CACHE_PATH.open("w", encoding="utf-8") as f:
                json.dump(drafts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Не удалось обновить кеш в /tmp: {e}")
    
    return success

def remove_draft_by_index(drafts: list, index: int) -> Tuple[Optional[dict], list]:
    """Удаляет черновик по индексу и возвращает его и обновленный список"""
    if not drafts or index >= len(drafts):
        return None, drafts
    
    draft = drafts.pop(index)
    return draft, drafts

# ================== СТАТИСТИКА ИСТОЧНИКОВ ==================

def load_source_stats() -> List[dict]:
    stats = read_json_from_mounted_bucket(SOURCE_STATS_FILENAME, [])
    print(f"Загружено {len(stats)} записей source_stats.json")
    return stats

def save_source_stats(stats: List[dict]) -> bool:
    success = write_json_to_mounted_bucket(SOURCE_STATS_FILENAME, stats)
    if success:
        print(f"source_stats.json сохранён, записей: {len(stats)}")
    return success

def cleanup_source_stats(stats: List[dict]) -> List[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=SOURCE_WINDOW_HOURS)
    cleaned: List[dict] = []
    for item in stats:
        ts_str = item.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts >= cutoff:
            cleaned.append(item)
    return cleaned

def is_source_recent(url: str, stats: List[dict]) -> bool:
    """
    Проверяет, был ли URL источника недавно использован для опубликованного поста.
    Игнорирует записи с полем "reason" (отклонённые) и записи без поля "url".
    """
    canonical_url = canonicalize_url(url)
    if not canonical_url:
        return False
        
    for item in stats:
        # Пропускаем записи без URL
        if "url" not in item:
            continue
            
        # Пропускаем записи с полем "reason" - это отклонённые новости
        if "reason" in item:
            continue
            
        # Также пропускаем записи, где явно указано, что новость отклонена
        if item.get("status") == "rejected":
            continue
            
        # Если нашли совпадение URL, считаем источник недавно использованным
        if canonicalize_url(item.get("url", "")) == canonical_url:
            return True
            
    return False

def add_source_stat(url: str, stats: List[dict]) -> List[dict]:
    """
    Добавляет URL источника в статистику только для опубликованных постов.
    Добавляет флаг status: "published" для ясности.
    """
    canonical_url = canonicalize_url(url)
    if not canonical_url:
        return stats
        
    now = datetime.now(timezone.utc).isoformat()
    stats.append({
        "url": canonical_url,
        "timestamp": now, 
        "status": "published"  # Явно указываем, что пост был опубликован
    })
    return stats

# ================== ФОРМАТИРОВАНИЕ ==================

def format_draft_for_telegram(draft: dict) -> str:
    summary_ru = (draft.get("summary_ru") or "").strip()
    source_urls = draft.get("source_urls", []) or []

    parts: List[str] = []

    if summary_ru:
        parts.append(summary_ru)

    if source_urls:
        main_url = (source_urls[0] or "").strip()
        if main_url:
            parts.append("")
            parts.append(f'<a href="{main_url}">Источник</a>')

    parts.append("")
    parts.append(
        '<a href="https://t.me/rudubainews">Дубай: факты и новости</a>'
    )

    return "\n".join(parts).strip()

# ================== ОСНОВНАЯ ЛОГИКА ==================

def get_next_post_payload_with_image() -> Tuple[Optional[str], Optional[str], Optional[dict]]:
    print(f"\n📁 Работаем со смонтированным бакетом: {MOUNTED_BUCKET_PATH}")
    
    if not os.path.exists(MOUNTED_BUCKET_PATH):
        print(f"❌ Смонтированный бакет не найден по пути: {MOUNTED_BUCKET_PATH}")
        return None, None, None
    
    drafts = load_drafts()
    if not drafts:
        print("Черновики не найдены")
        return None, None, None

    print(f"Загружено {len(drafts)} черновиков")

    # Загружаем статистику источников
    source_stats = load_source_stats()
    source_stats = cleanup_source_stats(source_stats)
    
    # Загружаем историю публикаций
    published_history = load_published_history()

    draft: Optional[dict] = None
    draft_index = -1
    updated_drafts = drafts.copy()

    # Ищем подходящий черновик
    for i, candidate in enumerate(updated_drafts):
        if not is_verified_draft(candidate):
            print("⏸️ Черновик ожидает проверки соответствия источнику")
            continue
        source_urls = candidate.get("source_urls") or []
        main_url = (source_urls[0] or "").strip() if source_urls else ""

        # Проверка 1: источник не должен быть недавно использован
        if main_url and is_source_recent(main_url, source_stats):
            print(f"⏭️ Пропускаем: источник {main_url} уже использован недавно для публикации")
            continue
        
        # Проверка 2: контент не должен быть уже опубликован
        if is_draft_already_published(candidate, published_history):
            print(f"⏭️ Пропускаем: этот пост уже был опубликован ранее")
            # Удаляем дубликат из черновиков
            draft, updated_drafts = remove_draft_by_index(updated_drafts, i)
            save_drafts(updated_drafts)
            print(f"✅ Удален дубликат из черновиков")
            # Начинаем поиск заново с обновленным списком
            return get_next_post_payload_with_image()

        candidate_image_url = (candidate.get("image_url") or "").strip()
        if candidate_image_url and not is_valid_image_url(candidate_image_url):
            print(f"⏭️ Пропускаем: изображение невалидно")
            continue

        # Нашли подходящий черновик
        draft = candidate
        draft_index = i
        break

    if not draft:
        print(
            "Нет черновиков с новым источником за последние "
            f"{SOURCE_WINDOW_HOURS} часов"
        )
        save_source_stats(source_stats)
        return None, None, None

    # Удаляем выбранный черновик из списка
    if draft_index >= 0:
        _, updated_drafts = remove_draft_by_index(updated_drafts, draft_index)
    
    # Добавляем информацию об источнике в статистику (только для опубликованных)
    source_urls = draft.get("source_urls") or []
    main_url = (source_urls[0] or "").strip() if source_urls else ""
    if main_url:
        source_stats = add_source_stat(main_url, source_stats)
    
    # Сохраняем все изменения
    save_drafts(updated_drafts)
    save_source_stats(source_stats)

    print(f"✅ Выбран пост: {draft.get('title', 'Без названия')}")

    text = format_draft_for_telegram(draft)

    # В цикле выше проверили: если изображение есть, то оно валидно.
    image_url = (draft.get("image_url") or "").strip()
    if image_url:
        print(f"✅ Используем валидное изображение: {image_url}")
    else:
        print("📝 У драфта нет изображения, публикуем текстовый пост")

    return text, image_url, draft

def get_next_post_with_image() -> Tuple[Optional[str], Optional[str]]:
    """Обратная совместимость: возвращает только текст и изображение."""
    text, image_url, _ = get_next_post_payload_with_image()
    return text, image_url

def main():
    print("=" * 80)
    print("ГЕНЕРАЦИЯ СЛЕДУЮЩЕГО ПОСТА ДЛЯ TELEGRAM")
    print("=" * 80)
    print(f"📁 Используется смонтированный бакет: {MOUNTED_BUCKET_PATH}")

    text, image_url, _ = get_next_post_payload_with_image()
    if not text:
        print("Черновики не найдены или не удалось сформировать пост.")
        return

    print("\n" + "=" * 80)
    print("СЛЕДУЮЩИЙ ПОСТ ДЛЯ TELEGRAM")
    print("=" * 80)
    print(text)
    print()
    print(f"Картинка: {image_url or 'не найдена'}")
    print("=" * 80)

    result_path = Path("/tmp/next_post.json")
    result_data = {
        "text": text,
        "image_url": image_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with result_path.open("w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    print(f"\nРезультат сохранен в {result_path}")

if __name__ == "__main__":
    main()
