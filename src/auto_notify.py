import os
import json
from datetime import datetime, timezone

import requests
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

# ================== ФУНКЦИИ ОТПРАВКИ ==================

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
        resp = requests.post(api_url, json=payload, timeout=30)
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

    # Отправляем в Telegram с автоматической обработкой ошибок фото
    result = send_telegram_message(text, image_url)
    
    if result["success"]:
        if selected_draft:
            history = add_to_published_history(selected_draft)
            save_published_history(history)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "published": True,
                "time_utc": current_time.isoformat(),
                "with_image": result.get("with_image", False),
                "message_id": result.get("message_id"),
            }),
        }
    else:
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
            }),
        }

# ================== ЛОКАЛЬНЫЙ ЗАПУСК ==================

if __name__ == "__main__":
    print("🚀 Запуск авто-публикации локально...")
    print("⚠️ Для локального тестирования используйте ./storage/dubai_news")
    
    # Для локального тестирования подменяем путь в next_draft
    import next_draft
    next_draft.MOUNTED_BUCKET_PATH = "./storage/dubai_news"
    
    result = handler({}, None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
