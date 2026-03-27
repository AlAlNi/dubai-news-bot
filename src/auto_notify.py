import os
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from http_client import request_with_retry
from next_draft import (
    get_next_post_payload_with_image,
    add_to_published_history,
    save_published_history,
)

# ================== НАСТРОЙКИ ==================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # @channel или -100...

# Определяем путь к хранилищу (для информации)
if os.getenv("GITHUB_ACTIONS") == "true":
    MOUNTED_BUCKET_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage", "dubai_news")
else:
    MOUNTED_BUCKET_PATH = "./storage/dubai_news"

PUBLISH_LOCK_FILENAME = "publish_job.lock"
PUBLISH_DEDUPE_FILENAME = "publish_dedupe.json"
LOCK_STALE_SECONDS = 15 * 60
DEDUPE_RETENTION_DAYS = 30

# ================== ФУНКЦИИ ОТПРАВКИ ==================

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
    return urlunparse((parsed.scheme, host, path, parsed.params, query, parsed.fragment))

def normalize_text_for_compare(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()

def _get_storage_path(filename: str) -> Path:
    return Path(MOUNTED_BUCKET_PATH) / filename

def _acquire_publish_lock() -> tuple[bool, str]:
    """
    Создаёт lock-файл в атомарном режиме (O_EXCL), чтобы исключить
    параллельную публикацию из нескольких воркеров.
    """
    lock_path = _get_storage_path(PUBLISH_LOCK_FILENAME)
    lock_payload = {
        "pid": os.getpid(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(MOUNTED_BUCKET_PATH, exist_ok=True)

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lock_payload, f, ensure_ascii=False, indent=2)
        return True, "ok"
    except FileExistsError:
        try:
            mtime = datetime.fromtimestamp(lock_path.stat().st_mtime, tz=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - mtime).total_seconds()
            if age_seconds > LOCK_STALE_SECONDS:
                print(f"⚠️ Найден протухший lock ({int(age_seconds)} сек), удаляем")
                lock_path.unlink(missing_ok=True)
                return _acquire_publish_lock()
        except Exception as e:
            print(f"⚠️ Не удалось проверить актуальность lock-файла: {e}")
        return False, "locked"
    except Exception as e:
        print(f"❌ Ошибка создания lock-файла: {e}")
        return False, "lock_error"

def _release_publish_lock() -> None:
    lock_path = _get_storage_path(PUBLISH_LOCK_FILENAME)
    try:
        if lock_path.exists():
            lock_path.unlink()
            print("🔓 Lock-файл публикации удалён")
    except Exception as e:
        print(f"⚠️ Не удалось удалить lock-файл: {e}")

def _build_dedupe_hashes(draft: dict) -> tuple[str, str]:
    source_urls = draft.get("source_urls", []) or []
    main_url = (source_urls[0] or "").strip() if source_urls else ""
    canonical_url = canonicalize_url(main_url)
    title = draft.get("title", "")
    summary = draft.get("summary_ru", "")
    strict_signature = f"{title.strip()}|{summary.strip()}|{canonical_url}"
    fuzzy_signature = (
        f"{normalize_text_for_compare(title)}|"
        f"{normalize_text_for_compare(summary)}|"
        f"{canonical_url}"
    )
    strict_hash = hashlib.sha256(strict_signature.encode("utf-8")).hexdigest()
    fuzzy_hash = hashlib.sha256(fuzzy_signature.encode("utf-8")).hexdigest()
    return strict_hash, fuzzy_hash

def _load_dedupe_registry() -> dict:
    path = _get_storage_path(PUBLISH_DEDUPE_FILENAME)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"⚠️ Ошибка чтения dedupe-реестра: {e}")
        return {}

def _save_dedupe_registry(registry: dict) -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - DEDUPE_RETENTION_DAYS * 86400
    cleaned_registry = {}
    for key, value in registry.items():
        updated_at = value.get("updated_at")
        try:
            ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp() if updated_at else cutoff
        except Exception:
            ts = cutoff
        if ts >= cutoff:
            cleaned_registry[key] = value

    path = _get_storage_path(PUBLISH_DEDUPE_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cleaned_registry, f, ensure_ascii=False, indent=2)

def _is_duplicate_publish_attempt(dedupe_key: str, fuzzy_hash: str = "") -> bool:
    registry = _load_dedupe_registry()
    entry = registry.get(dedupe_key, {})
    if entry.get("status") == "sent":
        return True
    if fuzzy_hash:
        for item in registry.values():
            if item.get("status") == "sent" and item.get("fuzzy_hash") == fuzzy_hash:
                return True
    return False

def _mark_dedupe_status(
    dedupe_key: str,
    status: str,
    message_id: int = None,
    strict_hash: str = "",
    fuzzy_hash: str = "",
) -> None:
    registry = _load_dedupe_registry()
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if message_id is not None:
        payload["message_id"] = message_id
    if strict_hash:
        payload["strict_hash"] = strict_hash
    if fuzzy_hash:
        payload["fuzzy_hash"] = fuzzy_hash
    registry[dedupe_key] = payload
    _save_dedupe_registry(registry)

def send_telegram_message(text: str, image_url: str = None, retry_without_image: bool = True):
    """
    Отправляет сообщение в Telegram.
    Если с фото возникает ошибка, автоматически пробует отправить без фото.
    """
    method = "sendPhoto" if image_url else "sendMessage"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "parse_mode": "HTML",
    }

    if image_url:
        payload["photo"] = image_url
        payload["caption"] = text
        print(f"📸 Отправка с фото: {image_url}")
    else:
        payload["text"] = text
        print("📝 Отправка без фото")

    print(f"📨 Отправляем в Telegram...")
    print(f"   Длина текста: {len(text)} символов")

    try:
        resp = request_with_retry("POST", api_url, json=payload, timeout=30)
        print(f"📨 Telegram ответ: {resp.status_code}")
        
        if resp.status_code == 200:
            result = resp.json()
            print(f"✅ Успешно опубликовано! Message ID: {result.get('result', {}).get('message_id')}")
            return {
                "success": True,
                "with_image": bool(image_url),
                "message_id": result.get('result', {}).get('message_id'),
                "response": result
            }
        else:
            print(f"❌ Ошибка Telegram API: {resp.status_code}")
            print(f"   Ответ: {resp.text[:200]}")
            
            # Если ошибка с фото и разрешена повторная попытка без фото
            if image_url and retry_without_image and resp.status_code == 400:
                error_text = resp.text.lower()
                if "wrong type of the web page content" in error_text or "bad request" in error_text:
                    print("🔄 Фото невалидно, пробуем отправить без фото...")
                    return send_telegram_message(text, image_url=None, retry_without_image=False)
            
            return {
                "success": False,
                "error": "telegram_api_error",
                "status_code": resp.status_code,
                "response": resp.text[:500],
                "with_image": bool(image_url)
            }

    except requests.exceptions.Timeout:
        print("❌ Таймаут при отправке в Telegram")
        return {"success": False, "error": "timeout", "with_image": bool(image_url)}
    except requests.exceptions.ConnectionError as e:
        print(f"❌ Ошибка подключения к Telegram: {e}")
        return {"success": False, "error": f"connection_error: {e}", "with_image": bool(image_url)}
    except Exception as e:
        print(f"❌ Ошибка отправки в Telegram: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e), "with_image": bool(image_url)}

# ================== ОСНОВНОЙ HANDLER ==================

def handler(event, context):
    """
    Автопубликация поста в канал.
    Предполагается, что:
    - rss_collect.py по своему крону уже наполнил drafts.json
      только одобренными ИИ‑редактором новостями;
    - next_draft.py умеет брать следующий драфт и возвращать (text, image_url)
      в нужном формате (HTML, ссылки, хэштеги и т.п.) из смонтированного бакета.
    """
    print("=" * 60)
    print("⏰ АВТОПУБЛИКАЦИЯ ЗАПУЩЕНА")
    print("=" * 60)
    
    current_time = datetime.now(timezone.utc)
    hour_utc = current_time.hour
    hour_dubai = (hour_utc + 4) % 24
    
    print(f"📁 Работаем со смонтированным бакетом: {MOUNTED_BUCKET_PATH}")
    print(f"🕐 UTC: {hour_utc:02d}:00, Дубай: {hour_dubai:02d}:00")

    # Проверка наличия токенов
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не задан")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "TELEGRAM_BOT_TOKEN not set"}),
        }

    if not TELEGRAM_CHANNEL_ID:
        print("❌ TELEGRAM_CHANNEL_ID не задан")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "TELEGRAM_CHANNEL_ID not set"}),
        }

    lock_acquired, lock_reason = _acquire_publish_lock()
    if not lock_acquired:
        print("⏭️ Публикация пропущена: уже выполняется другой инстанс")
        return {
            "statusCode": 200,
            "body": json.dumps({
                "published": False,
                "reason": lock_reason,
                "time_utc": current_time.isoformat(),
            }),
        }

    try:
        # Берём следующий пост из next_draft.py (работает с смонтированным бакетом)
        try:
            text, image_url, selected_draft = get_next_post_payload_with_image()
        except Exception as e:
            print(f"❌ Ошибка при вызове get_next_post_payload_with_image: {e}")
            import traceback
            traceback.print_exc()
            return {
                "statusCode": 500,
                "body": json.dumps({"error": f"next_draft error: {e}"}),
            }

        if not text:
            print("ℹ️ Нет доступных постов для публикации (drafts.json пустой или все исчерпаны)")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "published": False,
                    "reason": "no_posts",
                    "time_utc": current_time.isoformat(),
                }),
            }

        strict_hash, fuzzy_hash = _build_dedupe_hashes(selected_draft or {})
        dedupe_key = strict_hash
        if dedupe_key and _is_duplicate_publish_attempt(dedupe_key, fuzzy_hash):
            print(f"⏭️ Публикация пропущена по dedupe_key: {dedupe_key}")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "published": False,
                    "reason": "duplicate_dedupe_key",
                    "dedupe_key": dedupe_key,
                    "time_utc": current_time.isoformat(),
                }),
            }

        if dedupe_key:
            _mark_dedupe_status(
                dedupe_key,
                "in_progress",
                strict_hash=strict_hash,
                fuzzy_hash=fuzzy_hash,
            )

        # Отправляем в Telegram с автоматической обработкой ошибок фото
        result = send_telegram_message(text, image_url)
        
        if result["success"]:
            if selected_draft:
                history = add_to_published_history(selected_draft)
                save_published_history(history)
            if dedupe_key:
                _mark_dedupe_status(
                    dedupe_key,
                    "sent",
                    message_id=result.get("message_id"),
                    strict_hash=strict_hash,
                    fuzzy_hash=fuzzy_hash,
                )

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "published": True,
                    "time_utc": current_time.isoformat(),
                    "with_image": result.get("with_image", False),
                    "message_id": result.get("message_id"),
                    "dedupe_key": dedupe_key,
                }),
            }
        else:
            if dedupe_key:
                _mark_dedupe_status(
                    dedupe_key,
                    "failed",
                    strict_hash=strict_hash,
                    fuzzy_hash=fuzzy_hash,
                )
            # Определяем HTTP статус код для ответа
            status_code = 500
            if result.get("status_code"):
                status_code = result["status_code"]
            elif result.get("error") == "timeout":
                status_code = 504
            elif "connection" in str(result.get("error", "")).lower():
                status_code = 502
                
            return {
                "statusCode": status_code,
                "body": json.dumps({
                    "published": False,
                    "error": result.get("error"),
                    "with_image_attempt": result.get("with_image", False),
                    "details": result.get("response", result.get("error")),
                    "dedupe_key": dedupe_key,
                }),
            }
    finally:
        _release_publish_lock()

# ================== ЛОКАЛЬНЫЙ ЗАПУСК ==================

if __name__ == "__main__":
    print("🚀 Запуск авто-публикации локально...")
    print("⚠️ Для локального тестирования используйте ./storage/dubai_news")
    
    # Для локального тестирования подменяем путь в next_draft
    import next_draft
    next_draft.MOUNTED_BUCKET_PATH = "./storage/dubai_news"
    
    result = handler({}, None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
