"""
scripts/ingest_hoopr.py

Ingests hoopR ESPN play-by-play data into PostgreSQL.

Data source: sportsdataverse GitHub releases
  https://github.com/sportsdataverse/sportsdataverse-data/releases/download/
  espn_mens_college_basketball_pbp/play_by_play_{season}.parquet

Populates:
  - hoopr_team_season_stats  (~365 D1 teams, 11 PBP-derived features per season)

Raw PBP is NOT stored in PostgreSQL (2.9M rows/season — no row-level query use case).
Aggregated features only. Raw parquet uploaded to s3://portalpoint-data/raw/hoopr/

Coordinate system (ESPN PBP, center-origin, feet):
  - Full-court horizontal axis: -47 to +47, baskets at ±41.75
  - Vertical axis: -25 to +25 (sidelines)
  - Normalize to half-court: norm_x = abs(coordinate_x), rim at (41.75, 0.0)
  - Filter overflow sentinel values: |coordinate_x| <= 50, |coordinate_y| <= 30

Usage:
  # Download current season + ingest
  uv run python scripts/ingest_hoopr.py

  # Use locally cached parquet (skip download)
  uv run python scripts/ingest_hoopr.py --local-parquet notebooks/data/mbb_pbp_2026.parquet

  # Dry run — compute features, report match rates, no DB writes
  uv run python scripts/ingest_hoopr.py --dry-run

  # Multiple seasons
  uv run python scripts/ingest_hoopr.py --season 2025 --season 2026
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import logging
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd
import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portalpoint.db.models import HoopRTeamSeasonStats, School
from portalpoint.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path("data/hoopr")

PBP_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "espn_mens_college_basketball_pbp/play_by_play_{season}.parquet"
)

# ---------------------------------------------------------------------------
# Court geometry — ESPN center-origin coordinate system
# ---------------------------------------------------------------------------
# Full court: x in [-47, +47], baskets at ±41.75 ft from center
# Normalize: norm_x = abs(coordinate_x), norm_y = coordinate_y
# Valid shot coords: |coordinate_x| <= 50, |coordinate_y| <= 30 (filters INT32 sentinels)
RIM_NORM_X = 41.75      # rim position after folding half-courts (norm_x axis)
RIM_NORM_Y = 0.0        # rim at center of backboard (norm_y axis)
COORD_MAX_X = 50.0      # filter out overflow sentinel values
COORD_MAX_Y = 30.0

# Zone distance boundaries from rim (feet)
ZONE1_MAX_DIST = 6.0        # restricted area
ZONE2_MAX_DIST = 20.75      # 3PT arc radius (~22ft corners, 23.75ft top)

# Zone 3 (corner 3): beyond arc AND |norm_y| >= 22
CORNER_3_MIN_Y = 22.0
# Zone 4 (straight-on 3): beyond arc AND |norm_y| < 8
STRAIGHT_3_MAX_Y = 8.0
# Zone 5 (wing 3): everything else beyond arc

# Possession timing
POSS_MAX_SEC = 45.0     # filter noise: possession duration > 45s = invalid
TRANSITION_MAX_SEC = 7.0  # shots within 7s of possession start = transition

# Rim shot event types in ESPN PBP
_RIM_TYPES = {"LayUpShot", "DunkShot", "TipShot"}

# D1 team ESPN IDs (from eda_hoopr.ipynb — 365 teams, 2025-26 season)
D1_TEAM_IDS: set[int] = {
      2,   5,   6,   8,   9,  12,  13,  16,  21,  23,  24,  25,  26,  27,  28,
     30,  36,  38,  41,  43,  44,  45,  46,  47,  48,  50,  52,  55,  56,  57,
     58,  59,  61,  62,  66,  68,  70,  71,  77,  79,  82,  84,  85,  87,  88,
     91,  93,  94,  96,  97,  98,  99, 103, 104, 107, 108, 111, 113, 116, 119,
    120, 127, 130, 135, 139, 140, 142, 145, 147, 149, 150, 151, 152, 153, 154,
    155, 156, 158, 159, 160, 161, 163, 164, 166, 167, 171, 172, 179, 183, 189,
    193, 194, 195, 197, 198, 201, 202, 204, 213, 218, 219, 221, 222, 225, 227,
    228, 231, 232, 233, 235, 236, 238, 239, 242, 245, 248, 249, 250, 251, 252,
    253, 254, 256, 257, 258, 259, 261, 264, 265, 269, 270, 275, 276, 277, 278,
    279, 282, 284, 288, 290, 292, 294, 295, 299, 300, 301, 302, 304, 305, 309,
    311, 314, 315, 322, 324, 325, 326, 328, 331, 333, 338, 339, 344, 349, 350,
    356, 357, 399, 526,
    2000, 2005, 2006, 2010, 2011, 2016, 2026, 2029, 2031, 2032, 2046, 2050,
    2057, 2065, 2066, 2083, 2084, 2086, 2097, 2099, 2110, 2115, 2116, 2117,
    2127, 2130, 2132, 2142, 2154, 2166, 2168, 2169, 2172, 2174, 2181, 2182,
    2184, 2193, 2197, 2198, 2199, 2210, 2217, 2226, 2229, 2230, 2239, 2241,
    2244, 2247, 2250, 2253, 2261, 2272, 2275, 2277, 2287, 2294, 2296, 2305,
    2306, 2309, 2320, 2325, 2329, 2330, 2335, 2344, 2348, 2349, 2350, 2351,
    2352, 2363, 2368, 2377, 2378, 2379, 2382, 2385, 2390, 2393, 2400, 2405,
    2413, 2415, 2426, 2427, 2428, 2429, 2430, 2433, 2437, 2439, 2440, 2441,
    2443, 2447, 2448, 2449, 2450, 2453, 2454, 2458, 2459, 2460, 2463, 2464,
    2466, 2473, 2483, 2492, 2501, 2502, 2504, 2506, 2507, 2509, 2511, 2514,
    2515, 2520, 2523, 2529, 2534, 2535, 2539, 2540, 2541, 2545, 2546, 2547,
    2550, 2561, 2565, 2567, 2569, 2571, 2572, 2579, 2582, 2598, 2599, 2603,
    2608, 2612, 2617, 2619, 2623, 2627, 2628, 2630, 2633, 2634, 2635, 2636,
    2638, 2640, 2641, 2643, 2649, 2653, 2655, 2670, 2674, 2678, 2681, 2692,
    2698, 2710, 2711, 2717, 2724, 2729, 2737, 2739, 2747, 2750, 2751, 2752,
    2754, 2755, 2771, 2803, 2815, 2837, 2856, 2870, 2885, 2900, 2908, 2916,
    2934, 3084, 3101, 112358,
}

# ESPN team name → barttorvik/DB school name
ESPN_TEAM_ALIASES: dict[str, str] = {
    "North Carolina": "UNC",
    "Connecticut": "UConn",
    "Saint Mary's": "St. Mary's",
    "Pitt": "Pittsburgh",
    "Southern California": "Southern Cal",
    "USF": "South Florida",
    "UMass": "Massachusetts",
    "URI": "Rhode Island",
    "UIC": "Illinois Chicago",
    "UIW": "Incarnate Word",
    "ULM": "UL Monroe",
    "UNI": "Northern Iowa",
    "UMES": "Maryland Eastern Shore",
    "UNCW": "UNC Wilmington",
    "UNC Charlotte": "Charlotte",
    "App State": "Appalachian State",
    "Boston U.": "Boston University",
    "FGCU": "Florida Gulf Coast",
    "FDU": "Fairleigh Dickinson",
    "Grambling": "Grambling State",
    "UMKC": "UMKC",
    "LMU": "Loyola Marymount",
    "McNeese": "McNeese State",
    "Middle Tennessee": "Middle Tennessee",
    "NIU": "Northern Illinois",
    "Nicholls": "Nicholls State",
    "North Alabama": "North Alabama",
    "Northern Kentucky": "Northern Kentucky",
    "Nebraska Omaha": "Nebraska Omaha",
    "SFA": "Stephen F. Austin",
    "SIUE": "SIU Edwardsville",
    "Southeastern Louisiana": "Southeastern Louisiana",
    "Southern Illinois": "Southern Illinois",
    "UT Martin": "Tennessee Martin",
    "UTRGV": "UT Rio Grande Valley",
    "Alcorn": "Alcorn State",
    "Bethune-Cookman": "Bethune Cookman",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def _normalize_espn_name(name: str | None) -> str:
    if not name:
        return ""
    return ESPN_TEAM_ALIASES.get(name.strip(), name.strip())


def _fuzzy_match(name: str, candidates: list[str], threshold: float = 0.82) -> str | None:
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=threshold)
    return matches[0] if matches else None


def _build_team_name_map(pbp: pd.DataFrame) -> dict[str, str]:
    """Build {str(float(espn_id)): team_name} matching team_id float64 key format."""
    frames = []
    for id_col, name_col in [("home_team_id", "home_team_name"), ("away_team_id", "away_team_name")]:
        if id_col in pbp.columns and name_col in pbp.columns:
            frames.append(
                pbp[[id_col, name_col]]
                .rename(columns={id_col: "id", name_col: "name"})
                .drop_duplicates()
                .dropna()
            )
    if not frames:
        return {}
    combined = pd.concat(frames).drop_duplicates()
    # team_id column is float64, so keys must be str(float) e.g. "2083.0"
    return (
        combined
        .assign(id=lambda x: x["id"].astype(float).astype(str))
        .set_index("id")["name"]
        .to_dict()
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_parquet(season: int, dest_dir: Path) -> Path:
    url = PBP_URL.format(season=season)
    dest = dest_dir / f"play_by_play_{season}.parquet"
    log.info("downloading %s", url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=600, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
            fh.write(chunk)
    log.info("saved %s (%d MB)", dest.name, dest.stat().st_size // (1024 * 1024))
    return dest


# ---------------------------------------------------------------------------
# Feature computation — matches eda_hoopr.ipynb approach exactly
# ---------------------------------------------------------------------------

def compute_team_features(pbp: pd.DataFrame) -> pd.DataFrame:
    """Aggregate PBP rows into one feature row per D1 team.

    Coordinate system: ESPN center-origin (feet).
    Normalize to half-court: norm_x = abs(coordinate_x), rim at (41.75, 0.0).
    Zone boundaries match eda_hoopr.ipynb spatial_features_df.

    Returns DataFrame indexed by espn_team_name with columns:
        espn_team_id, espn_team_name, games_tracked, possessions_tracked,
        pbp_possession_sec, pbp_rim_pct, pbp_three_pct, pbp_mid_pct,
        pbp_zone1_restricted_pct ... pbp_zone5_wing3_pct,
        pbp_turnover_rate, pbp_transition_rate
    """
    log.info("raw PBP rows: %d", len(pbp))

    # ---- D1 filter ----
    pbp = pbp[
        pbp["home_team_id"].isin(D1_TEAM_IDS) &
        pbp["away_team_id"].isin(D1_TEAM_IDS)
    ].copy()
    log.info("D1-only rows: %d", len(pbp))

    # ---- Team name map ----
    team_name_map = _build_team_name_map(pbp)
    log.info("ESPN ID→name map: %d teams", len(team_name_map))

    # team_id is float64 in PBP; str() gives "2083.0" matching map keys
    pbp["team_name"] = pbp["team_id"].astype(str).map(team_name_map)

    # ---- Shot profile + spatial zones ----
    # Filter to valid shot coordinates (sentinel overflow = INT32_MAX ≈ 2.1e8)
    shots = pbp[pbp["shooting_play"]].copy()
    shots = shots.loc[
        shots["coordinate_x"].abs().le(COORD_MAX_X) &
        shots["coordinate_y"].abs().le(COORD_MAX_Y)
    ].copy()

    # Half-court normalization (EDA approach: fold both baskets to one side)
    shots["norm_x"] = shots["coordinate_x"].abs()
    shots["norm_y"] = shots["coordinate_y"]
    shots["dist_from_rim"] = np.sqrt(
        (shots["norm_x"] - RIM_NORM_X) ** 2 +
        (shots["norm_y"] - RIM_NORM_Y) ** 2
    )

    # Shot categories
    dist       = shots["dist_from_rim"]
    norm_y_abs = shots["norm_y"].abs()
    beyond_arc = dist > ZONE2_MAX_DIST

    shots["is_rim"] = shots["type_text"].isin(_RIM_TYPES)
    if "points_attempted" in shots.columns:
        shots["is_three"] = (shots["points_attempted"] == 3) & ~shots["is_rim"]
    else:
        # pre-2023 schemas lack points_attempted; geometry is equivalent
        shots["is_three"] = beyond_arc & ~shots["is_rim"]
    shots["is_mid"] = ~shots["is_three"] & ~shots["is_rim"]

    # Spatial zones (matching EDA zone boundaries)

    zone_conds = [
        dist <= ZONE1_MAX_DIST,
        (dist > ZONE1_MAX_DIST) & (dist <= ZONE2_MAX_DIST),
        beyond_arc & (norm_y_abs >= CORNER_3_MIN_Y),
        beyond_arc & (norm_y_abs < STRAIGHT_3_MAX_Y),
    ]
    zone_choices = ["zone1_restricted", "zone2_mid", "zone3_corner3", "zone4_straight3"]
    shots["zone"] = np.select(zone_conds, zone_choices, default="zone5_wing3")

    shots_named = shots[shots["team_name"].notna()].copy()

    shot_agg = shots_named.groupby("team_name").agg(
        total_shots=("shooting_play", "count"),
        rim_shots=("is_rim", "sum"),
        three_shots=("is_three", "sum"),
        mid_shots=("is_mid", "sum"),
        z1=("zone", lambda z: (z == "zone1_restricted").sum()),
        z2=("zone", lambda z: (z == "zone2_mid").sum()),
        z3=("zone", lambda z: (z == "zone3_corner3").sum()),
        z4=("zone", lambda z: (z == "zone4_straight3").sum()),
        z5=("zone", lambda z: (z == "zone5_wing3").sum()),
    ).reset_index()

    total = shot_agg["total_shots"].clip(lower=1)
    shot_agg["pbp_rim_pct"]                = shot_agg["rim_shots"]   / total
    shot_agg["pbp_three_pct"]              = shot_agg["three_shots"]  / total
    shot_agg["pbp_mid_pct"]               = shot_agg["mid_shots"]    / total
    shot_agg["pbp_zone1_restricted_pct"]  = shot_agg["z1"] / total
    shot_agg["pbp_zone2_mid_pct"]         = shot_agg["z2"] / total
    shot_agg["pbp_zone3_corner3_pct"]     = shot_agg["z3"] / total
    shot_agg["pbp_zone4_straight3_pct"]   = shot_agg["z4"] / total
    shot_agg["pbp_zone5_wing3_pct"]       = shot_agg["z5"] / total

    # ---- Possession timing (EDA approach) ----
    # start/end_period_seconds_remaining absent in pre-2023 schemas; skip timing features if so.
    _has_timing = (
        "start_period_seconds_remaining" in pbp.columns and
        "end_period_seconds_remaining" in pbp.columns
    )
    if not _has_timing:
        log.warning("timing columns absent for this season — pbp_possession_sec and pbp_transition_rate will be NULL")

    if _has_timing:
        df_w = pbp.sort_values(["game_id", "half", "game_play_number"]).copy()
        df_w["game_id_s"] = df_w["game_id"].astype(str)
        df_w["half_s"]    = df_w["half"].astype(str)

        is_turnover  = df_w["type_text"].str.contains("Turnover|Steal", case=False, na=False)
        is_def_reb   = df_w["type_text"] == "Defensive Rebound"
        is_made_shot = df_w["scoring_play"] & df_w["shooting_play"]
        df_w["poss_end"] = is_turnover | is_def_reb | is_made_shot

        df_w["poss_num"] = (
            df_w.groupby(["game_id_s", "half_s"])["poss_end"]
            .shift(1).fillna(False).astype(int)
        )
        df_w["poss_num"] = df_w.groupby(["game_id_s", "half_s"])["poss_num"].cumsum()
        df_w["poss_id"]  = df_w["game_id_s"] + "_" + df_w["half_s"] + "_" + df_w["poss_num"].astype(str)

        poss_team = (
            df_w[(df_w["shooting_play"] | is_turnover) & df_w["team_name"].notna()]
            .groupby("poss_id")["team_name"]
            .first()
        )

        poss_metrics = df_w.groupby("poss_id").agg(
            start_time=("start_period_seconds_remaining", "max"),
            end_time=("end_period_seconds_remaining", "min"),
        )
        poss_metrics["duration"] = poss_metrics["start_time"] - poss_metrics["end_time"]
        poss_metrics["team_name"] = poss_metrics.index.map(poss_team)
        poss_metrics = poss_metrics.dropna(subset=["team_name"])
        poss_metrics = poss_metrics[poss_metrics["duration"].between(0, POSS_MAX_SEC)]

        tempo_df = (
            poss_metrics.groupby("team_name")["duration"]
            .agg(pbp_possession_sec="mean", n_possessions="count")
            .reset_index()
            .query("n_possessions > 5")
        )
    else:
        is_turnover = pbp["type_text"].str.contains("Turnover|Steal", case=False, na=False)
        tempo_df = pd.DataFrame(columns=["team_name", "pbp_possession_sec", "n_possessions"])

    # ---- Turnover rate ----
    to_counts = (
        pbp[is_turnover & pbp["team_name"].notna()]
        .groupby("team_name")
        .size()
        .reset_index(name="n_turnovers")
    )

    # ---- Transition rate ----
    if _has_timing:
        shots_full = pbp[pbp["shooting_play"]].copy()
        shots_full["play_sec"] = (
            pd.to_numeric(shots_full["start_period_seconds_remaining"], errors="coerce") -
            pd.to_numeric(shots_full["end_period_seconds_remaining"], errors="coerce")
        )
        shots_full["team_name"] = shots_full["team_id"].astype(str).map(team_name_map)
        fast = shots_full[shots_full["play_sec"].between(0, TRANSITION_MAX_SEC) & shots_full["team_name"].notna()]
        transition_counts = (
            fast.groupby("team_name")
            .size()
            .reset_index(name="n_transition_shots")
        )
    else:
        transition_counts = pd.DataFrame(columns=["team_name", "n_transition_shots"])

    # ---- Game counts ----
    game_counts = (
        pbp[["team_name", "game_id"]].dropna(subset=["team_name"])
        .drop_duplicates()
        .groupby("team_name")["game_id"]
        .nunique()
        .reset_index(name="games_tracked")
    )

    # ---- Merge all ----
    result = shot_agg.merge(tempo_df, on="team_name", how="left")
    result = result.merge(to_counts, on="team_name", how="left")
    result = result.merge(transition_counts, on="team_name", how="left")
    result = result.merge(game_counts, on="team_name", how="left")

    result["possessions_tracked"] = result["n_possessions"].fillna(0).astype(int)
    result["pbp_turnover_rate"] = (
        result["n_turnovers"].fillna(0) / result["possessions_tracked"].clip(lower=1)
    )
    result["pbp_transition_rate"] = (
        result["n_transition_shots"].fillna(0) / result["total_shots"].clip(lower=1)
    )

    # Attach ESPN team_id (reverse map: name → id)
    name_to_id = {v: k for k, v in team_name_map.items()}
    result["espn_team_id"] = result["team_name"].map(name_to_id)
    result = result.rename(columns={"team_name": "espn_team_name"})

    log.info("features computed for %d teams", len(result))
    return result


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _build_school_map(session) -> dict[str, int]:
    result = await session.execute(select(School.id, School.name))
    return {row.name: row.id for row in result}


async def _upsert(session, rows: list[dict]) -> int:
    if not rows:
        return 0
    for row in rows:
        stmt = pg_insert(HoopRTeamSeasonStats).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row if k not in ("id", "school_id", "season")}
        stmt = stmt.on_conflict_do_update(index_elements=["school_id", "season"], set_=update_cols)
        await session.execute(stmt)
    await session.commit()
    return len(rows)


async def ingest_features(
    session,
    features: pd.DataFrame,
    school_map: dict[str, int],
    season: int,
    dry_run: bool = False,
) -> int:
    db_names = list(school_map.keys())
    unmatched: list[str] = []
    records: list[dict] = []

    for _, row in features.iterrows():
        espn_name = str(row["espn_team_name"])
        canonical = _normalize_espn_name(espn_name)
        school_id = school_map.get(canonical)
        if school_id is None:
            fuzzy = _fuzzy_match(canonical, db_names)
            if fuzzy:
                school_id = school_map[fuzzy]
                log.debug("fuzzy: '%s' → '%s'", espn_name, fuzzy)
            else:
                unmatched.append(espn_name)
                continue

        records.append({
            "school_id": school_id,
            "season": season,
            "espn_team_id": str(row["espn_team_id"]) if pd.notna(row.get("espn_team_id")) else None,
            "espn_team_name": espn_name,
            "pbp_possession_sec":       _safe_float(row.get("pbp_possession_sec")),
            "pbp_rim_pct":              _safe_float(row.get("pbp_rim_pct")),
            "pbp_three_pct":            _safe_float(row.get("pbp_three_pct")),
            "pbp_mid_pct":              _safe_float(row.get("pbp_mid_pct")),
            "pbp_zone1_restricted_pct": _safe_float(row.get("pbp_zone1_restricted_pct")),
            "pbp_zone2_mid_pct":        _safe_float(row.get("pbp_zone2_mid_pct")),
            "pbp_zone3_corner3_pct":    _safe_float(row.get("pbp_zone3_corner3_pct")),
            "pbp_zone4_straight3_pct":  _safe_float(row.get("pbp_zone4_straight3_pct")),
            "pbp_zone5_wing3_pct":      _safe_float(row.get("pbp_zone5_wing3_pct")),
            "pbp_turnover_rate":        _safe_float(row.get("pbp_turnover_rate")),
            "pbp_transition_rate":      _safe_float(row.get("pbp_transition_rate")),
            "games_tracked":            int(row.get("games_tracked") or 0),
            "possessions_tracked":      int(row.get("possessions_tracked") or 0),
        })

    matched = len(records)
    total   = len(features)
    log.info(
        "teams: %d feature rows, %d matched (%.0f%%), %d unmatched",
        total, matched, 100 * matched / max(total, 1), len(unmatched),
    )
    if unmatched:
        log.warning(
            "unmatched ESPN names (add to ESPN_TEAM_ALIASES):\n  %s",
            "\n  ".join(sorted(set(unmatched))[:30]),
        )
    if dry_run:
        return total
    return await _upsert(session, records)


def _try_s3_upload(local_path: Path, s3_key: str) -> None:
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
# Main
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for season in args.season:
        log.info("=== season %d ===", season)

        if args.local_parquet:
            parquet_path = Path(args.local_parquet)
            if not parquet_path.exists():
                log.error("local parquet not found: %s", parquet_path)
                log.error("re-run EDA notebook to regenerate, or omit --local-parquet to download")
                sys.exit(1)
        else:
            cached = DATA_DIR / f"play_by_play_{season}.parquet"
            alt_cache = Path("notebooks/data") / f"mbb_pbp_{season}.parquet"
            if alt_cache.exists() and not args.force_download:
                log.info("using EDA cache: %s", alt_cache)
                parquet_path = alt_cache
            elif cached.exists() and not args.force_download:
                log.info("using cached parquet: %s", cached)
                parquet_path = cached
            else:
                parquet_path = download_parquet(season, DATA_DIR)

        log.info("loading %s", parquet_path)
        pbp = pd.read_parquet(parquet_path)
        log.info("loaded %d rows, %d cols", len(pbp), len(pbp.columns))

        features = compute_team_features(pbp)

        if args.dry_run:
            log.info("[dry-run] features computed — no DB writes")
            show_cols = [
                "espn_team_name", "pbp_possession_sec", "pbp_rim_pct",
                "pbp_three_pct", "pbp_zone1_restricted_pct",
                "pbp_zone3_corner3_pct", "games_tracked",
            ]
            print(features[[c for c in show_cols if c in features.columns]].head(30).to_string(index=False))
            continue

        async with AsyncSessionLocal() as session:
            school_map = await _build_school_map(session)
            n = await ingest_features(session, features, school_map, season)
            log.info("hoopr_team_season_stats upserted: %d rows", n)

        _date = datetime.now().strftime("%Y-%m-%d")
        _try_s3_upload(parquet_path, f"raw/hoopr/{_date}/play_by_play_{season}.parquet")

    log.info("done")


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest hoopR ESPN PBP data into PostgreSQL")
    p.add_argument(
        "--season",
        type=int,
        action="append",
        default=None,
        metavar="YEAR",
        help="Season end year (e.g. 2026). Repeatable. Default: 2026.",
    )
    p.add_argument(
        "--local-parquet",
        metavar="PATH",
        help=(
            "Path to local .parquet file (skips download). "
            "EDA cache is at notebooks/data/mbb_pbp_2026.parquet. "
            "Single season only."
        ),
    )
    p.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download even if a cached parquet already exists.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute features and print sample — no DB writes.",
    )
    args = p.parse_args()
    if args.season is None:
        args.season = [2026]
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
