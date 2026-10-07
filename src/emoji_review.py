"""Exact identities of emoji prefixes inserted before the final review."""
PALETTE = ("✈️", "🚇", "🚌", "🚗", "🅿️", "🍽️", "🎭", "🏦", "🏠", "🎓", "🏥", "📅", "📦", "💳", "💱", "💻", "💰", "🧪")
PALETTE += ("🚲", "⚡", "🔋", "🔌", "🏃", "🏅", "🎨", "📚", "🌳", "🌧️", "☀️", "🛒", "🏗️", "📱", "⚖️", "♿")


def inserted_emoji(summary):
    result = []
    for index, paragraph in enumerate(summary.split("\n\n")):
        prefix = paragraph[3:] if index == 0 and paragraph.startswith("<b>") else paragraph
        for symbol in PALETTE:
            if prefix.startswith(symbol + " "):
                result.append({"paragraph": index, "emoji": symbol})
                break
    return result


def valid_emoji_verdicts(summary, verdicts):
    if not isinstance(verdicts, list):
        return False
    expected = {(item["paragraph"], item["emoji"]) for item in inserted_emoji(summary)}
    seen = set()
    for item in verdicts:
        if (not isinstance(item, dict)
                or set(item) != {"paragraph", "emoji", "supported", "reason"}
                or type(item["paragraph"]) is not int
                or not isinstance(item["emoji"], str)
                or item["supported"] is not True
                or not isinstance(item["reason"], str) or not item["reason"].strip()):
            return False
        identity = (item["paragraph"], item["emoji"])
        if identity not in expected or identity in seen:
            return False
        seen.add(identity)
    return seen == expected
