"""Bounded, free fallback from publisher section links to verified article HTML."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from http_client import request_with_retry
from search_sources import allowed_url, fetch_article, url_identity

SECTIONS = ('https://gulfnews.com/uae/dubai',
            'https://www.thenationalnews.com/news/uae/')
MAX_SECTION_BYTES = 3_000_000
MAX_PER_SECTION = 4


class SectionLinks(HTMLParser):
    def __init__(self, section):
        super().__init__()
        self.section, self.urls, self.seen = section, [], set()

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href') or ''
        url = urljoin(self.section, href)
        if not allowed_url(url):
            return
        parsed = urlsplit(url)
        if parsed.hostname != urlsplit(self.section).hostname:
            return
        path = parsed.path.lower()
        if 'dubai' not in path:
            return
        article_path = (re.search(r'-1\.\d+$', path) if parsed.hostname == 'gulfnews.com'
                        else re.match(r'^/news/uae/\d{4}/\d{2}/\d{2}/[^/]+/?$', path))
        if not article_path:
            return
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
        identity = url_identity(url)
        if identity not in self.seen:
            self.seen.add(identity)
            self.urls.append(url)


def section_urls(section, diagnostics):
    try:
        response = request_with_retry('GET', section, timeout=15, max_attempts=1,
                                      allow_redirects=False, stream=True,
                                      headers={'User-Agent': 'DubaiNewsBot/1.0 (+https://github.com/AlAlNi/dubai-news-bot)'})
        try:
            if response.status_code != 200:
                raise ValueError(f'http_{response.status_code}')
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                raise ValueError('not_html')
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > MAX_SECTION_BYTES:
                    raise ValueError('section_too_large')
                chunks.append(chunk)
            parser = SectionLinks(section)
            parser.feed(b''.join(chunks).decode('utf-8', errors='replace'))
            return parser.urls
        finally:
            response.close()
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        diagnostics.append({'url': section, 'reason': reason})
        return []


def latest_articles(seen_urls, now):
    seen = {url_identity(url) for url in seen_urls}
    items, diagnostics, checked = [], [], 0
    for section in SECTIONS:
        candidates = [u for u in section_urls(section, diagnostics) if url_identity(u) not in seen]
        for url in candidates[:MAX_PER_SECTION]:
            seen.add(url_identity(url))
            checked += 1
            article = fetch_article(url, now, diagnostics=diagnostics)
            if article:
                canonical = url_identity(article['link'])
                if canonical != url_identity(url) and canonical in seen:
                    continue
                seen.add(canonical)
                article['method'] = 'publisher_latest'
                items.append(article)
            if len(items) >= 5:
                break
        if len(items) >= 5:
            break
    print(f'Publisher sections: checked={checked}, readable_fresh={len(items)}, rejections={diagnostics}')
    return {'items': items, 'checked': checked, 'rejections': diagnostics}
