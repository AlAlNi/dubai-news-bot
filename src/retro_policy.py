"""Shared archive source policy. Discovery is not publication approval."""
from urllib.parse import urlsplit
from retro_photos import public_url

ARCHIVE_HOSTS = {'www.thenationalnews.com', 'thenationalnews.com'}


def source_allowed(url):
    return (public_url(url) and urlsplit(url).hostname in ARCHIVE_HOSTS
            and urlsplit(url).path.startswith('/news/uae/'))


def usage_allowed(item, channel):
    """A manually reviewed, photo-specific permission, never an inferred license."""
    rights = item.get('usage_review') or {}
    if not isinstance(rights, dict):
        return False
    return (rights.get('status') == 'approved'
            and rights.get('basis') in {'written_permission', 'explicit_license'}
            and all(isinstance(rights.get(k), str) and rights[k].strip()
                    for k in ('reviewer', 'reviewed_at', 'evidence', 'rights_holder', 'attribution'))
            and rights.get('image_url') == item.get('image_url')
            and rights.get('source_url') == item.get('source_url')
            and rights.get('channel_id') == channel
            and rights.get('telegram_republication') is True)
