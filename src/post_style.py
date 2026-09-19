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
        if tag in {"b", "code"}:
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


def format_summary(text):
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    parser = PostHTML()
    parser.feed(text)
    parser.close()
    text = "".join(parser.parts) + "".join(f"</{tag}>" for tag in reversed(parser.stack))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    headline = re.match(r"^<b>(.*?)</b>\s*(.*)$", text, re.S)
    if headline:
        title, body = headline.groups()
        return f"<b>{title.strip()}</b>" + ("\n\n" + body.strip() if body.strip() else "")
    lines = text.split("\n", 1)
    if len(lines) == 2 and len(lines[0]) <= 200:
        return f"<b>{lines[0]}</b>\n\n{lines[1].strip()}"
    return text


def headline_only(text):
    return bool(re.fullmatch(r"<b>[^<]+</b>", text.strip(), re.S))


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
