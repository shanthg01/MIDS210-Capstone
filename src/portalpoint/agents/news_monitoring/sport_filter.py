"""Deterministic sport scoping for news-monitoring articles.

Rejects non-men's-college-basketball content before classification so football
portal stories (e.g. Darian Mensah) and other-sport portal stories (e.g. baseball
shortstop) do not pollute detected events.
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
# Match path segments and slug tokens like "creighton-basketball-2026-..."
_BASKETBALL_URL_RE = re.compile(
    r"/mens-college-basketball/|/college-basketball/|-basketball(?:/|-|_|\.|$)|/cbb/",
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

# Other sports that also use "transfer portal" language.
_OTHER_SPORT_TEXT_RES: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bshortstop\b",
        r"\bpitcher\b",
        r"\binfielder\b",
        r"\boutfielder\b",
        r"\bbaseball\b",
        r"\bsoftball\b",
        r"\bsoccer\b",
        r"\bhockey\b",
        r"\blacrosse\b",
        r"\bwrestling\b",
        r"\bvolleyball\b",
    )
]

# CBB journalism lexicon — bare "guard"/"forward" are common in headlines.
_BASKETBALL_TEXT_RES: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bmen'?s basketball\b",
        r"\bcollege basketball\b",
        r"\bbasketball player\b",
        r"\bbasketball program\b",
        r"\bbasketball team\b",
        r"\bbasketball coach\b",
        r"\bbasketball\b",
        r"\bhoops?\b",
        r"\bcbb\b",
        r"\bpoint guard\b",
        r"\bshooting guard\b",
        r"\bsmall forward\b",
        r"\bpower forward\b",
        r"\bguard\b",
        r"\bforward\b",
        r"\b(pg|sg|sf|pf)\b",
        r"\bppg\b",
        r"\brebounds?\b",
        r"\bncaa tournament\b",
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

    Rejects clear women's, football, and other-sport signals. When football and
    basketball signals conflict, reject (precision over recall). Ambiguous
    articles with no sport signal are kept — agent search queries are already
    CBB-scoped, and coach-departure headlines often omit the word "basketball".
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

    other_sport = _first_match(_OTHER_SPORT_TEXT_RES, blob)
    if other_sport and not (basketball_url or basketball_text):
        return True, f"other_sport_text:{other_sport}"

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
