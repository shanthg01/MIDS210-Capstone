"""Deterministic sport scoping for news-monitoring articles.

Rejects non-men's-college-basketball content before classification so football
portal stories (e.g. Darian Mensah) do not pollute detected events.
"""
from __future__ import annotations

import re
from typing import Any

# URL path fragments that strongly indicate non-CBB content.
_FOOTBALL_URL_RE = re.compile(
    r"/college-football/|/football/|-football/|/nfl/|/cfb/",
    re.IGNORECASE,
)
_WOMENS_BASKETBALL_URL_RE = re.compile(
    r"/womens-college-basketball/|/women-s-college-basketball/|/wbb/",
    re.IGNORECASE,
)
_BASKETBALL_URL_RE = re.compile(
    r"/mens-college-basketball/|/college-basketball/|-basketball/",
    re.IGNORECASE,
)

# Text signals — football / other sports.
_FOOTBALL_TEXT_RES: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bquarterback\b",
        r"\bwide receiver\b",
        r"\btight end\b",
        r"\blinebacker\b",
        r"\brunning back\b",
        r"\bdefensive end\b",
        r"\bcollege football\b",
        r"\bncaa football\b",
        r"\bgridiron\b",
        r"\bfootball team\b",
        r"\bfootball program\b",
        r"\bfootball player\b",
        r"\bfootball coach\b",
    )
]

_WOMENS_BASKETBALL_TEXT_RES: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwomen'?s basketball\b",
        r"\bwbb\b",
        r"\blady\s+\w+\b",
    )
]

_BASKETBALL_TEXT_RES: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bmen'?s basketball\b",
        r"\bcollege basketball\b",
        r"\bbasketball player\b",
        r"\bbasketball program\b",
        r"\bbasketball team\b",
        r"\bpoint guard\b",
        r"\bshooting guard\b",
        r"\bsmall forward\b",
        r"\bpower forward\b",
        r"\b(pg|sg|sf|pf|c)\b",
    )
]


def _article_blob(article: dict[str, Any]) -> str:
    return " ".join(
        str(article.get(key, "") or "")
        for key in ("title", "content", "url")
    )


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def is_non_basketball_article(article: dict[str, Any]) -> tuple[bool, str | None]:
    """Return ``(is_rejected, reason)`` for articles outside men's CBB scope.

    When football and basketball signals conflict, reject (precision over recall).
    """
    blob = _article_blob(article)
    url = str(article.get("url", "") or "")

    if _WOMENS_BASKETBALL_URL_RE.search(url):
        return True, "womens_basketball_url"
    womens_text = _first_match(_WOMENS_BASKETBALL_TEXT_RES, blob)
    if womens_text:
        return True, f"womens_basketball_text:{womens_text}"

    football_url = _FOOTBALL_URL_RE.search(url)
    football_text = _first_match(_FOOTBALL_TEXT_RES, blob)

    basketball_url = bool(_BASKETBALL_URL_RE.search(url))
    basketball_text = _first_match(_BASKETBALL_TEXT_RES, blob)

    if football_url or football_text:
        if basketball_url or basketball_text:
            return True, "conflicting_sport_signals"
        if football_url:
            return True, "football_url"
        return True, f"football_text:{football_text}"

    return False, None


def filter_basketball_articles(
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition articles into (kept, rejected) men's CBB candidates."""
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for article in articles:
        is_rejected, reason = is_non_basketball_article(article)
        if is_rejected:
            rejected.append({**article, "filtered_reason": reason})
        else:
            kept.append(article)
    return kept, rejected
