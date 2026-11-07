"""Month-end rubric, sharing the existing workflow lock and account budget.

No external side effects unless explicitly executed by the production workflows.
Sending is at-most-once: an ambiguous Telegram receipt requires human reconciliation.
"""
import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openai_budget import atomic_json, BudgetUnavailable
from openai_writer import prepare_post
from openai_verifier import verify_with_openai
from source_verification import REVIEW_PROMPT, _validate_completion, fingerprint
from search_sources import fetch_article, url_identity
from monthly_discovery import discover, allowed_primary

ROOT = Path(__file__).resolve().parents[1]
STORAGE = ROOT / 'storage/dubai_news'
DUBAI = timezone(timedelta(hours=4))
LIMIT_REASONS = {'Daily OpenAI call limit reached', 'Monthly OpenAI budget reached',
                 'Daily OpenAI search limit reached'}
MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
          'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')


def target_month(now, publish=False):
    """Last day, or bounded first-day carryover after the UTC budget reset."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('timezone_aware_time_required')
    local = now.astimezone(DUBAI)
    if local.day == 1:
        return local.date() if local.hour >= 4 else None
    tomorrow = local.date() + timedelta(days=1)
    if tomorrow.day != 1 or local.hour < (18 if publish else 17):
        return None
    return tomorrow


def production_allowed():
    return (os.getenv('GITHUB_ACTIONS') == 'true'
            and os.getenv('GITHUB_REF') == 'refs/heads/main'
            and os.getenv('GITHUB_EVENT_NAME') in {'schedule', 'workflow_dispatch', 'workflow_run'})


def persist(path, state):
    """Push the slot marker before discovery and before any Telegram request."""
    import subprocess
    atomic_json(path, state)
    if os.getenv('GITHUB_ACTIONS') == 'true':
        if not production_allowed():
            raise RuntimeError('monthly_production_guard')
        relative = path.resolve().relative_to(ROOT).as_posix()
        for args in (['add', '--', relative],
                     ['commit', '--only', '-m', 'Save monthly rubric state [skip ci]', '--', relative],
                     ['push', 'origin', 'HEAD:main']):
            subprocess.run(['git', '-C', str(ROOT), *args], check=True,
                           capture_output=True, timeout=60)


def load_state(storage):
    path = storage / 'monthly_changes.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': 1, 'months': {}}
    if state.get('version') != 1 or not isinstance(state.get('months'), dict):
        raise ValueError('invalid_monthly_state')
    return path, state


def collect_sources(urls, now):
    notices, seen = [], set()
    for url in urls[:5]:
        if not allowed_primary(url):
            continue
        article = fetch_article(url, now, max_age_hours=24 * 366,
                                require_full=True, url_policy=allowed_primary)
        if not article or not allowed_primary(article['link']):
            continue
        identity = url_identity(article['link'])
        if identity in seen:
            continue
        seen.add(identity)
        # Full downloaded articles only. No search snippets or truncation.
        notice = {'title': article['title'], 'text': article['description'],
                  'url': article['link'], 'published_at': article['published_at'],
                  'retrieved_at': article['source_retrieved_at']}
        if len(notice['text']) > 10000:
            continue
        notices.append(notice)
        if len(notices) == 3:
            break
    # Bound whole requests without clipping a notice or a material exception.
    while notices and sum(len(n['text']) + len(n['title']) + len(n['url']) + 100 for n in notices) > 11000:
        notices.pop()
    return notices


def combined_source(notices):
    return {'title': 'Official notices', 'url': notices[0]['url'],
            'text': '\n\n'.join(f"Notice {i}: {n['title']}\nSource: {n['url']}\n{n['text']}"
                                for i, n in enumerate(notices, 1))}


def review(source, post, storage, target):
    policy = (REVIEW_PROMPT + f' This is a monthly rubric for {target.isoformat()}. '
              'Every listed change MUST be explicitly confirmed as taking effect exactly on this day, '
              'month and year in its own official notice, for Dubai or UAE residents. '
              'Reject if this is only the publication date, a deadline, tentative proposal, speculation, '
              'a prior rollout or another effective date. Do not infer missing dates/year. '
              'Reject empty roundups or claims that no changes exist. Check EVERY factual assertion '
              'including all dates, scope, exceptions and the heading. Never mix notices as evidence '
              'for different changes. Retrieved source URLs are provenance, not evidence by themselves.')
    return verify_with_openai(source, post, policy, _validate_completion, 40, storage)


def prepare(storage=STORAGE, now=None, *, execute=False):
    now = now or datetime.now(timezone.utc)
    target = target_month(now)
    if target is None:
        return {'status': 'not_due'}
    if not execute or not production_allowed():
        return {'status': 'dry_run', 'target': target.isoformat()}
    storage.mkdir(parents=True, exist_ok=True)
    path, state = load_state(storage)
    slot = target.strftime('%Y-%m')
    previous = state['months'].get(slot)
    carryover = now.astimezone(DUBAI).day == 1
    if previous:
        if not (carryover and previous['status'] == 'budget_deferred'
                and not previous.get('carryover_used')):
            return {'status': 'already_attempted', 'previous_status': previous['status']}
        record = previous
        record['status'] = 'preparing'
        record['carryover_used'] = True
        record['carryover_at'] = now.isoformat()
    elif carryover:
        return {'status': 'no_pending_issue'}
    else:
        record = {'status': 'preparing', 'target': target.isoformat(), 'at': now.isoformat()}
        state['months'][slot] = record
    persist(path, state)  # A crash consumes the monthly attempt, never a second paid search.
    try:
        if 'urls' not in record:
            record['urls'] = discover(storage, target, now)
            persist(path, state)
        # Re-fetch primary notices after a limit deferral: stale snapshots cannot
        # establish a new day's publication; writing cache remains content-keyed.
        notices = collect_sources(record['urls'], now)
        record['notices'] = notices
        if not notices:
            record['status'] = 'no_confirmed_changes'
        else:
            source = combined_source(notices)
            record['source_snapshot'] = source
            written = prepare_post(source, storage, kind='monthly', target_date=target.isoformat())
            record['writing_status'] = written['status']
            if written['status'] != 'prepared':
                record['status'] = ('budget_deferred' if written.get('budget_limited')
                                    else 'preparation_' + written['status'])
            elif not written['post'].strip() or len(written['post']) > 800:
                record['status'] = 'invalid_post'
            else:
                heading = f'📅 <b>Что нового с 1 {MONTHS[target.month - 1]} {target.year}</b>'
                post = heading + '\n\n' + html.escape(written['post'].strip())
                record['post_html'] = post
                result = review(source, post, storage, target)
                record['verification'] = result
                record['status'] = ('ready' if result['status'] == 'approved' else
                                    'budget_deferred' if result.get('reason') in LIMIT_REASONS else
                                    'verification_' + result['status'])
    except BudgetUnavailable as exc:
        record['status'] = 'budget_deferred' if str(exc) in LIMIT_REASONS else 'preparation_failed'
    except Exception:
        record['status'] = 'preparation_failed'
    persist(path, state)
    return {'status': record['status'], 'target': target.isoformat()}


def send_post(text):
    import requests
    token, channel = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHANNEL_ID')
    if not token or not channel:
        return {'status': 'send_failed', 'reason': 'missing_telegram_credentials'}
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                 json={'chat_id': channel, 'text': text, 'parse_mode': 'HTML',
                                       'link_preview_options': {'is_disabled': True}},
                                 timeout=30, allow_redirects=False)
        try:
            data = response.json()
            if response.status_code != 200 or data.get('ok') is not True:
                return {'status': 'send_failed'}
            return {'status': 'published', 'message_id': data['result']['message_id']}
        finally:
            response.close()
    except Exception:
        return {'status': 'send_unknown'}  # Never retry: Telegram may have accepted it.


def publish(storage=STORAGE, now=None, *, execute=False):
    now = now or datetime.now(timezone.utc)
    target = target_month(now, publish=True)
    if target is None:
        return {'status': 'not_due'}
    if not execute or not production_allowed():
        return {'status': 'dry_run', 'target': target.isoformat()}
    path, state = load_state(storage)
    record = state['months'].get(target.strftime('%Y-%m'), {})
    if record.get('status') != 'ready':
        return {'status': 'not_ready', 'previous_status': record.get('status')}
    source, post = record.get('source_snapshot'), record.get('post_html', '')
    verification = record.get('verification', {})
    if (record.get('target') != target.isoformat() or not source or not post
            or verification.get('status') != 'approved' or verification.get('provider') != 'openai'
            or verification.get('source_hash') != fingerprint(source)
            or verification.get('summary_hash') != fingerprint(post)):
        return {'status': 'verification_invalid'}
    # Links derive only from the downloaded evidence, never from generated prose.
    notices = record.get('notices', [])
    if not notices or combined_source(notices) != source or not all(allowed_primary(n['url']) for n in notices):
        return {'status': 'verification_invalid'}
    # Recheck the downloaded notices immediately before delivery (also on the
    # first-day carryover). Changed/missing evidence never inherits yesterday's
    # approval. This uses no paid call; any changed claims need a separate review.
    current = collect_sources([n['url'] for n in notices], now)
    if not current or combined_source(current) != source:
        return {'status': 'evidence_changed_or_unavailable'}
    links = '\n'.join(f'<a href="{html.escape(n["url"], quote=True)}">Источник {i}</a>'
                      for i, n in enumerate(notices, 1))
    text = post + '\n\n' + links + '\n\n#СПервогоЧисла'
    if len(text) > 4096:
        return {'status': 'post_too_long'}
    record['status'] = 'sending'
    record['send_started_at'] = now.isoformat()
    persist(path, state)  # Successful remote push required before side effect.
    result = send_post(text)
    record.update(result)
    persist(path, state)
    return result


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2 or sys.argv[1] not in {'prepare', 'publish'}:
        raise SystemExit('Usage: monthly_changes.py prepare|publish')
    try:
        result = globals()[sys.argv[1]](execute=True)
    except Exception:
        # Do not expose credentials/response bodies or interrupt ordinary news.
        # Durable slot markers still fail closed after any persistence failure.
        result = {'status': 'technical_failure', 'operation': sys.argv[1]}
    print(json.dumps(result, ensure_ascii=False))
    if result['status'] in {'technical_failure', 'preparation_failed', 'send_failed', 'send_unknown', 'verification_invalid', 'post_too_long', 'evidence_changed_or_unavailable'}:
        print('::error::Monthly rubric blocked: ' + result['status'] + '; inspect monthly_changes.json before any retry')
    elif result['status'] in {'budget_deferred', 'preparation_deferred', 'verification_deferred', 'verification_error'}:
        print('::warning::Monthly rubric deferred: ' + result['status'])
    if result['status'] != 'not_due' and os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write('## Monthly changes\n\n' + json.dumps(result, ensure_ascii=False) + '\n')
