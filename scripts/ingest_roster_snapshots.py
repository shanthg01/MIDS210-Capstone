"""
scripts/ingest_roster_snapshots.py

Ingests current-roster snapshots from barttorvik.com/rostercast.php into
PostgreSQL — one row per school per scrape date, so roster composition can
be tracked day-to-day during the transfer portal window.

Data source: https://barttorvik.com/rostercast.php?team={SchoolName}
  Gated by a trivial "Verifying Browser..." page — not real bot detection,
  just a hidden form that auto-POSTs js_test_submitted=1 back to itself.
  Replay that POST once with a requests.Session() cookie jar and every
  subsequent GET on that session works. Not in barttorvik's robots.txt
  disallow list (unlike playerstat.php / *.json — see
  scripts/ingest_transfers_247sports.py's docstring for that history).

Team name in the URL must match schools.name exactly — same site as the
existing barttorvik ingest, so no alias step is needed here.

Populates:
  - roster_snapshots         (one row per school per snapshot_date)
  - roster_snapshot_players  (one row per player on that snapshot)

returning_status is computed by us, not given by barttorvik — the base
rostercast.php table carries no departure/incoming markers (confirmed by
inspecting the live page: the Transfers/Seniors/Recruits selects are a
manual "build next year's roster" simulator, not a real status feed):
  - returning:    player_id resolved AND was on this same school last season
  - transfer_in:  player_id resolved AND was on a different school last season
  - new:          player_id unresolved or no prior player_season_stats row
                   (can't distinguish true freshman vs. JUCO from this alone)
"departing" is intentionally not a value here — a departed player isn't on
the current snapshot at all, so detecting it requires diffing two snapshots
or last season's full roster. That's issue #17 items 5/6, not this script.

Usage:
  uv run python scripts/ingest_roster_snapshots.py
  uv run python scripts/ingest_roster_snapshots.py --schools Duke "North Carolina"
  uv run python scripts/ingest_roster_snapshots.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import logging
import re
import sys
import time
from datetime import date, datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portalpoint.db.models import (
    Player,
    PlayerSeasonStats,
    RosterSnapshot,
    RosterSnapshotPlayer,
    School,
)
from portalpoint.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REQUEST_DELAY = 0.5  # seconds between requests — matches existing ingest_barttorvik.py convention
ROSTERCAST_URL = "https://barttorvik.com/rostercast.php"
CURRENT_SEASON = 2026
ROSTERCAST_TEAM_ALIASES = {
    # DB schools.name -> barttorvik rostercast.php team parameter. The core
    # barttorvik stats ingest normalizes these names into DB-friendly labels;
    # rostercast.php still expects the source-site spelling.
    "Cal State Fullerton": "Cal St. Fullerton",
    "Cal State Northridge": "Cal St. Northridge",
    "Florida International": "FIU",
    "Miami": "Miami FL",
    "Mississippi State": "Mississippi St.",
    "Ole Miss": "Mississippi",
    "St. Mary's": "Saint Mary's",
    "UConn": "Connecticut",
}

ROW_RE = re.compile(
    r"<tr>\s*"
    r"<td[^>]*><a[^>]*>([^<]+)</a></td>\s*"
    r"<td[^>]*>([^<]*)</td>\s*"
    r"<td>([^<]*)</td>\s*"
    r"<td>([^<]*)</td>\s*"
    r"<td>([^<]*)</td>\s*"
    r"<td>([^<]*)</td>\s*"
    r"</tr>",
    re.S,
)
TABLE_RE = re.compile(r'<table\s+id="tblData"[^>]*>(.*?)</table>', re.S)


def _safe_float(val: str) -> float | None:
    val = (val or "").strip()
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _make_session() -> requests.Session:
    """One-time JS-challenge bypass: the gate is a hidden form that
    auto-POSTs js_test_submitted=1 back to itself — replaying that once
    sets the js_verified cookie for the rest of this session."""
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    session.post(ROSTERCAST_URL, data={"js_test_submitted": "1"}, timeout=30)
    return session


def rostercast_team_name(school_name: str) -> str:
    return ROSTERCAST_TEAM_ALIASES.get(school_name, school_name)


def fetch_roster(session: requests.Session, team: str) -> list[dict]:
    time.sleep(REQUEST_DELAY)
    resp = session.get(ROSTERCAST_URL, params={"team": rostercast_team_name(team)}, timeout=30)
    resp.raise_for_status()
    return parse_roster(resp.text)


def parse_roster(html: str) -> list[dict]:
    m = TABLE_RE.search(html)
    if not m:
        return []
    rows = []
    for player, yr, ht, mins, ortg, usage in ROW_RE.findall(m.group(1)):
        rows.append({
            "raw_player_name": player.strip(),
            "class_year": yr.strip() or None,
            "height": ht.strip() or None,
            "min_pct": _safe_float(mins),
            "ortg": _safe_float(ortg),
            "usage_rate": _safe_float(usage),
        })
    return rows


# ---------------------------------------------------------------------------
# Player resolution + returning_status
# ---------------------------------------------------------------------------

async def _build_roster_index(session, season: int) -> dict[int, list[tuple[int, str]]]:
    """school_id -> [(player_id, full_name), ...] for `season` — used both to
    fuzzy-match scraped names and to compute returning_status."""
    stmt = (
        select(PlayerSeasonStats.school_id, PlayerSeasonStats.player_id, Player.full_name)
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.season == season)
    )
    result = await session.execute(stmt)
    index: dict[int, list[tuple[int, str]]] = {}
    for school_id, player_id, full_name in result.all():
        index.setdefault(school_id, []).append((player_id, full_name))
    return index


def _match_player(
    raw_name: str,
    roster: list[tuple[int, str]],
    threshold: float = 0.82,
) -> tuple[int | None, float | None, str]:
    if not roster:
        return None, None, "unmatched"
    names = [name for _, name in roster]
    matches = difflib.get_close_matches(raw_name, names, n=2, cutoff=threshold)
    if not matches:
        return None, None, "unmatched"
    if len(matches) > 1:
        return None, None, "ambiguous"
    name = matches[0]
    confidence = difflib.SequenceMatcher(None, raw_name, name).ratio()
    player_id = next(pid for pid, n in roster if n == name)
    return player_id, round(confidence, 3), "matched"


def _player_to_school(roster_index: dict[int, list[tuple[int, str]]]) -> dict[int, int]:
    player_id_to_school: dict[int, int] = {}
    for school_id, roster in roster_index.items():
        for player_id, _ in roster:
            player_id_to_school[player_id] = school_id
    return player_id_to_school


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _chunked(rows: list[dict], size: int = 1000):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


async def _upsert_snapshot(
    session,
    school_id: int,
    season: int,
    snapshot_date: date,
    source: str,
) -> int:
    stmt = pg_insert(RosterSnapshot).values(
        school_id=school_id, season=season, snapshot_date=snapshot_date, source=source,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["school_id", "snapshot_date"],
        set_={"season": stmt.excluded.season, "source": stmt.excluded.source},
    ).returning(RosterSnapshot.id)
    result = await session.execute(stmt)
    snapshot_id = result.scalar_one()
    await session.commit()
    return snapshot_id


async def _bulk_upsert_players(session, rows: list[dict]) -> int:
    if not rows:
        return 0
    conflict_cols = ["snapshot_id", "raw_player_name"]
    exclude = set(conflict_cols) | {"id"}
    for chunk in _chunked(rows):
        stmt = pg_insert(RosterSnapshotPlayer).values(chunk)
        set_cols = {k: stmt.excluded[k] for k in chunk[0] if k not in exclude}
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=set_cols)
        await session.execute(stmt)
    await session.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def ingest_team(
    http_session,
    db_session,
    school_id: int,
    school_name: str,
    season: int,
    snapshot_date: date,
    roster_index: dict[int, list[tuple[int, str]]],
    player_to_school: dict[int, int],
    all_players: list[tuple[int, str]],
    dry_run: bool = False,
) -> dict:
    roster = fetch_roster(http_session, school_name)
    this_school_roster = roster_index.get(school_id, [])

    counts = {"matched": 0, "unmatched": 0, "ambiguous": 0}
    status_counts = {"returning": 0, "transfer_in": 0, "new": 0}
    records = []
    for row in roster:
        # Try this school's own prior roster first (small pool, low ambiguity
        # risk — covers the common "returning player" case). A transfer-in
        # was, by definition, NOT on this school's prior roster, so only the
        # global fallback below can ever find them.
        player_id, confidence, match_status = _match_player(
            row["raw_player_name"],
            this_school_roster,
        )
        if player_id is None:
            player_id, confidence, match_status = _match_player(row["raw_player_name"], all_players)
        counts[match_status] = counts.get(match_status, 0) + 1

        transfer_source_school_id = None
        if player_id is None:
            returning_status = "new"
        else:
            prior_school_id = player_to_school.get(player_id)
            if prior_school_id == school_id:
                returning_status = "returning"
            elif prior_school_id is not None:
                returning_status = "transfer_in"
                transfer_source_school_id = prior_school_id
            else:
                returning_status = "new"
        status_counts[returning_status] = status_counts.get(returning_status, 0) + 1

        records.append({
            "player_id": player_id,
            "raw_player_name": row["raw_player_name"],
            "class_year": row["class_year"],
            "height": row["height"],
            "min_pct": row["min_pct"],
            "ortg": row["ortg"],
            "usage_rate": row["usage_rate"],
            "returning_status": returning_status,
            "transfer_source_school_id": transfer_source_school_id,
            "match_confidence": confidence,
        })

    log.info(
        "%s: %d players | matched %d | ambiguous %d | unmatched %d || "
        "returning %d | transfer_in %d | new %d",
        school_name,
        len(roster),
        counts.get("matched", 0),
        counts.get("ambiguous", 0),
        counts.get("unmatched", 0),
        status_counts["returning"], status_counts["transfer_in"], status_counts["new"],
    )

    if dry_run or not records:
        return {"players": len(records)}

    snapshot_id = await _upsert_snapshot(
        db_session,
        school_id,
        season,
        snapshot_date,
        "barttorvik_rostercast",
    )
    for r in records:
        r["snapshot_id"] = snapshot_id
    n = await _bulk_upsert_players(db_session, records)
    return {"players": n}


async def run(args: argparse.Namespace) -> None:
    snapshot_date = datetime.now().date()
    http_session = _make_session()

    async with AsyncSessionLocal() as db_session:
        result = await db_session.execute(select(School.id, School.name))
        schools = list(result.all())
        if args.schools:
            wanted = set(args.schools)
            schools = [s for s in schools if s.name in wanted]
            missing = wanted - {s.name for s in schools}
            if missing:
                log.warning("schools not found in DB, skipping: %s", sorted(missing))

        log.info("roster snapshot for %d school(s), date=%s", len(schools), snapshot_date)
        roster_index = await _build_roster_index(db_session, CURRENT_SEASON)
        player_to_school = _player_to_school(roster_index)
        all_players = [pair for roster in roster_index.values() for pair in roster]

        total_players = 0
        for school_id, school_name in schools:
            result = await ingest_team(
                http_session,
                db_session,
                school_id,
                school_name,
                CURRENT_SEASON,
                snapshot_date,
                roster_index,
                player_to_school,
                all_players,
                dry_run=args.dry_run,
            )
            total_players += result["players"]

    verb = "[dry-run] computed" if args.dry_run else "upserted"
    log.info(
        "%s %d roster_snapshot_players rows across %d schools",
        verb,
        total_players,
        len(schools),
    )
    log.info("done")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Ingest barttorvik rostercast.php roster snapshots into PostgreSQL"
    )
    p.add_argument(
        "--schools",
        nargs="+",
        metavar="NAME",
        help="Limit to these schools.name values (default: all schools — ~365 requests, ~3 min).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and compute match rates — no DB writes.",
    )
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
