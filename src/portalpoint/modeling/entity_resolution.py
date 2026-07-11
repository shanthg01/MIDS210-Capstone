"""Shared name-normalisation and fuzzy-matching for player + school resolution.

Extracted from scripts/ingest_transfers_247sports.py so both the deterministic
247Sports ETL script and the news-monitoring agent call the same matcher instead
of diverging over time.  No DB imports — pure stdlib.
"""
from __future__ import annotations

import difflib
import unicodedata

# 247sports institution name → schools.name.  Same canonical set as
# ESPN_TEAM_ALIASES (ingest_hoopr.py) / TEAM_NAME_ALIASES (ingest_barttorvik.py);
# duplicated here rather than cross-imported, matching one-script-per-source
# convention.  Add entries as unmatched-school warnings surface.
SCHOOL_ALIASES: dict[str, str] = {
    "Penn State": "Penn St.",
    "Alcorn State": "Alcorn St.",
    "Ball State": "Ball St.",
    "Boise State": "Boise St.",
    "California Baptist": "Cal Baptist",
    "FIU": "Florida International",
    "Fresno State": "Fresno St.",
    "Idaho State": "Idaho St.",
    "Iowa State": "Iowa St.",
    "Kansas City": "UMKC",
    "Kansas State": "Kansas St.",
    "Kent State": "Kent St.",
    "Loyola Maryland": "Loyola MD",
    "McNeese": "McNeese State",
    "Morgan State": "Morgan St.",
    "Murray State": "Murray St.",
    "Nicholls": "Nicholls State",
    "Ohio State": "Ohio St.",
    "Omaha": "Nebraska Omaha",
    "Oregon State": "Oregon St.",
    "Pennsylvania": "Penn",
    "Saint Mary's": "St. Mary's",
    "South Carolina Upstate": "USC Upstate",
    "Texas State": "Texas St.",
    "UMass": "Massachusetts",
    "UT Martin": "Tennessee Martin",
    "UTRGV": "UT Rio Grande Valley",
    "Utah State": "Utah St.",
    "Weber State": "Weber St.",
    "Wright State": "Wright St.",
    "College of Charleston": "Charleston",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip accents and generational suffixes for fuzzy matching."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower().strip()
    for suffix in (" jr.", " jr", " sr.", " sr", " ii", " iii", " iv"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return " ".join(name.split())


def resolve_school(raw_name: str | None, school_map: dict[str, int]) -> int | None:
    """Map a raw school name (possibly aliased or fuzzy) to a schools.id."""
    if not raw_name:
        return None
    canonical = SCHOOL_ALIASES.get(raw_name, raw_name)
    if canonical in school_map:
        return school_map[canonical]
    fuzzy = difflib.get_close_matches(canonical, list(school_map.keys()), n=1, cutoff=0.82)
    return school_map.get(fuzzy[0]) if fuzzy else None


def match_player(
    raw_name: str,
    roster: list[tuple[int, str, str]],
    threshold: float = 0.82,
    position: str | None = None,
) -> tuple[int | None, float | None, str]:
    """Match a raw player name to a roster entry.

    Args:
        raw_name:  Extracted name (from news text, 247Sports JSON, etc.).
        roster:    List of (player_id, full_name, position) from the DB.
        threshold: Strict-pass cutoff; relaxed pass uses 0.75.
        position:  Optional position string (PG/SG/SF/PF/C) for pre-filtering.

    Returns:
        ``(player_id, confidence, status_tag)`` where *status_tag* is one of
        ``'matched'``, ``'unmatched'``, ``'ambiguous'``, ``'no_school'``.

    Matching strategy (same as ingest_transfers_247sports.py):
    1. Normalize both query and candidates (accents, suffixes, case).
    2. Position pre-filter: exact position match narrows candidates before
       fuzzy scoring — reduces false ambiguity from same-named players.
    3. Pass 1 — strict threshold (default 0.82).
    4. Pass 2 — relaxed threshold (0.75).
    5. Pass 3 — full-roster fallback when position-filtered set fails.
    6. Position-based disambiguation when multiple candidates remain.
    """
    if not roster:
        return None, None, "no_school"

    norm_query = normalize_name(raw_name)
    norm_roster = [(pid, name, pos, normalize_name(name)) for pid, name, pos in roster]

    if position:
        pos_filtered = [r for r in norm_roster if r[2] == position]
        candidates = pos_filtered if pos_filtered else norm_roster
    else:
        candidates = norm_roster

    norm_names = [r[3] for r in candidates]

    # Pass 1: strict
    matches = difflib.get_close_matches(norm_query, norm_names, n=3, cutoff=threshold)
    if not matches:
        # Pass 2: relaxed
        matches = difflib.get_close_matches(norm_query, norm_names, n=3, cutoff=0.75)
        if not matches:
            # Pass 3: full-roster fallback (only relevant when position pre-filter was applied)
            if candidates is not norm_roster:
                fallback_names = [r[3] for r in norm_roster]
                matches = difflib.get_close_matches(norm_query, fallback_names, n=3, cutoff=0.75)
                candidates = norm_roster
            if not matches:
                return None, None, "unmatched"

    if len(matches) == 1:
        pid, _name, _pos, _ = next(r for r in candidates if r[3] == matches[0])
        confidence = difflib.SequenceMatcher(None, norm_query, matches[0]).ratio()
        return pid, round(confidence, 3), "matched"

    # Multiple candidates — position disambiguation
    if position:
        pos_matches = [
            m for m in matches
            if next(r for r in norm_roster if r[3] == m)[2] == position
        ]
        if len(pos_matches) == 1:
            pid, _name, _pos, _ = next(r for r in norm_roster if r[3] == pos_matches[0])
            confidence = difflib.SequenceMatcher(None, norm_query, pos_matches[0]).ratio()
            return pid, round(confidence, 3), "matched"

    return None, None, "ambiguous"
