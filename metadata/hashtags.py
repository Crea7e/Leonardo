from parsers.base import Trend


def build_hashtags(trend: Trend, keywords: list[str], max_count: int = 30) -> list[str]:
    """Rule-based hashtag builder — no LLM needed."""
    raw = [trend.keyword] + keywords
    tags: list[str] = []
    seen: set[str] = set()

    for kw in raw:
        tag = "#" + kw.strip().lower().replace(" ", "")
        if tag not in seen and len(tag) > 1:
            tags.append(tag)
            seen.add(tag)
        if len(tags) >= max_count:
            break

    return tags
