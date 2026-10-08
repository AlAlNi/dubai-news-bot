# Согласованный единичный тест фото

8 октября пользователь разрешил перенос в develop, одну штатную платную проверку
в общем бюджете и одну отправку в существующий приватный тестовый канал только
после approved. Новые секреты, workflow main и расписания не нужны и не создаются.
Используется существующий staging.yml с operation=retro, refresh_search=false,
bypass_daily_limit=false. Этот workflow остаётся на main, как требует DEPLOYMENT.md,
и загружает только код/tests/config develop; production код main не меняется.

config/retro_beach_test.json фиксирует одобренный текст и оригинал, SHA-256 полного
итогового HTML caption и разрешение только existing_private_test_channel. Numeric
ID берётся из существующего TEST_TELEGRAM_CHANNEL_ID, уже переданного workflow в
TELEGRAM_CHANNEL_ID; unchanged scripts/run_staging.py проверяет приватность и
отличие от production getChat до расхода. Runtime разрешение связывается с этим
точным ID после проверки. Private IDs/tokens не публикуются в config, журнале или логах.

retro_publish.run направляет только существующую manual staging operation=retro
в guarded retro_stage. Нужны точные workflow/context, main ref, workflow_dispatch,
BOT_ENVIRONMENT=staging, SOURCE_VERIFIER=openai и GITHUB_RUN_ATTEMPT=1.
Новые main workflows, production/main merges, writer, emoji, Serper и дополнительные
AI стадии отсутствуют. Повторная платная попытка и workflow rerun запрещены.

У Commons повторно проверяются pinned metadata, дата именно DateTimeOriginal,
автор, неизвестное точное место, Flickr provenance, лицензия и SHA-1 оригинала.
Штатный verify_summary получает полный caption, а не только основной абзац.
Верификатор использует max_attempts=1 и общий бюджет; лимиты остаются $3/месяц,
8 вызовов/день, daily bypass=false.

storage/dubai_news_staging/retro_photo_publications.json содержит отдельную запись
staging:ISOweek, без destination в ключах и вложенных полях. verifying сохраняется
и push выполняется до платного запроса; sending — до sendPhoto. Ошибка push отменяет
следующий side effect. sending/send_unknown/verifying и все завершённые исходы
не повторяются. Final result сохраняется в той же записи. Общий budget ledger
изменяется только штатной reservation/usage одной проверки.

Ручной dispatch допустим только после подтверждения свежего remote develop SHA,
общего бюджета и отсутствия активных запусков. Результат сверяется с remote state,
api_calls, source_hash/summary_hash, message_id и HTTP outcome; повторный dispatch
не используется для получения потерянного результата. При отказе verifier фото
не отправляется. Пересмотр текста потребует нового согласования/бюджетной команды.
