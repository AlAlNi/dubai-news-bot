"""Weekly archive posts with photo-specific evidence and durable send deduplication."""
import html
import json
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import requests
from post_style import format_summary, headline_only
from retro_photos import collect, persist, public_url
from retro_policy import source_allowed
from search_sources import ArticleHTML, clean_text
from source_verification import source_snapshot, verify_summary

STORAGE = Path('storage/dubai_news')
LAUNCH = Path('config/retro_first_post.json')


def image_identity(url):
    from retro_commons import commons_image_identity
    pinned = commons_image_identity(url)
    if pinned:
        return pinned
    p = urlsplit(url)
    return p.hostname.lower() + p.path


class PhotoCaptions(HTMLParser):
    def __init__(self):
        super().__init__()
        self.photos = {}

    def handle_starttag(self, tag, attrs):
        if tag != 'img':
            return
        attrs = dict(attrs)
        url, caption = attrs.get('src', ''), attrs.get('alt', '')
        if public_url(url) and caption:
            self.photos[image_identity(url)] = clean_text(caption)


def load_evidence(item, include_article=False, allow_commons=False):
    url = item['source_url']
    if allow_commons and urlsplit(url).hostname == 'commons.wikimedia.org':
        from retro_commons import load_commons_evidence
        return load_commons_evidence(item)
    if not source_allowed(url) or not public_url(item.get('image_url')):
        raise ValueError('unsupported_archive_source')
    response = requests.get(url, timeout=20, allow_redirects=False, stream=True,
                            headers={'User-Agent': 'DubaiNewsBot/1.0'})
    try:
        if response.status_code != 200 or 'text/html' not in response.headers.get('Content-Type', '').lower():
            raise ValueError('archive_unavailable')
        chunks, size = [], 0
        for chunk in response.iter_content(8192):
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError('archive_too_large')
            chunks.append(chunk)
        page = b''.join(chunks).decode('utf-8', errors='replace')
    finally:
        response.close()
    parser = PhotoCaptions()
    parser.feed(page)
    caption = parser.photos.get(image_identity(item['image_url']), '')
    if (not re.search(r'\b(?:18|19|20)\d{2}s?\b', caption)
            or not re.search(r'Dubai|Sheikh Zayed|Deira|Jumeirah', caption, re.I)):
        raise ValueError('photo_date_or_location_not_confirmed')
    text = 'Caption of the selected photograph: ' + caption
    if include_article:
        article = ArticleHTML()
        article.feed(page)
        body = re.sub(r'\s+([,.!?;:])', r'\1', clean_text(' '.join(article.body)))
        if len(body) < 200 or len(body) > 10000:
            raise ValueError('archive_article_text_unavailable')
        text += '\nArticle describing this photograph:\n' + body
    # Search titles, query years and captions of other photos are never evidence.
    return source_snapshot('Historical photograph of Dubai', text, url)


def write_post(source, storage=STORAGE):
    from openai_writer import prepare_post
    result = prepare_post(source, storage, kind="retro")
    if result["status"] != "prepared":
        raise RuntimeError(result["reason"])
    return '📷 ' + format_summary(result["post"].removeprefix('📷 ').strip())


def valid_post(text):
    plain = html.unescape(re.sub('<[^>]*>', '', text))
    return bool(text.startswith('📷 <b>Дубай раньше:') and 80 <= len(plain) <= 900
                and not headline_only(text.removeprefix('📷 ')))


def send_post(text, source_url):
    token, channel = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHANNEL_ID')
    if not token or not channel:
        raise RuntimeError('Missing Telegram credentials')
    payload = {'chat_id': channel, 'parse_mode': 'HTML',
               'text': text + '\n\n<a href="' + html.escape(source_url, quote=True)
                       + '">Источник и снимок — The National</a>\n\n#ДубайРаньше',
               'link_preview_options': {'is_disabled': True}}
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                 json=payload, timeout=30, allow_redirects=False)
        try:
            data = response.json()
            if response.status_code != 200 or data.get('ok') is not True:
                return {'status': 'send_failed', 'error_code': data.get('error_code', response.status_code)}
            message = data['result']
            return {'status': 'published', 'message_id': message['message_id'],
                    'channel_username': message.get('chat', {}).get('username')}
        finally:
            response.close()
    except Exception:
        # No retries: Telegram might have accepted a request before the connection broke.
        return {'status': 'send_unknown'}


def used_images(storage, filename='retro_publications.json'):
    path = storage / filename
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'slots': {}}
    used = {e['image_identity'] for e in state['slots'].values() if e.get('image_identity')}
    replacement = storage / 'retro_creek_replacement.json'
    if replacement.exists():
        entry = json.loads(replacement.read_text(encoding='utf-8'))
        if entry.get('image_url'):
            used.add(image_identity(entry['image_url']))
    return used


