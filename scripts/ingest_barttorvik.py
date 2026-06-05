"""
scripts/ingest_barttorvik.py

Fetches data from barttorvik.com and upserts into PostgreSQL.

Populates:
  - schools             (name, conference, region)
  - team_season_stats   (adj_em, adj_o, adj_d, barthag, pace, four factors)
  - players             (name, position, height, class year, hometown)
  - player_season_stats (usage, ts%, assist rate, bpm, shot distribution)

Usage:
  uv run python scripts/ingest_barttorvik.py
  uv run python scripts/ingest_barttorvik.py --seasons 2023 2024 2025
  uv run python scripts/ingest_barttorvik.py --seasons 2025 --no-cache
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import json
import logging
import time
from io import StringIO
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portalpoint.core.config import settings
from portalpoint.db.base import Base
from portalpoint.db.models import Player, PlayerSeasonStats, School, TeamSeasonStats
from portalpoint.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://barttorvik.com/"
CACHE_DIR = Path(".torvik_cache")
REQUEST_DELAY = 0.5  # seconds between requests — be polite

# Column names for getadvstats.php (positions 1-47 of the 67-column response)
# Source: eda_barttorvik.ipynb PLAYER_STATS_POSITION_LABELS
PLAYER_STATS_COLS = [
    "player", "team", "conf", "gp", "min_per", "ortg", "usage", "efg",
    "ts_pct", "or_pct", "dr_pct", "ast_pct", "to_pct", "ftm", "fta",
    "ft_pct", "fg2m", "fg2a", "fg2_pct", "fg3m", "fg3a", "fg3_pct",
    "blk_pct", "stl_pct", "ftr", "yr", "ht", "rec_rank", "bpm", "drtg",
    "ast_to_ratio", "year", "player_id", "hometown", "rsci", "min_per_game",
    "rim_made", "rim_att", "mid_made", "mid_att", "rim_pct", "mid_pct",
    "dunk_made", "dunk_att", "role", "role_metric", "birth_date",
]

# Barttorvik team name → normalized name (for cross-source matching)
TEAM_NAME_ALIASES: dict[str, str] = {
    "Mississippi": "Ole Miss",
    "Connecticut": "UConn",
    "Miami FL": "Miami",
    "NC State": "NC State",
    "Saint Mary's": "St. Mary's",
    "Louisiana St.": "LSU",
    "Texas Christian": "TCU",
    "Southern Methodist": "SMU",
    "Mississippi St.": "Mississippi State",
    "Alabama Birmingham": "UAB",
    "Central Florida": "UCF",
    "Cal St. Fullerton": "Cal State Fullerton",
    "Cal St. Northridge": "Cal State Northridge",
    "Loyola Maryland": "Loyola (MD)",
    "Loyola Chicago": "Loyola Chicago",
    "VCU": "VCU",
    "UTSA": "UTSA",
    "UTEP": "UTEP",
    "FIU": "Florida International",
    "LIU": "LIU",
}

# Conference → geographic region
CONF_REGION: dict[str, str] = {
    "ACC": "Southeast",
    "SEC": "Southeast",
    "American": "Southeast",
    "SBC": "Southeast",
    "CUSA": "Southeast",
    "Big South": "Southeast",
    "A-Sun": "Southeast",
    "OVC": "Southeast",
    "Southern": "Southeast",
    "Big East": "Mid-Atlantic",
    "A-10": "Mid-Atlantic",
    "Ivy": "Northeast",
    "Patriot": "Northeast",
    "CAA": "Mid-Atlantic",
    "NEC": "Northeast",
    "MAAC": "Northeast",
    "Big Ten": "Midwest",
    "MVC": "Midwest",
    "Horizon": "Midwest",
    "Summit": "Midwest",
    "MAC": "Midwest",
    "Big 12": "Midwest",
    "WCC": "Pacific",
    "Pac-12": "Pacific",
    "Big West": "Pacific",
    "WAC": "West",
    "MWC": "West",
    "BSky": "Pacific",
    "Ind": "Midwest",
}


# ---------------------------------------------------------------------------
# HTTP / cache helpers (ported from eda_barttorvik.ipynb)
# ---------------------------------------------------------------------------

def _cache_path(url: str, params: dict | None) -> Path:
    key = url + json.dumps(params or {}, sort_keys=True)
    return CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".cache")


def fetch_text(
    path: str,
    params: dict | None = None,
    timeout: int = 30,
    use_cache: bool = True,
    gz: bool = False,
) -> str:
    url = BASE_URL + path
    cp = _cache_path(url, params)
    if use_cache and cp.exists():
        log.debug("cache hit %s", path)
        return cp.read_text(encoding="utf-8")
    log.info("fetching %s params=%s", path, params)
    time.sleep(REQUEST_DELAY)
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    content = resp.text
    if gz:
        content = gzip.decompress(resp.content).decode("utf-8")
    CACHE_DIR.mkdir(exist_ok=True)
    cp.write_text(content, encoding="utf-8")
    return content


def read_csv_endpoint(
    path: str,
    params: dict | None = None,
    has_header: bool = True,
    col_names: list[str] | None = None,
    use_cache: bool = True,
) -> list[dict]:
    """Fetch a CSV endpoint and return list of dicts."""
    import pandas as pd

    text = fetch_text(path, params=params, use_cache=use_cache)
    df = pd.read_csv(
        StringIO(text),
        header=0 if has_header else None,
        names=col_names if not has_header else None,
        on_bad_lines="skip",
    )
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df.to_dict("records")


def read_json_endpoint(
    path: str,
    params: dict | None = None,
    use_cache: bool = True,
) -> list[dict]:
    text = fetch_text(path, params=params, use_cache=use_cache)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Transform helpers
# ---------------------------------------------------------------------------

def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    name = str(name).strip()
    return TEAM_NAME_ALIASES.get(name, name)


def _region(conf: str | None) -> str:
    return CONF_REGION.get(str(conf or "").strip(), "Midwest")


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if f != f else f  # NaN check
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    f = _safe_float(val)
    return None if f is None else int(f)


def _parse_height(ht) -> int | None:
    """Convert '6-3' or '75' to inches."""
    if not ht:
        return None
    s = str(ht).strip()
    if "-" in s:
        parts = s.split("-")
        try:
            return int(parts[0]) * 12 + int(parts[1])
        except (ValueError, IndexError):
            return None
    return _safe_int(s)


def _shot_distribution(row: dict) -> tuple[float | None, float | None, float | None]:
    """Compute (three_point_rate, rim_rate, mid_range_rate) from attempt counts."""
    fg2a = _safe_float(row.get("fg2a")) or 0.0
    fg3a = _safe_float(row.get("fg3a")) or 0.0
    rim_att = _safe_float(row.get("rim_att")) or 0.0
    mid_att = _safe_float(row.get("mid_att")) or 0.0
    total = fg2a + fg3a
    if total < 1:
        return None, None, None
    return (
        round(fg3a / total, 4),
        round(rim_att / total, 4),
        round(mid_att / total, 4),
    )


# ---------------------------------------------------------------------------
# DB upsert helpers
# ---------------------------------------------------------------------------

async def _upsert(session, model, conflict_cols: list[str], rows: list[dict]) -> int:
    if not rows:
        return 0
    count = 0
    for row in rows:
        stmt = pg_insert(model).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row if k not in conflict_cols and k != "id"}
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols)
        await session.execute(stmt)
        count += 1
    await session.commit()
    return count


# ---------------------------------------------------------------------------
# Ingest: schools
# ---------------------------------------------------------------------------

async def ingest_schools(session, team_rows: list[dict]) -> dict[str, int]:
    """Upsert schools. Returns {barttorvik_name: school_id}."""
    rows = []
    for r in team_rows:
        name = _normalize_name(r.get("team"))
        conf = str(r.get("conf") or "").strip()
        if not name:
            continue
        rows.append({
            "name": name,
            "conference": conf,
            "city": "",
            "state": "",
            "region": _region(conf),
            "barttorvik_id": str(r.get("team") or "").strip(),
        })

    n = await _upsert(session, School, ["name"], rows)
    log.info("schools upserted: %d", n)

    # Return name → id map for FK lookups
    result = await session.execute(select(School.id, School.name))
    return {row.name: row.id for row in result}


# ---------------------------------------------------------------------------
# Ingest: team season stats
# ---------------------------------------------------------------------------

async def ingest_team_season_stats(
    session,
    team_rows: list[dict],
    four_factors_rows: list[dict],
    season: int,
    school_map: dict[str, int],
) -> int:
    # Build four-factors lookup: normalized_name → row
    ff_map: dict[str, dict] = {}
    for r in four_factors_rows:
        name = _normalize_name(r.get("team"))
        if name:
            ff_map[name] = r

    rows = []
    for r in team_rows:
        name = _normalize_name(r.get("team"))
        school_id = school_map.get(name)
        if not school_id:
            log.warning("school not found: %s", name)
            continue

        ff = ff_map.get(name, {})

        adj_o = _safe_float(r.get("adjoe"))
        adj_d = _safe_float(r.get("adjde"))
        adj_em = round(adj_o - adj_d, 3) if adj_o is not None and adj_d is not None else None

        # Parse wins/losses from record string like "24-8"
        wins, losses = None, None
        rec = str(r.get("rec") or r.get("record") or "")
        if "-" in rec:
            parts = rec.split("-")
            wins = _safe_int(parts[0])
            losses = _safe_int(parts[1]) if len(parts) > 1 else None

        rows.append({
            "school_id": school_id,
            "season": season,
            "adj_o": adj_o,
            "adj_d": adj_d,
            "adj_em": adj_em,
            "barthag": _safe_float(r.get("barthag")),
            "adj_tempo": _safe_float(r.get("adjt")),
            "pace": _safe_float(r.get("adjt")),  # barttorvik adjt ≈ pace
            # Four factors (prefer four-factors endpoint if available)
            "efg_pct": _safe_float(ff.get("efg_pct") or ff.get("efg") or r.get("efg_pct")),
            "tov_rate": _safe_float(ff.get("to_pct") or ff.get("to") or r.get("to_pct")),
            "orb_rate": _safe_float(ff.get("or_pct") or ff.get("or") or r.get("or_pct")),
            "ft_rate": _safe_float(ff.get("ft_rate") or ff.get("ftr") or r.get("ftr")),
            "wins": wins,
            "losses": losses,
            "games_played": (wins or 0) + (losses or 0) or None,
        })

    n = await _upsert(session, TeamSeasonStats, ["school_id", "season"], rows)
    log.info("team_season_stats upserted: %d (season=%d)", n, season)
    return n


# ---------------------------------------------------------------------------
# Ingest: players
# ---------------------------------------------------------------------------

async def ingest_players(
    session,
    player_rows: list[dict],
    school_map: dict[str, int],
) -> dict[str, int]:
    """Upsert players. Returns {barttorvik_player_id: player_id}."""
    CLASS_YEAR_MAP = {"Fr": "freshman", "So": "sophomore", "Jr": "junior", "Sr": "senior", "Gr": "graduate"}

    rows = []
    for r in player_rows:
        bart_pid = str(r.get("player_id") or "").strip()
        name = str(r.get("player") or "").strip()
        if not name or not bart_pid:
            continue
        rows.append({
            "full_name": name,
            "position": "G",  # barttorvik doesn't give position directly; update from cbbpy later
            "height_inches": _parse_height(r.get("ht")),
            "class_year": CLASS_YEAR_MAP.get(str(r.get("yr") or "").strip(), "freshman"),
            "hometown": str(r.get("hometown") or "").strip() or None,
            "cbbpy_id": None,
            "verbalcommits_id": None,
        })

    # Deduplicate by full_name (multiple seasons = same player)
    seen: set[str] = set()
    deduped = []
    for row in rows:
        key = row["full_name"]
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    n = await _upsert(session, Player, ["full_name"], deduped)
    log.info("players upserted: %d", n)

    result = await session.execute(select(Player.id, Player.full_name))
    return {row.full_name: row.id for row in result}


# ---------------------------------------------------------------------------
# Ingest: player season stats
# ---------------------------------------------------------------------------

async def ingest_player_season_stats(
    session,
    player_rows: list[dict],
    season: int,
    school_map: dict[str, int],
    player_map: dict[str, int],
) -> int:
    rows = []
    for r in player_rows:
        name = str(r.get("player") or "").strip()
        player_id = player_map.get(name)
        school_name = _normalize_name(r.get("team"))
        school_id = school_map.get(school_name)

        if not player_id or not school_id:
            continue

        gp = _safe_int(r.get("gp")) or 0
        mpg = _safe_float(r.get("min_per_game"))
        three_rate, rim_rate, mid_rate = _shot_distribution(r)

        # Minimum quality gate: at least 5 games
        if gp < 5:
            continue

        rows.append({
            "player_id": player_id,
            "school_id": school_id,
            "season": season,
            "games_played": gp,
            "minutes_per_game": mpg or 0.0,
            # Traditional (not in barttorvik — filled by cbbpy ingest)
            "points_per_game": 0.0,
            "rebounds_per_game": 0.0,
            "assists_per_game": 0.0,
            "steals_per_game": 0.0,
            "blocks_per_game": 0.0,
            "turnovers_per_game": 0.0,
            # Advanced
            "per": None,  # not provided by barttorvik
            "true_shooting_pct": _safe_float(r.get("ts_pct")),
            "usage_rate": _safe_float(r.get("usage")),
            "assist_rate": _safe_float(r.get("ast_pct")),
            "bpm": _safe_float(r.get("bpm")),
            "win_shares": None,
            # Shot distribution
            "three_point_rate": three_rate,
            "rim_rate": rim_rate,
            "mid_range_rate": mid_rate,
            "assisted_fg_pct": None,  # not in barttorvik player stats
            # Quality flags
            "data_complete": False,  # will be True after cbbpy fills traditional stats
            "minutes_threshold_met": gp >= 10,
        })

    n = await _upsert(session, PlayerSeasonStats, ["player_id", "school_id", "season"], rows)
    log.info("player_season_stats upserted: %d (season=%d)", n, season)
    return n


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run(seasons: list[int], use_cache: bool = True) -> None:
    log.info("starting barttorvik ingest for seasons=%s", seasons)

    async with AsyncSessionLocal() as session:
        for season in seasons:
            log.info("── season %d ──────────────────────────────────", season)

            # 1. Team ratings
            log.info("fetching team ratings")
            team_rows = read_csv_endpoint(
                f"{season}_team_results.csv",
                use_cache=use_cache,
            )
            if not team_rows:
                log.warning("no team data for season %d — skipping", season)
                continue

            # 2. Four factors
            log.info("fetching four factors")
            ff_rows = read_csv_endpoint(
                f"{season}_fffinal.csv",
                use_cache=use_cache,
            )

            # 3. Player stats
            log.info("fetching player stats")
            player_raw = read_csv_endpoint(
                "getadvstats.php",
                params={"year": season, "csv": 1},
                use_cache=use_cache,
            )
            # Apply known column names if the endpoint returns positional headers
            if player_raw and all(k.startswith("col") or k.isdigit() for k in list(player_raw[0].keys())[:3]):
                keys = list(player_raw[0].keys())
                player_rows = []
                for r in player_raw:
                    vals = list(r.values())
                    named = {PLAYER_STATS_COLS[i]: vals[i] for i in range(min(len(PLAYER_STATS_COLS), len(vals)))}
                    player_rows.append(named)
            else:
                # Headers already present — normalize to our known names
                col_remap = {
                    "min%": "min_per", "o-rtg": "ortg", "ts%": "ts_pct",
                    "or%": "or_pct", "dr%": "dr_pct", "ast%": "ast_pct", "to%": "to_pct",
                }
                player_rows = [
                    {col_remap.get(k.lower().strip(), k.lower().strip()): v for k, v in r.items()}
                    for r in player_raw
                ]

            # 4. Upsert
            school_map = await ingest_schools(session, team_rows)
            await ingest_team_season_stats(session, team_rows, ff_rows, season, school_map)
            player_map = await ingest_players(session, player_rows, school_map)
            await ingest_player_season_stats(session, player_rows, season, school_map, player_map)

    log.info("ingest complete")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest barttorvik data into PostgreSQL")
    parser.add_argument(
        "--seasons",
        nargs="+",
        type=int,
        default=[2025],
        help="Season year(s) to ingest (e.g. 2023 2024 2025). Year = season end year.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass file cache and re-fetch all endpoints",
    )
    args = parser.parse_args()
    asyncio.run(run(args.seasons, use_cache=not args.no_cache))


if __name__ == "__main__":
    main()
