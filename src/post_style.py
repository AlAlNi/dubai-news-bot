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


_LEADING_EMOJI = re.compile(r"^(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]\s*)+")
_TOPIC_EMOJI = (
    (r"\bбанк\w*|\bплат[её]ж\w*|\bфинанс\w*", "🏦"),
    (r"\bавиа\w*|\bаэропорт\w*|\bрейс\w*", "✈️"),
    # A trip (поездка) is not a train, and a route need not be a bus route.
    (r"\bметро\b|\bпоезд(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b|\bжелезнодорож\w*", "🚇"),
    (r"\bавтобус\w*", "🚌"),
    (r"\bмузе\w*|\bвыставк\w*|\bтеатр\w*|\bфестивал\w*", "🎭"),
    (r"\bшкол\w*|\bобразован\w*|\bуниверситет\w*", "🎓"),
    (r"\bмедицин\w*|\bбольниц\w*|\bклиник\w*", "🏥"),
    (r"\bнедвижим\w*|\bжиль\w*|\bаренд\w*", "🏠"),
)
_BODY_EMOJI = (
    (r"\bтестов\w*|\bиспытан\w*|\bsandbox\b", "🧪"),
    (r"\bтрансгранич\w*|\bвалют\w*", "💱"),
    (r"\bплат[её]ж\w*|\bоплат\w*|\bрасч[её]т\w*", "💳"),
    (r"\bтехнолог\w*|\bцифров\w*", "💻"),
    (r"\bстоимост\w*|\bцен[ауы]\b|\bтариф\w*", "💰"),
    (r"\bсентябр\w*|\bоктябр\w*|\bноябр\w*|\bдекабр\w*|\bянвар\w*|"
     r"\bфеврал\w*|\bмарт[ае]?\b|\bапрел\w*|\bма[йяе]\b|\bию[нл][ьяе]\b|"
     r"\bавгуст\w*", "📅"),
    (r"\b(?:груз(?:а|у|ом|е|ы|ов|ам|ами|ах)?|грузов\w*|"
     r"грузоперевоз\w*|грузопод[ъь][её]м\w*)\b", "📦"),
    *_TOPIC_EMOJI,
)


_FACT_DETAIL = re.compile(
    r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
    r"сентября|октября|ноября|декабря)(?:\s+\d{4}\s+года)?\b|"
    r"\b(?:(?:до|от|около|почти|более|менее|не\s+более|не\s+менее)\s+)?"
    r"\d+(?:[.,]\d+)?(?:\s+(?:тыс\.|млн|млрд))?\s+"
    r"(?:тонн\w*|дирхам\w*|доллар\w*|AED|USD)\b", re.I)


def emphasize_details(text):
    """Highlight at most two existing short facts; never decorate quotes or tags."""
    title, separator, body = text.partition("\n\n")
    if not separator:
        return text
    remaining = max(0, 2 - len(re.findall(r"<b>", body)))
    stack = []
    pieces = []

    def highlight(match):
        nonlocal remaining
        value = match.group()
        start = match.string.rfind("\n\n", 0, match.start()) + 2
        if start == 1:
            start = 0
        end = match.string.find("\n\n", match.end())
        paragraph = match.string[start:end if end >= 0 else len(match.string)]
        if not remaining or len(value) > 60 or paragraph.strip() == value:
            return value
        remaining -= 1
        return "<b>" + value + "</b>"

    for token in re.split(r"(<[^>]*>)", body):
        if token.startswith("<"):
            if token.startswith("</"):
                if stack:
                    stack.pop()
            else:
                stack.append(token)
            pieces.append(token)
        else:
            pieces.append(_FACT_DETAIL.sub(highlight, token) if not stack else token)
    return title + separator + "".join(pieces)


def contextual_emoji(text):
    """Decorate only recognizable topics; never edit words or direct quotations."""
    text = emphasize_details(format_summary(text))
    paragraphs = re.split(r"\n\n(?!(?:(?!<blockquote>).)*</blockquote>)", text, flags=re.S)
    used = set()
    for index, paragraph in enumerate(paragraphs):
        if "<blockquote>" in paragraph or not paragraph.strip():
            continue
        plain = unescape(re.sub(r"<[^>]*>", "", paragraph))
        rules = _TOPIC_EMOJI if index == 0 else _BODY_EMOJI
        emoji = next((symbol for pattern, symbol in rules
                      if symbol not in used and re.search(pattern, plain, re.I)), None)
        if emoji is None:
            continue
        if index == 0:
            title = re.sub(r"<[^>]*>", "", paragraph)
            paragraphs[index] = "<b>" + emoji + " " + _LEADING_EMOJI.sub("", title) + "</b>"
        else:
            paragraphs[index] = emoji + " " + _LEADING_EMOJI.sub("", paragraph)
        used.add(emoji)
        if len(used) >= 4:
            break
    return "\n\n".join(paragraphs)


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
