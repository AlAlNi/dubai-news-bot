# Подготовка переноса «Дубай раньше» в main

11 октября 2026. Локальная ветка fix/retro-main-preparation-20261011, checkout
retro-main-prep. База — свежий main 02b9d92e3301ea3262218527a060cbe6fed99cb6;
develop остаётся 3d8a56cc1add87a730d819935c04ddebb0f21ca0. Это подготовленный
локальный diff, не внедрение. AGENTS.md и .agents/skills в репозитории и проверенных
родительских каталогах не найдены; прочитаны README, DEPLOYMENT, RETRO_PHOTOS и код/тесты.

## Почему нельзя просто merge develop

Develop содержит также отдельные новостные/эмодзи изменения и одобрение разового
теста пляжа. Успешное сообщение 76 подтверждало только конкретный тестовый путь.
Старый production run продолжал Serper → The National → writer → текстовую отправку;
слепой перенос тестового adapter не снабжает недельный запуск новым снимком.
Поэтому перенесены только ретро-политика/доказательства/диагностика и фото-механика.
Разовое retro_beach_test.json, draft/config/news_discovery, scripts/run_staging,
новостные/эмодзи модули и workflows не перенесены.

## Предлагаемый недельный путь

Существующий retro_publish.yml и cron субботы 14:00 Дубая не меняются. Его привычный
`python src/retro_publish.py` использует новую очередь config/retro_publish_queue.json.
Очередь сейчас пуста: результат no_reviewed_photo / manual_queue_required. Ни Serper,
ни writer, ни OpenAI, ни getChat/sendPhoto не вызываются без одобренного кандидата.
Discovery остаётся отдельным прежним процессом; его результаты не становятся
одобренной очередью автоматически. Список источников: прежний The National плюс
конкретные вручную проверенные карточки Commons. Домен Commons целиком не разрешён.

Для каждого нового выпуска редактор должен выбрать и визуально проверить оригинал,
установить автора, дату/место с оговорками, происхождение и допустимое использование,
подготовить короткую подпись и согласовать её для rudubainews. Поля элемента очереди:

- id, source_url, image_url, post_html в существующем формате рубрики.
- usage_review: status=approved, basis=written_permission или explicit_license,
  reviewer, reviewed_at, evidence, rights_holder, attribution, точные image_url и
  source_url, target_username=rudubainews, telegram_republication=true.
  channel_id отсутствует/null: числовой ID связывается с проверенным публичным
  username только после getChat. Конфликтующий ранее заданный ID не принимается.
- publication_review: status=approved, reviewer, reviewed_at, visual_checked=true,
  target_username=rudubainews, image_identity из image_identity(image_url),
  caption_sha256=SHA-256 полного итогового UTF-8 caption_html, включая источник/атрибуцию.
- Для Commons необходим ручной реестр config/retro_commons_reviews.json с точной
  карточкой, автором, DateTimeOriginal, описанием/неопределённостью, Flickr credit,
  лицензией, dimensions и SHA-1/SHA-256. usage_review дополнительно фиксирует
  точную license_url, rights_holder/attribution, image_sha1, modifications=none,
  share_alike=true. Ни одна лицензия не выводится из доступности картинки или ссылки.

Недельный запуск перечитывает очередь/историю под lock, проверяет разрешённый main
workflow и production destination, заново загружает доказательства (Commons API
проверяется по pinned полям/хешу). Затем один штатный verify_summary проверяет полный
caption. Writer/emoji стадии отсутствуют; текущие бюджетные лимиты и общий ledger
сохранены. Только approved допускает guarded sendPhoto. Caption не обрезается:
максимум 1024 UTF-16 единицы после HTML parsing. Неподходящие кандидаты объясняются,
не исчезают молча; лимит загрузок доказательств остаётся три.

Учитываются старый production retro_publications, Creek replacement и тестовый
retro_photo_publications. Пляж message76 исключён даже в другой неделе. Preparing/
verifying и sending сохраняются удалённо до платной проверки/отправки через прежний
persist; сбой push останавливает следующий side effect. Timeout или сбой сохранения
после Telegram не повторяется. Сбой процесса с lock требует ручной сверки. Записи
прошлых попыток и журнал не удаляются ради retry. Исторический legacy_run оставлен
только для тестов совместимости; run и CLI его не вызывают.

## Проверено и осталось

Текущий inventory main: 22 discovery-кандидата, 0 usage_review; есть 11 ссылок
The National, но это не разрешения на перепубликацию. Новый пригодный снимок не
выбран. Реестр содержит старую Commons-карточку лишь для доказательств/дедупликации,
не старую подпись и не разрешение её повторной отправки. Fixtures синтетические
для sender/queue, сохранённая публичная Commons API fixture — для parser регрессий.
Автономный еженедельный поиск/лицензирование не реализованы и не обещаются.

Полный runner отключает унаследованные токены и блокирует реальные socket connections.
Тесты старого текстового пути явно вызывают legacy_run; новые тесты проверяют реальный
weekly entrypoint, пустую/одобренную очередь, destination, права, source/date/license
drift, полный caption, дедупликацию message76, lock, persist failures и no-resend.
Production state, бюджеты, обычные новости, расписание и чужие изменения не менялись.
Прежние разовые разрешения на платные вызовы/отправки не использованы.

Следующий пакет: проверить локальный diff/patch; подобрать НОВЫЙ снимок с доказанным
допустимым использованием и согласовать полный caption/элемент очереди. Затем отдельно
согласовать перенос выбранного commit в main и конкретный проверочный/публикационный
запуск. До такого решения нет push, main merge, Actions, платных вызовов или Telegram.
Если разрешить только перенос механики с пустой очередью, рубрика будет корректно
пропускать выпуск, а не публиковать неподтверждённое или повторное фото.


## Independent review follow-up (2026-10-11)

The independent read-only review found and the local follow-up fixes two defects:
remote-URL sending did not pin the actual image bytes, and malformed queue captions
could abort inventory. The weekly path now downloads at most 10 MiB without redirects,
checks SHA-256 before paid verification, and uploads those exact bytes as multipart.
Commons uses the manually reviewed registry hash; National candidates must additionally
supply `publication_review.image_sha256` from the visually reviewed original. This hash
pins the complete file, including its reviewed dimensions; no image transformations occur.
Malformed rows receive individual diagnostics without hiding remaining candidates.
An explicit regression checks failure of the durable `sending` marker prevents sending
and leaves the prior `verifying` attempt blocked on rerun.

Validation: full offline suite 202 tests + 68 subtests passed. Network connections
were blocked; six pre-existing attempts from four unrelated news tests were denied.
The reviewed publication queue remains empty. No deployment or fresh-photo selection
was performed, and the old private-channel image remains excluded by history.