def select_photo(choices, used, include_article=False, eligible=None, allow_commons=False):
    diagnostics = []
    attempts = 0
    selected = source = None
    for candidate in choices:
        reason = None
        if not public_url(candidate.get('image_url')):
            reason = 'invalid_image_url'
        elif image_identity(candidate['image_url']) in used:
            reason = 'already_used'
        elif not source_allowed(candidate.get('source_url'), allow_commons=allow_commons):
            reason = 'unsupported_archive_source'
        elif eligible and not eligible(candidate):
            reason = 'usage_not_approved'
        elif selected is not None:
            reason = 'not_selected'
        elif attempts >= 3:
            reason = 'evidence_attempt_limit'
        else:
            attempts += 1
            try:
                if allow_commons and urlsplit(candidate['source_url']).hostname == 'commons.wikimedia.org':
                    source = load_evidence(candidate, allow_commons=True)
                else:
                    source = load_evidence(candidate, include_article=include_article)
                selected = candidate
                reason = 'selected'
            except Exception as exc:
                # Only known codes, never exception text / credentials.
                reason = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                    'archive_unavailable', 'archive_too_large', 'archive_article_text_unavailable',
                    'photo_date_or_location_not_confirmed', 'unsupported_archive_source',
                    'commons_manual_review_required', 'commons_metadata_changed'} else 'evidence_error'
        diagnostics.append({'id': candidate.get('id'), 'reason': reason})
    counts = {}
    for entry in diagnostics:
        counts[entry['reason']] = counts.get(entry['reason'], 0) + 1
    return selected, source, {'candidates': len(choices), 'evidence_attempts': attempts,
                             'counts': counts, 'items': diagnostics}


def run(storage=STORAGE, launch=LAUNCH, now=None):
    if (os.getenv('BOT_ENVIRONMENT') == 'staging' and os.getenv('GITHUB_ACTIONS') == 'true'
            and os.getenv('STAGING_OPERATION') == 'retro'):
        from retro_stage import run_existing_workflow
        return run_existing_workflow(storage)
    if os.getenv('GITHUB_ACTIONS') == 'true' and os.getenv('GITHUB_REF') != 'refs/heads/main':
        raise RuntimeError('Publication is allowed only on main')
    if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHANNEL_ID'):
        raise RuntimeError('Missing Telegram credentials')
    if os.getenv('SOURCE_VERIFIER') != 'openai':
        raise RuntimeError('OpenAI source verification is required')
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    slot = f'{year}-W{week:02d}'
    path = storage / 'retro_publications.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': 1, 'slots': {}}
    seed = json.loads(launch.read_text(encoding='utf-8'))
    previous = state['slots'].get(slot)
    # One reviewed config correction may retry a rejected FIRST post, never a send.
    retry_first = bool(previous and os.getenv('GITHUB_EVENT_NAME') == 'push'
                       and previous['status'] == 'verification_rejected'
                       and previous.get('seed_revision', 1) == 1 and seed.get('revision') == 2
                       and previous.get('image_identity') == image_identity(seed['image_url']))
    if previous and not retry_first:
        return {'status': 'already_attempted', 'previous_status': previous['status']}
    used = used_images(storage)
    if retry_first:
        used.discard(image_identity(seed['image_url']))
    seed_unused = retry_first or image_identity(seed['image_url']) not in used
    # A push of the one-time launch config can only publish the initial agreed post.
    if os.getenv('GITHUB_EVENT_NAME') == 'push' and not seed_unused:
        return {'status': 'first_post_already_attempted'}
    if seed_unused:
        choices = [seed]
    else:
        photos, _ = collect(storage / 'retro_photos.json', now)
        choices = list(reversed(photos['candidates']))
    item, source, diagnostics = select_photo(choices, used, include_article=seed_unused)
    if item is None:
        return {'status': 'no_verified_photo', 'rejected': diagnostics['evidence_attempts'],
                'diagnostics': diagnostics}
    record = {'status': 'preparing', 'at': now.isoformat(), 'image_identity': image_identity(item['image_url']),
              'source_url': item['source_url'], 'source_snapshot': source, 'diagnostics': diagnostics}
    if seed_unused:
        record['seed_revision'] = seed.get('revision', 1)
    if retry_first:
        record['previous_attempt'] = previous
    state['slots'][slot] = record
    persist(path, state)  # Limit generation to one attempt per week, even after crashes.
    try:
        text = seed['post_html'] if seed_unused else write_post(source, storage)
        if not valid_post(text):
            raise ValueError('Invalid post format or length')
        record['post_html'] = text
        verification = verify_summary(source, text, storage_dir=storage)
        record['verification'] = verification
        if verification['status'] != 'approved':
            record['status'] = 'verification_' + verification['status']
            persist(path, state)
            return {'status': record['status'], 'reason': verification.get('reason')}
    except Exception:
        record['status'] = 'preparation_failed'
        persist(path, state)
        raise RuntimeError('Retro preparation failed; weekly attempt retained') from None
    record['status'] = 'sending'
    persist(path, state)  # Durable no-resend marker BEFORE Telegram side effect.
    result = send_post(text, item['source_url'])
    record.update(result)
    persist(path, state)
    return result


if __name__ == '__main__':
    result = run()
    report = json.dumps(result, ensure_ascii=False)
    print(report)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write('## Дубай раньше\n\n' + report + '\n')
    if result['status'] in {'send_failed', 'send_unknown'}:
        raise SystemExit(1)
