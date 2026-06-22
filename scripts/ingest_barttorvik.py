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
import gzip
import hashlib
import json
import logging
import sys
import time
from datetime import datetime
from io import StringIO
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portalpoint.db.models import Player, PlayerSeasonStats, School, TeamSeasonStats
from portalpoint.db.session import AsyncSessionLocal

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://barttorvik.com/"
CACHE_DIR = Path(".torvik_cache")
REQUEST_DELAY = 0.5  # seconds between requests — be polite

# Column names for getadvstats.php — 67-column response.
# Source: eda_barttorvik.ipynb PLAYER_STATS_POSITION_LABELS
# (1-based positions → 0-based indices here).
# BUG FIX: role/role_metric/birth_date are at 1-based positions 65-67 (0-based 64-66),
# NOT at positions 45-47 as previously coded — prior stored role values were from unknown columns.
# Positions 45-64 (0-based 44-63) are partially identified:
#   - col 44 = dunk_pct (confirmed: dunk_made/dunk_att ratio)
#   - cols 57-63 = per-game box stats (ppg validated via pts formula; others provisional)
#   - cols 45-56 = unconfirmed extended metrics (stored as ext_* pending barttorvik docs)
PLAYER_STATS_COLS = [
    # 0-43: documented (EDA PLAYER_STATS_POSITION_LABELS positions 1-44)
    "player", "team", "conf", "gp", "min_per", "ortg", "usage", "efg",
    "ts_pct", "or_pct", "dr_pct", "ast_pct", "to_pct", "ftm", "fta",
    "ft_pct", "fg2m", "fg2a", "fg2_pct", "fg3m", "fg3a", "fg3_pct",
    "blk_pct", "stl_pct", "ftr", "yr", "ht", "rec_rank", "bpm", "drtg",
    "ast_to_ratio", "year", "player_id", "hometown", "rsci", "min_per_game",
    "rim_made", "rim_att", "mid_made", "mid_att", "rim_pct", "mid_pct",
    "dunk_made", "dunk_att",
    # 44-63: extended columns (EDA positions 45-64 — partially identified)
    "dunk_pct",   # 44: dunk% — confirmed (dunk_made/dunk_att)
    "ext_46",     # 45: unknown — often empty
    "ext_47",     # 46: unknown (~117-126, candidate: on-court team OE)
    "ext_48",     # 47: unknown (~118-125, candidate: on-court team DE)
    "ext_49",     # 48: unknown
    "ext_50",     # 49: unknown
    "ext_51",     # 50: unknown (negative float)
    "ext_52",     # 51: unknown
    "ext_53",     # 52: unknown
    "ext_54",     # 53: unknown
    "ext_55",     # 54: unknown (positive ~20-25)
    "ext_56",     # 55: unknown
    "ext_57",     # 56: unknown
    "raw_fpg",    # 57: per-game stat (~0.76) — provisional (fouls or steals/g)
    "raw_apg",    # 58: per-game stat — assists per game (provisional)
    "raw_rpg",    # 59: per-game stat — rebounds per game (provisional)
    "raw_tpg",    # 60: per-game stat — turnovers per game (provisional)
    "raw_x61",    # 61: per-game stat — unconfirmed
    "raw_bpg",    # 62: per-game stat — blocks per game (provisional)
    "raw_ppg",    # 63: per-game stat — points per game (validated via pts formula)
    # 64-66: confirmed (EDA positions 65-67)
    "role",       # 64: role label ("Combo G", "Scoring PG", ...)
    "role_metric",# 65: numeric role score
    "birth_date", # 66: date of birth (YYYY-MM-DD string)
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
        index_col=False,
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


def _try_s3_upload(local_path: Path, s3_key: str) -> None:
    """Upload a single file to S3; log warning and continue on any failure."""
    try:
        _script_dir = Path(__file__).resolve().parent
        _repo_root = next(
            p for p in [_script_dir, *_script_dir.parents]
            if (p / "pyproject.toml").exists()
        )
        import sys as _sys
        _sys.path.insert(0, str(_repo_root / "notebooks" / "utils"))
        from s3_helpers import upload
        upload(local_path, s3_key)
    except Exception as _exc:
        log.warning("S3 upload skipped for %s: %s", local_path.name, _exc)


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


def _infer_position(role, height_inches: int | None) -> str:
    """Map BartTorvik's role label to the canonical player.position field.

    BartTorvik does not expose a plain PG/SG/SF/PF/C column in getadvstats.php,
    but its role labels are position-informative enough for display/defaults.
    """
    role_norm = str(role or "").strip().lower()
    role_map = {
        "pure pg": "PG",
        "scoring pg": "PG",
        "combo g": "SG",
        "wing g": "SG",
        "wing f": "SF",
        "stretch 4": "PF",
        "pf/c": "C",
        "c": "C",
    }
    if role_norm in role_map:
        return role_map[role_norm]
    if height_inches is None:
        return "SG"
    if height_inches <= 73:
        return "PG"
    if height_inches <= 76:
        return "SG"
    if height_inches <= 79:
        return "SF"
    if height_inches <= 82:
        return "PF"
    return "C"


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

def _parse_conf_record(val) -> tuple[int | None, int | None]:
    """Parse conference record string like '12-6' → (12, 6)."""
    s = str(val or "").strip()
    if "-" in s:
        parts = s.split("-")
        try:
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass
    return None, None


async def ingest_team_season_stats(
    session,
    team_rows: list[dict],
    four_factors_rows: list[dict],
    season: int,
    school_map: dict[str, int],
) -> int:
    # BUG FIX: four_factors CSV uses "teamname" column, not "team".
    # Previous code used r.get("team") which always returned None, so ff_map
    # was always empty and four factors were never written to the database.
    ff_map: dict[str, dict] = {}
    for r in four_factors_rows:
        name = _normalize_name(r.get("teamname"))
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

        wins, losses = None, None
        rec = str(r.get("rec") or r.get("record") or "")
        if "-" in rec:
            parts = rec.split("-")
            wins = _safe_int(parts[0])
            losses = _safe_int(parts[1]) if len(parts) > 1 else None

        conf_wins, conf_losses = _parse_conf_record(r.get("con rec."))

        # Four factors columns after pd.read_csv + .lower() strip:
        #   offensive: "efg%", "to%", "or%", "ftr"
        #   defensive: "efg% def", "to% def.", "dr%", "ftr def"
        #   shooting: "3p%", "3pd%", "2p%", "arate", "arated"
        rows.append({
            "school_id": school_id,
            "season": season,
            "adj_o": adj_o,
            "adj_d": adj_d,
            "adj_em": adj_em,
            "barthag": _safe_float(r.get("barthag")),
            "adj_tempo": _safe_float(r.get("adjt")),
            "pace": _safe_float(r.get("adjt")),
            # Offensive four factors
            "efg_pct": _safe_float(ff.get("efg%")),
            "tov_rate": _safe_float(ff.get("to%")),
            "orb_rate": _safe_float(ff.get("or%")),
            "ft_rate": _safe_float(ff.get("ftr")),
            # Defensive four factors (new — were never written before bug fix)
            "efg_pct_def": _safe_float(ff.get("efg% def")),
            "tov_rate_def": _safe_float(ff.get("to% def.")),
            "drb_rate": _safe_float(ff.get("dr%")),
            "ft_rate_def": _safe_float(ff.get("ftr def")),
            # Shooting splits
            "three_pct_off": _safe_float(ff.get("3p%")),
            "three_pct_def": _safe_float(ff.get("3pd%")),
            "two_pct_off": _safe_float(ff.get("2p%")),
            "assist_rate": _safe_float(ff.get("arate")),
            "assist_rate_opp": _safe_float(ff.get("arated")),
            # Team metadata from team_results
            "national_rank": _safe_int(r.get("rank")),
            "wab": _safe_float(r.get("wab")),
            "sos": _safe_float(r.get("sos")),
            "ncsos": _safe_float(r.get("ncsos")),
            "wins": wins,
            "losses": losses,
            "conf_wins": conf_wins,
            "conf_losses": conf_losses,
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
) -> dict[str, int]:
    """Upsert players. Returns {barttorvik_id: player.id}."""
    CLASS_YEAR_MAP = {
        "Fr": "freshman",
        "So": "sophomore",
        "Jr": "junior",
        "Sr": "senior",
        "Gr": "graduate",
    }

    rows = []
    for r in player_rows:
        bart_pid = str(r.get("player_id") or "").strip()
        name = str(r.get("player") or "").strip()
        if not name or not bart_pid:
            continue
        height_inches = _parse_height(r.get("ht"))
        rows.append({
            "full_name": name,
            "position": _infer_position(r.get("role"), height_inches),
            "height_inches": height_inches,
            "class_year": CLASS_YEAR_MAP.get(str(r.get("yr") or "").strip(), "freshman"),
            "hometown": str(r.get("hometown") or "").strip() or None,
            "barttorvik_id": bart_pid,
            "cbbpy_id": None,
            "verbalcommits_id": None,
        })

    # Deduplicate by barttorvik_id (same player across multiple season rows)
    seen: set[str] = set()
    deduped = []
    for row in rows:
        key = row["barttorvik_id"]
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    n = await _upsert(session, Player, ["barttorvik_id"], deduped)
    log.info("players upserted: %d", n)

    result = await session.execute(select(Player.id, Player.barttorvik_id))
    return {row.barttorvik_id: row.id for row in result if row.barttorvik_id}


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
        bart_pid = str(r.get("player_id") or "").strip()
        player_id = player_map.get(bart_pid)
        school_name = _normalize_name(r.get("team"))
        school_id = school_map.get(school_name)

        if not player_id or not school_id:
            continue

        gp = _safe_int(r.get("gp")) or 0
        mpg = _safe_float(r.get("min_per_game"))
        # % of team minutes (0-100); min_per_game is sparsely populated.
        min_pct = _safe_float(r.get("min_per"))
        three_rate, rim_rate, mid_rate = _shot_distribution(r)

        # Minimum quality gate: at least 5 games
        if gp < 5:
            continue

        # birth_date: parse YYYY-MM-DD string → date object
        birth_date_val = None
        bd_str = str(r.get("birth_date") or "").strip()
        if bd_str and bd_str != "nan":
            try:
                from datetime import date as _date
                parts = bd_str.split("-")
                birth_date_val = _date(int(parts[0]), int(parts[1]), int(parts[2]))
            except (ValueError, IndexError):
                pass

        # raw_ppg: per-game points (validated via pts formula; others provisional)
        raw_ppg = _safe_float(r.get("raw_ppg"))
        raw_rpg = _safe_float(r.get("raw_rpg"))
        raw_apg = _safe_float(r.get("raw_apg"))
        raw_spg = _safe_float(r.get("raw_fpg"))   # provisional label for col 57
        raw_bpg = _safe_float(r.get("raw_bpg"))
        raw_tpg = _safe_float(r.get("raw_tpg"))

        rows.append({
            "player_id": player_id,
            "school_id": school_id,
            "season": season,
            "games_played": gp,
            "minutes_per_game": mpg or 0.0,
            "min_pct": min_pct,
            # Traditional — barttorvik per-game cols (provisional; confirmed for ppg)
            "points_per_game": raw_ppg or 0.0,
            "rebounds_per_game": raw_rpg or 0.0,
            "assists_per_game": raw_apg or 0.0,
            "steals_per_game": raw_spg or 0.0,
            "blocks_per_game": raw_bpg or 0.0,
            "turnovers_per_game": raw_tpg or 0.0,
            # Advanced
            "per": None,
            "true_shooting_pct": _safe_float(r.get("ts_pct")),
            "usage_rate": _safe_float(r.get("usage")),
            "assist_rate": _safe_float(r.get("ast_pct")),
            "bpm": _safe_float(r.get("bpm")),
            "win_shares": None,
            # Shot distribution
            "three_point_rate": three_rate,
            "rim_rate": rim_rate,
            "mid_range_rate": mid_rate,
            "assisted_fg_pct": None,
            # Barttorvik advanced — previously labeled but not stored
            "offensive_rating": _safe_float(r.get("ortg")),
            "defensive_rating": _safe_float(r.get("drtg")),
            "efg_pct": _safe_float(r.get("efg")),
            "off_reb_pct": _safe_float(r.get("or_pct")),
            "def_reb_pct": _safe_float(r.get("dr_pct")),
            "tov_pct": _safe_float(r.get("to_pct")),
            "free_throw_rate": _safe_float(r.get("ftr")),
            "ft_pct": _safe_float(r.get("ft_pct")),
            "fg2_pct": _safe_float(r.get("fg2_pct")),
            "fg3_pct": _safe_float(r.get("fg3_pct")),
            "block_pct": _safe_float(r.get("blk_pct")),
            "steal_pct": _safe_float(r.get("stl_pct")),
            "rim_pct": _safe_float(r.get("rim_pct")),
            "mid_pct": _safe_float(r.get("mid_pct")),
            "dunk_made": _safe_int(r.get("dunk_made")),
            "dunk_att": _safe_int(r.get("dunk_att")),
            "barttorvik_role": str(r.get("role") or "").strip() or None,
            "barttorvik_role_metric": _safe_float(r.get("role_metric")),
            "rsci": _safe_float(r.get("rsci")),
            "birth_date": birth_date_val,
            # Quality flags
            "data_complete": False,
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

            # 3. Player stats — endpoint returns headerless CSV; apply positional names
            log.info("fetching player stats")
            player_raw = read_csv_endpoint(
                "getadvstats.php",
                params={"year": season, "csv": 1},
                has_header=False,
                use_cache=use_cache,
            )
            player_rows = []
            for r in player_raw:
                vals = list(r.values())
                named = {
                    PLAYER_STATS_COLS[i]: vals[i]
                    for i in range(min(len(PLAYER_STATS_COLS), len(vals)))
                }
                player_rows.append(named)

            # 4. Upsert
            school_map = await ingest_schools(session, team_rows)
            await ingest_team_season_stats(session, team_rows, ff_rows, season, school_map)
            player_map = await ingest_players(session, player_rows)
            await ingest_player_season_stats(session, player_rows, season, school_map, player_map)

            # Upload raw data files to S3
            _date = datetime.now().strftime("%Y-%m-%d")
            _s3_uploads = [
                (
                    _cache_path(BASE_URL + f"{season}_team_results.csv", None),
                    f"raw/barttorvik/{_date}/{season}_team_results.csv",
                ),
                (
                    _cache_path(BASE_URL + f"{season}_fffinal.csv", None),
                    f"raw/barttorvik/{_date}/{season}_fffinal.csv",
                ),
                (
                    _cache_path(BASE_URL + "getadvstats.php", {"year": season, "csv": 1}),
                    f"raw/barttorvik/{_date}/{season}_player_stats.csv",
                ),
            ]
            for _local, _key in _s3_uploads:
                if _local.exists():
                    _try_s3_upload(_local, _key)

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
