"""Read dated article text from a small set of news publishers, never from AI prose."""
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from http_client import request_with_retry


ALLOWED_DOMAINS = (
    "mediaoffice.ae", "rta.ae", "dubaipolice.gov.ae", "dewa.gov.ae",
    "dubaiairports.ae", "emirates.com", "gulfnews.com", "khaleejtimes.com",
    "thenationalnews.com", "arabianbusiness.com", "gulfbusiness.com",
)
MAX_PAGE_BYTES = 2_000_000
UAE_TIMEZONE = timezone(timedelta(hours=4))


def allowed_url(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and any(host == domain or host.endswith("." + domain) for domain in ALLOWED_DOMAINS))
    except (TypeError, ValueError):
        return False


def url_identity(url):
    try:
        parsed = urlsplit(url)
        return urlunsplit(("https", (parsed.hostname or "").lower().removeprefix("www."),
                           parsed.path.rstrip("/"), "", ""))
    except (TypeError, ValueError):
        return ""


def clean_text(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", value))).strip()


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.jsonld, self.body, self.heading = {}, [], [], []
        self.stack, self.script = [], None
        self.relative_date, self.date_level = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            self.meta[key] = attrs.get("content") or ""
        parent_skip = self.stack[-1][1] if self.stack else False
        parent_body = self.stack[-1][2] if self.stack else False
        css = (attrs.get("class") or "").lower()
        skip = parent_skip or tag in {"script", "style", "nav", "aside", "header", "footer"}
        skip = skip or any(word in css for word in ("related", "advert", "newsletter", "social-share", "also-read"))
        article = parent_body or tag == "article" or attrs.get("itemprop") == "articleBody"
        article = article or bool(set(css.split()) & {"article-body", "story-body", "story-content", "article-content", "news-detail-left"})
        if "banner-i-rel" in css.split():
            self.date_level = len(self.stack) + 1
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.script = []
        if tag not in {"meta", "link", "img", "br", "hr", "input", "source", "wbr", "area", "base", "embed", "param", "track", "col"}:
            self.stack.append((tag, skip, article))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            try:
                self.jsonld.append(json.loads("".join(self.script)))
            except (ValueError, TypeError):
                pass
            self.script = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if self.date_level is not None and len(self.stack) < self.date_level:
            self.date_level = None

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if not self.stack or self.stack[-1][1]:
            return
        if self.date_level is not None:
            self.relative_date.append(data)
        if any(tag == "h1" for tag, _, _ in self.stack):
            self.heading.append(data)
        if self.stack[-1][2]:
            self.body.append(data)


def article_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from article_nodes(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if isinstance(types, list) and any(t in {"NewsArticle", "Article", "ReportageNewsArticle"} for t in types):
            yield value
        # Only page roots/graphs, not arbitrary nested related-article carousels.
        for key in ("@graph", "mainEntity"):
            yield from article_nodes(value.get(key))


def publication_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        return (parsed.replace(tzinfo=UAE_TIMEZONE) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def mediaoffice_date(parser, url, now):
    """Confirm a dated Media Office news URL against its visible relative age."""
    host = (urlsplit(url).hostname or "").lower()
    if host not in {"mediaoffice.ae", "www.mediaoffice.ae"}:
        return None
    path = re.fullmatch(r"/en/news/(\d{4})/[a-z]+/(\d{2})-(\d{2})/[^/]+/?", urlsplit(url).path)
    age = re.fullmatch(r"(\d+)\s+(minute|hour|day)s?\s+ago", clean_text(" ".join(parser.relative_date)).lower())
    if not path or not age:
        return None
    try:
        date = datetime(int(path[1]), int(path[3]), int(path[2]), tzinfo=UAE_TIMEZONE)
        inferred = now - timedelta(**{age[2] + "s": int(age[1])})
        if inferred.astimezone(UAE_TIMEZONE).date() != date.date():
            return None
        # Save the conservative beginning of the confirmed date, not an invented exact time.
        return date.isoformat()
    except (ValueError, OverflowError):
        return None


def parse_article(html, url, now=None):
    now = now or datetime.now(timezone.utc)
    parser = ArticleHTML()
    parser.feed(html)
    nodes = list(article_nodes(parser.jsonld))
    matching = []
    for node in nodes:
        reference = node.get("url") or node.get("mainEntityOfPage")
        if isinstance(reference, dict):
            reference = reference.get("@id")
        if isinstance(reference, str) and url_identity(urljoin(url, reference)) == url_identity(url):
            matching.append(node)
    if len(matching) == 1:
        node = matching[0]
    elif len(matching) > 1:
        # Gulf News emits both Article and NewsArticle for the same canonical URL.
        specific = [n for n in matching if n.get("@type") == "NewsArticle"]
        dates = {publication_date(n.get("datePublished")) for n in matching}
        if len(specific) != 1 or len(dates) != 1 or None in dates:
            return None
        node = specific[0]
    elif len(nodes) == 1 and not (nodes[0].get("url") or nodes[0].get("mainEntityOfPage")):
        node = nodes[0]
    elif nodes:
        return None  # Ambiguous/mismatched structured article; do not mix its date with another story.
    elif parser.meta.get("og:type") == "article":
        node = {}
    elif mediaoffice_date(parser, url, now):
        node = {"datePublished": mediaoffice_date(parser, url, now), "headline": " ".join(parser.heading)}
    else:
        return None
    raw_date = node.get("datePublished") or parser.meta.get("article:published_time") or parser.meta.get("datepublished")
    published = publication_date(raw_date)
    if published is None or not now - timedelta(hours=48) <= published <= now + timedelta(minutes=5):
        return None
    title = node.get("headline") or parser.meta.get("og:title") or " ".join(parser.heading)
    body = node.get("articleBody") or " ".join(parser.body)
    if not isinstance(title, str) or not isinstance(body, str):
        return None
    title, body = clean_text(title), clean_text(body)
    if len(title) < 10 or len(body) < 200:
        return None
    # Avoid returning a broken final sentence when limiting a long article.
    if len(body) > 12000:
        end = max(body.rfind(". ", 6000, 12000), body.rfind("! ", 6000, 12000), body.rfind("? ", 6000, 12000))
        if end == -1:
            return None
        body = body[:end + 1]
    return {
        "title": title, "description": body, "link": url,
        "source": (urlsplit(url).hostname or "").removeprefix("www."),
        "published_at": published.isoformat(), "source_retrieved_at": now.isoformat(),
        "method": "openai_web_search", "source_priority": 3, "lang": "en",
    }


def fetch_article(url, now=None):
    for _ in range(4):
        if not allowed_url(url):
            return None
        try:
            response = request_with_retry(
                "GET", url, timeout=15, max_attempts=1, allow_redirects=False, stream=True,
                headers={"User-Agent": "DubaiNewsBot/1.0 (+https://github.com/AlAlNi/dubai-news-bot)"},
            )
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", "").lower():
                    return None
                chunks, total = [], 0
                for chunk in response.iter_content(chunk_size=8192):
                    total += len(chunk)
                    if total > MAX_PAGE_BYTES:
                        return None
                    chunks.append(chunk)
                raw = b"".join(chunks)
                encoding = response.encoding if response.encoding and response.encoding.lower() != "iso-8859-1" else "utf-8"
                return parse_article(raw.decode(encoding, errors="replace"), url, now)
            finally:
                response.close()
        except Exception:
            return None
    return None
