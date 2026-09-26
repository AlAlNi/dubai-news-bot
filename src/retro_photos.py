"""Manual Serper image discovery; candidates are never published automatically."""
import hashlib
import html
import ipaddress
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from openai_budget import atomic_json

QUERIES = ('Dubai Creek 1960 historical photographs', 'Deira Dubai 1970 old photographs',
           'Sheikh Zayed Road 1980 historical photographs', 'Dubai 1950 archival photographs')
STATE = Path('storage/dubai_news/retro_photos.json')


def public_url(value):
    if not isinstance(value, str) or len(value) > 3000:
        return False
    try:
        p = urlsplit(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
            return False
        if '.' not in p.hostname or p.hostname.endswith(('.local', '.localhost')):
            return False
        try:
            return ipaddress.ip_address(p.hostname).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def candidates(rows, seen):
    found = []
    for row in rows[:30]:
        if not isinstance(row, dict):
            continue
        image, source = row.get('imageUrl'), row.get('link')
        if not public_url(image) or not public_url(source):
            continue
        # Ignore resize/tracking variants when detecting exact-image URL repeats.
        p = urlsplit(image)
        identity = hashlib.sha256((p.hostname.lower() + p.path).encode()).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        found.append({'id': identity, 'title': str(row.get('title') or 'Фото')[:300],
                      'image_url': image, 'source_url': source,
                      'status': 'needs_review', 'year': None, 'license': None})
        if len(found) == 10:
            break
    return found


def persist(path, state):
    atomic_json(path, state)
    if os.getenv('GITHUB_ACTIONS') == 'true':
        subprocess.run(['git', 'add', '--', str(path)], check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'Save retro photo search state [skip ci]'],
                       check=True, capture_output=True)
        subprocess.run(['git', 'push', 'origin', 'HEAD:main'], check=True, capture_output=True)


def collect(path=STATE, now=None):
    key = os.getenv('SERPER_API_KEY', '').strip()
    if not key:
        raise RuntimeError('SERPER_API_KEY is missing')
    if os.getenv('GITHUB_ACTIONS') == 'true' and (
        os.getenv('GITHUB_EVENT_NAME') != 'workflow_dispatch' or os.getenv('GITHUB_REF') != 'refs/heads/main'
    ):
        raise RuntimeError('Retro search requires manual launch on main')
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    slot = f'{year}-W{week:02d}'
    query = QUERIES[(week - 1) % len(QUERIES)]
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'version': 1, 'searches': {}, 'seen': [], 'candidates': []}
    if slot in state['searches']:
        return state, True
    state['searches'][slot] = {'query': query, 'status': 'reserved', 'at': now.isoformat()}
    # Durable reservation before the paid call; errors consume this weekly slot.
    persist(path, state)
    try:
        response = requests.post('https://google.serper.dev/images',
                                 headers={'X-API-KEY': key, 'Content-Type': 'application/json'},
                                 json={'q': query, 'hl': 'en', 'gl': 'ae', 'num': 10},
                                 timeout=30, allow_redirects=False)
        try:
            if response.status_code != 200:
                raise RuntimeError(f'Serper HTTP {response.status_code}')
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('images'), list):
                raise RuntimeError('Serper returned invalid image results')
            seen = set(state['seen'])
            new = candidates(data['images'], seen)
        finally:
            response.close()
        state['seen'] = sorted(seen)
        state['candidates'].extend(new)
        state['searches'][slot].update(status='completed', added=len(new))
    except Exception as exc:
        # Never store API response bodies or exception text that could contain credentials.
        state['searches'][slot].update(status='error', error_type=type(exc).__name__)
        persist(path, state)
        raise RuntimeError('Serper search failed; weekly reservation retained. Check API key and Serper balance.') from None
    persist(path, state)
    return state, False


def gallery(state, path=Path('retro-photos.html')):
    parts = ['<!doctype html><meta charset="utf-8"><title>Дубай раньше</title>',
             '<h1>Дубай раньше — подборка для проверки</h1>',
             '<p>Год, место и право публикации нужно подтвердить по источнику. Это кандидаты, не готовые посты.</p>']
    for item in reversed(state['candidates'][-40:]):
        if not public_url(item['image_url']) or not public_url(item['source_url']):
            continue
        parts.append('<section><h2>' + html.escape(item['title']) + '</h2><img loading="lazy" '
                     'referrerpolicy="no-referrer" style="max-width:600px;max-height:400px" src="'
                     + html.escape(item['image_url'], quote=True) + '"><p><a href="'
                     + html.escape(item['source_url'], quote=True) + '">Источник</a></p></section>')
    path.write_text('\n'.join(parts), encoding='utf-8')


if __name__ == '__main__':
    state, cached = collect()
    gallery(state)
    message = f'Retro photos: cached={cached}; candidates={len(state["candidates"])}. Download retro-photo-gallery artifact. No Telegram publication.'
    print(message)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write('## Ретрофото Дубая\n\n' + message + '\n')
