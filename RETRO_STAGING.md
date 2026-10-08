# «Дубай раньше»: локальная проверка фото

Дополнение: после отдельного согласования пользователя добавлен точечный Commons
staging маршрут и черновик пляжа. Текущее состояние — [RETRO_COMMONS_DRAFT.md](RETRO_COMMONS_DRAFT.md).
Исходный inventory ниже относится к состоянию до добавления Commons.

## Состояние на 8 октября 2026

Изолированный checkout retro-photo, ветка fix/retro-photo-staging, база main
314258824385a5746e939f8f3ac336a1a6015afe. Read-only fetch сравнил актуальные
main/develop: код ретрофото одинаков; develop 7b866930 содержит отдельные изменения
эмодзи/новостей. Исходный checkout experiment/emoji-subject-selection не изменён;
старый D:\dubai-news-bot с чужим изменением src/next_draft.py также не изменён.
AGENTS.md и .agents/skills в репозитории и проверенных родительских каталогах
отсутствуют. Прочитаны RETRO_PHOTOS.md, README.md, DEPLOYMENT.md, код, тесты и workflows.

Production workflow, cron, конфигурации и storage не менялись. Регулярная production
публикация остаётся текстовой. Новая отправка фото — отдельный локальный staging
entrypoint, без нового workflow. Зарплаты не реализованы.

Отбор объясняет каждого кандидата, включая сторонний источник, повтор, ошибку
доказательств и лимит попыток. Кандидаты не удаляются. Только допустимые источники
расходуют лимит трёх загрузок. Общая политика допускает лишь The National /news/uae/.
История включает отдельную замену Creek message1606, а не только weekly slots.

## Блокер реального снимка

Актуальный inventory: 12 кандидатов, 11 с недопустимым источником, 1 уже использован.
Фото с подтверждённым правом на перепубликацию не найдено. Тесты используют
синтетическую метадату/URL и mock ответов, не разрешение на реальное фото.
Проверка точной подписи и даты load_evidence и фактов verify_summary сохранена.

Проверены [условия The National](https://www.thenationalnews.com/terms-and-conditions/)
8 октября 2026, раздел TRADEMARKS, COPYRIGHTS AND RESTRICTIONS: материалы, включая
изображения, защищены; перепубликация/электронное распространение требуют
предварительного письменного согласия. Ссылка, доступность, старый год съёмки,
credit и согласие отправить пост не являются разрешением правообладателя.
Нужен документ на конкретный снимок, Telegram и целевой канал, с атрибуцией.
Издатель может использовать материал по лицензии третьей стороны: нужно
подтвердить полномочия разрешающей стороны. Контакт из условий:
info@thenationalnews.com. Запрос не отправлялся. Источники не расширялись.

## Проверки сейчас

`python src/retro_stage.py` — только read-only inventory, без API и отправки.
Embedded Python ноутбука требует src в sys.path:

```powershell
& 'C:\Users\dobri\Documents\Codex\2026-10-02\task-2\python-runtime\python.exe' -c "import sys,runpy; sys.path.insert(0,'src'); runpy.run_path('src/retro_stage.py',run_name='__main__')"
```

Полный offline runner: ..\run_offline_tests.py. Удаляет унаследованные ключи/токены,
блокирует socket connect/create_connection. Все фикстуры остаются внутри tests.

## Следующий пакет для тестового канала

1. Найти неиспользованный снимок на разрешённом источнике. Проверить точную подпись,
   дату, место, происхождение, визуально подтвердить кадр. Получить документ о
   Telegram-перепубликации. Отдельно согласовать бюджет верификации и одну реальную
   тестовую отправку. На текущем этапе это не разрешено.
2. Создать локальный JSON-массив из одного кандидата: id, image_url, source_url,
   короткий post_html в существующем формате «Дубай раньше». Добавить usage_review:
   status=approved, basis=written_permission (или проверенная explicit_license),
   reviewer, reviewed_at, evidence (документ и условия), rights_holder, attribution,
   точные image_url/source_url/channel_id, telegram_republication=true. Все поля
   проверяются; это ручной проверенный документ, не автоматически выданная лицензия.
   Фикстуры tests для реальной отправки не использовать.
3. После отдельного разрешения задать BOT_ENVIRONMENT=staging, SOURCE_VERIFIER=openai
   и существующие OpenAI настройки. Сначала сверить актуальный общий account-wide
   бюджетный журнал: изолированный checkout содержит только snapshot main, не
   синхронный глобальный ledger; нельзя сбросить расход или обойти лимиты. Код
   использует штатный общий ledger storage/dubai_news/openai_budget.json для staging.
   Это будущая разрешённая запись бюджета; сейчас production storage не изменялся.
   Выполнить `python src/retro_stage.py --config <file> --channel <numeric-id>
   --prepare --allow-paid-verification`. Может вызвать платный verify_summary, но
   не Telegram. Проверить prepared запись в storage/dubai_news_staging/retro:
   фото, caption, source_snapshot, usage_review, verification.
4. Только после разрешения одной отправки задать отдельные TEST_TELEGRAM_BOT_TOKEN
   и TEST_TELEGRAM_CHANNEL_ID и повторить с --send вместо --prepare. Тестовые токен
   и канал должны отличаться от production. Отправляется sendPhoto с историей,
   источником, атрибуцией; максимум 1024 UTF-16 единицы после HTML parsing. Текст
   не обрезается. Ответ должен подтвердить точный chat_id и photo_file_id.
5. Проверить кадр, caption, источник, атрибуцию и message_id в канале. Повторный
   вызов должен дать already_attempted. Production внедрение/Actions — отдельное
   решение; расписание и workflow этой доработкой не меняются.

## Сбои и учёт

Эксклюзивный retro.lock охватывает подготовку/отправку. После аварийного завершения
lock остаётся: сначала ручная сверка, затем снятие. Перед HTTP сохраняется sending.
Published учитывается в единственной записи weekly slot тестового канала.
Send_unknown, send_failed, sending, verifying и отклонённая верификация автоматически
не повторяются. Сбой сохранения результата оставляет sending: без повторной отправки
и двойного учёта. Не удалять маркеры ради retry; сверить канал и журнал и вручную
зафиксировать outcome. Prepared не означает published и проверяется снова перед
явной отправкой. История production читается для dedupe, staging журнал изолирован.
Ошибки не записывают токен/response body; неизвестные evidence errors дают безопасный код.
