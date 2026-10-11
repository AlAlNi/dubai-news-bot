"""Weekly photo release from an explicit manual queue; discovery is not approval."""
import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from retro_photos import persist, public_url
from retro_policy import source_allowed, usage_allowed
from retro_photo import caption_html, photo_payload, validate_destination, send_photo
from retro_publish import image_identity, used_images, select_photo
from source_verification import verify_summary

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT/'config/retro_publish_queue.json'
STORAGE = ROOT/'storage/dubai_news'
STAGING = ROOT/'storage/dubai_news_staging'


def candidate_reason(item, used):
    if not public_url(item.get('image_url')):
        return 'invalid_image_url'
    identity = image_identity(item['image_url'])
    if identity in used:
        return 'already_used_in_production_or_test'
    if not source_allowed(item.get('source_url'), allow_commons=True):
        return 'unsupported_or_unreviewed_source'
    review = item.get('publication_review')
    if not isinstance(review, dict) or review.get('status') != 'approved':
        return 'manual_publication_review_required'
    if (review.get('target_username') != 'rudubainews' or review.get('image_identity') != identity
            or review.get('visual_checked') is not True or not review.get('reviewer') or not review.get('reviewed_at')):
        return 'publication_review_mismatch'
    rights = item.get('usage_review') or {}
    if (not isinstance(rights, dict) or rights.get('target_username') != 'rudubainews'
            or rights.get('channel_id') not in (None, '@rudubainews', 'reviewed_public_target:rudubainews')):
        return 'usage_target_not_approved'
    checked = copy.deepcopy(item)
    checked['usage_review']['channel_id'] = 'reviewed_public_target:rudubainews'
    if not usage_allowed(checked, 'reviewed_public_target:rudubainews'):
        return 'usage_not_approved'
    try:
        payload = photo_payload(checked, 'reviewed_public_target:rudubainews')
    except ValueError:
        return 'invalid_caption_or_rights'
    if hashlib.sha256(payload['caption'].encode()).hexdigest() != review.get('caption_sha256'):
        return 'approved_caption_changed'
    return 'pending_live_evidence_recheck'


def queue_inventory(queue, storage, staging):
    data = json.loads(Path(queue).read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('candidates'), list):
        raise ValueError('invalid_reviewed_queue')
    used = used_images(storage) | used_images(staging) | used_images(staging, 'retro_photo_publications.json')
    diagnostics, ready = [], []
    for item in data['candidates']:
        if not isinstance(item, dict):
            reason, item = 'invalid_candidate', {'id': None}
        else:
            reason = candidate_reason(item, used)
        diagnostics.append({'id': item.get('id'), 'reason': reason})
        if reason == 'pending_live_evidence_recheck':
            ready.append(item)
    counts = {}
    for entry in diagnostics:
        counts[entry['reason']] = counts.get(entry['reason'], 0) + 1
    return ready, used, {'candidates': len(diagnostics), 'ready_for_recheck': len(ready),
                         'counts': counts, 'items': diagnostics}


def run_weekly(*, storage=STORAGE, queue=QUEUE, staging=STAGING, now=None, execute=False):
    storage, queue, staging = Path(storage), Path(queue), Path(staging)
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    slot = f'{year}-W{week:02d}'
    path = storage/'retro_publications.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': 1, 'slots': {}}
    previous = state['slots'].get(slot)
    if previous:
        return {'status': 'already_attempted', 'previous_status': previous['status']}
    ready, used, diagnostics = queue_inventory(queue, storage, staging)
    if not ready:
        return {'status': 'no_reviewed_photo', 'reason': 'manual_queue_required', 'diagnostics': diagnostics}
    if not execute:
        return {'status': 'reviewed_queue_ready', 'reason': 'facts_and_destination_not_rechecked', 'diagnostics': diagnostics}
    if not (os.getenv('GITHUB_ACTIONS') == 'true' and os.getenv('GITHUB_REF') == 'refs/heads/main'
            and os.getenv('GITHUB_WORKFLOW') == 'Publish Dubai Then'
            and os.getenv('GITHUB_EVENT_NAME') in {'schedule', 'workflow_dispatch'}
            and os.getenv('BOT_ENVIRONMENT', 'production') == 'production'
            and os.getenv('SOURCE_VERIFIER') == 'openai'):
        raise ValueError('approved_production_workflow_required')
    token, channel = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHANNEL_ID')
    if not token or not channel:
        raise ValueError('production_credentials_required')
    storage.mkdir(parents=True, exist_ok=True)
    lock = storage/'retro_publish.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {'status': 'locked', 'reason': 'reconcile_before_retry'}
    os.close(fd)
    try:
        # Read state/history again under lock; no stale pre-lock approval can send twice.
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': 1, 'slots': {}}
        previous = state['slots'].get(slot)
        if previous:
            return {'status': 'already_attempted', 'previous_status': previous['status']}
        ready, used, diagnostics = queue_inventory(queue, storage, staging)
        if not ready:
            return {'status': 'no_reviewed_photo', 'reason': 'manual_queue_required', 'diagnostics': diagnostics}
        confirmed_channel = validate_destination(token, channel, 'rudubainews')
        choices = copy.deepcopy(ready)
        for item in choices:
            item['usage_review']['channel_id'] = confirmed_channel
        item, source, evidence = select_photo(choices, used, eligible=lambda c: usage_allowed(c, confirmed_channel), allow_commons=True)
        if item is None:
            return {'status': 'no_verified_photo', 'reason': 'live_evidence_recheck_failed',
                    'diagnostics': diagnostics, 'evidence_diagnostics': evidence}
        payload = photo_payload(item, confirmed_channel)
        record = {'status': 'verifying', 'at': now.isoformat(), 'image_identity': image_identity(item['image_url']),
                  'source_snapshot': source, 'target_username': 'rudubainews',
                  'caption_html': payload['caption'], 'publication_review': item['publication_review'],
                  'usage_review': {k:v for k,v in item['usage_review'].items() if k != 'channel_id'},
                  'diagnostics': diagnostics, 'evidence_diagnostics': evidence}
        state['slots'][slot] = record
        persist(path, state)  # Durable attempt before the one standard paid verifier.
        verification = verify_summary(source, payload['caption'], storage_dir=storage)
        record['verification'] = verification
        if verification['status'] != 'approved':
            record['status'] = 'verification_' + verification['status']
            persist(path, state)
            return {'status': record['status'], 'reason': verification.get('reason')}
        record['status'] = 'sending'
        persist(path, state)  # Durable no-resend marker before Telegram.
        result = send_photo(payload, token)
        record.update(result)
        persist(path, state)
        return result
    finally:
        lock.unlink()
