"""Explicit one-time replacement requested after the user deleted message 1605."""
import html
import json
import os
from pathlib import Path

import requests
from retro_photos import persist
from retro_publish import load_evidence, valid_post
from source_verification import verify_summary

CONFIG = Path('config/retro_creek_replacement.json')
STATE = Path('storage/dubai_news/retro_creek_replacement.json')


def send_photo(item):
    token, channel = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHANNEL_ID')
    if not token or not channel:
        raise RuntimeError('Missing Telegram credentials')
    caption = item['post_html'] + '\n\n<a href="' + html.escape(item['source_url'], quote=True) + '">Источник — The National</a>\n\n#ДубайРаньше'
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/sendPhoto', timeout=40,
                                 allow_redirects=False, json={'chat_id': channel, 'photo': item['image_url'],
                                                             'caption': caption, 'parse_mode': 'HTML'})
        try:
            data = response.json()
            if response.status_code != 200 or data.get('ok') is not True:
                return {'status': 'send_failed', 'error_code': data.get('error_code', response.status_code)}
            message = data['result']
            return {'status': 'published', 'message_id': message['message_id'],
                    'channel_username': message.get('chat', {}).get('username'),
                    'photo_file_id': message['photo'][-1]['file_id']}
        finally:
            response.close()
    except Exception:
        return {'status': 'send_unknown'}


def run(config=CONFIG, path=STATE):
    if path.exists():
        state = json.loads(path.read_text(encoding='utf-8'))
        return {'status': 'already_attempted', 'previous_status': state['status']}
    if os.getenv('GITHUB_ACTIONS') == 'true' and os.getenv('GITHUB_REF') != 'refs/heads/main':
        raise RuntimeError('Replacement runs only on main')
    if os.getenv('SOURCE_VERIFIER') != 'openai':
        raise RuntimeError('OpenAI verification is required')
    if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHANNEL_ID'):
        raise RuntimeError('Missing Telegram credentials')
    item = json.loads(config.read_text(encoding='utf-8'))
    if not valid_post(item['post_html']):
        raise RuntimeError('Invalid replacement post')
    source = load_evidence(item, include_article=True)
    state = {'status': 'verifying', 'replaces_message_id': 1605, 'user_confirmed_deletion': True,
             'image_url': item['image_url'], 'source_snapshot': source, 'post_html': item['post_html']}
    persist(path, state)
    verification = verify_summary(source, item['post_html'], os.getenv('DEEPSEEK_API_KEY'), storage_dir=path.parent)
    state['verification'] = verification
    if verification['status'] != 'approved':
        state['status'] = 'verification_' + verification['status']
        persist(path, state)
        return {'status': state['status'], 'reason': verification.get('reason')}
    state['status'] = 'sending'
    persist(path, state)
    result = send_photo(item)
    state.update(result)
    persist(path, state)
    return result


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, ensure_ascii=False))
    if result['status'] not in {'published', 'already_attempted'}:
        raise SystemExit(1)
