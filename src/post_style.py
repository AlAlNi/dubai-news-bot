"""Telegram presentation applied before source-fidelity verification."""
import re
from html import escape, unescape
from html.parser import HTMLParser


class PostHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag in {"b", "i", "code", "blockquote"}:
            self.parts.append(f"<{tag}>")
            self.stack.append(tag)
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                closing = self.stack.pop()
                self.parts.append(f"</{closing}>")
                if closing == tag:
                    break
        elif tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(escape(data, quote=False))


def _clean_html(text):
    parser = PostHTML()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts) + "".join(f"</{tag}>" for tag in reversed(parser.stack))


def format_summary(text):
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", _clean_html(text)).strip()
    first, separator, rest = text.partition("\n")
    # Detect a heading only inside the first line. Never nest <b> around an
    # emoji followed by a bold heading, nor consume body paragraphs as title.
    heading = re.fullmatch(r"<b>(.*?)</b>\s*(.*)", first, re.S)
    if heading and heading[2].strip():
        first, rest = heading[1], heading[2].strip() + ("\n" + rest if rest else "")
        separator = "\n"
    title = re.sub(r"<[^>]*>", "", first).strip()
    if len(title) > 200 or (not separator and not first.startswith("<b>")):
        return text
    body = _clean_html(rest).strip()  # drops orphan closings left by malformed wrappers
    count = 0

    def selective_bold(match):
        nonlocal count
        value = match[1]
        plain = unescape(re.sub(r"<[^>]*>", "", value)).strip()
        if ("\n" in value or len(plain) > 60 or len(plain.split()) > 8
                or plain.endswith((".", "!", "?")) or count >= 2):
            return value
        count += 1
        return "<b>" + value + "</b>"

    body = re.sub(r"<b>(.*?)</b>", selective_bold, body, flags=re.S)
    return "<b>" + title + "</b>" + ("\n\n" + body if body else "")


_EMOJI_CHAR = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


def plain_news_summary(text):
    """Keep only the headline bold; news decoration belongs to the AI stage."""
    text = format_summary(text)
    title, separator, body = text.partition("\n\n")
    title = re.sub(r"<[^>]*>", "", title)
    title = _EMOJI_CHAR.sub("", title).strip()
    body = re.sub(r"</?(?:b|i|code|blockquote)>", "", body)
    body = _EMOJI_CHAR.sub("", body)
    body = "\n".join(line.strip() for line in body.split("\n"))
    return "<b>" + title + "</b>" + (separator + body if separator else "")


def headline_only(text):
    return bool(re.fullmatch(r"<b>[^<]+</b>", text.strip(), re.S))


def compact_summary(text, limit=900):
    """Keep complete paragraphs before fact verification; never slice HTML or a quote."""
    def visible(value):
        return len(unescape(re.sub(r'<[^>]*>', '', value)))
    if visible(text) <= limit:
        return text
    # Blank lines inside a quote are not paragraph boundaries.
    text = re.sub(r'<blockquote>.*?</blockquote>',
                  lambda m: re.sub(r'\n\s*\n', '\n', m.group()), text, flags=re.S)
    kept = []
    for paragraph in re.split(r'\n\s*\n', text):
        candidate = '\n\n'.join([*kept, paragraph])
        if visible(candidate) > limit:
            break
        kept.append(paragraph)
    return '\n\n'.join(kept)


def incomplete_excerpt(text):
    plain = unescape(re.sub(r"<[^>]*>", " ", text))
    return bool(re.search(r"\[\s*(?:\+?\d+\s+chars|\.{3}|…)\s*\]|(?:\.{3}|…)\s*$", plain, re.I))


def clean_editorial_text(text):
    """Remove exact repeats and editorial scaffolding, never infer missing facts."""
    text = re.sub(r"Об этом (?:сообщается|говорится) в материале под заголовком\s+«[^»]*»\.?", "", text)
    text = re.sub(r"(?:В исходном тексте|В исходнике|В статье) (?:отмечается|указывается|говорится),? что\s+", "", text)
    text = re.sub(r"При этом указывается,? что\s+", "", text)
    # Keep named attribution (e.g. 'По данным RTA') and uncertainty intact.
    def signature(value):
        return re.sub(r"[\W_]+", " ", unescape(re.sub(r"<[^>]*>", "", value)).lower()).strip()
    heading = re.match(r"^(<b>.*?</b>)\s*(.*)$", text, re.S)
    if not heading:
        return text
    title, body = heading.groups()
    seen = {signature(title)}
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", body):
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])", paragraph.strip()):
            key = signature(sentence)
            if key and key not in seen:
                kept.append(sentence)
                seen.add(key)
        if kept:
            paragraphs.append(" ".join(kept))
    return "\n\n".join([title, *paragraphs])
