"""Shared archive source policy. Discovery is not publication approval."""
from urllib.parse import urlsplit
from retro_photos import public_url

ARCHIVE_HOSTS = {'www.thenationalnews.com', 'thenationalnews.com'}


def source_allowed(url, allow_commons=False):
    if not public_url(url):
        return False
    if urlsplit(url).hostname in ARCHIVE_HOSTS and urlsplit(url).path.startswith('/news/uae/'):
        return True
    if allow_commons:
        from retro_commons import commons_review
        return commons_review(url) is not None
    return False


def usage_allowed(item, channel):
    """A manually reviewed, photo-specific permission, never an inferred license."""
    rights = item.get('usage_review') or {}
    if not isinstance(rights, dict):
        return False
    allowed = (rights.get('status') == 'approved'
            and rights.get('basis') in {'written_permission', 'explicit_license'}
            and all(isinstance(rights.get(k), str) and rights[k].strip()
                    for k in ('reviewer', 'reviewed_at', 'evidence', 'rights_holder', 'attribution'))
            and rights.get('image_url') == item.get('image_url')
            and rights.get('source_url') == item.get('source_url')
            and rights.get('channel_id') == channel
            and rights.get('telegram_republication') is True)
    if not allowed:
        return False
    if urlsplit(item.get('source_url', '')).hostname == 'commons.wikimedia.org':
        from retro_commons import commons_review, same_original
        review = commons_review(item['source_url'])
        return bool(review and same_original(item['image_url'], review)
                    and rights.get('basis') == 'explicit_license'
                    and rights.get('license_url') == review['metadata']['LicenseUrl'].rstrip('/') + '/'
                    and rights.get('rights_holder') == review['author']
                    and rights.get('attribution') == review['attribution']
                    and rights.get('image_sha1') == review['sha1']
                    and rights.get('modifications') == 'none'
                    and rights.get('share_alike') is True)
    return True


def discovery_eligibility(url):
    if source_allowed(url):
        return 'needs_review'
    if source_allowed(url, allow_commons=True):
        return 'commons_manual_usage_review_required'
    return 'unsupported_archive_source'
