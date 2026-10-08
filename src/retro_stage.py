"""Explicit staging photo preparation. No scheduler, discovery or default sending."""
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from openai_budget import atomic_json
from retro_policy import usage_allowed
from retro_commons import commons_review
from retro_publish import select_photo, used_images, image_identity, valid_post
from source_verification import verify_summary

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / 'storage/dubai_news'
STAGING = ROOT / 'storage/dubai_news_staging/retro'


def caption_html(item):
    text = item['post_html']
    review = commons_review(item['source_url'])
    if review:
        footer = (html.escape(review['attribution']) + '\n<a href="'
                  + html.escape(item['source_url'], quote=True) + '">Карточка Wikimedia Commons</a>'
                  + ' · <a href="' + html.escape(item['usage_review']['license_url'], quote=True)
                  + '">' + html.escape(review['metadata']['LicenseShortName']) + '</a>'
                  + '\nБез изменений.')
    else:
        footer = ('<a href="' + html.escape(item['source_url'], quote=True)
                  + '">Источник — The National</a>\n' + html.escape(item['usage_review']['attribution']))
    caption = text + '\n\n' + footer + '\n#ДубайРаньше'
    return caption


def photo_payload(item, channel):
    if not valid_post(item['post_html']) or not usage_allowed(item, channel):
        raise ValueError('invalid_post_or_usage_review')
    caption = caption_html(item)
    plain = html.unescape(re.sub('<[^>]*>', '', caption))
    # Telegram counts UTF-16 units after entity parsing. Reject, never truncate facts.
    if len(plain.encode('utf-16-le')) // 2 > 1024:
        raise ValueError('photo_caption_too_long')
    return {'chat_id': channel, 'photo': item['image_url'], 'caption': caption, 'parse_mode': 'HTML'}


def send_photo(payload):
    token = os.environ['TEST_TELEGRAM_BOT_TOKEN']
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/sendPhoto',
                                 json=payload, timeout=40, allow_redirects=False)
        try:
            data = response.json()
            if response.status_code != 200 or data.get('ok') is not True:
                return {'status': 'send_failed', 'error_code': data.get('error_code', response.status_code)}
            message = data['result']
            if str(message['chat']['id']) != payload['chat_id']:
                return {'status': 'send_unknown'}
            return {'status': 'published', 'message_id': message['message_id'],
                    'photo_file_id': message['photo'][-1]['file_id']}
        finally:
            response.close()
    except Exception:
        return {'status': 'send_unknown'}  # No retry after ambiguous delivery.


def run(choices, channel, *, storage=STAGING, production=PRODUCTION, send=False, now=None):
    """Preparation can use paid verification: caller must explicitly authorize it.

    Tests replace evidence/verification with fixtures; never treat fixtures as real approval.
    A send requires separate test credentials and a reviewed exact destination.
    """
    storage, production = Path(storage), Path(production)
    if storage.resolve() == production.resolve() or production.resolve() in storage.resolve().parents:
        raise ValueError('staging_storage_required')
    if os.getenv('GITHUB_ACTIONS') == 'true':
        raise ValueError('local_staging_only')
    if os.getenv('BOT_ENVIRONMENT') != 'staging':
        raise ValueError('staging_environment_required_for_shared_budget')
    if os.getenv('SOURCE_VERIFIER') != 'openai':
        raise ValueError('openai_verification_required')
    if not re.fullmatch(r'-\d+', channel) or channel == os.getenv('TELEGRAM_CHANNEL_ID'):
        raise ValueError('explicit_test_channel_required')
    if send and (os.getenv('TEST_TELEGRAM_CHANNEL_ID') != channel
                 or not os.getenv('TEST_TELEGRAM_BOT_TOKEN')
                 or os.getenv('TEST_TELEGRAM_BOT_TOKEN') == os.getenv('TELEGRAM_BOT_TOKEN')
                 or os.getenv('SOURCE_VERIFIER') != 'openai'):
        raise ValueError('separate_test_credentials_and_verifier_required')
    storage.mkdir(parents=True, exist_ok=True)
    lock = storage / 'retro.lock'
    # Exclusive across preparation and send. Crash leaves lock: reconcile manually.
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {'status': 'locked', 'reason': 'reconcile_before_retry'}
    os.close(fd)
    try:
        now = now or datetime.now(timezone.utc)
        year, week, _ = now.isocalendar()
        slot = f'{channel}:{year}-W{week:02d}'
        path = storage / 'retro_publications.json'
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': 1, 'slots': {}}
        previous = state['slots'].get(slot)
        if previous and previous['status'] != 'prepared':
            return {'status': 'already_attempted', 'previous_status': previous['status']}
        used = used_images(production) | used_images(storage)
        if previous:
            used.discard(previous['image_identity'])
            used |= used_images(production)
        item, source, diagnostics = select_photo(choices, used, eligible=lambda c: usage_allowed(c, channel), allow_commons=True)
        if item is None:
            result = {'status': 'no_verified_photo', 'diagnostics': diagnostics}
            atomic_json(storage / 'retro_diagnostics.json', result)
            return result
        payload = photo_payload(item, channel)
        record = {'status': 'verifying', 'image_identity': image_identity(item['image_url']),
                  'source_snapshot': source, 'usage_review': item['usage_review'],
                  'payload': payload, 'diagnostics': diagnostics, 'at': now.isoformat()}
        state['slots'][slot] = record
        atomic_json(path, state)
        verification = verify_summary(source, item['post_html'], storage_dir=storage)
        record['verification'] = verification
        if verification['status'] != 'approved':
            record['status'] = 'verification_' + verification['status']
        elif not send:
            record['status'] = 'prepared'
        else:
            record['status'] = 'sending'
            atomic_json(path, state)  # Durable reservation before side effect.
            record.update(send_photo(payload))
        atomic_json(path, state)  # Failure leaves sending: never resend or count twice.
        return {'status': record['status'], 'diagnostics': diagnostics,
                **({'message_id': record['message_id']} if 'message_id' in record else {})}
    finally:
        lock.unlink()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='Manually reviewed list of photo candidates')
    parser.add_argument('--channel', help='Exact numeric test channel ID')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--allow-paid-verification', action='store_true')
    args = parser.parse_args()
    if args.prepare or args.send:
        if not args.config or not args.channel or not args.allow_paid_verification:
            parser.error('Explicit config, channel and --allow-paid-verification required')
        result = run(json.loads(args.config.read_text(encoding='utf-8')), args.channel, send=args.send)
    else:
        # Safe read-only inventory: never evidence requests, credentials or state writes.
        photos = json.loads((PRODUCTION / 'retro_photos.json').read_text(encoding='utf-8'))
        item, source, report = select_photo(photos['candidates'], used_images(PRODUCTION), eligible=lambda c: False)
        result = {'status': 'inventory_only', 'diagnostics': report}
    print(json.dumps(result, ensure_ascii=False, indent=2))
