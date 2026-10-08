"""Pinned manual reviews of individual Commons cards; no domain-wide approval."""
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests
from source_verification import source_snapshot

REVIEWS = Path(__file__).resolve().parents[1] / 'config/retro_commons_reviews.json'
API = 'https://commons.wikimedia.org/w/api.php'


def reviews():
    return json.loads(REVIEWS.read_text(encoding='utf-8'))['files']


def commons_review(url):
    if not isinstance(url, str):
        return None
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname != 'commons.wikimedia.org' or p.username
            or p.password or p.port not in (None, 443) or p.query or p.fragment):
        return None
    key = unquote(p.path).replace(' ', '_')
    for review in reviews():
        if unquote(urlsplit(review['source_url']).path).replace(' ', '_') == key:
            if review.get('status') == 'manually_reviewed_for_draft':
                return review
    return None


def same_original(url, review):
    p, original = urlsplit(url), urlsplit(review['image_url'])
    return (p.scheme == 'https' and p.hostname == original.hostname == 'upload.wikimedia.org'
            and not p.username and not p.password and p.port in (None, 443)
            and unquote(p.path) == unquote(original.path))


def commons_image_identity(url):
    for review in reviews():
        if same_original(url, review):
            return 'commons-sha1:' + review['sha1']
    return None


def validate_metadata(item, data):
    review = commons_review(item.get('source_url'))
    if not review or not same_original(item.get('image_url', ''), review):
        raise ValueError('commons_manual_review_required')
    pages = data.get('query', {}).get('pages', {})
    if len(pages) != 1:
        raise ValueError('commons_metadata_changed')
    page = next(iter(pages.values()))
    info = page.get('imageinfo', [{}])[0]
    if (page.get('pageid') != review['pageid'] or page.get('title') != review['title']
            or info.get('sha1') != review['sha1']
            or (info.get('width'), info.get('height')) != tuple(review['dimensions'])
            or not same_original(info.get('url', ''), review)
            or commons_review(info.get('descriptionurl')) != review):
        raise ValueError('commons_metadata_changed')
    for key, value in review['metadata'].items():
        if info.get('extmetadata', {}).get(key, {}).get('value') != value:
            raise ValueError('commons_metadata_changed')
    # DateTime is upload date, never capture date. Facts come from pinned DateTimeOriginal
    # and the exact description, including uncertainty about the specific beach.
    text = ('Selected photograph: ' + review['title']
            + '\nCapture date recorded on Commons: ' + review['metadata']['DateTimeOriginal']
            + '\nAuthor: ' + review['author']
            + '\nOriginal author description: ' + review['metadata']['ImageDescription']
            + '\nLicense: ' + review['metadata']['LicenseShortName']
            + '\nLicense URL: ' + review['metadata']['LicenseUrl']
            + '\nImage handling: the original JPEG was reviewed visually and matched the Commons SHA-1; no edits were made.'
            + '\nProvenance: ' + review['provenance_note'])
    return source_snapshot('Historical photograph of Dubai', text, review['source_url'])


def load_commons_evidence(item):
    review = commons_review(item.get('source_url'))
    if not review:
        raise ValueError('commons_manual_review_required')
    response = requests.get(API, params={'action': 'query', 'titles': review['title'],
        'prop': 'imageinfo', 'iiprop': 'url|extmetadata|sha1|size', 'format': 'json'},
        timeout=20, allow_redirects=False, stream=True, headers={'User-Agent': 'DubaiNewsBot/1.0'})
    try:
        if response.status_code != 200 or 'json' not in response.headers.get('Content-Type', '').lower():
            raise ValueError('archive_unavailable')
        chunks, size = [], 0
        for chunk in response.iter_content(8192):
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError('archive_too_large')
            chunks.append(chunk)
        return validate_metadata(item, json.loads(b''.join(chunks)))
    finally:
        response.close()
