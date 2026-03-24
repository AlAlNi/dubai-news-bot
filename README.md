\# Dubai News Bot 🤖



Автоматический сбор и публикация новостей о Дубае в Telegram.



\## 📋 Описание



Бот собирает новости о Дубае и ОАЭ из нескольких источников:

\- RSS-ленты (Gulf News, Khaleej Times, The National и др.)

\- GNews.io API



Затем новости проходят проверку через DeepSeek AI (фильтрация по релевантности, дубликатам, качеству), после чего сохраняются как черновики. Автоматическая публикация отправляет новости в Telegram-канал.



\## 🚀 Автоматизация



\### GitHub Actions Workflows



| Workflow | Расписание | Описание |

|----------|------------|----------|

| \*\*Dubai News Collector\*\* | Каждые 6 часов | Сбор новостей из RSS и GNews, фильтрация через AI |

| \*\*Publish News to Telegram\*\* | Через 10 мин после сбора | Публикация следующей новости в Telegram |



\### Ручной запуск



Можно запустить вручную из интерфейса GitHub:

\- `Actions` → `Dubai News Collector` → `Run workflow`

\- `Actions` → `Publish News to Telegram` → `Run workflow`



\## 🔧 Настройка



\### Необходимые секреты GitHub



В репозитории добавьте следующие секреты (`Settings` → `Secrets and variables` → `Actions`):



| Секрет | Описание |

|--------|----------|

| `TELEGRAM\_BOT\_TOKEN` | Токен Telegram бота |

| `TELEGRAM\_CHANNEL\_ID` | ID канала (например, `@channel\_name` или `-1001234567890`) |

| `DEEPSEEK\_API\_KEY` | API ключ DeepSeek для фильтрации и перевода |

| `GNEWS\_API\_KEY` | API ключ GNews.io |

| `GNEWS\_API\_KEY\_BACKUP` | Резервный ключ GNews (опционально) |



\### Локальный запуск для тестирования



```bash

\# Установка зависимостей

cd src

pip install -r requirements.txt



\# Экспорт переменных окружения (создайте .env файл)

export DEEPSEEK\_API\_KEY=your\_key

export GNEWS\_API\_KEY=your\_key

export TELEGRAM\_BOT\_TOKEN=your\_token

export TELEGRAM\_CHANNEL\_ID=your\_channel



\# Запуск сбора новостей

python -c "from rss\_collect import handler; handler({}, None)"



\# Запуск публикации

python -c "from auto\_notify import handler; handler({}, None)"

