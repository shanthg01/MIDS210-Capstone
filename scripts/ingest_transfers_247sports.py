"""
scripts/ingest_transfers_247sports.py

Ingests college basketball transfer-portal events from 247sports.com into
PostgreSQL.

Data source: https://247sports.com/season/{season}-basketball/transferportal/?sport=basketball&page={page}
  Server-rendered into a `window.__INITIAL_DATA__` JS object literal (not
  strict JSON — has unquoted `undefined` values that need a regex fix before
  json.loads). No JS challenge gate on this domain, unlike barttorvik.

Why 247sports and not barttorvik: barttorvik's own transfer JSON
(`{season}_transfer_stats.json`) has a clean players.barttorvik_id join key,
but robots.txt disallows `/*.json` and `/playerstat.php` — the only two real
transfer sources on that domain. 247sports' transferportal page is not
disallowed.

Populates:
  - transfer_portal_events  (raw staging, every scraped row, matched or not)
  - transfers                (promoted rows only, once player_id resolves —
                               keeps that table's existing NOT NULL player_id
                               contract untouched)

portal_entry_date/commitment_date fill in incrementally across repeated
scrapes: a single scrape only exposes a player's *current* status and its
timestamp. A player scraped while status=Entered records portal_entry_date;
a later scrape after they commit fills commitment_date without erasing the
already-stored portal_entry_date (COALESCE on upsert).

Usage:
  uv run python scripts/ingest_transfers_247sports.py --seasons 2026
  uv run python scripts/ingest_transfers_247sports.py --seasons 2020 2021 2022 2023 2024 2025 2026
  uv run python scripts/ingest_transfers_247sports.py --seasons 2026 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import logging
import re
import sys
import time
import unicodedata
from datetime import date, datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from curl_cffi import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portalpoint.db.models import Player, PlayerSeasonStats, School, Transfer, TransferPortalEvent
from portalpoint.db.session import AsyncSessionLocal
from portalpoint.modeling.availability import sync_portal_candidate_flags
from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.minutes import resolved_minutes_per_game

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REQUEST_DELAY = 0.5  # seconds between requests — be polite
BASE_URL = "https://247sports.com/season/{season}-basketball/transferportal/"

# 247sports institution name -> schools.name. Same mappings already validated
# in ESPN_TEAM_ALIASES (scripts/ingest_hoopr.py) / TEAM_NAME_ALIASES
# (scripts/ingest_barttorvik.py) for the same canonical schools.name set —
# duplicated here rather than cross-imported, matching this repo's existing
# one-script-per-source convention (those two files don't share a dict with
# each other either). Add more here as unmatched-school warnings surface.
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


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00")).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Fetch + parse window.__INITIAL_DATA__
# ---------------------------------------------------------------------------

def fetch_page(season: int, page: int) -> dict:
    """curl_cffi (libcurl/BoringSSL TLS stack, impersonate="chrome") instead of
    plain requests/urllib3 — confirmed live that requests' default TLS
    fingerprint gets 403'd by 247sports' WAF on some networks even with
    identical headers/IP/delay, while curl (and curl_cffi, which mimics
    curl's handshake) passes. requests' fingerprint is widely blocklisted
    since so many scrapers default to it."""
    url = BASE_URL.format(season=season)
    log.debug("fetching season=%d page=%d", season, page)
    time.sleep(REQUEST_DELAY)
    resp = requests.get(
        url,
        params={"sport": "basketball", "page": page},
        impersonate="chrome",
        timeout=30,
    )
    resp.raise_for_status()
    return _parse_initial_data(resp.text)


def _parse_initial_data(html: str) -> dict:
    """Extract window.__INITIAL_DATA__ — a JS object literal, not strict JSON
    (has unquoted `undefined` values and is too large/nested for a naive
    regex match, hence brace-counting to find the real end)."""
    marker = "window.__INITIAL_DATA__ = "
    idx = html.find(marker)
    if idx == -1:
        raise ValueError("window.__INITIAL_DATA__ not found in page — site layout may have changed")
    start = idx + len(marker)
    depth = 0
    end = None
    for i in range(start, len(html)):
        c = html[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("could not find matching closing brace for window.__INITIAL_DATA__")
    blob = re.sub(r":undefined", ":null", html[start:end])
    return json.loads(blob)


def fetch_season(season: int) -> list[dict]:
    """All transfer-portal player records for a season, across all pages."""
    data = fetch_page(season, 1)
    tr = data["main"]["transferResults"]
    players = list(tr["players"])
    page_count = tr["pagination"]["pageCount"]
    log.info("season %d: %d pages, %d total entries", season, page_count, tr["pagination"]["count"])
    for page in range(2, page_count + 1):
        data = fetch_page(season, page)
        players.extend(data["main"]["transferResults"]["players"])
    return [p["player"] for p in players]


# ---------------------------------------------------------------------------
# School + player resolution
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Lowercase, strip accents and generational suffixes for fuzzy matching."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower().strip()
    for suffix in (" jr.", " jr", " sr.", " sr", " ii", " iii", " iv"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return " ".join(name.split())


def _resolve_school(raw_name: str | None, school_map: dict[str, int]) -> int | None:
    if not raw_name:
        return None
    canonical = SCHOOL_ALIASES.get(raw_name, raw_name)
    if canonical in school_map:
        return school_map[canonical]
    fuzzy = difflib.get_close_matches(canonical, list(school_map.keys()), n=1, cutoff=0.82)
    return school_map.get(fuzzy[0]) if fuzzy else None


def _committed_destination(transfer: dict | None) -> str | None:
    """destination is a list (rumored/considered schools before commitment) —
    take the one flagged transferred=True; if none yet (still uncommitted),
    fall back to the first listed."""
    if not transfer or not transfer.get("destination"):
        return None
    dest = transfer["destination"]
    for d in dest:
        if d.get("transferred"):
            return d.get("institution")
    return dest[0].get("institution")


async def _build_school_map(session) -> dict[str, int]:
    result = await session.execute(select(School.id, School.name))
    return {row.name: row.id for row in result}


async def _build_roster(session, school_id: int, season: int) -> list[tuple[int, str, str]]:
    """(player_id, full_name, position) for school_id's roster in `season`.

    barttorvik and 247sports both use the end-year-of-academic-year convention,
    so a player transferring for season=2026 (the 2026-27 cycle) has their most
    recent stats at the from_school filed under season=2026 too (2025-26),
    not season=2025. Verified empirically against a known transfer.
    """
    stmt = (
        select(PlayerSeasonStats.player_id, Player.full_name, Player.position)
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.school_id == school_id, PlayerSeasonStats.season == season)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1], row[2] or "") for row in result.all()]


def _match_player(
    raw_name: str,
    roster: list[tuple[int, str, str]],
    threshold: float = 0.82,
    position_247: str | None = None,
) -> tuple[int | None, float | None, str]:
    """Match a 247Sports player name to a barttorvik roster entry.

    Improvements over naive difflib:
    - Name normalization (accents, generational suffixes, case)
    - Exact position pre-filter when position is available (reduces false ambiguity)
    - Two-pass threshold: strict (threshold) then relaxed (0.75)
    - Position-based disambiguation when multiple candidates match
    """
    if not roster:
        return None, None, "no_school"

    norm_query = _normalize_name(raw_name)
    # (pid, raw_name, position, normalized_name)
    norm_roster = [(pid, name, pos, _normalize_name(name)) for pid, name, pos in roster]

    # Position pre-filter: exact match on DB position (PG/SG/SF/PF/C)
    if position_247:
        pos_filtered = [r for r in norm_roster if r[2] == position_247]
        candidates = pos_filtered if pos_filtered else norm_roster
    else:
        candidates = norm_roster

    norm_names = [r[3] for r in candidates]

    # Pass 1: strict threshold
    matches = difflib.get_close_matches(norm_query, norm_names, n=3, cutoff=threshold)
    # Pass 2: relaxed threshold
    if not matches:
        matches = difflib.get_close_matches(norm_query, norm_names, n=3, cutoff=0.75)
        if not matches:
            # If position-filtered candidates failed, retry on full roster at relaxed threshold
            if candidates is not norm_roster:
                fallback_names = [r[3] for r in norm_roster]
                matches = difflib.get_close_matches(norm_query, fallback_names, n=3, cutoff=0.75)
                candidates = norm_roster  # switch to full roster for lookup below
            if not matches:
                return None, None, "unmatched"

    if len(matches) == 1:
        pid, name, pos, _ = next(r for r in candidates if r[3] == matches[0])
        confidence = difflib.SequenceMatcher(None, norm_query, matches[0]).ratio()
        return pid, round(confidence, 3), "matched"

    # Multiple candidates — try position to pick one
    if position_247:
        pos_matches = [m for m in matches if next(r for r in norm_roster if r[3] == m)[2] == position_247]
        if len(pos_matches) == 1:
            pid, name, pos, _ = next(r for r in norm_roster if r[3] == pos_matches[0])
            confidence = difflib.SequenceMatcher(None, norm_query, pos_matches[0]).ratio()
            return pid, round(confidence, 3), "matched"

    return None, None, "ambiguous"


# ---------------------------------------------------------------------------
# Build + upsert records
# ---------------------------------------------------------------------------

def _chunked(rows: list[dict], size: int = 1000):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def _dedupe_by_key(rows: list[dict], key_cols: list[str]) -> list[dict]:
    """Postgres can't ON CONFLICT DO UPDATE the same row twice within one
    multi-row INSERT — last occurrence wins. Used where duplicates are rare
    edge cases (promotion's player_id collisions), not the expected case
    handled by _merge_events_by_key below."""
    deduped: dict[tuple, dict] = {}
    for row in rows:
        deduped[tuple(row[c] for c in key_cols)] = row
    return list(deduped.values())


def _merge_events_by_key(rows: list[dict], key_cols: list[str]) -> list[dict]:
    """247sports lists one card per lifecycle stage, not one per player —
    a player who entered the portal and has since committed appears TWICE
    on the same season's listing (confirmed live: Dasonte Bowen had a
    status=Entered row dated 2026-03-30 and a status=Committed row dated
    2026-06-17, same source/destination, same source_player_key). These are
    two snapshots of one transfer, not two transfers — merge them into one
    row instead of picking one and discarding the other's date, otherwise
    portal_entry_date or commitment_date silently disappears."""
    merged: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row[c] for c in key_cols)
        if key not in merged:
            merged[key] = dict(row)
            continue
        existing = merged[key]
        # Committed is the more authoritative/final lifecycle stage — prefer
        # its school/status fields, but keep whichever dates either row has.
        primary, other = (row, existing) if row["status"] == "Committed" else (existing, row)
        combined = dict(primary)
        combined["portal_entry_date"] = primary["portal_entry_date"] or other["portal_entry_date"]
        combined["commitment_date"] = primary["commitment_date"] or other["commitment_date"]
        merged[key] = combined
    return list(merged.values())


async def _bulk_upsert_events(session, rows: list[dict]) -> int:
    if not rows:
        return 0
    conflict_cols = ["source", "source_player_key", "season"]
    rows = _merge_events_by_key(rows, conflict_cols)
    exclude = set(conflict_cols) | {"id", "portal_entry_date"}
    for chunk in _chunked(rows):
        stmt = pg_insert(TransferPortalEvent).values(chunk)
        set_cols = {k: stmt.excluded[k] for k in chunk[0] if k not in exclude}
        # COALESCE so a later scrape that only has commitment_date (player
        # already committed) doesn't erase an earlier scrape's portal_entry_date.
        set_cols["portal_entry_date"] = func.coalesce(
            TransferPortalEvent.portal_entry_date, stmt.excluded.portal_entry_date
        )
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=set_cols)
        await session.execute(stmt)
    await session.commit()
    return len(rows)


async def ingest_season(session, season: int, school_map: dict[str, int], dry_run: bool = False) -> dict:
    players = fetch_season(season)
    log.info("season %d: fetched %d player records", season, len(players))

    # Key: (school_id, season) to support multi-season fallback lookups
    roster_cache: dict[tuple[int, int], list[tuple[int, str, str]]] = {}
    records: list[dict] = []
    counts = {"matched": 0, "unmatched": 0, "ambiguous": 0, "no_school": 0}

    async def _get_roster(school_id: int, szn: int) -> list[tuple[int, str, str]]:
        key = (school_id, szn)
        if key not in roster_cache:
            roster_cache[key] = await _build_roster(session, school_id, szn)
        return roster_cache[key]

    for p in players:
        transfer = p.get("transfer") or {}
        from_name = (transfer.get("source") or {}).get("institution")
        to_name = _committed_destination(transfer)
        from_school_id = _resolve_school(from_name, school_map)
        to_school_id = _resolve_school(to_name, school_map)

        raw_name = f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
        position_247 = p.get("position") or None  # PG/SG/SF/PF/C from 247Sports

        player_id = confidence = None
        status_tag = "no_school"
        if from_school_id is not None:
            roster = await _get_roster(from_school_id, season)
            player_id, confidence, status_tag = _match_player(raw_name, roster, position_247=position_247)

            # Fallback: try previous season's roster (season-labeling edge cases,
            # grad transfers whose last barttorvik row is season-1)
            if status_tag in ("unmatched", "ambiguous") and season > 2021:
                prev_roster = await _get_roster(from_school_id, season - 1)
                pid2, conf2, tag2 = _match_player(raw_name, prev_roster, position_247=position_247)
                if tag2 == "matched":
                    player_id, confidence, status_tag = pid2, conf2, "matched"

        counts[status_tag] += 1

        status = p.get("status") or "Unknown"
        # statusDate/transferDate are card-specific (when *this* status became
        # current); transferCommitDateTime is static across all of a player's
        # cards (their eventual final commit date) — confirmed live: an
        # Entered-status card and a Committed-status card for the same player
        # had identical transferCommitDateTime but different statusDate.
        event_date = _parse_date(p.get("statusDate") or p.get("transferDate"))
        records.append({
            "season": season,
            "source": "247sports",
            "source_player_key": str(p["key"]),
            "player_id": player_id,
            "raw_player_name": raw_name,
            "match_confidence": confidence,
            "match_status": status_tag,
            "from_school_id": from_school_id,
            "to_school_id": to_school_id,
            "from_institution_raw": from_name,
            "to_institution_raw": to_name,
            "status": status,
            "portal_entry_date": event_date if status == "Entered" else None,
            "commitment_date": event_date if status == "Committed" else None,
        })

    total = len(records)
    log.info(
        "season %d: matched %d (%.1f%%) | ambiguous %d | unmatched %d | no_school %d",
        season, counts["matched"], 100 * counts["matched"] / max(total, 1),
        counts["ambiguous"], counts["unmatched"], counts["no_school"],
    )
    if dry_run:
        return {"events": total, "promoted": 0}

    n_events = await _bulk_upsert_events(session, records)
    n_promoted = await _promote_transfers(session, season)
    return {"events": n_events, "promoted": n_promoted}


async def _promote_transfers(session, season: int) -> int:
    """Upsert matched transfer_portal_events rows into `transfers`, backfilling
    pre_per/pre_minutes_per_game/pre_usage_rate from that player's own
    player_season_stats at `season` (their last season at from_school —
    see _build_roster's docstring for the season-convention note). MPG is
    derived from min_pct because player_season_stats.minutes_per_game is a
    legacy, unreliable BartTorvik field in local data."""
    result = await session.execute(
        select(TransferPortalEvent).where(
            TransferPortalEvent.season == season,
            TransferPortalEvent.match_status == "matched",
        )
    )
    events = result.scalars().all()
    if not events:
        return 0

    pre_stats_result = await session.execute(
        select(PlayerSeasonStats).where(
            PlayerSeasonStats.season == season,
            PlayerSeasonStats.player_id.in_([e.player_id for e in events]),
        )
    )
    pre_stats = {row.player_id: row for row in pre_stats_result.scalars().all()}

    rows = []
    for e in events:
        pre = pre_stats.get(e.player_id)
        rows.append({
            "player_id": e.player_id,
            "from_school_id": e.from_school_id,
            "to_school_id": e.to_school_id,
            "season": season,
            "portal_entry_date": e.portal_entry_date,
            "commitment_date": e.commitment_date,
            "pre_per": pre.per if pre else None,
            "pre_minutes_per_game": (
                resolved_minutes_per_game(pre.min_pct, pre.minutes_per_game) if pre else None
            ),
            "pre_usage_rate": pre.usage_rate if pre else None,
        })

    conflict_cols = ["player_id", "season"]
    rows = _dedupe_by_key(rows, conflict_cols)
    exclude = set(conflict_cols) | {"id"}
    for chunk in _chunked(rows):
        stmt = pg_insert(Transfer).values(chunk)
        set_cols = {k: stmt.excluded[k] for k in chunk[0] if k not in exclude}
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=set_cols)
        await session.execute(stmt)
    await session.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as session:
        school_map = await _build_school_map(session)
        for season in args.seasons:
            log.info("=== season %d ===", season)
            result = await ingest_season(session, season, school_map, dry_run=args.dry_run)
            if args.dry_run:
                log.info("[dry-run] season %d: %d events computed — no DB writes", season, result["events"])
            else:
                log.info(
                    "season %d: transfer_portal_events upserted=%d, transfers promoted=%d",
                    season, result["events"], result["promoted"],
                )
    if not args.dry_run:
        # Keep player_team_fit_scores.is_portal_candidate fresh as soon as new
        # portal events land, instead of waiting for the next gap-matching
        # rerun or a manual backfill — see portalpoint.modeling.availability.
        flagged = sync_portal_candidate_flags(get_sync_engine(), args.seasons)
        for season, count in flagged.items():
            log.info("season %d: is_portal_candidate flagged on %d existing fit-score rows", season, count)
    log.info("done")


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest 247sports transfer-portal events into PostgreSQL")
    p.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        default=None,
        metavar="YEAR",
        help="Season end year(s), e.g. 2026. Multiple allowed. Default: 2026.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute records and print match-rate summary — no DB writes.",
    )
    args = p.parse_args()
    if args.seasons is None:
        args.seasons = [2026]
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
