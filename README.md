\# Dubai News Bot 🤖



Автоматический сбор и публикация новостей о Дубае в Telegram.



\## 📋 Описание



Схема: **Astra → полный текст статьи → DeepSeek → GPT-4.1 mini → Telegram**.

- **Astra** (`gpt-6-astra`) ищет свежие статьи и возвращает ссылки с источниками.
- **Бот** скачивает статьи, проверяет дату и удаляет повторы.
- **DeepSeek** отбирает материалы и пишет русские посты с оформлением Telegram.
- **GPT-4.1 mini** сверяет пост с загруженным исходником. Только проверенные посты публикуются.

Один основной поиск и максимум один дополнительный при неудаче, до пяти ссылок
в каждом списке. Оба списка кешируются на день Дубая.
RSS/GNews исключены из рабочего сбора. Лимит OpenAI — до $3 в месяц;
из-за консервативного резерва поиск может остановиться раньше исчерпания этой суммы.
Настройка, расходы и ручная проверка: [OPENAI_SETUP.md](OPENAI_SETUP.md).

## 🧭 Редакционная модель

### Миссия, цель и задачи редакции

Можно настроить через переменные окружения:

- `NEWSROOM_MISSION` — миссия редакции.
- `NEWSROOM_GOAL` — измеримая цель редакции.
- `NEWSROOM_TASKS` — задачи редакции (через `;`).
- `AD_MIN_QUALITY_SCORE` — порог качества для рекламной готовности.

Каждый черновик теперь хранит:
- профиль редакции (миссия/цель/задачи),
- целевую суточную квоту редакционного микса,
- редакторское решение,
- оценку качества и готовности к монетизации.

Это позволяет постепенно выйти на самоокупаемость: сначала рост и качество контента, затем подключение рекламы, когда посты стабильно проходят порог качества.



\## 🚀 Автоматизация



\### GitHub Actions Workflows



| Workflow | Расписание | Описание |

|----------|------------|----------|

| \*\*Dubai News Collector\*\* | Каждый час | Обработка ссылок Astra; новый поиск не чаще дневного лимита |

| \*\*Publish News to Telegram\*\* | На 5-й минуте каждого часа | Публикация следующей новости в Telegram |



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





\### Локальный запуск для тестирования



```bash

\# Установка зависимостей

cd src

pip install -r requirements.txt



\# Экспорт переменных окружения (создайте .env файл)

export DEEPSEEK\_API\_KEY=your\_key


export TELEGRAM\_BOT\_TOKEN=your\_token

export TELEGRAM\_CHANNEL\_ID=your\_channel

export MAX\_DRAFTS=50

export MAX\_AGE\_HOURS=24

export MAX\_NEWS\_AGE\_DAYS=2

export MAX\_SOURCE\_STATS\_DAYS=7

export SOURCE\_WINDOW\_HOURS=48

export DRAFT\_MAX\_AGE\_DAYS=7

export CONTENT\_UNIQUE\_DAYS=30
export NEWSROOM\_MISSION="Давать русскоязычным жителям Дубая проверенные и полезные новости"
export NEWSROOM\_GOAL="Построить устойчивое медиа и выйти в рекламную монетизацию"
export NEWSROOM\_TASKS="Оперативный поиск;Фактчекинг;Подготовка постов;Редакторское одобрение;Монетизация"
export AD\_MIN\_QUALITY\_SCORE=75
export DAILY\_QUOTA\_URGENT=1
export DAILY\_QUOTA\_PRACTICAL=1



\# Запуск сбора новостей

python -c "from rss\_collect import handler; handler({}, None)"



\# Запуск публикации

python -c "from auto\_notify import handler; handler({}, None)"
