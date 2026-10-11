"""Photo payload and no-retry transport, shared source/rights policy."""
import hashlib
import html
import re

import requests
from retro_commons import commons_review
from retro_policy import usage_allowed
from retro_publish import valid_post


def caption_html(item):
    review = commons_review(item['source_url'])
    rights = item['usage_review']
    if review:
        footer = (html.escape(review['attribution']) + '\nИсточник: <a href="'
                  + html.escape(item['source_url'], quote=True) + '">Wikimedia Commons</a>'
                  + '\n<a href="' + html.escape(rights['license_url'], quote=True)
                  + '">' + html.escape(review['metadata']['LicenseShortName']) + '</a>'
                  + '\nФото без изменений.')
    else:
        footer = ('<a href="' + html.escape(item['source_url'], quote=True)
                  + '">Источник — The National</a>\n' + html.escape(rights['attribution']))
        if rights.get('license_url'):
            footer += '\n<a href="' + html.escape(rights['license_url'], quote=True) + '">Условия использования</a>'
    return item['post_html'] + '\n\n' + footer + '\n#ДубайРаньше'


def photo_payload(item, channel):
    if not valid_post(item['post_html']) or not usage_allowed(item, channel):
        raise ValueError('invalid_post_or_usage_review')
    caption = caption_html(item)
    plain = html.unescape(re.sub('<[^>]*>', '', caption))
    if len(plain.encode('utf-16-le')) // 2 > 1024:
        raise ValueError('photo_caption_too_long')
    return {'chat_id': channel, 'photo': item['image_url'], 'caption': caption, 'parse_mode': 'HTML'}


def approved_image_hash(item):
    review = commons_review(item.get('source_url'))
    digest = review['sha256'] if review else item.get('publication_review', {}).get('image_sha256')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('approved_image_hash_required')
    return digest


def download_approved_photo(item):
    """Bounded original download; the exact approved bytes are uploaded, never a URL."""
    digest = approved_image_hash(item)
    try:
        response = requests.get(item['image_url'], stream=True, timeout=30, allow_redirects=False)
        try:
            if response.status_code != 200:
                raise ValueError('image_download_failed')
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > 10 * 1024 * 1024:
                    raise ValueError('image_download_too_large')
                chunks.append(chunk)
            content = b''.join(chunks)
            if not content or hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('approved_image_bytes_changed')
            return content
        finally:
            response.close()
    except ValueError:
        raise
    except Exception:
        raise ValueError('image_download_failed') from None


def validate_destination(token, configured_channel, expected_username):
    if expected_username != 'rudubainews':
        raise ValueError('publication_target_not_approved')
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/getChat',
            json={'chat_id': configured_channel}, timeout=20, allow_redirects=False)
        try:
            data = response.json()
            chat = data.get('result', {})
            if (response.status_code != 200 or data.get('ok') is not True
                    or chat.get('type') != 'channel' or chat.get('username') != expected_username):
                raise ValueError()
            return str(chat['id'])
        finally:
            response.close()
    except Exception:
        raise ValueError('production_destination_not_confirmed') from None


def send_photo(payload, token, image_bytes):
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/sendPhoto',
                                 data={k: v for k, v in payload.items() if k != 'photo'},
                                 files={'photo': ('approved-original.jpg', image_bytes, 'application/octet-stream')},
                                 timeout=40, allow_redirects=False)
        try:
            data = response.json()
            if response.status_code != 200 or data.get('ok') is not True:
                return {'status': 'send_failed', 'error_code': data.get('error_code', response.status_code)}
            message = data['result']
            if str(message['chat']['id']) != payload['chat_id']:
                return {'status': 'send_unknown'}
            return {'status': 'published', 'message_id': message['message_id'],
                    'photo_file_id': message['photo'][-1]['file_id'],
                    'photo_dimensions': {k: message['photo'][-1][k] for k in ('width', 'height') if k in message['photo'][-1]}}
        finally:
            response.close()
    except Exception:
        return {'status': 'send_unknown'}
